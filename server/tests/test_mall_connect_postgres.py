"""Opt-in mall rotation races on an explicitly configured disposable local PostgreSQL."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import uuid

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from app import mall_connect, repo
from app.auth import require_user
from app.main import create_app
from conftest import make_settings
from test_mall_connect import KEYS, USER


class PgPool:
    def __init__(self, dsn, schema):
        self.dsn, self.schema = dsn, schema

    @asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(
            self.dsn, row_factory=dict_row, application_name=self.schema,
            options=f"-c search_path={self.schema},public -c statement_timeout=10000", connect_timeout=3,
        ) as conn:
            yield conn


@pytest.fixture
def mall_pg(monkeypatch):
    dsn = os.environ.get("MALL_CONNECTION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("MALL_CONNECTION_TEST_DATABASE_URL is not configured")
    options = conninfo_to_dict(dsn)
    host = options.get("host", "")
    if host not in {"127.0.0.1", "localhost", "::1"} and not (host.startswith("/") and "," not in host):
        pytest.fail("mall integration requires an explicit disposable local database")
    if options.get("hostaddr", "127.0.0.1") not in {"127.0.0.1", "::1"} or "service" in options or "servicefile" in options:
        pytest.fail("mall integration forbids remote hostaddr and connection service overrides")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    schema = "mall_race_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=3)
    admin.execute(psycopg.sql.SQL("create schema {}").format(psycopg.sql.Identifier(schema)))
    try:
        admin.execute(psycopg.sql.SQL("set search_path to {}, public").format(psycopg.sql.Identifier(schema)))
        migration = Path(__file__).resolve().parents[2] / "supabase/migrations/20261024120000_mall_connections.sql"
        for statement in migration.read_text().split(";"):
            if "create table" in statement or "create unique index" in statement:
                admin.execute(statement.replace("public.mall_", f"{schema}.mall_")
                              .replace("references auth.users(id) on delete cascade", ""))
        cafe24 = migration.with_name("20261027100000_mall_cafe24.sql")
        for statement in cafe24.read_text().split(";"):
            if "alter table public.mall_connections" in statement or "alter table public.mall_defaults" in statement:
                admin.execute(statement.replace("public.mall_", f"{schema}.mall_"))
        admin.execute("""create table client_event_rate_limits (
            scope text, key text, window_start timestamptz, request_count integer)""")
        admin.execute("""create table jobs (
            user_id uuid, kind text, status text, payload jsonb, result jsonb)""")
        client_class = mall_connect.ZigzagClient

        def respond(_request):
            return httpx.Response(200, json={"data": {"shop": {
                "shop_id": "s1", "shop_name": "테스트 스토어", "allowed_brand_list": [], "site_country_list": [],
            }}})

        monkeypatch.setattr(mall_connect, "ZigzagClient", lambda *args, **kwargs:
            client_class(*args, **{**kwargs, "transport": httpx.MockTransport(respond)}))
        app = create_app(make_settings(mall_connect_enabled=True, mall_keys_kek="mall-test-kek"))
        pool = PgPool(dsn, schema)
        app.state.pool = pool
        app.dependency_overrides[require_user] = lambda: USER
        yield app, admin, pool
    finally:
        admin.execute(psycopg.sql.SQL("drop schema {} cascade").format(psycopg.sql.Identifier(schema)))
        admin.close()


def test_cafe24_migration_checks_actual_mall_constraints_and_token_columns(mall_pg):
    _, admin, _pool = mall_pg
    malls = ("zigzag", "ably", "smartstore", "cafe24")
    for table in ("mall_connections", "mall_defaults"):
        constraint = admin.execute(
            "select pg_get_constraintdef(oid) as definition from pg_constraint "
            "where conrelid = %s::regclass and conname = %s",
            (table, f"{table}_mall_check"),
        ).fetchone()
        assert constraint is not None
        assert all(f"'{mall}'::text" in constraint["definition"] for mall in malls)
        for mall in malls:
            admin.execute(psycopg.sql.SQL("insert into {} (user_id, mall) values (%s, %s)")
                          .format(psycopg.sql.Identifier(table)), (USER, mall))
    row = admin.execute("select token_expires_at, refresh_expires_at, scopes "
                        "from mall_connections where mall = 'cafe24'").fetchone()
    assert row == {"token_expires_at": None, "refresh_expires_at": None, "scopes": []}


@pytest.mark.parametrize("change", ["disconnect", "replace"])
def test_rotation_checks_original_connection_after_waiting_for_the_write_lock(mall_pg, change):
    app, admin, pool = mall_pg

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
            assert (await browser.post("/v1/malls/zigzag/connect", json=KEYS)).status_code == 200
            async with pool.connection() as blocker:
                # Leave the original row visible to the request's initial snapshot until commit.
                await repo.revoke_mall_connection(blocker, user_id=USER, mall="zigzag")
                if change == "replace":
                    await repo.upsert_mall_connection(blocker, user_id=USER, mall="zigzag", shop_id="s1",
                        shop_name="테스트 스토어", key_hint="22223333", access_key="winner-fixture-22223333",
                        secret_key="winner-secret", permissions=["상품 조회와 등록"], kek="mall-test-kek")
                pending = asyncio.create_task(browser.post("/v1/malls/zigzag/rotate", json={**KEYS, "accessKey": "stale-fixture-87654321"}))
                try:
                    async def wait_for_lock():
                        while not admin.execute("""select exists (
                            select 1 from pg_locks l join pg_stat_activity a on a.pid = l.pid
                            where l.locktype = 'advisory' and not l.granted and a.application_name = %s
                        ) as waiting""", (pool.schema,)).fetchone()["waiting"]:
                            if pending.done():
                                pytest.fail("rotation completed before waiting for the connection write lock")
                            await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_for_lock(), timeout=5)
                    await blocker.commit()
                    response = await asyncio.wait_for(pending, timeout=5)
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "connection_changed"
            async with pool.connection() as conn:
                keys = await repo.get_mall_connection_keys(conn, user_id=USER, mall="zigzag", kek="mall-test-kek")
            assert keys == (None if change == "disconnect" else ("winner-fixture-22223333", "winner-secret"))

    asyncio.run(scenario())
    rows = admin.execute("select status, access_key_enc, secret_key_enc from mall_connections order by created_at").fetchall()
    assert len(rows) == (1 if change == "disconnect" else 2)
    assert rows[0]["status"] == "revoked"
    assert rows[0]["access_key_enc"] is None and rows[0]["secret_key_enc"] is None


@pytest.mark.parametrize("first_action", ["revoke", "connect"])
def test_account_revoke_and_connect_serialize_on_account_lock(mall_pg, first_action):
    _, admin, pool = mall_pg

    async def save(conn):
        return await repo.upsert_mall_connection(conn, user_id=USER, mall="zigzag", shop_id="s1",
            shop_name="테스트 스토어", key_hint="12345678", access_key=KEYS["accessKey"],
            secret_key=KEYS["secretKey"], permissions=["상품 조회와 등록"], kek="mall-test-kek")

    async def close(conn):
        await repo.revoke_all_mall_connections(conn, USER)
        await conn.execute("""insert into jobs (user_id, kind, status, payload)
            values (%s, 'personalization_purge', 'pending', '{"reason":"account_delete"}')""", (USER,))

    async def scenario():
        async with pool.connection() as first, pool.connection() as second:
            # Keep the nested upsert transaction's locks until the caller commits.
            await first.execute("select 1")
            await (close(first) if first_action == "revoke" else save(first))
            pending = asyncio.create_task(save(second) if first_action == "revoke" else close(second))
            try:
                async def wait_for_account_lock():
                    while not admin.execute("""select exists (
                        select 1 from pg_locks where pid = %s and locktype = 'advisory'
                          and not granted and %s = any(pg_blocking_pids(pid))
                    ) as waiting""", (second.info.backend_pid, first.info.backend_pid)).fetchone()["waiting"]:
                        if pending.done():
                            pytest.fail("account operation completed without waiting for the shared advisory lock")
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_account_lock(), timeout=5)
                await first.commit()
                if first_action == "revoke":
                    with pytest.raises(repo.MallAccountClosed):
                        await asyncio.wait_for(pending, timeout=5)
                else:
                    await asyncio.wait_for(pending, timeout=5)
                await second.commit()
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())
    rows = admin.execute("select status, access_key_enc, secret_key_enc from mall_connections").fetchall()
    assert len(rows) == (0 if first_action == "revoke" else 1)
    assert all(row["status"] == "revoked" and row["access_key_enc"] is None and row["secret_key_enc"] is None
               for row in rows)
