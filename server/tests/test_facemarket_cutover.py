import asyncio
import copy
import inspect
import os
import json
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app import facemarket, facemarket_cutover, repo

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
        if "select id::text as id, status, started_at" in q and "from fm_cutover_batches" in q:
            self.last = self.store.batch_row
        elif q.startswith("select pg_advisory_xact_lock"):
            self.last = {"locked": True}
        elif q.startswith("select pg_try_advisory_lock") and params[0] == facemarket_cutover._CONTROLLER_LOCK_NAMESPACE:
            self.last = {"locked": self.store.controller_lock_available}
        elif q.startswith("select pg_advisory_unlock"):
            self.last = {"unlocked": True}
        elif q.startswith("select m.id::text as id") and "not exists" in q:
            self.last = [{"id": model_id} for model_id in self.store.target_model_ids]
        elif q.startswith("select id::text as id") and "from fm_cutover_batches" in q and "order by created_at" in q:
            self.last = list(self.store.batch_rows)
        elif q.startswith("insert into fm_cutover_batches"):
            self.last = {"id": self.store.batch_id}
        elif q.startswith("update fm_cutover_batches") and "target_digest" in q:
            self.store.batch_refreshed = True
            self.last = None
        elif "select id::text as id, status, target_digest" in q and "from fm_cutover_batches" in q:
            self.last = self.store.manifest_batch
        elif q.startswith("update jobs") and "metadata = metadata ||" in q:
            self.store.tagged_jobs = list(params[1])
            self.store.job_metadata_tag = params[2]
            self.last = None
        elif q.startswith("update fm_cutover_batches") and "set status='approved'" in q:
            self.store.batch_status = "approved"
            if self.store.manifest_batch:
                self.store.manifest_batch["status"] = "approved"
            self.last = None
        elif q.startswith("update fm_cutover_batches") and "set status='completed'" in q:
            self.store.batch_status = "completed"
            if self.store.manifest_batch:
                self.store.manifest_batch["status"] = "completed"
            self.last = None
        elif q.startswith("update fm_cutover_batches") and "set status='failed'" in q:
            self.store.batch_status = "failed"
            self.store.last_error_code = params[0]
            if self.store.manifest_batch:
                self.store.manifest_batch["status"] = "failed"
            self.last = None
        elif q.startswith("update fm_cutover_batches set status=%s"):
            self.store.batch_status = params[0]
            if self.store.manifest_batch:
                self.store.manifest_batch["status"] = params[0]
            self.last = None
        elif q.startswith("select count(*)::int as count") and "from fm_models" in q:
            self.last = {"count": len([r for r in self.store.models if r.get("reverification_batch_id") == params[0] and r["status"] == "reverification_required"])}
        elif q.startswith("select count(*)::int as count") and "from fm_licenses" in q:
            self.last = {"count": len([r for r in self.store.licenses if r.get("reverification_batch_id") == params[0] and r["status"] != "active"])}
        elif "from fm_licenses l" in q and "for update" in q:
            model_ids = set(params[0] or ())
            self.last = [
                copy.deepcopy(row)
                for row in self.store.licenses
                if row["model_id"] in model_ids
            ]
        elif q.startswith("select id::text as id, status") and "from fm_models" in q and "for update" in q:
            model_ids = set(params[0] or ())
            self.last = [
                copy.deepcopy(row)
                for row in self.store.models
                if row["id"] in model_ids
            ]
        elif q.startswith("update fm_licenses") and "previous_status=coalesce" in q:
            batch_id, ids = params
            for row in self.store.licenses:
                if row["id"] in set(ids):
                    row["previous_status"] = row.get("previous_status") or row["status"]
                    row["reverification_batch_id"] = row.get("reverification_batch_id") or batch_id
                    if row["status"] in {"pending", "active"}:
                        row["status"] = "reverification_required"
            self.store.freeze_steps.append("licenses")
            self.last = None
        elif q.startswith("update fm_models") and "previous_status=coalesce" in q:
            batch_id, ids = params
            for row in self.store.models:
                if row["id"] in set(ids):
                    row["previous_status"] = row.get("previous_status") or row["status"]
                    row["reverification_batch_id"] = row.get("reverification_batch_id") or batch_id
                    if row["status"] in {"pending", "verified"}:
                        row["status"] = "reverification_required"
            self.store.freeze_steps.append("models")
            self.last = None
        elif q.startswith("update fm_cutover_batches") and "case when status = 'draining'" in q:
            if self.store.batch_status == "draining":
                self.store.batch_status = "applying"
            self.store.freeze_steps.append("batch")
            self.last = None
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
        elif q.startswith("select id::text as id from fm_models where user_id"):
            self.last = [
                {"id": row["id"]}
                for row in self.store.models
                if row.get("user_id") == params[0]
            ]
        elif q.startswith("select id::text as id from fm_licenses where model_id = any"):
            model_ids = set(params[0] or ())
            self.last = [
                {"id": row["id"]}
                for row in self.store.licenses
                if row["model_id"] in model_ids
            ]
        elif "select id::text as id from fm_biometric_enrollments" in q:
            self.last = [{"id": eid} for eid in self.store.enrollment_ids]
        elif "select pg_try_advisory_lock" in q:
            namespace = params[0]
            if namespace == facemarket_cutover._MODEL_ASSET_FENCE_NAMESPACE:
                locks = self.store.model_asset_locks
            else:
                locks = self.store.photo_locks
            self.last = {"locked": locks.pop(0) if locks else True}
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
        self._snapshot = store.snapshot()

    def cursor(self):
        return _Cursor(self)

    async def commit(self):
        self.commits += 1
        self.store.commits += 1

    async def rollback(self):
        self.store.restore(self._snapshot)
        self.store.rollbacks += 1
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
        self.model_asset_locks = []
        self.profile_status = "purging"
        self.personalization_pending = []
        self.generation_errors = 0
        self.commits = 0
        self.rollbacks = 0
        self.freeze_steps = []
        self.batch_rows = []
        self.manifest_batch = None
        self.batch_refreshed = False
        self.tagged_jobs = []
        self.job_metadata_tag = None
        self.last_error_code = None
        self.controller_lock_available = True
        self.batch_started_at = "2026-08-21T00:00:00Z"
        self.batch_model_count = 1
        self.batch_license_count = 2
        self.target_model_ids = ["model-legacy"]
        self.models = [
            {
                "id": "model-legacy",
                "user_id": "user-1",
                "status": "verified",
                "previous_status": None,
                "reverification_batch_id": None,
            }
        ]
        self.licenses = [
            {
                "id": "license-a",
                "model_id": "model-legacy",
                "status": "active",
                "previous_status": None,
                "reverification_batch_id": None,
                "vc_id": "vc-a",
            },
            {
                "id": "license-b",
                "model_id": "model-legacy",
                "status": "revoked",
                "previous_status": None,
                "reverification_batch_id": None,
                "vc_id": "vc-b",
            },
        ]

    @property
    def batch_row(self):
        return {
            "id": self.batch_id,
            "status": self.batch_status,
            "started_at": self.batch_started_at,
            "model_count": self.batch_model_count,
            "license_count": self.batch_license_count,
        }

    def snapshot(self):
        return copy.deepcopy(
            {
                "batch_status": self.batch_status,
                "models": self.models,
                "licenses": self.licenses,
                "freeze_steps": self.freeze_steps,
            }
        )

    def restore(self, snapshot):
        self.batch_status = snapshot["batch_status"]
        self.models = snapshot["models"]
        self.licenses = snapshot["licenses"]
        self.freeze_steps = snapshot["freeze_steps"]


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


def test_quiesce_user_facemarket_waits_for_user_photo_session_lock(monkeypatch):
    """Break caught: account purge can delete R2 while an enrollment photo writer still owns the fence."""
    store = _Store()
    store.enrollment_ids = ["11111111-1111-1111-1111-111111111111"]
    store.photo_locks = [False, True]

    async def no_jobs(_conn, **_kwargs):
        return []

    monkeypatch.setattr(repo, "list_facemarket_scope_jobs", no_jobs)

    result = asyncio.run(facemarket_cutover.quiesce_user_facemarket_writers(
        _Pool(store),
        user_id="user-1",
        timeout_seconds=0.05,
        poll_interval_seconds=0,
    ))

    assert result == facemarket_cutover.WriterQuiescence(0, 0, 0)
    photo_lock_probes = [
        params
        for statement, params in store.statements
        if "pg_try_advisory_lock" in statement
        and params[0] == facemarket_cutover._PHOTO_FENCE_NAMESPACE
    ]
    assert len(photo_lock_probes) == 2


def test_quiesce_user_facemarket_times_out_on_held_user_photo_session_lock(monkeypatch):
    store = _Store()
    store.enrollment_ids = ["11111111-1111-1111-1111-111111111111"]
    store.photo_locks = [False] * 1000

    async def no_jobs(_conn, **_kwargs):
        return []

    monkeypatch.setattr(repo, "list_facemarket_scope_jobs", no_jobs)

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.quiesce_user_facemarket_writers(
            _Pool(store),
            user_id="user-1",
            timeout_seconds=0.001,
            poll_interval_seconds=0,
        ))

    assert exc.value.code == "writers_not_drained"


def test_quiesce_user_facemarket_waits_for_user_model_asset_session_lock(monkeypatch):
    """Break caught: account purge can race an in-flight model asset promotion for the same user."""
    store = _Store()
    store.model_asset_locks = [False, True]

    async def no_jobs(_conn, **_kwargs):
        return []

    monkeypatch.setattr(repo, "list_facemarket_scope_jobs", no_jobs)

    result = asyncio.run(facemarket_cutover.quiesce_user_facemarket_writers(
        _Pool(store),
        user_id="user-1",
        timeout_seconds=0.05,
        poll_interval_seconds=0,
    ))

    assert result == facemarket_cutover.WriterQuiescence(0, 0, 0)
    model_asset_probes = [
        params
        for statement, params in store.statements
        if "pg_try_advisory_lock" in statement
        and params[0] == facemarket_cutover._MODEL_ASSET_FENCE_NAMESPACE
    ]
    assert model_asset_probes == [
        (facemarket_cutover._MODEL_ASSET_FENCE_NAMESPACE, "model-legacy"),
        (facemarket_cutover._MODEL_ASSET_FENCE_NAMESPACE, "model-legacy"),
    ]


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


def test_freeze_initial_batch_links_terminal_licenses_and_queues_vcs_in_one_commit(monkeypatch):
    """Break caught: license freeze, VC enqueue, model freeze can split or skip terminal rows."""
    store = _Store()
    store.batch_status = "draining"
    calls = []

    async def enqueue(conn, **kwargs):
        calls.append(kwargs)
        conn.store.freeze_steps.append(f"queue:{kwargs['vc_id']}")

    monkeypatch.setattr(facemarket_cutover.facemarket, "enqueue_vc_revocation", enqueue)

    summary = asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
        _Pool(store), batch_id=store.batch_id
    ))

    assert summary == facemarket_cutover.FreezeSummary(
        model_count=1,
        license_count=2,
        revocation_target_count=2,
    )
    assert store.licenses == [
        {
            "id": "license-a",
            "model_id": "model-legacy",
            "status": "reverification_required",
            "previous_status": "active",
            "reverification_batch_id": store.batch_id,
            "vc_id": "vc-a",
        },
        {
            "id": "license-b",
            "model_id": "model-legacy",
            "status": "revoked",
            "previous_status": "revoked",
            "reverification_batch_id": store.batch_id,
            "vc_id": "vc-b",
        },
    ]
    assert store.models[0]["status"] == "reverification_required"
    assert store.models[0]["previous_status"] == "verified"
    assert calls == [
        {"license_id": "license-a", "model_id": "model-legacy", "vc_id": "vc-a"},
        {"license_id": "license-b", "model_id": "model-legacy", "vc_id": "vc-b"},
    ]
    assert store.freeze_steps == ["licenses", "queue:vc-a", "queue:vc-b", "models", "batch"]
    assert store.batch_status == "applying"
    assert store.commits == 1
    assert store.statements[0][0].startswith("select pg_advisory_xact_lock")


def test_freeze_replay_at_applying_returns_same_counts_without_rewriting_first_status(monkeypatch):
    """Break caught: applying replay can drift counts or overwrite original status snapshots."""
    store = _Store()
    store.batch_status = "applying"
    store.models[0].update(
        status="reverification_required",
        previous_status="verified",
        reverification_batch_id=store.batch_id,
    )
    store.licenses[0].update(
        status="reverification_required",
        previous_status="active",
        reverification_batch_id=store.batch_id,
    )
    store.licenses[1].update(
        previous_status="revoked",
        reverification_batch_id=store.batch_id,
    )
    queued = []

    async def enqueue(_conn, **kwargs):
        queued.append(kwargs["vc_id"])

    monkeypatch.setattr(facemarket_cutover.facemarket, "enqueue_vc_revocation", enqueue)

    summary = asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
        _Pool(store), batch_id=store.batch_id
    ))

    assert summary.license_count == 2
    assert store.batch_status == "applying"
    assert store.licenses[0]["previous_status"] == "active"
    assert store.models[0]["previous_status"] == "verified"
    assert queued == ["vc-a", "vc-b"]


def test_freeze_rolls_back_local_status_when_vc_enqueue_fails(monkeypatch):
    """Break caught: queue failure can leave local licenses blocked without a durable revoke job."""
    store = _Store()
    store.batch_status = "draining"

    async def fail_enqueue(_conn, **_kwargs):
        raise RuntimeError("holder vc:face:raw-id should not leak")

    monkeypatch.setattr(facemarket_cutover.facemarket, "enqueue_vc_revocation", fail_enqueue)

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
            _Pool(store), batch_id=store.batch_id
        ))

    assert exc.value.code == "vc_revocation_enqueue_failed"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert "vc:face" not in str(exc.value)
    assert "vc:face" not in repr(exc.value)
    assert store.licenses[0]["status"] == "active"
    assert store.models[0]["status"] == "verified"
    assert store.batch_status == "draining"
    assert store.rollbacks == 1
    assert store.commits == 0


@pytest.mark.parametrize(
    ("status", "started_at", "code"),
    [
        ("planned", "2026-08-21T00:00:00Z", "cutover_batch_not_draining"),
        ("approved", "2026-08-21T00:00:00Z", "cutover_batch_not_draining"),
        ("reconciling", "2026-08-21T00:00:00Z", "cutover_batch_not_draining"),
        ("completed", "2026-08-21T00:00:00Z", "cutover_batch_not_draining"),
        ("failed", "2026-08-21T00:00:00Z", "cutover_batch_not_draining"),
        ("draining", None, "cutover_batch_not_started"),
    ],
)
def test_freeze_rejects_invalid_batch_state_before_mutation(monkeypatch, status, started_at, code):
    """Break caught: freeze can mutate rows before the Task5 closed-started batch invariant holds."""
    store = _Store()
    store.batch_status = status
    store.batch_started_at = started_at
    queued = []

    async def enqueue(_conn, **kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(facemarket_cutover.facemarket, "enqueue_vc_revocation", enqueue)

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
            _Pool(store), batch_id=store.batch_id
        ))

    assert exc.value.code == code
    assert store.freeze_steps == []
    assert queued == []
    assert store.licenses[0]["status"] == "active"


def test_freeze_rejects_count_drift_and_foreign_batch_link_before_mutation(monkeypatch):
    """Break caught: freeze can accept stale approval counts or steal another batch's linkage."""
    async def enqueue(_conn, **_kwargs):
        raise AssertionError("enqueue must not run before scope guards pass")

    monkeypatch.setattr(facemarket_cutover.facemarket, "enqueue_vc_revocation", enqueue)

    drift = _Store()
    drift.batch_status = "draining"
    drift.batch_license_count = 3
    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
            _Pool(drift), batch_id=drift.batch_id
        ))
    assert exc.value.code == "target_scope_changed"
    assert drift.freeze_steps == []

    foreign = _Store()
    foreign.batch_status = "draining"
    foreign.licenses[0]["reverification_batch_id"] = "other-batch"
    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.freeze_initial_cutover_batch(
            _Pool(foreign), batch_id=foreign.batch_id
        ))
    assert exc.value.code == "target_scope_link_conflict"
    assert foreign.freeze_steps == []


def test_initial_scope_query_classifies_complete_provenance_without_status_or_expiry():
    """Break caught: modern revoked/suspended/expired models get frozen because status leaks into scope."""
    query = " ".join(facemarket_cutover._INITIAL_LEGACY_MODEL_SCOPE_SQL.split()).lower()

    assert "not exists" in query
    assert "e.status = 'passed'" in query
    assert "e.decision = 'passed'" in query
    assert "e.consent_version = %s" in query
    assert "m.assets_status = 'ready'" in query
    assert "l.enrollment_id = e.id" in query
    assert "l.vc_id = e.vc_id" in query
    assert "p.storage_state = 'approved'" in query
    assert "fa.view = 'face_front'" in query
    assert "ga.view = 'grid_sedcard'" in query
    assert "m.status" not in query
    assert "l.status" not in query
    assert "license_valid_until" not in query


def test_initial_manifest_digest_ignores_asset_count_and_uses_legacy_job_fallback(monkeypatch):
    """Break caught: mutable R2 evidence or default no-fallback job lookup changes approval identity."""
    calls = []
    asset_counts = [10, 12]

    async def model_ids(_conn):
        return ["model-b", "model-a"]

    async def license_ids(_conn, model_ids):
        assert model_ids == ["model-a", "model-b"]
        return ["license-b", "license-a", "license-a"]

    async def jobs(conn, **kwargs):
        calls.append(kwargs)
        return [
            {"id": "job-b", "created_at": 2},
            {"id": "job-a", "created_at": 1},
            {"id": "job-a", "created_at": 1},
        ]

    async def assets(_app, **kwargs):
        assert kwargs["model_ids"] == ("model-a", "model-b")
        assert kwargs["license_ids"] == ("license-a", "license-b")
        assert kwargs["job_ids"] == ("job-a", "job-b")
        return asset_counts.pop(0)

    monkeypatch.setattr(facemarket_cutover, "_initial_legacy_model_ids", model_ids)
    monkeypatch.setattr(facemarket_cutover, "_initial_legacy_license_ids", license_ids)
    monkeypatch.setattr(facemarket_cutover.repo, "list_facemarket_scope_jobs", jobs)
    monkeypatch.setattr(facemarket_cutover.biometric_purge, "initial_cutover_asset_count", assets)

    app = type("App", (), {"state": type("State", (), {"pool": _Pool(_Store())})()})()

    first = asyncio.run(facemarket_cutover.build_initial_cutover_manifest(app))
    second = asyncio.run(facemarket_cutover.build_initial_cutover_manifest(app))

    assert first.public_summary() == {
        "targetDigest": first.target_digest,
        "modelCount": 2,
        "licenseCount": 2,
        "jobCount": 2,
        "assetCount": 10,
    }
    assert second.asset_count == 12
    assert first.target_digest == second.target_digest
    assert calls == [
        {
            "model_ids": ("model-a", "model-b"),
            "license_ids": ("license-a", "license-b"),
            "initial_legacy_project_fallback": True,
        },
        {
            "model_ids": ("model-a", "model-b"),
            "license_ids": ("license-a", "license-b"),
            "initial_legacy_project_fallback": True,
        },
    ]
    assert "model-a" not in repr(first)
    assert "license-a" not in str(first.public_summary())


def test_approve_initial_batch_requires_admin_and_tags_exact_jobs(monkeypatch):
    """Break caught: approval can skip admin or reuse Task5's cancellation tag."""
    store = _Store()
    store.batch_status = "planned"
    manifest = facemarket_cutover.CutoverManifest(
        model_ids=("model-legacy",),
        license_ids=("license-a",),
        job_ids=("job-done", "job-running"),
        asset_count=3,
    )
    store.manifest_batch = {
        "id": store.batch_id,
        "status": "planned",
        "target_digest": manifest.target_digest,
        "model_count": 1,
        "license_count": 1,
        "job_count": 2,
        "asset_count": 3,
    }

    async def build(_app):
        return manifest

    async def is_admin(_conn, user_id):
        return user_id == "admin-user"

    monkeypatch.setattr(facemarket_cutover, "build_initial_cutover_manifest", build)
    monkeypatch.setattr(facemarket_cutover.repo, "is_admin", is_admin)
    app = type("App", (), {"state": type("State", (), {"pool": _Pool(store)})()})()

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(facemarket_cutover.approve_initial_cutover_batch(
            app, batch_id=store.batch_id, admin_user_id="normal-user"
        ))
    assert exc.value.code == "admin_required"

    asyncio.run(facemarket_cutover.approve_initial_cutover_batch(
        app, batch_id=store.batch_id, admin_user_id="admin-user"
    ))

    assert store.batch_status == "approved"
    assert store.tagged_jobs == ["job-done", "job-running"]
    assert store.job_metadata_tag == "facemarketManifestBatchId"
    assert store.job_metadata_tag != "cutoverBatchId"


def test_apply_initial_cutover_orders_reused_steps_and_completed_replay_is_noop(monkeypatch):
    """Break caught: Task8 starts R2 purge before close/quiesce/freeze or reruns completed batches."""
    store = _Store()
    store.batch_status = "approved"
    manifest = facemarket_cutover.CutoverManifest(
        model_ids=("model-legacy",),
        license_ids=("license-a", "license-b"),
        job_ids=(),
        asset_count=9,
    )
    store.manifest_batch = {
        "id": store.batch_id,
        "status": "approved",
        "target_digest": manifest.target_digest,
        "model_count": 1,
        "license_count": 2,
        "job_count": 0,
        "asset_count": 9,
    }
    order = []

    async def build(_app):
        order.append("manifest")
        return manifest

    async def close(_pool, *, batch_id):
        order.append("close")
        store.batch_status = "draining"

    async def quiesce(_pool, **_kwargs):
        order.append("quiesce")
        return facemarket_cutover.WriterQuiescence(0, 0, 0)

    async def freeze(_pool, *, batch_id):
        order.append("freeze")
        store.batch_status = "applying"
        store.models[0]["status"] = "reverification_required"
        store.models[0]["reverification_batch_id"] = batch_id
        for row in store.licenses:
            row["status"] = "reverification_required"
            row["reverification_batch_id"] = batch_id
        return facemarket_cutover.FreezeSummary(1, 2, 2)

    async def purge(_app, **kwargs):
        order.append("purge")
        assert kwargs == {"batch_id": store.batch_id, "reason": "reverification"}
        return type("Result", (), {
            "complete": True,
            "target_count": 4,
            "confirmed_absent_count": 4,
            "model_count": 1,
        })()

    monkeypatch.setattr(facemarket_cutover, "build_initial_cutover_manifest", build)
    monkeypatch.setattr(facemarket_cutover, "close_initial_cutover_writers", close)
    monkeypatch.setattr(facemarket_cutover, "quiesce_initial_cutover_writers", quiesce)
    monkeypatch.setattr(facemarket_cutover, "freeze_initial_cutover_batch", freeze)
    monkeypatch.setattr(facemarket_cutover, "purge_biometric_scope", purge)
    app = type("App", (), {"state": type("State", (), {"pool": _Pool(store)})()})()

    summary = asyncio.run(facemarket_cutover.apply_initial_cutover(
        app,
        batch_id=store.batch_id,
        confirmation=store.batch_id,
        drain_timeout_seconds=1,
    ))
    assert order == ["close", "quiesce", "manifest", "freeze", "purge"]
    assert summary == manifest.public_summary()
    assert store.batch_status == "completed"

    order.clear()
    replay = asyncio.run(facemarket_cutover.apply_initial_cutover(
        app,
        batch_id=store.batch_id,
        confirmation=store.batch_id,
        drain_timeout_seconds=1,
    ))
    assert replay == manifest.public_summary()
    assert order == []


def test_cutover_cli_help_has_no_mutation_modes():
    """Break caught: the corrected Task8 CLI exposes stale create/approve/apply modes."""
    from scripts import facemarket_security_cutover as script

    with pytest.raises(SystemExit) as exc:
        script.main(["--help"])
    assert exc.value.code == 0

    with pytest.raises(SystemExit):
        script.main(["--create-batch"])
    with pytest.raises(SystemExit):
        script.main(["--approve", _Store.batch_id])
    with pytest.raises(SystemExit):
        script.main(["--apply", _Store.batch_id])


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


def test_live_owner_revoke_fixture_uses_unique_vc_and_explicit_cleanup_contract():
    """Break caught: env-gated live test leaks fixed VC/user rows between runs."""
    body = inspect.getsource(
        test_live_owner_revoke_before_after_freeze_has_one_queue_row_and_no_deadlock
    ) + inspect.getsource(_cleanup_live_owner_revoke_fixture)

    assert "vc-live-cutover'" not in body
    assert 'vc-live-cutover"' not in body
    for table in (
        "fm_vc_revocation_jobs",
        "fm_licenses",
        "fm_models",
        "fm_cutover_batches",
        "auth.users",
    ):
        assert f"delete from {table}" in body.lower()


async def _cleanup_live_owner_revoke_fixture(
    conn, *, vc_id: str, license_id: str, model_id: str, batch_id: str, user_id: str
) -> None:
    await conn.execute("delete from fm_vc_revocation_jobs where vc_id = %s", (vc_id,))
    await conn.execute("delete from fm_licenses where id = %s", (license_id,))
    await conn.execute("delete from fm_models where id = %s", (model_id,))
    await conn.execute("delete from fm_cutover_batches where id = %s", (batch_id,))
    await conn.execute("delete from auth.users where id = %s", (user_id,))
    await conn.commit()


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="FACEMARKET_TEST_DATABASE_URL is not configured",
)
def test_live_owner_revoke_before_after_freeze_has_one_queue_row_and_no_deadlock():
    """Break caught: owner revoke and cutover freeze can deadlock or duplicate one VC revoke row."""

    class _RouteConn:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *_args):
            return False

    async def scenario():
        user_id = str(uuid.uuid4())
        model_id = str(uuid.uuid4())
        license_id = str(uuid.uuid4())
        batch_id = str(uuid.uuid4())
        vc_id = f"vc-live-cutover-{uuid.uuid4()}"
        revoke_conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        freeze_conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        verify_conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        original_get_conn = facemarket.get_conn
        primary_error = None
        try:
            await verify_conn.execute("insert into auth.users (id) values (%s)", (user_id,))
            await verify_conn.execute(
                "insert into fm_models (id, user_id, display_name, status) values (%s, %s, 'cutover', 'verified')",
                (model_id, user_id),
            )
            await verify_conn.execute(
                """
                insert into fm_licenses
                    (id, model_id, face_image_uri, face_image_key, face_image_digest,
                     license_valid_until, status, vc_id)
                values (%s, %s, '/face', 'legacy/front.png', 'sha256-x',
                        now() + interval '1 day', 'active', %s)
                """,
                (license_id, model_id, vc_id),
            )
            await verify_conn.execute(
                """
                insert into fm_cutover_batches
                    (id, status, target_digest, model_count, license_count, job_count, asset_count, started_at)
                values (%s, 'draining', 'digest', 1, 1, 0, 0, now())
                """,
                (batch_id,),
            )
            await verify_conn.commit()

            def route_conn(_request):
                return _RouteConn(revoke_conn)

            facemarket.get_conn = route_conn
            pool = type("Pool", (), {"connection": lambda _self: _RouteConn(freeze_conn)})()
            request = object()

            async def revoke_then_freeze():
                route_task = asyncio.create_task(
                    facemarket.revoke_license(request, license_id, user_id)
                )
                await asyncio.sleep(0)
                freeze_task = asyncio.create_task(
                    facemarket_cutover.freeze_initial_cutover_batch(pool, batch_id=batch_id)
                )
                await asyncio.gather(route_task, freeze_task)

            async def freeze_then_revoke():
                freeze_task = asyncio.create_task(
                    facemarket_cutover.freeze_initial_cutover_batch(pool, batch_id=batch_id)
                )
                await asyncio.sleep(0)
                route_task = asyncio.create_task(
                    facemarket.revoke_license(request, license_id, user_id)
                )
                await asyncio.gather(freeze_task, route_task)

            await asyncio.wait_for(revoke_then_freeze(), timeout=2)
            await verify_conn.execute("update fm_licenses set status='active' where id=%s", (license_id,))
            await verify_conn.execute("update fm_cutover_batches set status='draining' where id=%s", (batch_id,))
            await verify_conn.commit()
            await asyncio.wait_for(freeze_then_revoke(), timeout=2)
            row = await (
                await verify_conn.execute(
                    "select status from fm_licenses where id=%s", (license_id,)
                )
            ).fetchone()
            queued = await (
                await verify_conn.execute(
                    "select count(*)::int as count from fm_vc_revocation_jobs where vc_id=%s",
                    (vc_id,),
                )
            ).fetchone()
            assert row["status"] == "revoked"
            assert queued["count"] == 1
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            facemarket.get_conn = original_get_conn
            cleanup_error = None
            try:
                await revoke_conn.rollback()
                await freeze_conn.rollback()
                await verify_conn.rollback()
                await _cleanup_live_owner_revoke_fixture(
                    verify_conn,
                    vc_id=vc_id,
                    license_id=license_id,
                    model_id=model_id,
                    batch_id=batch_id,
                    user_id=user_id,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    await revoke_conn.rollback()
                    await freeze_conn.rollback()
                    await verify_conn.rollback()
                finally:
                    await revoke_conn.close()
                    await freeze_conn.close()
                    await verify_conn.close()
            if cleanup_error is not None and primary_error is None:
                raise cleanup_error

    asyncio.run(scenario())
