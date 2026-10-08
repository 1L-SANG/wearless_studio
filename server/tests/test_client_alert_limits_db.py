"""실제 로컬 Postgres에서 커서, 발송 예산, 요약과 수신 커밋 경계를 검증한다."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app import client_events
from app.workers import client_alerts as alerts
from test_client_events import sample
from test_client_events_db import local_db, run  # noqa: F401

NOW = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)


class NoCommit:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return self.conn.cursor()

    async def commit(self):
        pass

    async def rollback(self):
        await self.conn.rollback()


class DatabaseWorker(alerts.ClientAlerts):
    def __init__(self, sent):
        super().__init__(SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(slack_alert_webhook_url='test'))))
        self.sent = sent

    async def _retention(self, conn, now):
        pass

    async def _digest_days(self, conn, now):
        return []

    async def _send(self, payload):
        self.sent.append(payload)
        return 'sent'


async def reset(conn):
    async with conn.cursor() as cur:
        await cur.execute('delete from client_error_events')
        await cur.execute('delete from client_alert_ledger')
        await cur.execute('delete from client_alert_attempts')
        await cur.execute("update client_alert_progress set received_at='1970-01-01T00:00:00Z', event_id='00000000-0000-0000-0000-000000000000', suppressed_count=0, cases='[]'")


async def add(conn, *, user_id=None, signature='case', received_at=NOW, blocking=True):
    event = client_events.sanitize_event(sample(blocking=blocking))
    await client_events.store_event(conn, event, user_id, 'test-hmac')
    async with conn.cursor() as cur:
        await cur.execute('update client_error_events set signature=%s, received_at=%s where event_id=%s',
                          (signature, received_at, event['event_id']))
    return event


def test_database_budget_cursor_and_anonymous_filter(local_db):
    async def body(conn):
        await reset(conn)
        user = str(uuid4())
        async with conn.cursor() as cur:
            await cur.execute('insert into auth.users (id) values (%s)', (user,))
        for i in range(120):
            await add(conn, user_id=user, signature=f'signed-{i}', received_at=NOW - timedelta(seconds=1))
        for i in range(120):
            await add(conn, signature=f'anonymous-{i}', received_at=NOW - timedelta(seconds=1))
        sent = []
        first = DatabaseWorker(sent)
        second = DatabaseWorker(sent)
        tx = NoCommit(conn)
        stats = await first.tick(tx, now=NOW)
        assert stats['processed'] == 100
        await second.tick(tx, now=NOW + timedelta(seconds=10))
        assert len(sent) == 10
        assert sum('알림이 많아 일부를 묶었어요' in p['text'] for p in sent) == 1
        async with conn.cursor() as cur:
            await cur.execute('select count(*) as n from client_alert_attempts')
            assert (await cur.fetchone())['n'] == 10
            await cur.execute("select count(*) as n from client_alert_ledger where key like 'anonymous-%'")
            assert (await cur.fetchone())['n'] == 0
        await second.tick(tx, now=NOW + timedelta(minutes=59))
        assert len(sent) == 10
        await second.tick(tx, now=NOW + timedelta(hours=1, seconds=1))
        assert len(sent) == 11
        assert (await second._progress(tx))['suppressed_count'] == 0
    run(local_db, body)


def test_database_digest_counts_top_thirty_and_tail(local_db):
    async def body(conn):
        await reset(conn)
        for i in range(500):
            await add(conn, signature=f'{i:04}', received_at=NOW - timedelta(days=1), blocking=False)
        await add(conn, signature='0499', received_at=NOW - timedelta(days=1), blocking=False)
        worker = DatabaseWorker([])
        summary = await worker._digest_summary(conn, *alerts.digest_window((NOW - timedelta(days=1)).astimezone(alerts.KST).date()))
        assert len(summary['groups']) == 30
        assert summary['groups'][0]['signature'] == '0499'
        assert summary['other_signatures'] == 470 and summary['other_events'] == 470
        payload = alerts.slack_payload('하루 요약', **summary)
        assert '그 외 470종 470건' in payload['text'] and len(payload['text']) < 30000
    run(local_db, body)


def test_database_batch_waits_for_ingestion_commit(local_db):
    async def body():
        sent = []
        async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as writer:
            async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as reader:
                event = client_events.sanitize_event(sample())
                before = datetime.now(timezone.utc) - timedelta(seconds=1)
                user = str(uuid4())
                async with writer.cursor() as cur:
                    await cur.execute('insert into auth.users (id) values (%s)', (user,))
                await client_events.store_event(writer, event, user, 'test-hmac')
                ready = asyncio.Event()

                class Reader(DatabaseWorker):
                    async def _progress(self, conn):
                        ready.set()
                        return {'received_at': before, 'event_id': '00000000-0000-0000-0000-000000000000'}

                task = asyncio.create_task(Reader(sent)._batch(reader, datetime.now(timezone.utc) + timedelta(minutes=1)))
                try:
                    await ready.wait()
                    await asyncio.sleep(0.05)
                    assert not task.done()
                    await writer.commit()
                    batch = await asyncio.wait_for(task, timeout=5)
                    assert event['event_id'] in [e['event_id'] for e in batch]
                finally:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    await writer.rollback()
                    await reader.rollback()
                    async with writer.cursor() as cur:
                        await cur.execute('delete from auth.users where id=%s', (user,))
                    await writer.commit()
    asyncio.run(body())


def test_progress_and_budget_tables_are_private(local_db):
    with psycopg.connect(local_db, row_factory=dict_row) as conn:
        names = ['client_alert_progress', 'client_alert_attempts']
        rows = conn.execute('select relrowsecurity from pg_class where relname = any(%s)', (names,)).fetchall()
        assert len(rows) == 2 and all(r['relrowsecurity'] for r in rows)
        assert not conn.execute('select 1 from pg_policies where tablename = any(%s)', (names,)).fetchall()
        for role in ('anon', 'authenticated'):
            for table in names:
                assert not conn.execute("select has_table_privilege(%s,%s,'select') as granted", (role, table)).fetchone()['granted']


def test_database_overflow_failure_and_restore_commit_together(local_db):
    async def body(conn):
        await reset(conn)
        user = str(uuid4())
        async with conn.cursor() as cur:
            await cur.execute('insert into auth.users (id) values (%s)', (user,))
        for i in range(12):
            await add(conn, user_id=user, signature=f'signed-{i}', received_at=NOW - timedelta(seconds=1))

        class ObserveCommit(NoCommit):
            async def commit(self):
                async with self.conn.cursor() as cur:
                    await cur.execute("select status from client_alert_ledger where kind='overflow' and key='global'")
                    row = await cur.fetchone()
                    if row and row['status'] == 'failed':
                        await cur.execute('select suppressed_count from client_alert_progress where singleton')
                        assert (await cur.fetchone())['suppressed_count'] == 3

        class RejectOverflow(DatabaseWorker):
            async def _send(self, payload):
                self.sent.append(payload)
                return 'failed' if '알림이 많아 일부를 묶었어요' in payload['text'] else 'sent'

        await RejectOverflow([]).tick(ObserveCommit(conn), now=NOW)
        assert (await DatabaseWorker([])._progress(conn))['suppressed_count'] == 3
    run(local_db, body)
