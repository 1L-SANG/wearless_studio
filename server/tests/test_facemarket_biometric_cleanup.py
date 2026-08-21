import asyncio
import json
import types
import uuid
from datetime import timedelta

from app import facemarket_enrollment
from app.workers.dispatcher import JobDispatcher
from test_facemarket_biometric_enrollment import (
    NOW,
    FakePool,
    FakeR2,
    EnrollmentStore,
)


def _app(store, r2=None, *, enabled=True):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            pool=FakePool(store),
            r2_face=r2 or FakeR2(),
            settings=types.SimpleNamespace(fm_biometric_enrollment_enabled=enabled),
        )
    )


def _add_enrollment(store, *, status, expires_at=None, photos=()):
    enrollment_id = str(uuid.UUID(int=uuid.UUID(int=1).int + len(store.enrollments)))
    store.enrollments.append(
        {
            "id": enrollment_id,
            "user_id": f"user-{len(store.enrollments)}",
            "model_id": None,
            "device_digest": f"device-{len(store.enrollments)}",
            "consent_version": "2026-08-v1",
            "status": status,
            "decision": "failed" if status in {"failed", "cancelled", "expired"} else None,
            "reason": None,
            "provider_versions": {},
            "cooldown_until": None,
            "expires_at": expires_at or NOW + timedelta(hours=1),
            "completed_at": NOW if status in {"failed", "cancelled", "expired"} else None,
            "raw_deletion_evidence": {},
        }
    )
    for angle, key, storage_state in photos:
        store.photos.append(
            {
                "enrollment_id": enrollment_id,
                "angle": angle,
                "r2_key": key,
                "image_digest": f"sha256-{angle}",
                "mime_type": "image/jpeg",
                "byte_size": 10,
                "qc_status": "passed",
                "storage_state": storage_state,
                "uploaded_at": NOW,
            }
        )
    return enrollment_id


def _remaining_angles(store, enrollment_id):
    return [
        row["angle"]
        for row in store.photos
        if row["enrollment_id"] == enrollment_id
        and row["storage_state"] in {"quarantine", "delete_pending"}
    ]


def test_terminal_cleanup_deletes_only_quarantine_and_records_redacted_counts():
    store = EnrollmentStore()
    r2 = FakeR2()
    enrollment_id = _add_enrollment(
        store,
        status="failed",
        photos=[
            ("front", "private/front.jpg", "quarantine"),
            ("angle45", "private/angle45.jpg", "quarantine"),
            ("side", "private/side.jpg", "approved"),
        ],
    )
    r2.objects.update(
        {
            "private/front.jpg": (b"front", "image/jpeg"),
            "private/angle45.jpg": (b"angle45", "image/jpeg"),
            "private/side.jpg": (b"side", "image/jpeg"),
        }
    )

    assert asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            _app(store, r2), enrollment_id=enrollment_id
        )
    ) is True

    assert r2.deletes == ["private/front.jpg", "private/angle45.jpg"]
    assert [row["angle"] for row in store.photos] == ["side"]
    evidence = store.enrollments[0]["raw_deletion_evidence"]
    assert evidence["quarantineDeleted"] is True
    assert evidence["quarantineDeletedCount"] == 2
    assert "private/" not in json.dumps(evidence)


def test_cleanup_retries_only_objects_that_failed():
    store = EnrollmentStore()
    r2 = FakeR2()
    enrollment_id = _add_enrollment(
        store,
        status="failed",
        photos=[
            ("front", "private/front.jpg", "quarantine"),
            ("angle45", "private/angle45.jpg", "quarantine"),
            ("side", "private/side.jpg", "quarantine"),
        ],
    )
    for angle in ("front", "angle45", "side"):
        r2.objects[f"private/{angle}.jpg"] = (angle.encode(), "image/jpeg")
    original_delete = r2.delete
    failed_once = False

    def fail_angle45_once(key):
        nonlocal failed_once
        if key == "private/angle45.jpg" and not failed_once:
            failed_once = True
            raise RuntimeError("r2 unavailable")
        original_delete(key)

    r2.delete = fail_angle45_once

    assert asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            _app(store, r2), enrollment_id=enrollment_id
        )
    ) is False
    assert _remaining_angles(store, enrollment_id) == ["angle45"]

    assert asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            _app(store, r2), enrollment_id=enrollment_id
        )
    ) is True
    assert _remaining_angles(store, enrollment_id) == []


def test_sweep_expires_preapproval_rows_and_retries_pending_deletes():
    store = EnrollmentStore()
    r2 = FakeR2()
    expired_id = _add_enrollment(
        store,
        status="liveness_pending",
        expires_at=NOW - timedelta(seconds=1),
        photos=[("front", "private/front.jpg", "quarantine")],
    )
    active_id = _add_enrollment(
        store,
        status="asset_building",
        expires_at=NOW - timedelta(seconds=1),
        photos=[("front", "private/approved-front.jpg", "approved")],
    )
    r2.objects["private/front.jpg"] = (b"front", "image/jpeg")
    r2.objects["private/approved-front.jpg"] = (b"approved", "image/jpeg")

    cleaned = asyncio.run(
        facemarket_enrollment.sweep_terminal_enrollments(_app(store, r2), limit=100)
    )

    assert cleaned == 1
    assert store.enrollments[0]["status"] == "expired"
    assert store.enrollments[0]["reason"] == "enrollment_expired"
    assert store.enrollments[1]["id"] == active_id
    assert store.enrollments[1]["status"] == "asset_building"
    assert r2.deletes == ["private/front.jpg"]
    assert "private/approved-front.jpg" in r2.objects
    assert active_id != expired_id


def test_sweep_completes_terminal_false_evidence_with_no_cleanup_rows():
    store = EnrollmentStore()
    enrollment_id = _add_enrollment(store, status="failed")
    store.enrollments[0]["raw_deletion_evidence"] = {"quarantineDeleted": False}

    cleaned = asyncio.run(
        facemarket_enrollment.sweep_terminal_enrollments(_app(store), limit=100)
    )

    assert cleaned == 1
    assert store.terminal_cleanup_loads == 1
    assert store.enrollments[0]["id"] == enrollment_id
    assert store.enrollments[0]["raw_deletion_evidence"]["quarantineDeleted"] is True
    assert store.enrollments[0]["raw_deletion_evidence"]["quarantineDeletedCount"] == 0


def test_sweep_drains_due_license_pending_cleanup_rows():
    store = EnrollmentStore()
    r2 = FakeR2()
    enrollment_id = _add_enrollment(store, status="license_pending")
    store.cleanup.append(
        {
            "enrollment_id": enrollment_id,
            "angle": "front",
            "r2_key": "private/stale-front.jpg",
            "reason": "delete",
            "created_at": NOW,
            "not_before": NOW,
        }
    )
    r2.objects["private/stale-front.jpg"] = (b"front", "image/jpeg")

    cleaned = asyncio.run(
        facemarket_enrollment.sweep_terminal_enrollments(_app(store, r2), limit=100)
    )

    assert cleaned == 1
    assert r2.deletes == ["private/stale-front.jpg"]
    assert store.cleanup == []
    assert store.enrollments[0]["status"] == "license_pending"


def test_dispatcher_recovery_runs_biometric_sweep_when_feature_enabled(monkeypatch):
    calls = []

    async def fake_sweep(app, *, limit):
        calls.append((app, limit))
        return 0

    async def noop(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.workers.dispatcher.repo.recover_stale_leases", noop)
    monkeypatch.setattr("app.workers.dispatcher.repo.list_unsettled_errored_jobs", noop)
    monkeypatch.setattr(facemarket_enrollment, "sweep_terminal_enrollments", fake_sweep)
    store = EnrollmentStore()
    app = _app(store, enabled=True)
    dispatcher = JobDispatcher(app)

    asyncio.run(
        dispatcher._recover_stale(
            types.SimpleNamespace(job_lease_timeout_seconds=30), app.state.pool
        )
    )

    assert calls == [(app, 100)]
