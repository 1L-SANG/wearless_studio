"""Opt-in SQL regressions using temporary tables on localhost PostgreSQL only."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from app import repo
from app.services import sam_retry
from app.services import sam_autoscale
from app.workers.sam_retry_pusher import PUSH_KINDS, SamRetryPusher


def test_matching_status_uses_latest_generation_after_three_retries(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            base = "project:matching_cutout:custom:v1"
            for retry in range(4):
                await add(conn, sam_retry.generation_key(base, retry), retry=retry,
                          result={"state": "unavailable"})
            await conn.execute("update jobs set project_id = 'project'")
            assert await repo.has_active_matching_cutout_job(conn, "project") is False
    asyncio.run(scenario())


def test_direct_matching_ignores_terminal_sam_retry_wait(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            await add(conn, "project:matching_cutout:custom:v1", result={"state": "unavailable"})
            await conn.execute("update jobs set project_id = 'project'")
            assert await repo.has_active_matching_cutout_job(
                conn, "project", allow_sam_retry=False) is False
    asyncio.run(scenario())


def test_custom_gemini_upload_and_job_do_not_keep_sam_running(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            await conn.execute("create temporary table assets (source text, created_at timestamptz, metadata jsonb)")
            await add(conn, "matching", kind="matching_cutout", status="pending")
            await conn.execute("insert into assets values ('upload', now(), '{\"purpose\":\"custom_match_source\"}')")
            kinds = ("sam_preprocess", "editor_garment_mask")
            snap = await repo.sam_demand_snapshot(
                conn, kinds, exclude_upload_purposes=("custom_match_source",))
            assert snap.active_sam_jobs == 0
            assert snap.last_upload_at is None
            assert sam_autoscale.want_running(snap, idle_minutes=30) is False
            await conn.execute("insert into assets values ('upload', now(), '{\"purpose\":\"upload\"}')")
            snap = await repo.sam_demand_snapshot(
                conn, kinds, exclude_upload_purposes=("custom_match_source",))
            assert snap.last_upload_at is not None
            assert sam_autoscale.want_running(snap, idle_minutes=30) is True
    asyncio.run(scenario())


def test_a_stale_direct_matching_lease_is_terminal_while_sam_tasks_can_recover(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            await conn.execute("alter table jobs add column locked_at timestamptz, add column locked_by text")
            await conn.execute("create temporary table job_events (job_id uuid, event_type text, payload jsonb)")
            for kind in ("matching_cutout", "sam_preprocess", "editor_garment_mask"):
                await add(conn, kind, kind=kind, status="running")
            await conn.execute("update jobs set locked_at = now() - interval '20 minutes', locked_by = 'worker', finished_at = null")
            await repo.recover_stale_leases(
                conn, 900, ("matching_cutout", "sam_preprocess", "editor_garment_mask"),
                no_requeue_kinds=("matching_cutout",))
            cur = await conn.execute("select kind, status, finished_at from jobs")
            results = {row["kind"]: row for row in await cur.fetchall()}
            assert results["matching_cutout"]["status"] == "error"
            assert results["matching_cutout"]["finished_at"] is not None
            assert results["sam_preprocess"]["status"] == "pending"
            assert results["editor_garment_mask"]["status"] == "pending"
            cur = await conn.execute("select count(*) as count from job_events where event_type = 'error'")
            assert (await cur.fetchone())["count"] == 1
    asyncio.run(scenario())


@pytest.fixture
def local_dsn():
    dsn = os.environ.get("SAM_RETRY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("SAM_RETRY_TEST_DATABASE_URL is not configured")
    options = conninfo_to_dict(dsn)
    assert options.get("host") in {"localhost", "127.0.0.1", "::1"}
    assert options.get("hostaddr", "127.0.0.1") in {"127.0.0.1", "::1"}
    assert "service" not in options
    return dsn


@asynccontextmanager
async def database(dsn):
    # Pin the address even if environment variables provide a different PGHOSTADDR.
    async with await psycopg.AsyncConnection.connect(
        dsn, host="127.0.0.1", hostaddr="127.0.0.1", row_factory=dict_row,
    ) as conn:
        await conn.execute("""
            create temporary table jobs (
                id uuid primary key default gen_random_uuid(),
                user_id text not null default 'u', project_id text not null default gen_random_uuid()::text,
                kind text not null default 'matching_cutout', status text default 'done',
                payload jsonb default '{}', result jsonb, idempotency_key text unique,
                progress integer default 0, steps jsonb default '[]', error_message text,
                credits_reserved integer default 0, credits_charged integer,
                metadata jsonb default '{}', created_at timestamptz default now(),
                updated_at timestamptz default now(), finished_at timestamptz
            );
            create unique index jobs_active_unique_idx on jobs (project_id, kind)
                where status in ('pending', 'running')
        """)
        migration = Path(__file__).resolve().parents[2] / "supabase/migrations/20260821000000_sam_retry_index.sql"
        await conn.execute(migration.read_text().replace("public.jobs", "jobs"))
        yield conn
        await conn.rollback()


async def add(conn, key=None, *, retry=0, result=None, status="done", age=600,
              user="u", kind="matching_cutout", payload=None):
    cur = await conn.execute(
        "insert into jobs(idempotency_key, payload, result, status, finished_at, user_id, kind) "
        "values (%s, %s, %s, %s, now() - make_interval(secs => %s), %s, %s) returning id::text",
        (key or uuid.uuid4().hex, Jsonb(payload if payload is not None else {"retry": retry}),
         Jsonb(result), status, age, user, kind),
    )
    return (await cur.fetchone())["id"]


async def candidates(conn, **kwargs):
    return await repo.list_retryable_sam_jobs(
        conn, PUSH_KINDS, max_retries=sam_retry.MAX_RETRIES,
        min_age_seconds=min(sam_retry.BACKOFF_SECONDS), **kwargs)


def test_sql_retryability_matches_python_for_terminal_results(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            expected = set()
            states = ["unavailable", "unverified", "ready", "failed", "skipped", "partial",
                      "no_garment_candidate", "source_rejected", "", None, False, 0,
                      [], {}, True, 1, ["unavailable"], {"name": "unavailable"}]
            for status in ("done", "error"):
                for result in [None, {}, *({"state": state} for state in states)]:
                    ident = await add(conn, result=result, status=status)
                    if sam_retry.job_is_retryable({"status": status, "result": result}):
                        expected.add(ident)
            sql_null_error = await add(conn, status="error")
            await conn.execute("update jobs set result = null where id = %s", (sql_null_error,))
            expected.add(sql_null_error)
            assert {row["id"] for row in await candidates(conn, limit=100)} == expected
    asyncio.run(scenario())


def test_superseded_and_nonretryable_rows_do_not_fill_each_poll(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            transient = {"state": "unavailable"}
            for i in range(55):
                await add(conn, f"base-{i}", result=transient)
                await add(conn, f"base-{i}:r1", retry=1, result={"state": "ready"})
            await add(conn, "old-legacy", status="error", age=40 * 86400,
                      kind="sam_preprocess", payload={"mode": "canonical_cutout"})
            legacy = await add(conn, "legacy", status="error", age=600,
                               kind="sam_preprocess", payload={"mode": "canonical_cutout"})
            for _ in range(2):
                assert [row["id"] for row in await candidates(conn)] == [legacy]
            pusher = SamRetryPusher(app=None)
            assert await pusher._push_once(repo, conn) == 1
            latest = await repo.get_latest_job_generation(conn, "u", "legacy")
            assert latest["payload"] == {"mode": "canonical_cutout", "retry": 1}
            assert latest["status"] == "pending"
            assert "metadata" in latest and "steps" in latest
            assert await candidates(conn) == []
            assert await pusher._push_once(repo, conn) == 0
    asyncio.run(scenario())


def test_supersession_uses_existing_user_key_and_payload_order(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            transient = {"state": "unverified"}
            expected = set()
            for status in ("pending", "running", "done", "error", "cancelled"):
                await add(conn, status, result=transient)
                newer = await add(conn, status + ":r1", retry=1, status=status)
                if status == "error":
                    expected.add(newer)
            expected.add(await add(conn, "owner", result=transient))
            await add(conn, "owner:r1", retry=1, user="other")
            expected.add(await add(conn, "equal", retry=1, result=transient))
            await add(conn, "equal:r99", retry=1)
            expected.add(await add(conn, "malformed", result=transient))
            await add(conn, "malformed:r1", retry="bad")
            await add(conn, "suffix:r1", retry=1, result=transient)
            await add(conn, "suffix:r2", retry=2)
            # Existing latest-generation lookup accepts any :r suffix and LIKE wildcards.
            await add(conn, "wild_card", result=transient)
            await add(conn, "wildXcard:rlegacy", retry=2)
            await add(conn, "numeric", result=transient)
            await add(conn, "numeric:r9", retry="2")
            assert {row["id"] for row in await candidates(conn)} == expected
    asyncio.run(scenario())


def test_minimum_age_budget_and_missing_identity_boundaries(local_dsn):
    async def scenario():
        async with database(local_dsn) as conn:
            transient = {"state": "unavailable"}
            expected = set()
            # PostgreSQL now()는 트랜잭션 안에서 고정되어 경계값이 흔들리지 않는다.
            for age in (14.999, 15, 15.001, 1799.999, 1800, 1800.001,
                        3600, 3600.001, 40 * 86400):
                ident = await add(conn, result=transient, age=age)
                if 15 < age < 1800:
                    expected.add(ident)
            for retry in (2, 3, "bad"):
                ident = await add(conn, retry=retry, result=transient)
                if retry != 3:
                    expected.add(ident)
            await add(conn, result=transient, age=None)
            await add(conn, result=transient, kind="editor_garment_mask")
            for status in ("pending", "running", "cancelled"):
                await add(conn, status=status, result=transient, kind="sam_preprocess" if status == "running" else "matching_cutout")
            await conn.execute("update jobs set idempotency_key = null where status = 'pending'")
            await conn.execute("update jobs set status = 'done' where status in ('pending', 'running')")
            await conn.execute("update jobs set idempotency_key = '' where kind = 'sam_preprocess'")
            assert {row["id"] for row in await candidates(conn)} == expected
    asyncio.run(scenario())
