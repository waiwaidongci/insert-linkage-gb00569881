"""签核单据、流水登记与召回版本的发布门槛。

每次登记都在实物顺序上重算全部批次。任何一步库存不足时，候选版本记为
暂缓发布并保存原因；已发布版本保持原样，交付页面继续读取上一版。同一笔
调拨以请求键去重，重试不会重复扣库。
"""

from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domain import recall as domain
from src.models.recall import RecallEvent, RecallLedger, RecallSignoff, RecallVersion
from src.utils.time import iso, now


LEDGER_ID = 1


def _stamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError('发生时间需要明确时区')
    return value.astimezone(UTC).replace(tzinfo=None)


def event_dict(item):
    if isinstance(item, dict):
        return dict(item)
    return {
        'id': item.id, 'kind': item.kind, 'location': item.location, 'batch_no': item.batch_no,
        'from_location': item.from_location, 'to_location': item.to_location,
        'quantity': item.quantity, 'signoff_id': item.signoff_id, 'request_key': item.request_key,
        'occurred_at': item.occurred_at, 'author': item.author, 'created_at': item.created_at,
        'voided_at': item.voided_at, 'voided_by': item.voided_by, 'void_reason': item.void_reason,
    }


def view_event(item):
    plain = event_dict(item)
    return {**{key: value for key, value in plain.items() if key != 'occurred_at'},
            'occurred_at': iso(plain['occurred_at']), 'recorded_at': iso(plain.get('created_at'))}


def load_events(db):
    """只取有效流水；作废记录保留在库中供审计，但不参与重算。"""
    return [event_dict(item) for item in db.scalars(
        select(RecallEvent).where(RecallEvent.voided_at.is_(None)).order_by(RecallEvent.id))]


def _serialize(steps):
    return [{**step, 'occurred_at': iso(step['occurred_at'])} for step in steps]


def snapshot(events):
    """按实物顺序重放，存档版本所需的步骤、库存不足与各批号入库件数。

    发布只做随流水线性增长的重放；逐批号的上下界留到交付查询时按需计算，
    避免每次登记都替全部批号求解。
    """
    ordered = domain.physical_order(events)
    locations, steps, shortages, final = domain.timeline(ordered)
    batches = domain.batch_totals(ordered)
    return {
        'order': [event['id'] for event in ordered],
        'locations': locations,
        'steps': _serialize(steps),
        'shortages': [{**item, 'occurred_at': iso(item['occurred_at'])} for item in shortages],
        'batches': {
            key: {'batch_no': entry['batch_no'], 'inbound': entry['inbound']}
            for key, entry in batches.items()
        },
        'totals': final,
    }


def _shortage_reason(shortages):
    first = shortages[0]
    return (f"第 {first['position']} 步调拨需要 {first['required']} 件，"
            f"库位 {first['location']} 当时只有 {max(first['available'], 0)} 件，暂缓发布。")


def _next_number(db):
    latest = db.scalar(select(RecallVersion).order_by(RecallVersion.number.desc()).limit(1))
    return (latest.number if latest else 0) + 1


def publish(db, actor):
    """在当前事务内生成新版本；库存不足时只暂缓发布，不撤销上一版。"""
    snap = snapshot(load_events(db))
    shortages = snap['shortages']
    number = _next_number(db)
    version = RecallVersion(
        number=number, status='blocked' if shortages else 'published',
        reason=_shortage_reason(shortages) if shortages else None,
        snapshot=snap, author=actor, published_at=None if shortages else now(),
    )
    db.add(version)
    db.flush()
    return version


def latest_published(db):
    return db.scalar(select(RecallVersion).where(RecallVersion.status == 'published')
                     .order_by(RecallVersion.number.desc()).limit(1))


def events_from_snapshot(snapshot):
    """从已存档版本重建有序流水，使历史版本不受后来补录或作废影响。"""
    events = []
    for step in snapshot.get('steps', []):
        events.append({
            'id': step['event_id'], 'kind': step['kind'], 'location': step.get('location'),
            'batch_no': step.get('batch_no'), 'from_location': step.get('from_location'),
            'to_location': step.get('to_location'), 'quantity': step['quantity'],
            'occurred_at': datetime.fromisoformat(step['occurred_at']),
        })
    return events


def current_delivery(db, batch_no):
    """按召回批号读取最新已发布版本的上下界；按需计算并缓存到该版本快照。"""
    version = latest_published(db)
    if version is None:
        raise ValueError('尚无已发布的召回版本，请先登记流水。')
    key = batch_no.strip().casefold()
    entry = version.snapshot.get('batches', {}).get(key)
    if entry is None:
        raise LookupError(batch_no)
    cached = entry.get('delivery')
    if cached is not None:
        return version, cached
    events = events_from_snapshot(version.snapshot)
    result = domain.delivery(events, entry['batch_no'])
    snapshot = dict(version.snapshot)
    batches = dict(snapshot['batches'])
    batches[key] = {**entry, 'delivery': result}
    snapshot['batches'] = batches
    version.snapshot = snapshot
    db.commit()
    return version, result


def _validate_event(db, body):
    kind = body.kind
    if kind == 'inbound':
        if not body.location or not body.batch_no or body.signoff_id is None:
            raise ValueError('入库登记需要签核记录、库位、批号和件数。')
        if body.from_location or body.to_location:
            raise ValueError('入库登记不应填写调拨库位。')
        signoff = db.get(RecallSignoff, body.signoff_id)
        if signoff is None:
            raise ValueError('找不到对应的签核记录。')
        if signoff.status != 'signed':
            raise ValueError('该签核记录尚未签核，不能登记入库。')
    elif kind == 'transfer':
        if not body.from_location or not body.to_location:
            raise ValueError('调拨登记需要起始库位和目的库位。')
        if body.from_location == body.to_location:
            raise ValueError('调拨的起始和目的库位不能相同。')
        if body.location or body.batch_no or body.signoff_id is not None:
            raise ValueError('调拨只登记起止库位、件数和先后顺序。')
    else:
        raise ValueError('流水类型只能是入库或调拨。')
    if len(body.request_key) < 4:
        raise ValueError('请求键过短，请重新提交。')
    return _stamp(body.occurred_at)


def _payload(item):
    return (item.kind, item.location, item.batch_no, item.from_location, item.to_location,
            item.quantity, item.signoff_id, item.occurred_at)


def record_event(db, body, actor):
    """登记流水并生成版本；相同请求键视为重试，返回已有记录且不重复扣库。"""
    occurred_at = _validate_event(db, body)
    existing = db.scalar(select(RecallEvent).where(RecallEvent.request_key == body.request_key))
    if existing is not None:
        expected = (body.kind, body.location, body.batch_no, body.from_location, body.to_location,
                    body.quantity, body.signoff_id, occurred_at)
        if _payload(existing) != expected:
            raise LookupError(body.request_key)
        return existing, True, latest_published(db)
    ledger = db.get(RecallLedger, LEDGER_ID, with_for_update=True)
    if ledger is None:
        try:
            with db.begin_nested():
                db.add(RecallLedger(id=LEDGER_ID, revision=0))
                db.flush()
        except IntegrityError:
            pass  # 另一请求已建好台账锁
        ledger = db.get(RecallLedger, LEDGER_ID, with_for_update=True)
    item = RecallEvent(
        kind=body.kind, location=body.location, batch_no=body.batch_no,
        from_location=body.from_location, to_location=body.to_location,
        quantity=body.quantity, signoff_id=body.signoff_id, request_key=body.request_key,
        occurred_at=occurred_at, author=actor,
    )
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(RecallEvent).where(RecallEvent.request_key == body.request_key))
        if existing is None:
            raise
        if _payload(existing) != (body.kind, body.location, body.batch_no, body.from_location,
                                  body.to_location, body.quantity, body.signoff_id, occurred_at):
            raise LookupError(body.request_key) from None
        return existing, True, latest_published(db)
    ledger.revision += 1
    version = publish(db, actor)
    return item, False, version


def void_event(db, event_id, actor, reason):
    """作废一笔登记的流水并立即生成新版本，用来纠正库存不足的步骤。"""
    item = db.get(RecallEvent, event_id, with_for_update=True)
    if item is None:
        raise LookupError(event_id)
    if item.voided_at is not None:
        return item, latest_published(db)
    item.voided_at = now()
    item.voided_by = actor
    item.void_reason = reason[:200]
    ledger = db.get(RecallLedger, LEDGER_ID, with_for_update=True)
    if ledger is not None:
        ledger.revision += 1
    version = publish(db, actor)
    return item, version


def create_signoff(db, body, actor):
    """签核记录可挂在已通过复核的运行上；入库只能引用已签核记录。"""
    from src.constants.enums import Review
    from src.models.jobs import Run
    if db.scalar(select(RecallSignoff).where(RecallSignoff.code == body.code)) is not None:
        raise LookupError(body.code)
    if body.run_id is not None:
        run = db.get(Run, body.run_id)
        if run is None:
            raise ValueError('找不到关联的运行记录。')
        if run.review != Review.ACCEPTED:
            raise ValueError('只有复核通过的运行可以签核混箱调拨。')
    item = RecallSignoff(code=body.code, title=body.title, run_id=body.run_id,
                         note=body.note or None, signed_by=actor, signed_at=now())
    db.add(item)
    db.flush()
    return item


def seed(db):
    """幂等的演示流水：包含乱序补录与混箱调拨。"""
    if db.scalar(select(RecallEvent.id).limit(1)) is not None:
        return False
    signoff = db.scalar(select(RecallSignoff).where(RecallSignoff.code == '调拨单 CP-2611-1118'))
    if signoff is None:
        signoff = RecallSignoff(code='调拨单 CP-2611-1118', title='减速箱盖线边内衬混箱调拨',
                                run_id=None, note='11 月 18 日两座工厂之间的周转箱调拨。',
                                signed_by='admin', signed_at=now())
        db.add(signoff)
        db.flush()
    rows = (
        ('inbound', 'A-01', 'R-2611-01', None, None, 10, signoff.id, '2026-11-18T08:00:00+08:00', 'seed-in-1'),
        ('inbound', 'A-01', 'R-2611-02', None, None, 6, signoff.id, '2026-11-18T09:00:00+08:00', 'seed-in-2'),
        ('transfer', None, None, 'A-01', 'B-02', 8, None, '2026-11-18T10:00:00+08:00', 'seed-tr-1'),
        ('transfer', None, None, 'A-01', 'C-03', 5, None, '2026-11-18T11:00:00+08:00', 'seed-tr-2'),
        ('transfer', None, None, 'B-02', 'D-04', 4, None, '2026-11-18T12:00:00+08:00', 'seed-tr-3'),
        # 后补的早先入库：录入顺序在最后，实物顺序最前。
        ('inbound', 'B-02', 'R-2611-03', None, None, 4, signoff.id, '2026-11-18T07:30:00+08:00', 'seed-in-3'),
    )
    for kind, location, batch, source, target, quantity, signoff_id, stamp, request_key in rows:
        db.add(RecallEvent(kind=kind, location=location, batch_no=batch,
                           from_location=source, to_location=target, quantity=quantity,
                           signoff_id=signoff_id, request_key=request_key,
                           occurred_at=_stamp(datetime.fromisoformat(stamp)), author='seed'))
        db.flush()
    publish(db, 'seed')
    return True
