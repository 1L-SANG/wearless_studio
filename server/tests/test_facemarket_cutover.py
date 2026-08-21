import asyncio
import os
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app import facemarket_cutover, repo

TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.store = conn.store
        self.last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params=None):
        self.store.statements.append((" ".join(sql.split()).lower(), params))
        q = self.store.statements[-1][0]
        if q.startswith("select pg_advisory_xact_lock"):
            self.last = {"locked": True}
        elif "select id::text as id, user_id::text as user_id" in q and "from jobs" in q:
            self.last = self.store.pending_job
        elif q.startswith("update jobs set status = 'cancelled'"):
            self.store.job_status = "cancelled"
            self.last = None
        elif q.startswith("insert into job_events"):
            self.store.events += 1
            if "'cancelled'" in q:
                self.store.event_types.append("cancelled")
            elif "'error'" in q:
                self.store.event_types.append("error")
            else:
                self.store.event_types.append(params[1])
            self.last = None
        elif q.startswith("update fm_cutover_batches") and "status = 'draining'" in q:
            self.store.batch_status = "draining"
            self.last = {"id": self.store.batch_id}
        elif "select status from fm_cutover_batches" in q:
            self.last = {"status": self.store.batch_status}
        elif "select id::text as id from jobs" in q and "status='pending'" in q:
            self.last = list(self.store.pending_rows)
        elif "pending_count" in q and "running_count" in q:
            self.last = {
                "pending_count": self.store.pending_count,
                "running_count": self.store.running_count,
            }
        elif "select id::text as id from fm_biometric_enrollments" in q:
            self.last = [{"id": eid} for eid in self.store.enrollment_ids]
        elif "select pg_try_advisory_lock" in q:
            self.last = {"locked": self.store.photo_locks.pop(0) if self.store.photo_locks else False}
        elif q.startswith("select p.status from personalization_profiles"):
            self.last = {"status": self.store.profile_status}
        elif "from jobs j" in q and "j.status='pending'" in q:
            self.last = list(self.store.personalization_pending)
        elif q.startswith("update personalization_generations"):
            self.store.generation_errors += 1
            self.last = None
        else:
            self.last = None

    async def fetchone(self):
        if isinstance(self.last, list):
            return self.last.pop(0) if self.last else None
        return self.last

    async def fetchall(self):
        if isinstance(self.last, list):
            rows = self.last
            self.last = []
            return rows
        return []


class _Conn:
    def __init__(self, store):
        self.store = store
        self.commits = 0

    def cursor(self):
        return _Cursor(self)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


class _Pool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        pool = self

        class _CM:
            async def __aenter__(self):
                return _Conn(pool.store)

            async def __aexit__(self, *_args):
                return False

        return _CM()


class _Store:
    batch_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    def __init__(self):
        self.statements = []
        self.pending_job = {
            "id": "job-1",
            "user_id": "user-1",
            "project_id": "project-1",
            "credits_reserved": 7,
        }
        self.job_status = "pending"
        self.events = 0
        self.event_types = []
        self.batch_status = "approved"
        self.pending_rows = []
        self.pending_count = 0
        self.running_count = 0
        self.enrollment_ids = []
        self.photo_locks = []
        self.profile_status = "purging"
        self.personalization_pending = []
        self.generation_errors = 0


def test_cancel_pending_job_refunds_once_and_writes_cancelled_event(monkeypatch):
    """Break caught: replaying cutover cancellation can double-release reserved credits."""
    store = _Store()
    releases = []

    async def fake_release(conn, **kwargs):
        releases.append(kwargs)
        conn.store.pending_job = None
        return 9

    monkeypatch.setattr(repo, "release_credits", fake_release)
    conn = _Conn(store)

    first = asyncio.run(repo.cancel_pending_job_with_refund(
        conn,
        job_id="job-1",
        code="facemarket_cutover",
        message="cutover cancelled",
    ))
    second = asyncio.run(repo.cancel_pending_job_with_refund(
        conn,
        job_id="job-1",
        code="facemarket_cutover",
        message="cutover cancelled",
    ))

    assert (first, second) == (True, False)
    assert len(releases) == 1
    assert releases[0]["settle_key"] == "credit:job:job-1:settle"
    assert store.job_status == "cancelled"
    assert store.events == 1
    assert store.event_types == ["error"]


def test_cutover_cancel_event_type_matches_current_schema_contract():
    """Break caught: cancellation used an event_type rejected by the deployed CHECK."""
    sql = (ROOT / "supabase/migrations/20260612090000_init.sql").read_text()

    assert "event_type in ('progress', 'step', 'done', 'error')" in sql
    assert "'cancelled'" not in sql.split("create table public.job_events", 1)[1].split(");", 1)[0]


def test_close_initial_cutover_writers_moves_approved_batch_to_draining():
    """Break caught: cutover cancellation starts before the batch is durably closed."""
    store = _Store()

    asyncio.run(facemarket_cutover.close_initial_cutover_writers(
        _Pool(store), batch_id=store.batch_id
    ))

    assert store.batch_status == "draining"
    assert store.statements[0][0].startswith("select pg_advisory_xact_lock")
    assert "started_at = coalesce(started_at, now())" in store.statements[1][0]


def test_quiesce_initial_cutover_times_out_on_running_or_locked_writers():
    """Break caught: freeze could proceed while a running writer or photo lock is still live."""
    store = _Store()
    store.running_count = 1
    store.enrollment_ids = ["11111111-1111-1111-1111-111111111111"]
    store.photo_locks = [False]

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.quiesce_initial_cutover_writers(
            _Pool(store),
            batch_id=store.batch_id,
            timeout_seconds=0.001,
            poll_interval_seconds=0,
        ))

    assert exc.value.code == "writers_not_drained"


def test_quiesce_personalization_requires_purging_profile_and_marks_pending_generations():
    """Break caught: account/personalization purge can reconcile before pending writers are cancelled."""
    store = _Store()
    store.personalization_pending = [{"id": "job-1", "generation_id": "gen-1"}]
    seen = []

    async def fake_cancel(conn, **kwargs):
        seen.append(kwargs["job_id"])
        store.personalization_pending = []
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repo, "cancel_pending_job_with_refund", fake_cancel)
    try:
        result = asyncio.run(facemarket_cutover.quiesce_personalization_writers(
            _Pool(store),
            user_id="user-1",
            timeout_seconds=0.001,
            poll_interval_seconds=0,
        ))
    finally:
        monkeypatch.undo()

    assert result.cancelled_count == 1
    assert seen == ["job-1"]
    assert store.generation_errors == 1


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="FACEMARKET_TEST_DATABASE_URL is not configured",
)
def test_live_boundary_lock_interleavings_and_refund_idempotency():
    """Break caught: fake SQL-order tests cannot prove PostgreSQL advisory waits or ledger replay."""

    async def assert_second_connection_waits(first, second):
        await repo.lock_facemarket_writer_boundary(first)
        waiter = asyncio.create_task(repo.lock_facemarket_writer_boundary(second))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        await first.commit()
        await asyncio.wait_for(waiter, timeout=1)
        await second.rollback()

    async def scenario():
        first = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        second = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await assert_second_connection_waits(first, second)
            await assert_second_connection_waits(second, first)

            user_id = str(uuid.uuid4())
            project_id = str(uuid.uuid4())
            job_id = str(uuid.uuid4())
            await conn.execute("insert into auth.users (id) values (%s)", (user_id,))
            await conn.execute(
                "insert into credit_accounts (user_id, balance, reserved) values (%s, 7, 7)",
                (user_id,),
            )
            await conn.execute(
                "insert into projects (id, user_id, status, title) values (%s, %s, 'draft', 'cutover')",
                (project_id, user_id),
            )
            await conn.execute(
                """
                insert into jobs
                    (id, user_id, project_id, kind, status, payload, credits_reserved, metadata)
                values (%s, %s, %s, 'detail_page', 'pending', %s, 7, '{}'::jsonb)
                """,
                (job_id, user_id, project_id, Json({"mode": "generate"})),
            )

            first_cancel = await repo.cancel_pending_job_with_refund(
                conn,
                job_id=job_id,
                code="facemarket_cutover",
                message="cutover cancelled",
            )
            second_cancel = await repo.cancel_pending_job_with_refund(
                conn,
                job_id=job_id,
                code="facemarket_cutover",
                message="cutover cancelled",
            )

            account = await (
                await conn.execute(
                    "select reserved from credit_accounts where user_id = %s",
                    (user_id,),
                )
            ).fetchone()
            job = await (
                await conn.execute("select status from jobs where id = %s", (job_id,))
            ).fetchone()
            ledger_count = await (
                await conn.execute(
                    "select count(*)::int as count from credit_ledger where idempotency_key = %s",
                    (f"credit:job:{job_id}:settle",),
                )
            ).fetchone()
            event_count = await (
                await conn.execute(
                    "select count(*)::int as count from job_events where job_id = %s and event_type = 'error'",
                    (job_id,),
                )
            ).fetchone()

            assert (first_cancel, second_cancel) == (True, False)
            assert account["reserved"] == 0
            assert job["status"] == "cancelled"
            assert ledger_count["count"] == 1
            assert event_count["count"] == 1
        finally:
            await first.rollback()
            await second.rollback()
            await conn.rollback()
            await first.close()
            await second.close()
            await conn.close()

    asyncio.run(scenario())
