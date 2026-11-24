from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.domain import recall as domain
from src.models.identity import User
from src.models.recall import RecallEvent, RecallSignoff, RecallVersion
from src.schemas.requests import RecallEventRequest, SignoffRequest, VoidRequest
from src.services import recall as service
from src.services.audit import record
from src.utils.time import iso

router = APIRouter(prefix='/recall')


def _csv(rows):
    import csv
    import io
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(['库位', '下界', '上界', '库位实物总件数'])
    for row in rows:
        writer.writerow([row['location'], row['lower'], row['upper'], row['stock_total']])
    return '﻿' + buffer.getvalue()


@router.get('/signoffs')
def signoffs(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(RecallSignoff).order_by(RecallSignoff.signed_at.desc()).limit(100))
    return [{'id': item.id, 'code': item.code, 'title': item.title, 'status': item.status,
             'run_id': item.run_id, 'note': item.note, 'signed_by': item.signed_by,
             'signed_at': iso(item.signed_at)} for item in items]


@router.post('/signoffs', status_code=201)
def add_signoff(body: SignoffRequest, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN))):
    try:
        item = service.create_signoff(db, body, actor.username)
    except LookupError:
        db.rollback()
        raise HTTPException(409, '签核编号已存在，请核对调拨单据') from None
    record(db, actor.username, 'recall.signoff.created', item.id, code=item.code, run_id=item.run_id)
    db.commit()
    return {'id': item.id, 'code': item.code, 'title': item.title, 'status': item.status,
            'run_id': item.run_id, 'note': item.note, 'signed_by': item.signed_by,
            'signed_at': iso(item.signed_at)}


@router.get('/events')
def events(limit: int = Query(200, ge=1, le=1000),
           db: Session = Depends(connection), actor: User = Depends(current)):
    """按实物先后返回流水，并附上重放后的逐步总件数。作废记录单独标注。"""
    stored = [service.event_dict(item) for item in db.scalars(select(RecallEvent))]
    active = [item for item in stored if item.get('voided_at') is None]
    ordered = domain.physical_order(active)
    _, steps, shortages, totals = domain.timeline(ordered)
    position = {step['event_id']: step['position'] for step in steps}
    listed = sorted(stored, key=lambda item: (item['occurred_at'], item['id']), reverse=True)[:limit]
    return {
        'events': [{**service.view_event(item), 'physical_position': position.get(item['id']),
                    'voided': item.get('voided_at') is not None} for item in listed],
        'shortages': [{**item, 'occurred_at': iso(item['occurred_at'])} for item in shortages],
        'totals': totals,
    }


@router.post('/events', status_code=201)
def record_event(body: RecallEventRequest, response: Response, db: Session = Depends(connection),
                 actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    try:
        item, duplicate, version = service.record_event(db, body, actor.username)
    except LookupError:
        db.rollback()
        raise HTTPException(409, '相同请求键已用于另一笔流水，请核对后重新提交') from None
    if not duplicate:
        record(db, actor.username, f'recall.{item.kind}.recorded', item.id, quantity=item.quantity,
               version=version.number, status=version.status)
        if version.status == 'blocked':
            record(db, actor.username, 'recall.version.blocked', version.number, reason=version.reason)
    db.commit()
    if duplicate:
        response.status_code = 200
    return {
        'event': service.view_event(item),
        'duplicate': duplicate,
        'version': {'number': version.number, 'status': version.status, 'reason': version.reason} if version else None,
    }


@router.post('/events/{event_id}/void')
def void_event(event_id: int, body: VoidRequest, db: Session = Depends(connection),
               actor: User = Depends(allow(Role.ADMIN))):
    try:
        item, version = service.void_event(db, event_id, actor.username, body.reason)
    except LookupError:
        db.rollback()
        raise HTTPException(404, '找不到该流水记录') from None
    record(db, actor.username, 'recall.event.voided', item.id, reason=body.reason)
    db.commit()
    return {'event': service.view_event(item),
            'version': {'number': version.number, 'status': version.status, 'reason': version.reason} if version else None}


@router.get('/versions')
def versions(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(RecallVersion).order_by(RecallVersion.number.desc()).limit(100))
    return [{'number': item.number, 'status': item.status, 'reason': item.reason,
             'author': item.author, 'created_at': iso(item.created_at),
             'published_at': iso(item.published_at),
             'shortages': len(item.snapshot.get('shortages', [])),
             'batches': sorted(entry['batch_no'] for entry in item.snapshot.get('batches', {}).values())}
            for item in items]


@router.get('/versions/{number}')
def version(number: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.scalar(select(RecallVersion).where(RecallVersion.number == number))
    if item is None:
        raise HTTPException(404, '找不到该召回版本')
    return {'number': item.number, 'status': item.status, 'reason': item.reason,
            'created_at': iso(item.created_at), 'published_at': iso(item.published_at),
            'snapshot': item.snapshot}


@router.get('/delivery')
def delivery(batch_no: str = Query(min_length=1, max_length=80),
             download: bool = Query(False),
             db: Session = Depends(connection), actor: User = Depends(current)):
    """按召回批号给出各库位可能剩余件数的精确上下界。"""
    try:
        version, result = service.current_delivery(db, batch_no)
    except LookupError:
        hint = ('该批号所在版本暂缓发布，请先修正库存不足的步骤。' if _latest_blocked(db)
                else '找不到该批号已发布的召回范围。')
        raise HTTPException(404, hint) from None
    payload = {'version': version.number, 'published_at': iso(version.published_at), **result,
               'pending': _latest_blocked(db) is not None}
    if download:
        filename = f'recall-{result["batch_no"]}-v{version.number}.csv'
        return Response(_csv(result['locations']), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})
    return payload


def _latest_blocked(db):
    """最新一次登记若暂缓发布，返回该版本；作废后重新发布则不再待处理。"""
    latest = db.scalar(select(RecallVersion).order_by(RecallVersion.number.desc()).limit(1))
    return latest if latest is not None and latest.status == 'blocked' else None
