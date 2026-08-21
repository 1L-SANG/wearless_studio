"""Enrollment-bound FaceMarket asset promotion tests."""

import asyncio
import contextlib
import io
import json
import threading
import types
from dataclasses import dataclass

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app import facemarket, facemarket_enrollment
from app.workers.fm_model_asset_job import run_fm_model_asset_job
from conftest import make_settings

MODEL_ID = "11111111-1111-1111-1111-111111111111"
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"


def _png_bytes(color: int) -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.full((128, 128, 3), color, np.uint8)).save(out, format="PNG")
    return out.getvalue()


@dataclass
class CopyCall:
    source: str
    destination: str


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.store = conn.store
        self._last = None

    async def execute(self, sql, params=None):
        self.store.log.append((sql, params))
        s = " ".join(sql.split()).lower()
        if s.startswith("select pg_try_advisory_xact_lock"):
            self._last = {"locked": True}
        elif s.startswith("select pg_try_advisory_lock"):
            lock_key = tuple(params)
            owner = self.store.advisory_lock_owners.get(lock_key)
            locked = owner is None or owner is self.conn
            if locked:
                self.store.advisory_lock_owners[lock_key] = self.conn
                self.conn.advisory_locks.add(lock_key)
            self._last = {"locked": locked}
        elif s.startswith("select pg_advisory_unlock"):
            lock_key = tuple(params)
            self._last = {"unlocked": self.conn.release_advisory_lock(lock_key)}
        elif "from fm_biometric_enrollment_photos" in s and "join fm_biometric_enrollments" in s:
            self._last = self.store.enrollment_rows if self.store.initial_binding else []
        elif "from fm_models m join personalization_profiles" in s:
            self._last = {"status": "verified", "profile_id": "prof-1"}
        elif "from personalization_face_photos" in s:
            self._last = self.store.legacy_rows
        elif "from jobs" in s and "locked_by" in s and "status" in s and "running" in s:
            self._last = {"id": "job-1"} if self.store.next_lease_ok() else None
        elif "from fm_biometric_enrollments" in s and "for update" in s:
            self._last = (
                {"status": "asset_building", "match_policy_version": "policy-v1"}
                if self.store.final_binding
                else None
            )
        elif "from fm_models" in s and "for update" in s:
            self._last = (
                {"status": self.store.model_status, "current_enrollment_id": ENROLLMENT_ID}
                if self.store.final_binding
                else None
            )
        elif s.startswith("select c.model_id::text as model_id"):
            rows = [ref.copy() for ref in self.store.asset_cleanup_refs if ref["due"]]
            self._last = rows[: params[-1]]
        elif s.startswith("select 1 from fm_model_assets"):
            _model_id, key = params
            self._last = (
                {"?column?": 1}
                if key in self.store.old_asset_keys.values()
                else None
            )
        elif "from fm_model_assets" in s:
            self._last = [
                {"view": view, "r2_key": key}
                for view, key in self.store.old_asset_keys.items()
            ]
        elif s.startswith("insert into fm_model_assets"):
            _model_id, view, key, *_rest = params
            self.store.old_asset_keys[view] = key
            self._last = None
        elif s.startswith("update fm_models") and "assets_status='ready'" in s:
            self.store.ready_updates += 1
            self._last = None
        elif s.startswith("update fm_models") and "assets_status='failed'" in s:
            self.store.failed_updates += 1
            self._last = None
        elif s.startswith("update fm_models") and "set assets_status='building'" in s:
            self.store.building_updates += 1
            self._last = None
        elif s.startswith("update fm_biometric_enrollments") and "status='failed'" in s:
            self.store.enrollment_failed_updates += 1
            self._last = None
        elif s.startswith("insert into fm_biometric_enrollment_photo_cleanup"):
            self.store.cleanup_refs.append({"angle": params[1], "key": params[2], "reason": "delete"})
            self._last = None
        elif s.startswith("insert into fm_model_asset_cleanup"):
            model_id, key = params
            self.store.asset_cleanup_refs = [
                ref for ref in self.store.asset_cleanup_refs
                if (ref["model_id"], ref["r2_key"]) != (model_id, key)
            ]
            self.store.asset_cleanup_refs.append(
                {"model_id": model_id, "r2_key": key, "reason": "superseded", "due": True}
            )
            self._last = None
        elif s.startswith("update fm_model_asset_cleanup"):
            model_id, key = params
            for ref in self.store.asset_cleanup_refs:
                if (ref["model_id"], ref["r2_key"]) == (model_id, key):
                    ref["due"] = False
                    self.store.asset_cleanup_reschedules += 1
            self._last = None
        elif s.startswith("delete from fm_model_asset_cleanup"):
            model_id, key = params
            self.store.asset_cleanup_refs = [
                ref for ref in self.store.asset_cleanup_refs
                if (ref["model_id"], ref["r2_key"]) != (model_id, key)
            ]
            self._last = None
        elif s.startswith("delete from fm_biometric_enrollment_photo_cleanup"):
            enrollment_id, key = params
            self.store.cleanup_refs = [
                ref for ref in self.store.cleanup_refs
                if not (ref.get("key") == key and enrollment_id == ENROLLMENT_ID)
            ]
            self._last = None
        else:
            self._last = None

    async def fetchone(self):
        return self._last if not isinstance(self._last, list) else None

    async def fetchall(self):
        return self._last if isinstance(self._last, list) else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, store):
        self.store = store
        self.advisory_locks = set()
        self.closed = False

    async def commit(self):
        if self.closed:
            raise RuntimeError("connection closed")
        new_statements = [
            " ".join(sql.split()).lower()
            for sql, _ in self.store.log[self.store.last_commit_index:]
        ]
        self.store.commits.append(new_statements)
        self.store.last_commit_index = len(self.store.log)
        if (
            self.store.crash_after_done_commit
            and any("update jobs set status='done'" in stmt for stmt in new_statements)
        ):
            self.store.crash_after_done_commit = False
            raise RuntimeError("synthetic_after_commit_crash")
        return None

    async def rollback(self):
        if self.closed:
            raise RuntimeError("connection closed")
        return None

    def cursor(self):
        if self.closed:
            raise RuntimeError("connection closed")
        return _Cur(self)

    def release_advisory_lock(self, lock_key):
        if self.store.advisory_lock_owners.get(lock_key) is not self:
            return False
        self.store.advisory_lock_owners.pop(lock_key)
        self.advisory_locks.discard(lock_key)
        return True

    async def close(self):
        self.closed = True
        for lock_key in tuple(self.advisory_locks):
            self.release_advisory_lock(lock_key)


class _Pool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self.store)

        return _cm()


class _FaceR2:
    def __init__(self, *, fail_delete: str | None = None, fail_put: str | None = None):
        self.get_order: list[str] = []
        self.copies: list[CopyCall] = []
        self.puts: list[tuple[str, str]] = []
        self.deletes: list[str] = []
        self.delete_log_indexes: list[int] = []
        self.fail_delete = fail_delete
        self.fail_put = fail_put
        self.store = None
        self.block_first_put = False
        self.first_put_started = threading.Event()
        self.release_first_put = threading.Event()

    def get_bytes(self, key):
        angle = key.split("/")[-1].split(".")[0]
        self.get_order.append(angle)
        return _png_bytes({"front": 30, "angle45": 90, "side": 150}.get(angle, 10))

    def copy(self, source, destination, *_args, **_kwargs):
        self.copies.append(CopyCall(source, destination))

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, mime))
        if self.block_first_put and len(self.puts) == 1:
            self.first_put_started.set()
            self.release_first_put.wait(timeout=5)
        if self.fail_put and self.fail_put in key:
            raise RuntimeError("provider leaked/key.png")

    def delete(self, key):
        self.deletes.append(key)
        if self.store is not None:
            self.delete_log_indexes.append(len(self.store.log))
        if key == self.fail_delete:
            raise RuntimeError("provider leaked/key.png")


class _Store:
    def __init__(
        self,
        *,
        status="asset_building",
        initial_binding=True,
        final_binding=True,
        lease_ok=True,
        lease_sequence=None,
        photos=None,
        old_asset_keys=None,
        biometric_enabled=True,
        crash_after_done_commit=False,
    ):
        self.log = []
        self.initial_binding = initial_binding
        self.final_binding = final_binding
        self.lease_ok = lease_ok
        self.lease_sequence = list(lease_sequence or [])
        self.model_status = "pending"
        self.ready_updates = 0
        self.building_updates = 0
        self.failed_updates = 0
        self.enrollment_failed_updates = 0
        self.cleanup_refs = []
        self.asset_cleanup_refs = []
        self.asset_cleanup_reschedules = 0
        self.advisory_lock_owners = {}
        self.commits = []
        self.last_commit_index = 0
        self.crash_after_done_commit = crash_after_done_commit
        self.biometric_enabled = biometric_enabled
        self.old_asset_keys = old_asset_keys or {}
        self.legacy_rows = [
            {"angle": "side", "r2_key": "legacy/side.png", "mime_type": "image/png"},
            {"angle": "front", "r2_key": "legacy/front.png", "mime_type": "image/png"},
            {"angle": "angle45", "r2_key": "legacy/angle45.png", "mime_type": "image/png"},
        ]
        self.enrollment_rows = photos if photos is not None else [
            {
                "status": status,
                "match_policy_version": "policy-v1",
                "angle": "side",
                "r2_key": "quarantine/side.png",
                "mime_type": "image/png",
                "image_digest": "digest-side",
                "storage_state": "quarantine",
            },
            {
                "status": status,
                "match_policy_version": "policy-v1",
                "angle": "front",
                "r2_key": "quarantine/front.png",
                "mime_type": "image/png",
                "image_digest": "digest-front",
                "storage_state": "quarantine",
            },
            {
                "status": status,
                "match_policy_version": "policy-v1",
                "angle": "angle45",
                "r2_key": "quarantine/angle45.png",
                "mime_type": "image/png",
                "image_digest": "digest-angle45",
                "storage_state": "quarantine",
            },
        ]

    def next_lease_ok(self):
        if self.lease_sequence:
            return self.lease_sequence.pop(0)
        return self.lease_ok


def _job(payload=None):
    return {
        "id": "job-1",
        "user_id": "u1",
        "lease_token": "u1:tok",
        "payload": payload if payload is not None else {
            "modelId": MODEL_ID,
            "enrollmentId": ENROLLMENT_ID,
        },
    }


def build_worker_fixture(**store_kwargs):
    store = _Store(**store_kwargs)
    face_r2 = _FaceR2()
    face_r2.store = store
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(
            fm_face_qc_enabled=False,
            fm_biometric_enrollment_enabled=store.biometric_enabled,
        ),
    ))
    return app, store.log, face_r2, store


def test_asset_build_reads_only_enrollment_photos_and_promotes_in_contract_order():
    app, log, face_r2, _store = build_worker_fixture()

    asyncio.run(run_fm_model_asset_job(app, _job()))

    sql = " | ".join(" ".join(statement.split()) for statement, _ in log)
    assert "from fm_biometric_enrollment_photos" in sql
    assert "personalization_face_photos" not in sql
    assert face_r2.get_order == ["front", "angle45", "side"]
    assert [copy.destination for copy in face_r2.copies] == [
        f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/originals/front.png",
        f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/originals/angle45.png",
        f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/originals/side.png",
    ]


def test_asset_swap_is_bound_to_current_enrollment_and_version():
    app, log, face_r2, _store = build_worker_fixture(
        old_asset_keys={"face_front": "old/front.png", "grid_sedcard": "old/grid.png"}
    )

    asyncio.run(run_fm_model_asset_job(app, _job()))

    sql = " | ".join(" ".join(statement.split()) for statement, _ in log)
    assert "source_enrollment_id" in sql
    assert "evidence_version" in sql
    assert "current_enrollment_id" in sql
    assert "status='license_pending'" in sql
    assert face_r2.deletes[-2:] == ["old/front.png", "old/grid.png"]


@pytest.mark.parametrize(
    ("payload", "store_kwargs"),
    [
        ({"modelId": MODEL_ID}, {}),
        ({"modelId": MODEL_ID, "enrollmentId": ENROLLMENT_ID}, {"initial_binding": False}),
        ({"modelId": MODEL_ID, "enrollmentId": ENROLLMENT_ID}, {"status": "photos_pending"}),
        (
            {"modelId": MODEL_ID, "enrollmentId": ENROLLMENT_ID},
            {"photos": [
                {
                    "status": "asset_building",
                    "match_policy_version": "policy-v1",
                    "angle": "front",
                    "r2_key": "quarantine/front.png",
                    "mime_type": "image/png",
                    "image_digest": "digest-front",
                    "storage_state": "quarantine",
                }
            ]},
        ),
    ],
)
def test_invalid_or_stale_enrollment_build_creates_no_approved_object(payload, store_kwargs):
    app, _log, face_r2, store = build_worker_fixture(**store_kwargs)

    asyncio.run(run_fm_model_asset_job(app, _job(payload)))

    assert face_r2.copies == []
    assert face_r2.puts == []
    assert store.ready_updates == 0


def test_lost_final_lease_cleans_attempt_and_does_not_set_ready():
    app, _log, face_r2, store = build_worker_fixture(lease_ok=False)

    asyncio.run(run_fm_model_asset_job(app, _job()))

    attempted = [copy.destination for copy in face_r2.copies] + [key for key, _ in face_r2.puts]
    assert attempted
    assert face_r2.deletes == []
    assert set(attempted).issubset({ref["key"] for ref in store.cleanup_refs})
    assert store.ready_updates == 0


def test_failed_post_commit_delete_keeps_retry_reference_without_logging_private_key(caplog):
    store = _Store(old_asset_keys={"face_front": "old/front.png"})
    face_r2 = _FaceR2(fail_delete="old/front.png")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(fm_face_qc_enabled=False),
    ))

    asyncio.run(run_fm_model_asset_job(app, _job()))

    assert store.cleanup_refs
    assert "old/front.png" not in caplog.text
    assert "provider leaked/key.png" not in caplog.text


def test_final_swap_registers_cleanup_intents_before_commit_survives_crash():
    app, _log, _face_r2, store = build_worker_fixture(
        old_asset_keys={"face_front": "old/front.png"},
        crash_after_done_commit=True,
    )

    asyncio.run(run_fm_model_asset_job(app, _job()))

    keys = {ref["key"] for ref in store.cleanup_refs}
    assert {
        "quarantine/front.png",
        "quarantine/angle45.png",
        "quarantine/side.png",
        "old/front.png",
    }.issubset(keys)
    done_commit = next(
        commit for commit in store.commits
        if any("update jobs set status='done'" in statement for statement in commit)
    )
    assert any("fm_biometric_enrollment_photo_cleanup" in statement for statement in done_commit)


def test_prewrite_failure_leaves_durable_cleanup_without_private_leak(caplog):
    store = _Store()
    face_r2 = _FaceR2(fail_put="grid_sedcard")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(
            fm_face_qc_enabled=False,
            fm_biometric_enrollment_enabled=True,
        ),
    ))

    asyncio.run(run_fm_model_asset_job(app, _job()))

    assert any("grid_sedcard" in ref["key"] for ref in store.cleanup_refs)
    assert "provider leaked/key.png" not in caplog.text


def test_lost_lease_does_not_delete_newer_deterministic_keys_or_fail_model():
    app, _log, face_r2, store = build_worker_fixture(lease_ok=False)

    asyncio.run(run_fm_model_asset_job(app, _job()))

    assert face_r2.puts
    assert face_r2.deletes == []
    assert store.failed_updates == 0
    assert store.enrollment_failed_updates == 0


def test_flag_off_model_only_job_uses_legacy_source_without_qc():
    app, log, face_r2, store = build_worker_fixture(biometric_enabled=False)

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    sql = " | ".join(" ".join(statement.split()) for statement, _ in log)
    assert "personalization_face_photos" in sql
    assert "pairwise_min_similarity" not in sql
    assert face_r2.get_order == ["front", "angle45", "side"]
    assert face_r2.puts
    assert all(f"enrollments/legacy/assets/" in key for key, _mime in face_r2.puts)
    assert store.building_updates == 1
    assert store.ready_updates == 1


def test_flag_off_prewrite_failure_leaves_building_fence_and_redacts_key(caplog):
    store = _Store(biometric_enabled=False)
    face_r2 = _FaceR2(fail_put="grid_sedcard")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(
            fm_face_qc_enabled=False,
            fm_biometric_enrollment_enabled=False,
        ),
    ))

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    assert store.building_updates == 1
    assert store.ready_updates == 0
    assert store.failed_updates == 0
    assert "provider leaked/key.png" not in caplog.text


def test_flag_off_lost_final_lease_after_writes_leaves_resolver_closed():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        lease_sequence=[True, False],
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    assert face_r2.puts
    assert face_r2.deletes == []
    assert store.building_updates == 1
    assert store.ready_updates == 0
    assert store.failed_updates == 0


def test_flag_off_same_model_second_worker_cannot_write_while_first_holds_fence():
    store = _Store(biometric_enabled=False)
    face_r2 = _FaceR2()
    face_r2.store = store
    face_r2.block_first_put = True
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(
            fm_face_qc_enabled=False,
            fm_biometric_enrollment_enabled=False,
        ),
    ))

    async def run_two():
        first = asyncio.create_task(
            run_fm_model_asset_job(app, _job({"modelId": MODEL_ID}))
        )
        await asyncio.to_thread(face_r2.first_put_started.wait, 5)
        writes_after_first_started = len(face_r2.puts)
        await run_fm_model_asset_job(app, _job({"modelId": MODEL_ID}))
        writes_after_second = len(face_r2.puts)
        face_r2.release_first_put.set()
        await first
        return writes_after_first_started, writes_after_second

    writes_after_first_started, writes_after_second = asyncio.run(run_two())

    assert writes_after_first_started == 1
    assert writes_after_second == 1


def test_flag_off_stale_lease_before_write_does_not_touch_r2_or_prior_keys():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        lease_sequence=[False],
        old_asset_keys={"face_front": "old/front.png"},
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    assert face_r2.puts == []
    assert face_r2.deletes == []
    assert store.building_updates == 0
    assert store.ready_updates == 0


def test_flag_off_lost_final_lease_with_old_assets_deletes_nothing():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        lease_sequence=[True, False],
        old_asset_keys={"face_front": "old/front.png"},
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    assert face_r2.puts
    assert face_r2.deletes == []
    assert store.ready_updates == 0


def test_flag_off_prior_keys_delete_only_after_final_commit():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        old_asset_keys={"face_front": "old/front.png"},
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    done_statement_index = next(
        index for index, (statement, _params) in enumerate(store.log)
        if "update jobs set status='done'" in " ".join(statement.split()).lower()
    )
    assert face_r2.delete_log_indexes
    assert min(face_r2.delete_log_indexes) > done_statement_index


def test_flag_off_final_swap_persists_prior_key_cleanup_before_done_commit():
    app, _log, _face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        old_asset_keys={"face_front": "private/legacy-prior.png"},
        crash_after_done_commit=True,
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    done_commit = next(
        commit for commit in store.commits
        if any("update jobs set status='done'" in statement for statement in commit)
    )
    assert any("insert into fm_model_asset_cleanup" in statement for statement in done_commit)
    assert store.asset_cleanup_refs == [{
        "model_id": MODEL_ID,
        "r2_key": "private/legacy-prior.png",
        "reason": "superseded",
        "due": True,
    }]


def test_flag_off_post_commit_crash_leaves_prior_key_selectable_by_sweep():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        old_asset_keys={"face_front": "private/legacy-prior.png"},
        crash_after_done_commit=True,
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))
    assert store.asset_cleanup_refs

    cleaned = asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(app, limit=10))

    assert cleaned == 1
    assert face_r2.deletes == ["private/legacy-prior.png"]
    assert store.asset_cleanup_refs == []


def test_flag_off_delete_failure_reschedules_then_sweep_deletes_without_key_leak(caplog):
    store = _Store(
        biometric_enabled=False,
        old_asset_keys={"face_front": "private/legacy-prior.png"},
    )
    face_r2 = _FaceR2(fail_delete="private/legacy-prior.png")
    face_r2.store = store
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(fm_biometric_enrollment_enabled=False),
    ))

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    assert store.asset_cleanup_refs
    assert store.asset_cleanup_reschedules == 1
    assert "private/legacy-prior.png" not in caplog.text
    assert "provider leaked/key.png" not in caplog.text

    store.asset_cleanup_refs[0]["due"] = True
    face_r2.fail_delete = None
    assert asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(app, limit=10)) == 1
    assert face_r2.deletes == ["private/legacy-prior.png", "private/legacy-prior.png"]
    assert store.asset_cleanup_refs == []


def test_model_asset_cleanup_resolves_current_reference_without_deleting_it():
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        old_asset_keys={"face_front": "private/current.png"},
    )
    store.asset_cleanup_refs.append({
        "model_id": MODEL_ID,
        "r2_key": "private/current.png",
        "reason": "superseded",
        "due": True,
    })

    assert asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(app, limit=10)) == 1

    assert face_r2.deletes == []
    assert store.asset_cleanup_refs == []


def test_model_asset_cleanup_sweep_claim_is_bounded_and_multi_instance_safe():
    app, log, _face_r2, store = build_worker_fixture(biometric_enabled=False)
    store.asset_cleanup_refs.append({
        "model_id": MODEL_ID,
        "r2_key": "private/stale.png",
        "reason": "superseded",
        "due": True,
    })

    asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(app, limit=7))

    claim_sql, claim_params = next(
        (" ".join(sql.split()).lower(), params)
        for sql, params in log
        if "from fm_model_asset_cleanup" in sql.lower()
    )
    assert "for update skip locked" in claim_sql
    assert "limit %s" in claim_sql
    assert claim_params[-1] == 7
    assert any("pg_try_advisory_xact_lock" in sql.lower() for sql, _ in log)


def test_flag_off_done_result_event_and_failure_logs_never_expose_prior_key(caplog):
    store = _Store(
        biometric_enabled=False,
        old_asset_keys={"face_front": "private/legacy-prior.png"},
    )
    face_r2 = _FaceR2(fail_delete="private/legacy-prior.png")
    face_r2.store = store
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(fm_biometric_enrollment_enabled=False),
    ))

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    public_metadata = [
        getattr(value, "obj", value)
        for sql, params in store.log
        if ("update jobs set status='done'" in " ".join(sql.split()).lower()
            or "insert into job_events" in " ".join(sql.split()).lower())
        for value in (params or ())
    ]
    assert "private/legacy-prior.png" not in json.dumps(public_metadata)
    assert "private/legacy-prior.png" not in caplog.text
    assert "provider leaked/key.png" not in caplog.text


def test_flag_off_cancellation_releases_model_fence_and_keeps_resolver_closed():
    store = _Store(biometric_enabled=False)
    face_r2 = _FaceR2()
    face_r2.store = store
    face_r2.block_first_put = True
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(store),
        r2_face=face_r2,
        settings=make_settings(
            fm_face_qc_enabled=False,
            fm_biometric_enrollment_enabled=False,
        ),
    ))

    async def run_cancel():
        task = asyncio.create_task(
            run_fm_model_asset_job(app, _job({"modelId": MODEL_ID}))
        )
        await asyncio.to_thread(face_r2.first_put_started.wait, 5)
        task.cancel()
        face_r2.release_first_put.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_cancel())

    sql = " | ".join(" ".join(statement.split()) for statement, _ in store.log)
    assert "pg_advisory_unlock" in sql
    assert store.advisory_lock_owners == {}
    assert store.ready_updates == 0
    assert face_r2.deletes == []


def test_flag_off_repeat_removes_prior_legacy_keys_without_touching_stable_keys():
    prior_front = f"facemarket/models/{MODEL_ID}/enrollments/legacy-job-old/assets/face_front.png"
    prior_grid = f"facemarket/models/{MODEL_ID}/enrollments/legacy-job-old/assets/grid_sedcard.png"
    app, _log, face_r2, store = build_worker_fixture(
        biometric_enabled=False,
        old_asset_keys={"face_front": prior_front, "grid_sedcard": prior_grid},
    )

    asyncio.run(run_fm_model_asset_job(app, _job({"modelId": MODEL_ID})))

    new_keys = {key for key, _mime in face_r2.puts}
    assert new_keys == {
        f"facemarket/models/{MODEL_ID}/enrollments/legacy/assets/grid_sedcard.png",
        f"facemarket/models/{MODEL_ID}/enrollments/legacy/assets/face_front.png",
    }
    assert face_r2.deletes == [prior_front, prior_grid]
    assert not new_keys.intersection(face_r2.deletes)
    assert store.ready_updates == 1


def test_manual_build_rejects_when_biometric_enrollment_enabled(monkeypatch):
    class ManualCur:
        async def execute(self, *_args):
            return None

        async def fetchone(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class ManualConn:
        def cursor(self):
            return ManualCur()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield ManualConn()

    monkeypatch.setattr(facemarket, "get_conn", fake_conn)
    request = types.SimpleNamespace(app=types.SimpleNamespace(
        state=types.SimpleNamespace(settings=make_settings(
            fm_biometric_enrollment_enabled=True
        ))
    ))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(facemarket.build_my_model_assets(request, user_id="u1"))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "biometric_enrollment_required"
