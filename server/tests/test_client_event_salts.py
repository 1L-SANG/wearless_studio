import asyncio
import hashlib
import hmac
from datetime import date, datetime, timezone

import pytest

from app import client_events as events
from test_client_events_db import local_db  # noqa: F401


class SaltConn:
    def __init__(self, salts, *, fail_commit=False):
        self.salts = salts
        self.fail_commit = fail_commit
        self.queries = []
        self.commits = 0
        self.selected_day = None

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params):
        self.queries.append((sql, params))
        if sql.lstrip().startswith('insert'):
            self.salts.setdefault(params[0], params[1])
            await asyncio.sleep(0)
        else:
            self.selected_day = params[0]

    async def fetchone(self):
        return {'salt': self.salts[self.selected_day]}

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError('commit failed')
        self.commits += 1


def test_existing_daily_salt_wins_and_is_cached(monkeypatch):
    day = date(2026, 10, 8)
    conn = SaltConn({day: b'a' * 32})
    cache = {}
    calls = []

    def random_bytes(size):
        calls.append(size)
        return b'b' * size

    monkeypatch.setattr(events.secrets, 'token_bytes', random_bytes)

    async def run():
        now = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
        assert await events.get_daily_salt(conn, cache, now=now) == b'a' * 32
        assert await events.get_daily_salt(conn, cache, now=now) == b'a' * 32

    asyncio.run(run())
    assert calls == [32] and conn.commits == 1
    assert len(conn.queries) == 2
    assert 'on conflict (day) do nothing' in conn.queries[0][0].lower()
    assert conn.queries[1][0].lower().startswith('select salt')
    assert cache == {day: b'a' * 32}


def test_kst_midnight_rotates_salt_and_ip_hash(monkeypatch):
    salts = {}
    conn = SaltConn(salts)
    cache = {}
    values = iter((b'a' * 32, b'b' * 32))
    monkeypatch.setattr(events.secrets, 'token_bytes', lambda size: next(values))

    async def run():
        before = await events.get_daily_salt(conn, cache, now=datetime(2026, 10, 8, 14, 59, 59, tzinfo=timezone.utc))
        after = await events.get_daily_salt(conn, cache, now=datetime(2026, 10, 8, 15, tzinfo=timezone.utc))
        assert events.hash_ip('1.2.3.4', before) != events.hash_ip('1.2.3.4', after)

    asyncio.run(run())
    assert list(salts) == [date(2026, 10, 8), date(2026, 10, 9)]
    assert cache == {date(2026, 10, 9): b'b' * 32}


def test_two_process_caches_select_one_database_salt(monkeypatch):
    salts = {}
    first, second = SaltConn(salts), SaltConn(salts)
    cache1, cache2 = {}, {}
    values = iter((b'a' * 32, b'b' * 32))
    monkeypatch.setattr(events.secrets, 'token_bytes', lambda size: next(values))

    async def run():
        now = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)
        return await asyncio.gather(events.get_daily_salt(first, cache1, now=now),
                                    events.get_daily_salt(second, cache2, now=now))

    assert asyncio.run(run()) == [b'a' * 32, b'a' * 32]
    assert cache1 == cache2 and len(salts) == 1


def test_failed_commit_does_not_cache_uncommitted_salt():
    conn = SaltConn({}, fail_commit=True)
    cache = {}
    with pytest.raises(RuntimeError, match='commit failed'):
        asyncio.run(events.get_daily_salt(conn, cache, now=datetime(2026, 10, 8, 1, tzinfo=timezone.utc)))
    assert not cache


def test_ip_hash_is_hmac_sha256_with_byte_salt():
    salt = b'a' * 32
    expected = hmac.new(salt, b'1.2.3.4', hashlib.sha256).hexdigest()
    assert events.hash_ip('1.2.3.4', salt) == expected


def test_retention_deletes_salts_older_than_two_kst_days():
    from types import SimpleNamespace
    from app.workers.client_alerts import ClientAlerts

    conn = SaltConn({})
    worker = ClientAlerts(SimpleNamespace(state=SimpleNamespace()))
    asyncio.run(worker._retention(conn, datetime(2026, 10, 8, 15, tzinfo=timezone.utc)))
    query = next((sql, params) for sql, params in conn.queries if 'delete from client_event_salts' in sql)
    assert query[1] == (date(2026, 10, 7),)
    assert 'where day < %s' in query[0]
    assert conn.commits == 1

def test_real_daily_salt_is_shared_across_connections(local_db):
    from datetime import timedelta
    import psycopg
    from psycopg.rows import dict_row

    async def body():
        now = datetime.now(timezone.utc) + timedelta(days=36500)
        day = now.astimezone(events.KST).date()
        async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as first:
            async with await psycopg.AsyncConnection.connect(local_db, row_factory=dict_row) as second:
                try:
                    salt1, salt2 = await asyncio.gather(events.get_daily_salt(first, {}, now=now),
                                                      events.get_daily_salt(second, {}, now=now))
                    assert len(salt1) == 32 and salt1 == salt2
                    assert events.hash_ip('1.2.3.4', salt1) == events.hash_ip('1.2.3.4', salt2)
                    async with first.cursor() as cur:
                        await cur.execute('select count(*) as n from client_event_salts where day=%s', (day,))
                        assert (await cur.fetchone())['n'] == 1
                finally:
                    await first.rollback()
                    await second.rollback()
                    async with first.cursor() as cur:
                        await cur.execute('delete from client_event_salts where day=%s', (day,))
                    await first.commit()
    asyncio.run(body())
