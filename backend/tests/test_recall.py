"""召回流水与上下界的回归测试：不依赖网络，使用临时 SQLite 文件。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

_TMP = tempfile.mkdtemp(prefix='recall-test-')
os.environ.setdefault('DATABASE_URL', f'sqlite+pysqlite:///{_TMP}/test.db?check_same_thread=false')
os.environ.setdefault('JWT_SECRET', 'test-secret-that-is-long-enough-123456')
os.environ.setdefault('SEED_DEMO', 'false')

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.constants.enums import Role  # noqa: E402
from src.domain import recall as domain  # noqa: E402
from src.models.identity import User  # noqa: E402
from src.models.jobs import Delivery, Run, Schedule  # noqa: E402,F401
from src.models.recall import RecallEvent, RecallSignoff, RecallVersion  # noqa: E402,F401
from src.models.source import Dataset  # noqa: E402,F401
from src.utils.security import hash_password  # noqa: E402


def inbound(event_id, location, batch, qty, at, signoff=1):
    return {'id': event_id, 'kind': 'inbound', 'location': location, 'batch_no': batch,
            'from_location': None, 'to_location': None, 'quantity': qty,
            'signoff_id': signoff, 'occurred_at': at}


def transfer(event_id, source, target, qty, at):
    return {'id': event_id, 'kind': 'transfer', 'location': None, 'batch_no': None,
            'from_location': source, 'to_location': target, 'quantity': qty,
            'signoff_id': None, 'occurred_at': at}


def brute(events, batch):
    ordered = domain.physical_order(events)
    locations, _, _, _ = domain.timeline(ordered)
    key = batch.casefold()
    totals = domain._levels_totals(ordered, locations)
    results = []

    def walk(index, state):
        if index == len(ordered):
            results.append(dict(state))
            return
        event = ordered[index]
        if event['kind'] == 'inbound':
            nxt = dict(state)
            if event['batch_no'].casefold() == key:
                nxt[event['location']] += event['quantity']
            walk(index + 1, nxt)
            return
        source, target, qty = event['from_location'], event['to_location'], event['quantity']
        others = totals[index][source] - state[source]
        for moved in range(max(0, qty - others), min(qty, state[source]) + 1):
            nxt = dict(state)
            nxt[source] -= moved
            nxt[target] += moved
            walk(index + 1, nxt)

    walk(0, {location: 0 for location in locations})
    return {location: (min(item[location] for item in results), max(item[location] for item in results))
            for location in locations}


class TestBounds:
    def test_mixed_order_is_not_fifo(self):
        """先入 RED 后入 BLUE，整箱调走的分配不唯一：上下界必须覆盖两种可能。"""
        events = [
            inbound(1, 'A', 'RED', 4, 1), inbound(2, 'A', 'BLUE', 6, 2),
            transfer(3, 'A', 'B', 5, 3),
        ]
        result = domain.bounds(events, 'RED')
        # RED 可全部留在 A，也可 4 件全进 B；下界 0 说明不能假定先入先出。
        assert result['A'] == (0, 4)
        assert result['B'] == (0, 4)
        assert result == brute(events, 'RED')

    def test_pinned_when_single_batch(self):
        events = [inbound(1, 'A', 'RED', 3, 1), transfer(2, 'A', 'B', 1, 2)]
        assert domain.bounds(events, 'RED') == {'A': (2, 2), 'B': (1, 1)}

    def test_late_backfill_reorders_physically(self):
        """补录的早先入库应按实物时间排在前面，即使 id 更大。"""
        events = [
            inbound(1, 'A', 'RED', 5, 10),          # 实物时间 10
            transfer(2, 'A', 'B', 5, 20),
            inbound(3, 'B', 'BLUE', 2, 5),          # 补录：实物时间 5，早于调拨
        ]
        # 物理顺序：BLUE@B(5) -> RED@A(10) -> 调拨 A->B(20)，B 的总件数足以整取 5 件。
        result = domain.bounds(events, 'RED')
        assert result == brute(events, 'RED')
        assert result['B'] == (5, 5)

    def test_shortage_raises(self):
        events = [inbound(1, 'A', 'RED', 2, 1), transfer(2, 'A', 'B', 5, 2)]
        with pytest.raises(ValueError):
            domain.bounds(events, 'RED')

    @pytest.mark.parametrize('seed', range(6))
    def test_matches_exhaustive_enumeration(self, seed):
        import random
        rng = random.Random(seed)
        locations = ['A', 'B', 'C'][: rng.randint(1, 3)]
        running = {location: 0 for location in locations}
        events = []
        event_id = 0
        for location in locations:
            for _ in range(rng.randint(0, 2)):
                event_id += 1
                qty = rng.randint(1, 3)
                running[location] += qty
                events.append(inbound(event_id, location, rng.choice(['RED', 'BLUE']), qty, event_id))
        for _ in range(rng.randint(0, 4)):
            event_id += 1
            source = rng.choice(locations)
            targets = [loc for loc in locations if loc != source]
            if not targets or running[source] <= 0:
                qty = rng.randint(1, 2)
                running[source] += qty
                events.append(inbound(event_id, source, rng.choice(['RED', 'BLUE']), qty, event_id))
                continue
            target = rng.choice(targets)
            qty = rng.randint(1, running[source])
            running[source] -= qty
            running[target] += qty
            events.append(transfer(event_id, source, target, qty, event_id))
        batches = domain.batch_totals(domain.physical_order(events))
        for key in batches:
            assert domain.bounds(events, key) == brute(events, key)


ACCESS = {'admin': 'admin-demo-2611', 'analyst': 'analyst-demo-2611', 'viewer': 'viewer-demo-2611'}


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        for role in Role:
            db.add(User(username=role.value, role=role, password_hash=hash_password(ACCESS[role.value])))
    from src.main import app
    with TestClient(app) as test_client:
        yield test_client


def token(client, role='admin'):
    return {'Authorization': f'Bearer {client.post("/auth/login", json={"username": role, "password": ACCESS[role]}).json()["token"]}'}


def signoff(client, headers, code='S-1'):
    return client.post('/recall/signoffs', headers=headers,
                       json={'code': code, 'title': '混箱调拨', 'note': ''}).json()['id']


def post(client, headers, **body):
    return client.post('/recall/events', headers=headers, json=body)


class TestApi:
    def test_retry_is_idempotent(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        body = dict(kind='inbound', quantity=10, occurred_at='2026-11-18T08:00:00+08:00',
                    request_key='key-in-1', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        first = post(client, headers, **body)
        assert first.status_code == 201
        again = post(client, headers, **body)
        assert again.status_code == 200 and again.json()['duplicate'] is True
        totals = client.get('/recall/events', headers=headers).json()['totals']
        assert totals['A-01'] == 10

    def test_reused_key_with_different_payload_conflicts(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        base = dict(kind='inbound', quantity=10, occurred_at='2026-11-18T08:00:00+08:00',
                    request_key='key-in-2', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        post(client, headers, **base)
        changed = post(client, headers, **{**base, 'quantity': 11})
        assert changed.status_code == 409

    def test_shortage_blocks_new_version_but_keeps_previous(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        post(client, headers, kind='inbound', quantity=5, occurred_at='2026-11-18T08:00:00+08:00',
             request_key='key-a', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        before = client.get('/recall/delivery', headers=headers, params={'batch_no': 'R-1'}).json()
        blocked = post(client, headers, kind='transfer', quantity=9, occurred_at='2026-11-18T09:00:00+08:00',
                       request_key='key-b', from_location='A-01', to_location='B-02')
        assert blocked.json()['version']['status'] == 'blocked'
        after = client.get('/recall/delivery', headers=headers, params={'batch_no': 'R-1'}).json()
        assert after['version'] == before['version']
        assert after['pending'] is True
        versions = client.get('/recall/versions', headers=headers).json()
        assert [item['status'] for item in versions][0] == 'blocked'

    def test_void_recovers_pending_version(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        post(client, headers, kind='inbound', quantity=5, occurred_at='2026-11-18T08:00:00+08:00',
             request_key='key-c', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        bad = post(client, headers, kind='transfer', quantity=99, occurred_at='2026-11-18T09:00:00+08:00',
                   request_key='key-d', from_location='A-01', to_location='B-02').json()
        voided = client.post(f"/recall/events/{bad['event']['id']}/void", headers=headers,
                             json={'reason': '件数录错'})
        assert voided.json()['version']['status'] == 'published'
        delivery = client.get('/recall/delivery', headers=headers, params={'batch_no': 'R-1'}).json()
        assert delivery['pending'] is False
        assert delivery['locations'][0]['location'] == 'A-01'
        assert delivery['locations'][0]['pinned'] is True

    def test_inbound_requires_signed_record(self, client):
        headers = token(client)
        missing = post(client, headers, kind='inbound', quantity=1, occurred_at='2026-11-18T08:00:00+08:00',
                       request_key='key-e', location='A-01', batch_no='R-1', signoff_id=999)
        assert missing.status_code == 422

    def test_transfer_rejects_batch_fields(self, client):
        headers = token(client)
        result = post(client, headers, kind='transfer', quantity=1, occurred_at='2026-11-18T08:00:00+08:00',
                      request_key='key-f', from_location='A-01', to_location='B-02', batch_no='R-1')
        assert result.status_code == 422

    def test_viewer_cannot_record(self, client):
        headers = token(client, 'viewer')
        result = post(client, headers, kind='transfer', quantity=1, occurred_at='2026-11-18T08:00:00+08:00',
                      request_key='key-g', from_location='A-01', to_location='B-02')
        assert result.status_code == 403

    def test_delivery_csv(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        post(client, headers, kind='inbound', quantity=4, occurred_at='2026-11-18T08:00:00+08:00',
             request_key='key-h', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        response = client.get('/recall/delivery', headers=headers,
                              params={'batch_no': 'R-1', 'download': True})
        assert response.status_code == 200
        assert 'text/csv' in response.headers['content-type']
        assert 'A-01' in response.text

    def test_unknown_batch_returns_404(self, client):
        headers = token(client)
        signoff_id = signoff(client, headers)
        post(client, headers, kind='inbound', quantity=4, occurred_at='2026-11-18T08:00:00+08:00',
             request_key='key-i', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        assert client.get('/recall/delivery', headers=headers, params={'batch_no': 'NOPE'}).status_code == 404

    def test_published_version_survives_later_blocked_events(self, client):
        """后续登记库存不足后，上一版仍按自己的存档流水给出交付范围。"""
        headers = token(client)
        signoff_id = signoff(client, headers)
        post(client, headers, kind='inbound', quantity=5, occurred_at='2026-11-18T08:00:00+08:00',
             request_key='frz-1', location='A-01', batch_no='R-1', signoff_id=signoff_id)
        first = client.get('/recall/delivery', headers=headers, params={'batch_no': 'R-1'}).json()
        post(client, headers, kind='transfer', quantity=99, occurred_at='2026-11-18T09:00:00+08:00',
             request_key='frz-2', from_location='A-01', to_location='B-02')
        after = client.get('/recall/delivery', headers=headers, params={'batch_no': 'R-1'}).json()
        assert after['version'] == first['version']
        assert after['locations'] == first['locations']
        assert after['pending'] is True
