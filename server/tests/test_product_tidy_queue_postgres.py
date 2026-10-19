"""Real queue SQL and row locks, using an isolated schema on localhost only.

The fixture mirrors jobs' queue columns/types and active uniqueness, plus the
tables referenced by claim SQL. It does not run migrations or contact providers.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from app import repo, routes
from app.services import canonical_reference, product_tidy


@pytest.fixture
def database():
    dsn = (os.environ.get("PRODUCT_TIDY_TEST_DATABASE_URL")
           or os.environ.get("CLIENT_EVENTS_TEST_DATABASE_URL"))
    if not dsn:
        if os.environ.get("CLIENT_EVENTS_REQUIRE_TEST_DB") == "1":
            pytest.fail("product tidy queue SQL requires the local test DB", pytrace=False)
        pytest.skip("local product tidy test DB is not configured")
    options = conninfo_to_dict(dsn)
    assert options.get("host") in {"localhost", "127.0.0.1", "::1"}
    assert options.get("hostaddr", "127.0.0.1") in {"127.0.0.1", "::1"}
    assert "service" not in options
    schema = "tidy_queue_" + uuid.uuid4().hex
    try:
        conn = psycopg.connect(dsn, host="127.0.0.1", hostaddr="127.0.0.1",
                               connect_timeout=3, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError:
        if os.environ.get("CLIENT_EVENTS_REQUIRE_TEST_DB") == "1":
            pytest.fail("local product tidy test DB is unavailable", pytrace=False)
        pytest.skip("local product tidy test DB is unavailable")
    with conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
            conn.execute("""
                create table jobs (
                    id uuid primary key default gen_random_uuid(), user_id uuid not null,
                    project_id uuid, kind text not null, status text not null default 'pending',
                    payload jsonb not null default '{}', result jsonb, idempotency_key text unique,
                    progress integer not null default 0, steps jsonb not null default '[]',
                    error_message text, credits_reserved integer not null default 0,
                    credits_charged integer, metadata jsonb not null default '{}',
                    locked_by text, locked_at timestamptz, started_at timestamptz,
                    created_at timestamptz not null default now(),
                    updated_at timestamptz not null default now(), finished_at timestamptz
                );
                create unique index jobs_active_unique_idx on jobs(project_id, kind)
                    where status in ('pending', 'running');
                create table job_events (
                    id bigint generated always as identity primary key, job_id uuid,
                    event_type text, payload jsonb
                );
                create table fm_cutover_batches(status text);
                create table personalization_profiles(id uuid, status text);
                create table fm_models(id uuid, user_id uuid);
                create table assets(source text, created_at timestamptz not null default now())
            """)
            yield SimpleNamespace(dsn=dsn, schema=schema)
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


@asynccontextmanager
async def connection(database):
    async with await psycopg.AsyncConnection.connect(
        database.dsn, host="127.0.0.1", hostaddr="127.0.0.1", row_factory=dict_row,
    ) as conn:
        await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(database.schema)))
        await conn.execute("set statement_timeout = '5s'")
        yield conn


async def add(conn, *, payload=None, kind="sam_preprocess", status="pending", key=None,
              user_id=None, project_id=None):
    cur = await conn.execute(
        "insert into jobs(user_id, project_id, kind, payload, status, idempotency_key, locked_by, metadata) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id::text as id",
        (user_id or str(uuid.uuid4()), project_id or str(uuid.uuid4()), kind,
         Jsonb(payload if payload is not None else {"mode": "canonical_cutout"}), status,
         key or uuid.uuid4().hex, "off-lease" if status == "running" else None,
         Jsonb({"attempt": 2})),
    )
    return (await cur.fetchone())["id"]


async def row(conn, job_id):
    return await (await conn.execute("select * from jobs where id = %s", (job_id,))).fetchone()




def test_incapable_claim_skips_required_jobs_without_starving_other_work(database):
    async def scenario():
        async with connection(database) as conn:
            required = await add(conn, payload={"mode": "canonical_cutout", "productTidyRequired": True})
            eligible = set()
            for payload in ({}, {"mode": "canonical_cutout"}, {"productTidyRequired": False},
                            {"productTidyRequired": None}):
                eligible.add(await add(conn, payload=payload))
            eligible.add(await add(conn, kind="matching_cutout", payload={"productTidyRequired": True}))
            claimed = set()
            for _ in eligible:
                job = await repo.claim_next_job(conn, ("sam_preprocess", "matching_cutout"), "off",
                                                product_tidy_capable=False)
                assert job is not None
                claimed.add(job["id"])
            assert claimed == eligible
            assert await repo.claim_next_job(conn, ("sam_preprocess", "matching_cutout"), "off",
                                             product_tidy_capable=False) is None
            assert (await row(conn, required))["status"] == "pending"
            capable = await repo.claim_next_job(conn, ("sam_preprocess",), "on", product_tidy_capable=True)
            assert capable["id"] == required and capable["lease_token"].startswith("on:")
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,required,capable,want", [
    ("sam_preprocess", True, False, "pending"),
    ("sam_preprocess", True, True, "done"),
    ("sam_preprocess", False, False, "done"),
    ("matching_cutout", True, False, "done"),
])
def test_finalizer_requeues_only_required_jobs_on_incapable_workers(database, kind, required, capable, want):
    async def scenario():
        async with connection(database) as conn:
            payload = {"mode": "canonical_cutout", "productTidyRequired": required, "retry": 3}
            ident = await add(conn, kind=kind, status="running", payload=payload)
            assert await repo.finalize_uncharged_job(
                conn, job_id=ident, lease_token="off-lease", status="done", result={"state": "ready"},
                product_tidy_capable=capable)
            job = await row(conn, ident)
            assert job["status"] == want and job["locked_by"] is None and job["locked_at"] is None
            assert job["payload"] == payload and job["metadata"] == {"attempt": 2}
            assert job["credits_reserved"] == 0 and job["credits_charged"] is None
            events = await (await conn.execute("select event_type from job_events")).fetchall()
            assert events == ([] if want == "pending" else [{"event_type": "done"}])
            assert (job["finished_at"] is None) == (want == "pending")
    asyncio.run(scenario())


def test_incapable_finalizer_cannot_release_another_lease(database):
    async def scenario():
        async with connection(database) as conn:
            ident = await add(conn, status="running", payload={"productTidyRequired": True})
            before = await row(conn, ident)
            assert not await repo.finalize_uncharged_job(
                conn, job_id=ident, lease_token="stale", status="done", result={"state": "ready"},
                product_tidy_capable=False)
            assert await row(conn, ident) == before
            assert await (await conn.execute("select * from job_events")).fetchall() == []
    asyncio.run(scenario())


async def wait_until_blocked(waiter, observer):
    async def blocked():
        while True:
            cur = await observer.execute("select pg_blocking_pids(%s) as blockers", (waiter.info.backend_pid,))
            if (await cur.fetchone())["blockers"]:
                return
            await asyncio.sleep(0.01)
    await asyncio.wait_for(blocked(), timeout=3)


@pytest.mark.parametrize("old_suffix", ["", ":r2", ":product-tidy-v1-qwen"])
def test_running_ordinary_job_upgrade_wins_row_lock_before_off_finalizer(database, old_suffix):
    async def scenario():
        uid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        product = {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "src"}]}]}
        base = canonical_reference.preprocess_idempotency_key(pid, product)
        async with connection(database) as api, connection(database) as off, connection(database) as observer:
            ident = await add(api, status="running", user_id=uid, project_id=pid, key=base + old_suffix,
                              payload={"mode": "canonical_cutout", "retry": 2})
            await api.commit()
            assert await routes._enqueue_canonical_cutout(
                api, user_id=uid, project_id=pid, product=product, tidy_enabled=True)
            upgraded = await row(api, ident)
            assert upgraded["payload"] == {"mode": "canonical_cutout", "retry": 2, "productTidyRequired": True}
            assert upgraded["idempotency_key"] == base + ":product-tidy-v1-qwen:required-v2"
            finish = asyncio.create_task(repo.finalize_uncharged_job(
                off, job_id=ident, lease_token="off-lease", status="done", result={"state": "ready"},
                product_tidy_capable=False))
            try:
                await wait_until_blocked(off, observer)
            finally:
                await api.commit()
            assert await asyncio.wait_for(finish, timeout=3)
            await off.commit()
            assert (await row(api, ident))["status"] == "pending"
            assert await (await api.execute("select * from job_events")).fetchall() == []
            assert await repo.claim_next_job(api, ("sam_preprocess",), "off", product_tidy_capable=False) is None
            job = await repo.claim_next_job(api, ("sam_preprocess",), "on", product_tidy_capable=True)
            assert job["id"] == ident
            assert await repo.finalize_uncharged_job(
                api, job_id=ident, lease_token=job["lease_token"], status="done",
                result={"state": "ready", "productTidy": {"state": "ready"}}, product_tidy_capable=True)
            assert (await row(api, ident))["status"] == "done"
    asyncio.run(scenario())


def test_enqueue_retries_after_off_finalizer_wins_before_upgrade(database, monkeypatch):
    async def scenario():
        uid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        product = {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "src"}]}]}
        base = canonical_reference.preprocess_idempotency_key(pid, product)
        async with connection(database) as api, connection(database) as off:
            ident = await add(api, status="running", user_id=uid, project_id=pid, key=base)
            await api.commit()
            create = repo.create_job
            calls = 0

            async def finish_after_join(conn, **kw):
                nonlocal calls
                joined = await create(conn, **kw)
                calls += 1
                if calls == 1:
                    assert joined[0]["id"] == ident and not joined[1]
                    assert await repo.finalize_uncharged_job(
                        off, job_id=ident, lease_token="off-lease", status="done", result={"state": "ready"})
                    await off.commit()
                return joined

            monkeypatch.setattr(repo, "create_job", finish_after_join)
            await routes._enqueue_canonical_cutout(api, user_id=uid, project_id=pid, product=product, tidy_enabled=True)
            assert calls == 2
            old = await row(api, ident)
            assert old["status"] == "done" and old["idempotency_key"] == base
            latest = await repo.get_latest_job_generation(api, uid, product_tidy.preprocess_key(pid, product, enabled=True))
            assert latest["id"] != ident and latest["status"] == "pending"
            assert latest["payload"]["productTidyRequired"] is True
    asyncio.run(scenario())


def test_old_completed_on_job_does_not_poison_new_required_protocol(database):
    async def scenario():
        uid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        product = {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "src"}]}]}
        base = canonical_reference.preprocess_idempotency_key(pid, product)
        async with connection(database) as conn:
            old_id = await add(conn, status="done", user_id=uid, project_id=pid, key=base + ":product-tidy-v1-qwen")
            await routes._enqueue_canonical_cutout(conn, user_id=uid, project_id=pid, product=product, tidy_enabled=True)
            job = await repo.get_latest_job_generation(conn, uid, product_tidy.preprocess_key(pid, product, enabled=True))
            assert job["id"] != old_id and job["status"] == "pending"
            assert job["payload"]["productTidyRequired"] is True
    asyncio.run(scenario())


def test_enqueue_never_relabels_an_active_different_photo_or_owner(database):
    async def scenario():
        uid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        product = {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "src"}]}]}
        base = canonical_reference.preprocess_idempotency_key(pid, product)
        async with connection(database) as conn:
            ident = await add(conn, status="running", user_id=uid, project_id=pid, key=base + "-other-photo")
            before = await row(conn, ident)
            await routes._enqueue_canonical_cutout(conn, user_id=uid, project_id=pid, product=product, tidy_enabled=True)
            assert await row(conn, ident) == before
            assert await repo.require_product_tidy_job(
                conn, job_id=ident, user_id=str(uuid.uuid4()), project_id=pid,
                compatible_keys=(base + "-other-photo",), idempotency_key=base + ":required") is None
            assert await row(conn, ident) == before
    asyncio.run(scenario())
