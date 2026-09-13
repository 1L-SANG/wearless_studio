"""Registration recovery uses real handlers with in-memory DB and storage doubles."""

import importlib.util
from dataclasses import replace
from datetime import date

import pytest

from app import facemarket_enrollment
from test_facemarket_biometric_enrollment import (
    NOW, FakeCursor, auth, completion_fakes, create_enrollment,
    enrollment_store, fake_pool, fake_r2, fake_rekognition, fake_sts,
    liveness_off_client, verify_identity,
)


def test_photo_resolution_keeps_actual_rows_and_never_falls_back_from_deleted_canonical():
    assert importlib.util.find_spec("app.facemarket_photos") is not None
    from app.facemarket_photos import resolve_photo_rows, photo_slot_candidates

    legacy = {"angle": "front", "storage_state": "approved", "r2_key": "old"}
    canonical = {"angle": "face01", "storage_state": "delete_pending", "r2_key": "new"}
    assert resolve_photo_rows([legacy], ("face01",)) == [legacy]
    assert resolve_photo_rows([canonical, legacy], ("face01",)) == [canonical]
    assert resolve_photo_rows([legacy, canonical], ("front",)) == [canonical]
    assert photo_slot_candidates("front") == ("face01", "front")


def add_photo(store, storage, eid, angle, *, state="quarantine"):
    key = f"private/{angle}.jpg"
    store.photos.append({
        "enrollment_id": eid, "angle": angle, "r2_key": key,
        "mime_type": "image/jpeg", "qc_status": "passed", "image_digest": f"hash-{angle}",
        "storage_state": state, "uploaded_at": NOW,
    })
    storage.objects[key] = (angle.encode(), "image/jpeg")
    return key


def test_legacy_slot_reload_returns_saved_bytes(liveness_off_client, auth, enrollment_store, fake_r2):
    eid = create_enrollment(liveness_off_client, auth)
    add_photo(enrollment_store, fake_r2, eid, "front")
    response = liveness_off_client.get(f"/v1/facemarket/enrollments/{eid}/photos/face01", headers=auth())
    assert response.status_code == 200, response.text
    assert response.content == b"front"


def test_delete_removes_both_physical_names_and_retries_failed_cleanup(
    liveness_off_client, auth, enrollment_store, fake_r2,
):
    eid = create_enrollment(liveness_off_client, auth)
    legacy = add_photo(enrollment_store, fake_r2, eid, "front")
    canonical = add_photo(enrollment_store, fake_r2, eid, "face01")
    fake_r2.fail_delete_for.add(legacy)
    first = liveness_off_client.delete(f"/v1/facemarket/enrollments/{eid}/photos/face01", headers=auth())
    assert first.status_code == 503, first.text
    assert canonical not in fake_r2.objects
    assert any(row["r2_key"] == legacy for row in enrollment_store.cleanup)
    fake_r2.fail_delete_for.clear()
    retry = liveness_off_client.delete(f"/v1/facemarket/enrollments/{eid}/photos/front", headers=auth())
    assert retry.status_code == 204, retry.text
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []
    assert fake_r2.objects == {}


def test_application_identity_mismatch_is_rejected_with_face_comparison_off(
    liveness_off_client, auth, enrollment_store, completion_fakes, monkeypatch,
):
    eid = create_enrollment(liveness_off_client, auth, verify_identity=False)
    enrollment_store.enrollments[0]["application_id"] = "approved-application"
    liveness_off_client.app.state.settings = replace(liveness_off_client.app.state.settings, fm_application_required=True)
    original_execute = FakeCursor.execute

    async def execute(cursor, sql, params=None):
        query = " ".join(sql.split())
        if query.startswith("select applicant_name, birthdate"):
            cursor.result = {"applicant_name": "다른지원자", "birthdate": date(1992, 1, 1), "identity_mismatch_count": 0, "contact_email": "synthetic@example.invalid"}
        elif query.startswith("update fm_model_applications set identity_mismatch_count"):
            cursor.result = None
        elif query.startswith("update fm_biometric_enrollments set identity_tx_digest"):
            cursor.store.enrollments[0]["identity_tx_digest"] = params[0]
        else:
            await original_execute(cursor, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)
    response = verify_identity(liveness_off_client, auth, eid)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "identity_claim_mismatch"
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"


def prepare_license_pending(client, auth, store, storage):
    eid = create_enrollment(client, auth)
    for slot in ("face01", "face02", "face03", "face04", "face05", "face06", "face07", "face08",
                 "torso01", "torso02", "torso03", "torso04", "torso05",
                 "full01", "full02", "full03", "full04", "full05"):
        add_photo(store, storage, eid, slot)
    store.enrollments[0]["status"] = "liveness_pending"
    result = client.post(f"/v1/facemarket/enrollments/{eid}/complete", json={}, headers=auth())
    assert result.status_code == 202, result.text
    # The worker's persisted result: three source rows approved, identity recorded once.
    store.enrollments[0]["status"] = "license_pending"
    store.models[0]["assets_status"] = "ready"
    for photo in store.photos:
        if photo["angle"] in ("face01", "face03", "face05"):
            photo["storage_state"] = "approved"
    return eid


def test_reopen_preserves_identity_and_revalidates_approved_unchanged_photos(
    liveness_off_client, auth, enrollment_store, fake_r2,
):
    eid = prepare_license_pending(liveness_off_client, auth, enrollment_store, fake_r2)
    identity_before = list(enrollment_store.identities)
    opened = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    assert opened.status_code == 200, opened.text
    assert opened.json()["photoRevision"] == 1
    assert opened.json()["status"] == "liveness_pending"
    assert enrollment_store.models[0]["assets_status"] == "none"
    changed = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/photos", data={"slot": "face01"},
        files={"photo": ("new.jpg", b"changed-photo", "image/jpeg")}, headers=auth(),
    )
    assert changed.status_code == 201, changed.text
    completed = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/complete", json={}, headers=auth())
    assert completed.status_code == 202, completed.text
    assert completed.json()["status"] == "asset_building"
    assert enrollment_store.identities == identity_before
    assert len(identity_before) == 1
    assert enrollment_store.jobs[-1]["payload"]["photoRevision"] == 1


@pytest.mark.parametrize("status", ["vc_pending", "passed", "review_pending"])
def test_issued_or_issuing_enrollment_cannot_reopen(liveness_off_client, auth, enrollment_store, status):
    eid = create_enrollment(liveness_off_client, auth)
    enrollment_store.enrollments[0]["status"] = status
    response = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    assert response.status_code == 409, response.text
    assert enrollment_store.enrollments[0]["status"] == status


def test_pending_license_prevents_reopen_even_before_vc_status_commit(
    liveness_off_client, auth, enrollment_store, fake_r2,
):
    eid = prepare_license_pending(liveness_off_client, auth, enrollment_store, fake_r2)
    enrollment_store.licenses.append({"enrollment_id": eid, "status": "pending"})
    response = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    assert response.status_code == 409, response.text
    assert enrollment_store.enrollments[0]["status"] == "license_pending"


def test_reopen_retry_returns_same_revision_and_does_not_request_identity_again(
    liveness_off_client, auth, enrollment_store, fake_r2,
):
    eid = prepare_license_pending(liveness_off_client, auth, enrollment_store, fake_r2)
    first = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    second = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    assert first.status_code == second.status_code == 200
    assert first.json()["photoRevision"] == second.json()["photoRevision"] == 1
    assert len(enrollment_store.identities) == 1


@pytest.mark.parametrize("failure", ["cancel", "storage", "expired"])
def test_revalidation_terminal_cleanup_deletes_unchanged_approved_photos(
    liveness_off_client, auth, enrollment_store, fake_r2, failure,
):
    from datetime import datetime, timedelta, timezone

    eid = prepare_license_pending(liveness_off_client, auth, enrollment_store, fake_r2)
    opened = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/reopen-photos", headers=auth())
    assert opened.status_code == 200
    if failure == "cancel":
        response = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/cancel", headers=auth())
    else:
        if failure == "storage":
            fake_r2.objects.pop("private/face02.jpg")
        else:
            enrollment_store.enrollments[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        response = liveness_off_client.post(f"/v1/facemarket/enrollments/{eid}/complete", json={}, headers=auth())
    assert response.status_code == 200, response.text
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []
    assert fake_r2.objects == {}
    assert enrollment_store.enrollments[0]["raw_deletion_evidence"]["quarantineDeleted"] is True
