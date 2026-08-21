import contextlib
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_enrollment, r2
from app.main import create_app
from app.personalization_qc import FaceQcResult
from conftest import make_settings


NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
DEVICE_ID = "device-id-with-at-least-32-characters"
ACTIVE_STATUSES = {
    "photos_pending",
    "liveness_pending",
    "processing",
    "asset_building",
    "license_pending",
    "vc_pending",
}


class EnrollmentStore:
    def __init__(self):
        self.enrollments = []
        self.photos = []
        self.models = []
        self.licenses = []
        self.fail_photo_upsert = False

    def serialized(self):
        return json.dumps(
            {
                "enrollments": self.enrollments,
                "photos": self.photos,
                "models": self.models,
                "licenses": self.licenses,
            },
            default=str,
        )


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.result = None
        self.many = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.result = None
        self.many = []

        if query.startswith("select count(*) filter"):
            user_id, device_digest = params
            matching = [
                row
                for row in self.store.enrollments
                if row["status"] == "failed"
                and row.get("completed_at")
                and row["completed_at"] >= NOW - timedelta(minutes=3)
                and (row["user_id"] == user_id or row["device_digest"] == device_digest)
            ]
            cooldowns = [
                row["cooldown_until"]
                for row in self.store.enrollments
                if (row["user_id"] == user_id or row["device_digest"] == device_digest)
                and row.get("cooldown_until")
            ]
            self.result = {
                "recent_failures": len(matching),
                "cooldown_until": max(cooldowns, default=None),
            }
        elif query.startswith("select id::text as id, status from fm_models"):
            user_id = params[0]
            model = next(
                (row for row in reversed(self.store.models) if row["user_id"] == user_id),
                None,
            )
            self.result = {"id": model["id"], "status": model["status"]} if model else None
        elif query.startswith("update fm_models set status = 'reverification_required'"):
            model_id = params[0]
            model = next(row for row in self.store.models if row["id"] == model_id)
            model.update(
                status="reverification_required",
                assets_status="none",
                current_enrollment_id=None,
            )
        elif query.startswith("update fm_licenses set status = 'reverification_required'"):
            model_id = params[0]
            for license_row in self.store.licenses:
                if license_row["model_id"] == model_id and license_row["status"] == "active":
                    license_row["status"] = "reverification_required"
        elif query.startswith("insert into fm_biometric_enrollments"):
            user_id, model_id, device_digest, consent_version, expires_at = params
            existing = next(
                (
                    row
                    for row in self.store.enrollments
                    if row["user_id"] == user_id and row["status"] in ACTIVE_STATUSES
                ),
                None,
            )
            if existing:
                self.result = None
            else:
                row = {
                    "id": f"enrollment-{len(self.store.enrollments) + 1}",
                    "user_id": user_id,
                    "model_id": model_id,
                    "device_digest": device_digest,
                    "consent_version": consent_version,
                    "status": "photos_pending",
                    "decision": None,
                    "reason": None,
                    "cooldown_until": None,
                    "expires_at": expires_at,
                    "completed_at": None,
                    "raw_deletion_evidence": {},
                }
                self.store.enrollments.append(row)
                self.result = {"id": row["id"]}
        elif query.startswith("select id::text as id from fm_biometric_enrollments"):
            user_id = params[0]
            row = next(
                (
                    item
                    for item in reversed(self.store.enrollments)
                    if item["user_id"] == user_id and item["status"] in ACTIVE_STATUSES
                ),
                None,
            )
            self.result = {"id": row["id"]} if row else None
        elif query.startswith("select e.id::text as id") and "where e.user_id = %s" in query:
            user_id = params[0]
            row = next(
                (
                    item
                    for item in reversed(self.store.enrollments)
                    if item["user_id"] == user_id and item["status"] in ACTIVE_STATUSES
                ),
                None,
            )
            self.result = _enrollment_db_view(row) if row else None
        elif query.startswith("select e.id::text as id"):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id and item["user_id"] == user_id
                    and ("e.status in" not in query or item["status"] in ACTIVE_STATUSES)
                ),
                None,
            )
            self.result = _enrollment_db_view(row) if row else None
        elif query.startswith("select p.angle, p.qc_status, p.uploaded_at"):
            enrollment_id = params[0]
            self.many = [
                {
                    "angle": photo["angle"],
                    "qc_status": photo["qc_status"],
                    "uploaded_at": photo["uploaded_at"],
                }
                for photo in self.store.photos
                if photo["enrollment_id"] == enrollment_id
            ]
        elif query.startswith("select r2_key from fm_biometric_enrollment_photos"):
            enrollment_id, angle = params
            photo = next(
                (
                    item
                    for item in self.store.photos
                    if item["enrollment_id"] == enrollment_id and item["angle"] == angle
                ),
                None,
            )
            self.result = {"r2_key": photo["r2_key"]} if photo else None
        elif query.startswith("delete from fm_biometric_enrollment_photos"):
            enrollment_id, angle, key = params
            self.store.photos[:] = [
                photo
                for photo in self.store.photos
                if not (
                    photo["enrollment_id"] == enrollment_id
                    and photo["angle"] == angle
                    and photo["r2_key"] == key
                )
            ]
        elif query.startswith("insert into fm_biometric_enrollment_photos"):
            if self.store.fail_photo_upsert:
                raise RuntimeError("database unavailable")
            enrollment_id, angle, key, digest, mime, byte_size = params
            photo = next(
                (
                    item
                    for item in self.store.photos
                    if item["enrollment_id"] == enrollment_id and item["angle"] == angle
                ),
                None,
            )
            uploaded_at = NOW
            values = {
                "enrollment_id": enrollment_id,
                "angle": angle,
                "r2_key": key,
                "image_digest": digest,
                "mime_type": mime,
                "byte_size": byte_size,
                "qc_status": "passed",
                "storage_state": "quarantine",
                "uploaded_at": uploaded_at,
            }
            if photo:
                photo.update(values)
            else:
                self.store.photos.append(values)
            self.result = {"uploaded_at": uploaded_at}
        elif query.startswith("select count(*) as passed_count"):
            enrollment_id = params[0]
            self.result = {
                "passed_count": sum(
                    photo["enrollment_id"] == enrollment_id
                    and photo["qc_status"] == "passed"
                    for photo in self.store.photos
                )
            }
        elif query.startswith("update fm_biometric_enrollments set status = 'liveness_pending'"):
            enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            row["status"] = "liveness_pending"
        elif query.startswith("update fm_biometric_enrollments set status = 'photos_pending'"):
            enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            if row["status"] == "liveness_pending":
                row["status"] = "photos_pending"
        elif query.startswith("update fm_biometric_enrollments e set status = 'cancelled'"):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["user_id"] == user_id
                    and (item["status"] in ACTIVE_STATUSES or item["status"] == "cancelled")
                ),
                None,
            )
            if row:
                row["status"] = "cancelled"
                row["completed_at"] = row.get("completed_at") or NOW
                self.result = {"id": row["id"]}
        elif query.startswith("select e.status, p.angle, p.r2_key"):
            enrollment_id = params[0]
            enrollment = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["status"] in {"failed", "cancelled", "expired"}
                ),
                None,
            )
            if enrollment:
                photos = [
                    photo
                    for photo in self.store.photos
                    if photo["enrollment_id"] == enrollment_id
                    and photo["storage_state"] == "quarantine"
                ]
                self.many = (
                    [
                        {
                            "status": enrollment["status"],
                            "angle": photo["angle"],
                            "r2_key": photo["r2_key"],
                        }
                        for photo in photos
                    ]
                    or [{"status": enrollment["status"], "angle": None, "r2_key": None}]
                )
        elif query.startswith("select count(*) as remaining"):
            enrollment_id = params[0]
            self.result = {
                "remaining": sum(
                    photo["enrollment_id"] == enrollment_id
                    and photo["storage_state"] == "quarantine"
                    for photo in self.store.photos
                )
            }
        elif query.startswith("update fm_biometric_enrollments set raw_deletion_evidence"):
            complete, deleted_count, failed_count, enrollment_id = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            evidence = row["raw_deletion_evidence"]
            evidence.update(
                quarantineDeleted=complete,
                quarantineDeletedCount=evidence.get("quarantineDeletedCount", 0)
                + deleted_count,
                quarantineDeleteFailedCount=evidence.get("quarantineDeleteFailedCount", 0)
                + failed_count,
                quarantineCleanupAt=NOW.isoformat(),
            )
        else:
            raise AssertionError(f"unexpected SQL: {query}")

    async def fetchone(self):
        return self.result

    async def fetchall(self):
        return self.many


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        return None

    async def rollback(self):
        return None


class FakePool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        @contextlib.asynccontextmanager
        async def connection():
            yield FakeConn(self.store)

        return connection()


class FakeR2:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.deletes = []
        self.fail_delete_for = set()

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))
        self.objects[key] = (data, mime)

    def delete(self, key):
        if key in self.fail_delete_for:
            raise RuntimeError("r2 unavailable")
        self.deletes.append(key)
        self.objects.pop(key, None)


def _enrollment_db_view(row):
    return {
        "id": row["id"],
        "model_id": row["model_id"],
        "status": row["status"],
        "decision": row["decision"],
        "reason": row["reason"],
        "cooldown_until": row["cooldown_until"],
        "expires_at": row["expires_at"],
    }


@pytest.fixture()
def enrollment_store():
    return EnrollmentStore()


@pytest.fixture()
def fake_r2():
    return FakeR2()


@pytest.fixture()
def enrollment_client(keypair, monkeypatch, enrollment_store, fake_r2):
    _private_key, public_key = keypair
    settings = make_settings(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version="dev-gold-v1",
        fm_face_qc_enabled=True,
    )
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(enrollment_store)

    monkeypatch.setattr(facemarket_enrollment, "get_conn", fake_get_conn, raising=False)
    app = create_app(settings)
    app.state.jwt_key_resolver = lambda _token: public_key
    app.state.r2_face = fake_r2
    app.state.pool = FakePool(enrollment_store)
    return TestClient(app)


@pytest.fixture()
def auth(make_token):
    def headers(sub="user-1"):
        return {"Authorization": f"Bearer {make_token(sub=sub)}"}

    return headers


def create_enrollment(client, auth, *, device_id=DEVICE_ID):
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": device_id,
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def stub_qc(monkeypatch, verdict="pass", reasons=None):
    async def qc(*_args, **_kwargs):
        return FaceQcResult(verdict, reasons or [])

    monkeypatch.setattr(facemarket_enrollment, "evaluate_face_qc", qc)


def test_create_enrollment_records_consent_without_oacx_token(
    enrollment_client, auth, enrollment_store
):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "photos_pending"
    assert response.json()["requiredAngles"] == ["front", "angle45", "side"]
    assert "token" not in response.text
    assert "r2Key" not in response.text
    assert DEVICE_ID not in enrollment_store.serialized()
    assert enrollment_store.enrollments[0]["device_digest"] == hashlib.sha256(
        DEVICE_ID.encode()
    ).hexdigest()


def test_create_enrollment_rejects_stale_consent_version(
    enrollment_client, auth, enrollment_store
):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-07-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "stale_consent_version"
    assert enrollment_store.enrollments == []


def test_create_enrollment_requires_accepted_biometric_consent(
    enrollment_client, auth, enrollment_store
):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {
                "accepted": False,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "biometric_consent_required"
    assert enrollment_store.enrollments == []


def test_create_enrollment_rejects_short_device_id(
    enrollment_client, auth, enrollment_store
):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": " short device ",
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_device"
    assert enrollment_store.enrollments == []


def test_create_enrollment_enforces_recent_failure_cooldown(
    enrollment_client, auth, enrollment_store
):
    for index in range(5):
        enrollment_store.enrollments.append(
            {
                "id": f"failed-{index}",
                "user_id": "user-1",
                "model_id": None,
                "device_digest": "another-device",
                "consent_version": "2026-08-v1",
                "status": "failed",
                "decision": "failed",
                "reason": "liveness_failed",
                "cooldown_until": None,
                "expires_at": NOW + timedelta(hours=20),
                "completed_at": NOW - timedelta(minutes=1),
                "raw_deletion_evidence": {},
            }
        )

    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "liveness_cooldown"
    assert len(enrollment_store.enrollments) == 5


def test_create_enrollment_enforces_active_device_cooldown(
    enrollment_client, auth, enrollment_store
):
    enrollment_store.enrollments.append(
        {
            "id": "failed-1",
            "user_id": "different-user",
            "model_id": None,
            "device_digest": hashlib.sha256(DEVICE_ID.encode()).hexdigest(),
            "consent_version": "2026-08-v1",
            "status": "failed",
            "decision": "failed",
            "reason": "liveness_failed",
            "cooldown_until": NOW + timedelta(minutes=30),
            "expires_at": NOW + timedelta(hours=20),
            "completed_at": NOW - timedelta(minutes=10),
            "raw_deletion_evidence": {},
        }
    )

    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {
                "accepted": True,
                "documentVersion": "2026-08-v1",
            },
        },
        headers=auth(),
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "liveness_cooldown"


def test_create_enrollment_freezes_existing_verified_model_and_active_licenses(
    enrollment_client, auth, enrollment_store
):
    enrollment_store.models.append(
        {
            "id": "model-1",
            "user_id": "user-1",
            "status": "verified",
            "assets_status": "ready",
            "current_enrollment_id": "old-enrollment",
        }
    )
    enrollment_store.licenses.extend(
        [
            {"id": "license-1", "model_id": "model-1", "status": "active"},
            {"id": "license-2", "model_id": "model-1", "status": "revoked"},
        ]
    )

    enrollment_id = create_enrollment(enrollment_client, auth)

    assert enrollment_store.models[0] == {
        "id": "model-1",
        "user_id": "user-1",
        "status": "reverification_required",
        "assets_status": "none",
        "current_enrollment_id": None,
    }
    assert [row["status"] for row in enrollment_store.licenses] == [
        "reverification_required",
        "revoked",
    ]
    assert enrollment_store.enrollments[0]["id"] == enrollment_id
    assert enrollment_store.enrollments[0]["model_id"] == "model-1"


def test_repeated_create_returns_the_existing_active_enrollment(
    enrollment_client, auth, enrollment_store
):
    first_id = create_enrollment(enrollment_client, auth)

    second_id = create_enrollment(enrollment_client, auth, device_id="x" * 32)

    assert second_id == first_id
    assert len(enrollment_store.enrollments) == 1


def test_current_and_status_return_only_the_owned_enrollment_view(
    enrollment_client, auth
):
    enrollment_id = create_enrollment(enrollment_client, auth)

    current = enrollment_client.get(
        "/v1/facemarket/enrollments/current", headers=auth()
    )
    status = enrollment_client.get(
        f"/v1/facemarket/enrollments/{enrollment_id}", headers=auth()
    )

    assert current.status_code == 200
    assert status.status_code == 200
    assert current.json() == status.json()
    assert set(status.json()) == {
        "id",
        "modelId",
        "status",
        "photos",
        "requiredAngles",
        "passed",
        "retryable",
        "reason",
        "expiresAt",
    }
    assert "digest" not in status.text.lower()
    assert "r2" not in status.text.lower()


def test_biometric_r2_keys_are_deterministic_and_private():
    assert r2.enrollment_quarantine_key(
        "enrollment-1", "angle45", "jpg"
    ) == "facemarket/enrollments/enrollment-1/quarantine/angle45.jpg"
    assert r2.enrollment_original_key(
        "model-1", "enrollment-1", "front", "png"
    ) == "facemarket/models/model-1/enrollments/enrollment-1/originals/front.png"
    assert r2.model_asset_key(
        "model-1", "enrollment-1", "face_front", "webp"
    ) == "facemarket/models/model-1/enrollments/enrollment-1/assets/face_front.webp"


def test_r2_copy_stays_server_side_and_replaces_content_type():
    calls = []

    class FakeS3:
        def copy_object(self, **kwargs):
            calls.append(kwargs)

    client = object.__new__(r2.R2Client)
    client._bucket = "private-faces"
    client._s3 = FakeS3()

    client.copy("quarantine/front.jpg", "originals/front.jpg", "image/jpeg")

    assert calls == [
        {
            "Bucket": "private-faces",
            "Key": "originals/front.jpg",
            "CopySource": {"Bucket": "private-faces", "Key": "quarantine/front.jpg"},
            "ContentType": "image/jpeg",
            "MetadataDirective": "REPLACE",
        }
    ]


def test_upload_passed_photo_uses_quarantine_prefix(
    enrollment_client, auth, fake_r2, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "angle45"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 201
    assert response.json()["angle"] == "angle45"
    assert response.json()["qcStatus"] == "passed"
    assert fake_r2.puts[0][0] == (
        f"facemarket/enrollments/{enrollment_id}/quarantine/angle45.jpg"
    )
    assert "quarantine" not in response.text


def test_upload_rejects_invalid_angle(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "three-quarter"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_angle"
    assert fake_r2.puts == []


def test_upload_rejects_non_image_mime(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.gif", b"image", "image/gif")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_type"
    assert fake_r2.puts == []


def test_upload_rejects_empty_file(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_upload"
    assert fake_r2.puts == []


def test_upload_rejects_file_over_25_mib(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"x" * (25 * 1024 * 1024 + 1), "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert fake_r2.puts == []


def test_failed_basic_qc_never_writes_to_r2(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch, "reject", ["occlusion"])
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "face_quality"
    assert response.json()["error"]["reasons"] == ["occlusion"]
    assert fake_r2.puts == []


def test_three_passed_angles_transition_to_liveness_pending(
    enrollment_client, auth, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    for angle in ("front", "angle45", "side"):
        response = enrollment_client.post(
            f"/v1/facemarket/enrollments/{enrollment_id}/photos",
            data={"angle": angle},
            files={"photo": (f"{angle}.jpg", b"image", "image/jpeg")},
            headers=auth(),
        )
        assert response.status_code == 201, response.text

    status = enrollment_client.get(
        f"/v1/facemarket/enrollments/{enrollment_id}", headers=auth()
    )
    assert status.json()["status"] == "liveness_pending"
    assert [photo["angle"] for photo in status.json()["photos"]] == [
        "front",
        "angle45",
        "side",
    ]


def test_other_user_cannot_read_or_delete_enrollment(enrollment_client, auth):
    enrollment_id = create_enrollment(enrollment_client, auth)
    other = auth(sub="other-user")

    assert enrollment_client.get(
        f"/v1/facemarket/enrollments/{enrollment_id}", headers=other
    ).status_code == 404
    assert enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=other
    ).status_code == 404


def test_other_user_upload_is_removed_and_returns_same_not_found(
    enrollment_client, auth, fake_r2, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(sub="other-user"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert fake_r2.objects == {}
    assert "quarantine" not in response.text


def test_delete_photo_removes_private_object_before_metadata(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    uploaded = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    assert uploaded.status_code == 201

    response = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert response.status_code == 204
    assert fake_r2.deletes == [
        f"facemarket/enrollments/{enrollment_id}/quarantine/front.jpg"
    ]
    assert enrollment_store.photos == []


def test_delete_photo_r2_failure_leaves_metadata_for_retry(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    key = enrollment_store.photos[0]["r2_key"]
    fake_r2.fail_delete_for.add(key)

    response = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert response.status_code == 503
    assert [photo["angle"] for photo in enrollment_store.photos] == ["front"]


def test_upload_replacement_with_new_extension_deletes_old_object_after_commit(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    for filename, mime in (("front.jpg", "image/jpeg"), ("front.png", "image/png")):
        response = enrollment_client.post(
            f"/v1/facemarket/enrollments/{enrollment_id}/photos",
            data={"angle": "front"},
            files={"photo": (filename, b"image", mime)},
            headers=auth(),
        )
        assert response.status_code == 201, response.text

    old_key = f"facemarket/enrollments/{enrollment_id}/quarantine/front.jpg"
    new_key = f"facemarket/enrollments/{enrollment_id}/quarantine/front.png"
    assert fake_r2.deletes == [old_key]
    assert enrollment_store.photos[0]["r2_key"] == new_key


def test_upload_database_failure_removes_new_quarantine_object(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_store.fail_photo_upsert = True

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    key = f"facemarket/enrollments/{enrollment_id}/quarantine/front.jpg"
    assert response.status_code == 503
    assert key not in response.text
    assert "digest" not in response.text.lower()
    assert fake_r2.deletes == [key]
    assert key not in fake_r2.objects


def test_cancel_is_idempotent_and_cleans_quarantine_photos(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )
    second = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    assert enrollment_store.photos == []
    evidence = enrollment_store.enrollments[0]["raw_deletion_evidence"]
    assert evidence["quarantineDeleted"] is True
    assert evidence["quarantineDeletedCount"] == 1
    assert "facemarket/" not in json.dumps(evidence)
