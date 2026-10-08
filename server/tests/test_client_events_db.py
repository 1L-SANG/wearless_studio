"""로컬 테스트 DB에서만 forward migration과 원자 저장, 제한, 원장을 검증한다."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app import client_events as events
from app.workers.client_alerts import ClientAlerts
from test_client_events import sample

DB_URL = os.environ.get('CLIENT_EVENTS_TEST_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:54322/postgres')
assert urlparse(DB_URL).hostname in ('127.0.0.1', 'localhost', '::1'), '로컬 테스트 DB만 허용해요.'
MIGRATION = Path(__file__).resolve().parents[2] / 'supabase/migrations/20261008090000_client_error_events.sql'
LIMITS_MIGRATION = MIGRATION.with_name('20261008100000_client_alert_limits.sql')


@pytest.fixture(scope='module')
def local_db():
    try:
        with psycopg.connect(DB_URL, connect_timeout=3, row_factory=dict_row) as conn:
            if conn.execute("select to_regclass('public.client_error_events') as t").fetchone()['t'] is None:
                conn.execute(MIGRATION.read_text())
            if conn.execute("select to_regclass('public.client_alert_progress') as t").fetchone()['t'] is None:
                conn.execute(LIMITS_MIGRATION.read_text())
    except psycopg.OperationalError as exc:
        message = '로컬 테스트 DB 연결 실패: ' + str(exc).splitlines()[0]
        if os.environ.get('CLIENT_EVENTS_REQUIRE_TEST_DB') == '1':
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    return DB_URL


def run(local_db, body):
    async def main():
        async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as conn:
            try:
                await body(conn)
            finally:
                await conn.rollback()
    asyncio.run(main())


def test_migration_security_and_indexes(local_db):
    with psycopg.connect(local_db, row_factory=dict_row) as conn:
        rows = conn.execute("select relname, relrowsecurity from pg_class where relname = any(%s)",
                            (['client_error_events', 'client_alert_ledger', 'client_event_rate_limits', 'client_event_salts'],)).fetchall()
        assert len(rows) == 4 and all(row['relrowsecurity'] for row in rows)
        assert not conn.execute("select 1 from pg_policies where tablename = any(%s)",
                                (['client_error_events', 'client_alert_ledger', 'client_event_rate_limits', 'client_event_salts'],)).fetchall()
        for role in ('anon', 'authenticated'):
            for table in ('client_error_events', 'client_alert_ledger', 'client_event_rate_limits', 'client_event_salts'):
                assert not conn.execute("select has_table_privilege(%s, %s, 'select') as granted", (role, table)).fetchone()['granted']
        assert len(conn.execute("select indexname from pg_indexes where tablename='client_error_events'").fetchall()) == 6


def test_real_dedupe_and_signature(local_db):
    event = events.sanitize_event(sample())

    async def body(conn):
        await events.store_event(conn, event, None, 'test-hmac')
        await events.store_event(conn, event, None, 'test-hmac')
        async with conn.cursor() as cur:
            await cur.execute('select count(*) as n, min(signature) as signature from client_error_events where event_id=%s', (event['event_id'],))
            row = await cur.fetchone()
            assert row['n'] == 1 and row['signature'] == events.event_signature(event)
    run(local_db, body)


@pytest.mark.parametrize('scope', ['ip', 'user', 'global'])
def test_real_postgres_limits(local_db, monkeypatch, scope):
    monkeypatch.setitem(events.LIMITS, scope, 2)
    key = str(uuid4())

    async def body(conn):
        assert await events.consume_limit(conn, scope, key)
        assert await events.consume_limit(conn, scope, key)
        assert not await events.consume_limit(conn, scope, key)
    run(local_db, body)


def test_real_retention_and_withdrawal_delete(local_db):
    async def body(conn):
        user = str(uuid4())
        async with conn.cursor() as cur:
            await cur.execute('insert into auth.users (id) values (%s)', (user,))
        event = events.sanitize_event(sample())
        await events.store_event(conn, event, user, 'hash')
        await events.delete_user_events(conn, user)
        async with conn.cursor() as cur:
            await cur.execute('select count(*) as n from client_error_events where user_id=%s', (user,))
            assert (await cur.fetchone())['n'] == 0
        event = events.sanitize_event(sample())
        await events.store_event(conn, event, None, 'hash')
        async with conn.cursor() as cur:
            await cur.execute("update client_error_events set received_at=now()-interval '31 days' where event_id=%s", (event['event_id'],))
        worker = ClientAlerts(SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(slack_alert_webhook_url=''))))
        # 테스트 트랜잭션은 commit 없이 유지한다.
        class NoCommit:
            def cursor(self):
                return conn.cursor()
            async def commit(self):
                pass
        now = datetime.now(timezone.utc)
        day = now.astimezone(events.KST).date()
        async with conn.cursor() as cur:
            for offset in (2, 3):
                await cur.execute('insert into client_event_salts (day, salt) values (%s, %s) on conflict do nothing',
                                  (day - timedelta(days=offset), b'a' * 32))
        await worker._retention(NoCommit(), now)
        async with conn.cursor() as cur:
            await cur.execute('select day from client_event_salts where day = any(%s::date[])',
                              ([day - timedelta(days=2), day - timedelta(days=3)],))
            assert [row['day'] for row in await cur.fetchall()] == [day - timedelta(days=2)]
        async with conn.cursor() as cur:
            await cur.execute('select count(*) as n from client_error_events where event_id=%s', (event['event_id'],))
            assert (await cur.fetchone())['n'] == 0
    run(local_db, body)


def test_two_database_workers_respect_lock_and_committed_ledger(local_db):
    async def body():
        app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(slack_alert_webhook_url='test')))
        first, second = ClientAlerts(app), ClientAlerts(app)
        async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as conn1:
            async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as conn2:
                key = str(uuid4())
                try:
                    assert await first._lock(conn1)
                    assert not await second._lock(conn2)
                    await first._reserve(conn1, 'burst', key, datetime.now(timezone.utc))
                    await first._finish(conn1, 'burst', key, datetime.now(timezone.utc), 'sent')
                    await first._unlock(conn1)
                    assert await second._lock(conn2)
                    assert (await second._ledger(conn2, 'burst', key))['status'] == 'sent'
                    await second._unlock(conn2)
                finally:
                    await conn1.rollback()
                    async with conn1.cursor() as cur:
                        await cur.execute('delete from client_alert_ledger where key=%s', (key,))
                    await conn1.commit()
    asyncio.run(body())
