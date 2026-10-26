"""Opt-in job lease fencing on a disposable local PostgreSQL database."""

import asyncio
from contextlib import asynccontextmanager
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from app import repo


@pytest.fixture
def job_patch_pg(monkeypatch):
    dsn = os.environ.get("MALL_CONNECTION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("MALL_CONNECTION_TEST_DATABASE_URL is not configured")
    options = conninfo_to_dict(dsn)
    host = options.get("host", "")
    if host not in {"127.0.0.1", "localhost", "::1"} and not (host.startswith("/") and "," not in host):
        pytest.fail("job patch integration requires an explicit disposable local database")
    if options.get("hostaddr", "127.0.0.1") not in {"127.0.0.1", "::1"} or "service" in options or "servicefile" in options:
        pytest.fail("job patch integration forbids remote hostaddr and connection service overrides")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    schema = "mall_job_patch_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=3)
    admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    try:
        admin.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(schema)))
        admin.execute("""create table jobs (
            id uuid primary key, user_id uuid not null, project_id uuid, kind text, status text,
            progress integer not null default 0, steps jsonb, payload jsonb not null default '{}',
            result jsonb, error_message text, credits_reserved integer not null default 0,
            credits_charged integer not null default 0, metadata jsonb not null default '{}',
            created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
            started_at timestamptz, finished_at timestamptz, locked_by text, locked_at timestamptz)""")
        admin.execute("create table job_events (job_id uuid, event_type text, payload jsonb)")
        # The shared claim SQL reads these tables even for an unrelated job kind.
        admin.execute("create table fm_cutover_batches (status text)")
        admin.execute("create table personalization_profiles (id uuid, status text)")
        admin.execute("create table fm_models (id uuid, user_id uuid)")

        @asynccontextmanager
        async def connection():
            async with await psycopg.AsyncConnection.connect(
                dsn, row_factory=dict_row, connect_timeout=3,
                options=f"-c search_path={schema},public -c statement_timeout=10000",
            ) as conn:
                yield conn

        yield admin, connection
    finally:
        admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        admin.close()


def test_job_patch_lease_fence_rejects_stale_worker_after_reclaim(job_patch_pg):
    admin, connection = job_patch_pg
    job_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    admin.execute("insert into jobs (id, user_id, kind, status) values (%s, %s, 'mall_register', 'pending')",
                  (job_id, user_id))

    async def scenario():
        async with connection() as first, connection() as second:
            claimed = await repo.claim_next_job(first, ("mall_register",), "same-worker")
            old_lease = claimed["lease_token"]
            assert await repo.set_job_metadata_patch(
                first, job_id=job_id, patch={"stage": "upload"}, lease_token=old_lease)
            await first.commit()
            admin.execute("update jobs set locked_at = now() - interval '10 minutes' where id = %s", (job_id,))
            recovered = await repo.recover_stale_leases(second, 60, ("mall_register",))
            assert recovered[0]["status"] == "pending"
            await second.commit()
            reclaimed = await repo.claim_next_job(second, ("mall_register",), "same-worker")
            new_lease = reclaimed["lease_token"]
            assert old_lease != new_lease
            assert await repo.set_job_result_patch(
                second, job_id=job_id, patch={"outcome": "unknown"}, lease_token=new_lease)
            assert await repo.set_job_progress_fenced(
                second, job_id=job_id, progress=42, lease_token=new_lease)
            await second.commit()
            for fn in (repo.set_job_metadata_patch, repo.set_job_result_patch):
                assert await fn(first, job_id=job_id, patch={"stale": True}, lease_token=old_lease) is False
            assert await repo.set_job_progress_fenced(
                first, job_id=job_id, progress=99, lease_token=old_lease) is False
            assert await repo.finalize_uncharged_job(
                first, job_id=job_id, lease_token=old_lease, status="error",
                result={"stale": True}, error_message="stale") is False
            await first.commit()

    asyncio.run(scenario())
    row = admin.execute("select * from jobs where id = %s", (job_id,)).fetchone()
    assert row["status"] == "running" and row["progress"] == 42
    assert row["metadata"] == {"stage": "upload", "leaseRecoveries": 1}
    assert row["result"] == {"outcome": "unknown"}
    assert row["error_message"] is None
    assert admin.execute("select count(*) as count from job_events").fetchone()["count"] == 0
