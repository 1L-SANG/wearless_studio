import asyncio
import contextlib
from uuid import uuid4

from fastapi import Request

from app import client_events, repo, routes
from app.request_id import current_request_id
from app.workers.client_alerts import ClientAlerts
from conftest import FakeConn


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params=()):
        self.queries.append((sql, params))

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Conn(FakeConn):
    def __init__(self, rows):
        self.cur = Cursor(rows)

    def cursor(self):
        return self.cur


def test_new_job_keeps_originating_request_id_and_join_preserves_it():
    old = str(uuid4())
    new = str(uuid4())

    async def run(rows):
        conn = Conn(rows)
        token = current_request_id.set(new)
        try:
            job, created = await repo.create_job(conn, user_id='u', project_id='p', kind='analyze',
                payload={}, idempotency_key='key', credits_reserved=0, metadata={})
        finally:
            current_request_id.reset(token)
        return conn, job, created

    conn, _, created = asyncio.run(run([None, {'id': 'job', 'metadata': {'requestId': new}}]))
    assert created
    inserted = next(params[-1].obj for sql, params in conn.cur.queries if 'insert into jobs' in sql)
    assert inserted['requestId'] == new
    conn, job, created = asyncio.run(run([{'id': 'old', 'metadata': {'requestId': old}}]))
    assert not created and job['metadata']['requestId'] == old
    assert not any('insert into jobs' in sql for sql, _ in conn.cur.queries)


def test_job_pricing_does_not_discard_request_id():
    conn = Conn([{'id': 'job'}])
    asyncio.run(repo.set_pending_job_pricing(conn, user_id='u', job_id='job', credits_reserved=1,
                                            metadata={'creditCostVersion': 'v1'}))
    assert 'metadata = metadata ||' in conn.cur.queries[0][0]


def test_withdrawal_deletes_events(client, monkeypatch, make_token):
    deleted = []
    conn = Conn([None])

    @contextlib.asynccontextmanager
    async def connection(request):
        yield conn

    async def purge(conn, user_id, profile, reason):
        assert reason == 'account_delete'
        return 'job'

    async def delete(conn, user_id):
        deleted.append(user_id)

    monkeypatch.setattr(routes, 'get_conn', connection)
    monkeypatch.setattr(routes.personalization, '_start_purge', purge)
    monkeypatch.setattr(client_events, 'delete_user_events', delete)
    response = client.delete('/v1/me/account', headers={'Authorization': 'Bearer ' + make_token()})
    assert response.status_code == 202 and deleted == ['user-1']


def test_post_withdrawal_event_is_discarded(monkeypatch):
    from test_client_events import sample

    async def closed(conn, user_id):
        return True

    monkeypatch.setattr(repo, 'user_account_purge_closed', closed)
    conn = Conn([])
    asyncio.run(client_events.store_event(conn, client_events.sanitize_event(sample()), str(uuid4()), 'hash'))
    assert not any('insert into client_error_events' in sql for sql, _ in conn.cur.queries)


def test_deletion_queries_are_user_scoped():
    user = str(uuid4())
    conn = Conn([])
    asyncio.run(client_events.delete_user_events(conn, user))
    assert conn.cur.queries[1][1] == (user,)
    assert conn.cur.queries[2][1] == (user,)


def test_worker_only_starts_in_api_without_r2(monkeypatch):
    from app import main
    from conftest import make_settings
    from fastapi.testclient import TestClient

    starts = []

    class Pool:
        async def open(self):
            pass

        async def close(self):
            pass

    async def start(worker):
        starts.append(worker.name)

    async def stop(worker):
        pass

    monkeypatch.setattr(main, 'create_pool', lambda url: Pool())
    monkeypatch.setattr(ClientAlerts, 'start', start)
    monkeypatch.setattr(ClientAlerts, 'stop', stop)
    monkeypatch.setattr(main, 'configured_job_kinds', lambda: ('detail_page',))
    with TestClient(main.create_app(make_settings(database_url='local'))):
        pass
    assert not starts
    # API의 다른 워커는 이 테스트에서 기동하지 않게 한다.
    monkeypatch.setattr(main, 'configured_job_kinds', lambda: ('analyze',))
    monkeypatch.setattr(main.DraftAssetReclaimer, 'start', stop)
    monkeypatch.setattr(main.DraftAssetReclaimer, 'stop', stop)
    from app.workers.subscription_biller import _PeriodicWorker
    monkeypatch.setattr(_PeriodicWorker, 'start', stop)
    monkeypatch.setattr(_PeriodicWorker, 'stop', stop)
    with TestClient(main.create_app(make_settings(database_url='local'))):
        pass
    assert starts == ['client-alerts']


def test_http_request_id_reaches_job_insert(client):
    request_id = str(uuid4())
    conn = Conn([{'id': 'new'}])

    @client.app.post('/_test/new-job')
    async def endpoint(request: Request):
        await repo.create_job(conn, user_id='u', project_id='p', kind='analyze', payload={},
                              idempotency_key=None, credits_reserved=0, metadata={})
        return {'created': True}

    response = client.post('/_test/new-job', headers={'X-Client-Request-Id': request_id})
    assert response.status_code == 200
    metadata = next(params[-1].obj for sql, params in conn.cur.queries if 'insert into jobs' in sql)
    assert metadata['requestId'] == request_id
