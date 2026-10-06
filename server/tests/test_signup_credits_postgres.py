"""Real signup transaction regressions, opt-in on a disposable local PostgreSQL."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from app import legal_versions, repo, routes
from app.models import SellerConsentIn
from conftest import make_settings


@pytest.fixture
def signup_pg():
    dsn = os.environ.get("SIGNUP_BONUS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SIGNUP_BONUS_TEST_DATABASE_URL is not configured; local PostgreSQL test skipped")
    options = conninfo_to_dict(dsn)
    assert options.get("host") in {"localhost", "127.0.0.1", "::1"}
    assert options.get("hostaddr", "127.0.0.1") in {"127.0.0.1", "::1"}
    assert "service" not in options
    schema = "signup_test_" + uuid.uuid4().hex
    db = psycopg.connect(dsn, host="127.0.0.1", hostaddr="127.0.0.1", autocommit=True, row_factory=dict_row, connect_timeout=2)
    db.execute(psycopg.sql.SQL("create schema {}").format(psycopg.sql.Identifier(schema)))
    db.execute(psycopg.sql.SQL("set search_path to {}").format(psycopg.sql.Identifier(schema)))
    db.execute("""
        create table credit_accounts(user_id text primary key, balance integer, reserved integer);
        create table seller_consents(user_id text primary key, terms_version text, privacy_version text,
          age_attested boolean, accepted_at timestamptz default now(), history jsonb default '[]');
        create table credit_sources(id uuid primary key default gen_random_uuid(), user_id text,
          source_type text, initial_credits integer, remaining_credits integer, status text,
          period_end timestamptz, plan_id uuid, payment_id uuid);
        create table credit_ledger(id uuid primary key default gen_random_uuid(), user_id text,
          credit_source_id uuid references credit_sources(id), action_key text, delta integer,
          balance_after integer, available_after integer, idempotency_key text unique, metadata jsonb);
        insert into credit_accounts values ('seller-test', 0, 0);
    """)
    try:
        yield dsn, schema, db
    finally:
        db.execute(psycopg.sql.SQL("drop schema {} cascade").format(psycopg.sql.Identifier(schema)))
        db.close()


def wire_pg(monkeypatch, dsn, schema):
    @asynccontextmanager
    async def connection(_request):
        async with await psycopg.AsyncConnection.connect(
            dsn, host="127.0.0.1", hostaddr="127.0.0.1", row_factory=dict_row,
            connect_timeout=2,
            options=f"-c search_path={schema}",
        ) as conn:
            yield conn
    monkeypatch.setattr(routes, "get_conn", connection)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=make_settings(signup_credits=150))))
    body = SellerConsentIn(termsVersion=legal_versions.SELLER_TERMS_VERSION,
                           privacyVersion=legal_versions.SELLER_PRIVACY_VERSION, ageAttested=True)
    return request, body, connection


def test_concurrent_first_consents_grant_once(signup_pg, monkeypatch):
    dsn, schema, db = signup_pg
    request, body, _ = wire_pg(monkeypatch, dsn, schema)

    async def scenario():
        gate = asyncio.Event()
        async def consent():
            await gate.wait()
            return await routes.accept_consents(request, body, user_id="seller-test")
        tasks = [asyncio.create_task(consent()) for _ in range(2)]
        gate.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert sorted(r["signupCredits"] for r in results) == [0, 150]
    asyncio.run(scenario())
    assert db.execute("select balance from credit_accounts").fetchone()["balance"] == 150
    assert db.execute("select count(*) as n from credit_ledger where action_key='grant_signup'").fetchone()["n"] == 1
    assert db.execute("select count(*) as n from credit_sources").fetchone()["n"] == 1


def test_concurrent_repo_signup_keys_grant_once(signup_pg, monkeypatch):
    dsn, schema, db = signup_pg
    request, _, connection = wire_pg(monkeypatch, dsn, schema)
    async def grant():
        async with connection(request) as conn:
            result = await repo.grant_signup_credits(conn, user_id="seller-test", credits=150)
            await conn.commit()
            return result
    async def scenario():
        assert sorted(await asyncio.wait_for(asyncio.gather(grant(), grant()), timeout=10)) == [0, 150]
    asyncio.run(scenario())
    assert db.execute("select count(*) as n from credit_ledger").fetchone()["n"] == 1
    assert db.execute("select balance from credit_accounts").fetchone()["balance"] == 150


def test_sql_failure_inside_signup_savepoint_preserves_consent(signup_pg, monkeypatch):
    dsn, schema, db = signup_pg
    request, body, _ = wire_pg(monkeypatch, dsn, schema)
    original = repo.grant_signup_credits
    async def fail(conn, **kwargs):
        await original(conn, **kwargs)
        await conn.execute("select 1 / 0")
    monkeypatch.setattr(repo, "grant_signup_credits", fail)
    result = asyncio.run(routes.accept_consents(request, body, user_id="seller-test"))
    assert result["signupCredits"] == 0
    assert db.execute("select count(*) as n from seller_consents").fetchone()["n"] == 1
    assert db.execute("select count(*) as n from credit_sources").fetchone()["n"] == 0
    assert db.execute("select count(*) as n from credit_ledger").fetchone()["n"] == 0
    assert db.execute("select balance from credit_accounts").fetchone()["balance"] == 0
