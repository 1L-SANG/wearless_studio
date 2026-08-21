import asyncio
import base64
import contextlib
import copy
import hashlib
import hmac
import io
import json
import threading
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from botocore.exceptions import EndpointConnectionError
from psycopg.errors import UniqueViolation
from starlette.datastructures import Headers

from app import cx_identity, facemarket_enrollment, r2
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
PHOTO_FENCE_NAMESPACE = 0x464D5048
TEST_ENROLLMENT_ID = "123e4567-e89b-12d3-a456-426614174000"
TEST_MODEL_ID = "987fcdeb-51a2-43d7-9abc-def012345678"


def _json_value(value):
    return getattr(value, "obj", value)


class EnrollmentStore:
    def __init__(self):
        self.enrollments = []
        self.photos = []
        self.models = []
        self.licenses = []
        self.cleanup = []
        self.identities = []
        self.jobs = []
        self.advisory_lock_owners = {}
        self.terminal_cleanup_loads = 0
        self.now = NOW
        self.fail_photo_upsert = False
        self.fail_raw_release_evidence = False
        self.commit_attempts = 0
        self.fail_commit_attempts = set()
        self.unlock_started = None
        self.allow_unlock = None
        self.fail_unlock = False

    def serialized(self):
        return json.dumps(
            {
                "enrollments": self.enrollments,
                "photos": self.photos,
                "models": self.models,
                "licenses": self.licenses,
                "cleanup": self.cleanup,
                "identities": self.identities,
                "jobs": self.jobs,
            },
            default=str,
        )


class FakeRekognition:
    def __init__(self):
        self.session_id = "00000000-0000-0000-0000-000000000001"
        self.result = {
            "Status": "SUCCEEDED",
            "Confidence": 95.0,
            "ReferenceImage": {"Bytes": b"live-reference"},
        }
        self.calls = []
        self.result_calls = []
        self.failures = 0

    def create_face_liveness_session(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise EndpointConnectionError(endpoint_url="https://rekognition.test")
        return {"SessionId": self.session_id}

    def get_face_liveness_session_results(self, **kwargs):
        self.result_calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise EndpointConnectionError(endpoint_url="https://rekognition.test")
        return self.result


class FakeSts:
    def __init__(self):
        self.calls = []
        self.failures = 0

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise EndpointConnectionError(endpoint_url="https://sts.test")
        return {
            "Credentials": {
                "AccessKeyId": "temporary-access-key",
                "SecretAccessKey": "temporary-secret-key",
                "SessionToken": "temporary-session-token",
                "Expiration": NOW + timedelta(minutes=15),
            },
            "AssumedRoleUser": {
                "AssumedRoleId": "AROATEST:fm-live-123e4567e89b",
                "Arn": "arn:aws:sts::123456789012:assumed-role/test/fm-live-123e4567e89b",
            },
        }

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.store = conn.working
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

        if query.startswith("select pg_try_advisory_lock"):
            lock_key = tuple(params)
            owner = self.conn.store.advisory_lock_owners.get(lock_key)
            locked = owner is None or owner is self.conn
            if locked:
                self.conn.store.advisory_lock_owners[lock_key] = self.conn
                self.conn.advisory_locks.add(lock_key)
            else:
                self.conn.pool.failed_try_locks += 1
            self.result = {"locked": locked}
        elif query.startswith("select pg_advisory_unlock"):
            if self.conn.store.unlock_started is not None:
                self.conn.store.unlock_started.set()
                await self.conn.store.allow_unlock.wait()
            if self.conn.store.fail_unlock:
                raise RuntimeError("unlock unavailable")
            lock_key = tuple(params)
            unlocked = self.conn.release_advisory_lock(lock_key)
            self.result = {"unlocked": unlocked}
        elif query.startswith("select count(*) filter"):
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
        elif query.startswith("select e.id::text as id, e.user_id::text as user_id"):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id and item["user_id"] == user_id
                ),
                None,
            )
            self.result = (
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "model_id": row["model_id"],
                    "status": row["status"],
                    "cooldown_until": row.get("cooldown_until"),
                    "expires_at": row["expires_at"],
                    "liveness_session_digest": row.get("liveness_session_digest"),
                    "device_digest": row["device_digest"],
                }
                if row
                else None
            )
        elif query.startswith("select e.status, e.cooldown_until"):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id and item["user_id"] == user_id
                ),
                None,
            )
            self.result = (
                {
                    "status": row["status"],
                    "cooldown_until": row.get("cooldown_until"),
                    "liveness_nonce_digest": row.get("liveness_nonce_digest"),
                    "liveness_session_digest": row.get("liveness_session_digest"),
                }
                if row
                else None
            )
        elif query.startswith("select exists(") and "fm_identity_verifications" in query:
            token_digest = params[0]
            self.result = {
                "replayed": any(
                    row.get("cx_tx_id") == token_digest for row in self.store.identities
                )
                or any(
                    row.get("oacx_tx_digest") == token_digest
                    for row in self.store.enrollments
                )
            }
        elif query.startswith("select exists(") and "liveness_nonce_digest" in query:
            nonce_digest = params[0]
            self.result = {
                "replayed": any(
                    row.get("liveness_nonce_digest") == nonce_digest
                    for row in self.store.enrollments
                )
            }
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
                    "id": str(
                        uuid.UUID(
                            int=uuid.UUID(TEST_ENROLLMENT_ID).int
                            + len(self.store.enrollments)
                        )
                    ),
                    "user_id": user_id,
                    "model_id": model_id,
                    "device_digest": device_digest,
                    "consent_version": consent_version,
                    "status": "photos_pending",
                    "decision": None,
                    "reason": None,
                    "provider_versions": {},
                    "cooldown_until": None,
                    "expires_at": expires_at,
                    "completed_at": None,
                    "raw_deletion_evidence": {},
                }
                self.store.enrollments.append(row)
                self.result = {"id": row["id"]}
        elif query.startswith("with due as ( select id from fm_biometric_enrollments"):
            limit = params[0]
            due = sorted(
                [
                    row
                    for row in self.store.enrollments
                    if row["expires_at"] <= self.store.now
                    and row["status"]
                    in {"photos_pending", "liveness_pending", "processing"}
                ],
                key=lambda row: row["expires_at"],
            )[:limit]
            self.many = [{"id": row["id"]} for row in due]
            for row in due:
                row.update(
                    status="expired",
                    decision="failed",
                    reason="enrollment_expired",
                    completed_at=self.store.now,
                )
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
        elif (
            query.startswith("select e.id::text as id from fm_biometric_enrollments e")
            and "raw_deletion_evidence" in query
        ):
            limit = params[0]
            candidates = []
            for row in self.store.enrollments:
                if row["status"] not in {"failed", "cancelled", "expired"}:
                    continue
                if row["raw_deletion_evidence"].get("quarantineDeleted") is True:
                    continue
                has_photo = any(
                    photo["enrollment_id"] == row["id"]
                    and photo["storage_state"] in {"quarantine", "delete_pending"}
                    for photo in self.store.photos
                )
                has_cleanup = any(
                    cleanup["enrollment_id"] == row["id"]
                    for cleanup in self.store.cleanup
                )
                if has_photo or has_cleanup:
                    candidates.append(row)
            candidates.sort(
                key=lambda row: (
                    row.get("completed_at") is not None,
                    row.get("completed_at") or self.store.now,
                )
            )
            self.many = [{"id": row["id"]} for row in candidates[:limit]]
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
                and photo["storage_state"] == "quarantine"
            ]
        elif query.startswith("select angle, r2_key, mime_type"):
            enrollment_id = params[0]
            order = {"front": 0, "angle45": 1, "side": 2}
            self.many = sorted(
                [
                    {
                        "angle": photo["angle"],
                        "r2_key": photo["r2_key"],
                        "mime_type": photo["mime_type"],
                    }
                    for photo in self.store.photos
                    if photo["enrollment_id"] == enrollment_id
                    and photo["qc_status"] == "passed"
                    and photo["storage_state"] == "quarantine"
                    and photo["angle"] in order
                ],
                key=lambda row: order[row["angle"]],
            )
        elif query.startswith("select r2_key, storage_state"):
            enrollment_id, angle = params
            photo = next(
                (
                    item
                    for item in self.store.photos
                    if item["enrollment_id"] == enrollment_id and item["angle"] == angle
                ),
                None,
            )
            self.result = (
                {
                    "r2_key": photo["r2_key"],
                    "storage_state": photo["storage_state"],
                }
                if photo
                else None
            )
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
        elif query.startswith("insert into fm_biometric_enrollment_photo_cleanup"):
            enrollment_id, angle, key, *lease = params
            cleanup_key = (enrollment_id, key)
            row = {
                "enrollment_id": enrollment_id,
                "angle": angle,
                "r2_key": key,
                "reason": (
                    "superseded"
                    if "'superseded'" in query
                    else "delete"
                    if "'delete'" in query
                    else "upload_orphan"
                ),
                "created_at": self.store.now,
                "not_before": self.store.now + lease[0] if lease else self.store.now,
            }
            self.conn.cleanup_adds[cleanup_key] = row
            self.conn.cleanup_deletes.discard(cleanup_key)
            self.store.cleanup[:] = [
                item
                for item in self.store.cleanup
                if (item["enrollment_id"], item["r2_key"]) != cleanup_key
            ]
            self.store.cleanup.append(copy.deepcopy(row))
        elif query.startswith("select c.angle, c.r2_key, c.reason"):
            enrollment_id = params[0]
            index = 1
            angle = params[index] if "c.angle = %s" in query else None
            index += int(angle is not None)
            key = params[index] if "c.r2_key = %s" in query else None
            index += int(key is not None)
            reason = params[index] if "c.reason = %s" in query else None
            self.many = []
            for cleanup in self.store.cleanup:
                if cleanup["enrollment_id"] != enrollment_id:
                    continue
                if angle is not None and cleanup["angle"] != angle:
                    continue
                if key is not None and cleanup["r2_key"] != key:
                    continue
                if reason is not None and cleanup["reason"] != reason:
                    continue
                if (
                    "c.not_before <= now()" in query
                    and cleanup["not_before"] > self.store.now
                ):
                    continue
                photo = next(
                    (
                        item
                        for item in self.store.photos
                        if item["enrollment_id"] == enrollment_id
                        and item["angle"] == cleanup["angle"]
                        and item["r2_key"] == cleanup["r2_key"]
                    ),
                    None,
                )
                self.many.append(
                    {
                        "angle": cleanup["angle"],
                        "r2_key": cleanup["r2_key"],
                        "reason": cleanup["reason"],
                        "current_state": photo["storage_state"] if photo else None,
                    }
                )
        elif query.startswith("delete from fm_biometric_enrollment_photo_cleanup"):
            enrollment_id, key = params
            cleanup_key = (enrollment_id, key)
            self.conn.cleanup_deletes.add(cleanup_key)
            self.conn.cleanup_adds.pop(cleanup_key, None)
            self.store.cleanup[:] = [
                item
                for item in self.store.cleanup
                if (item["enrollment_id"], item["r2_key"]) != cleanup_key
            ]
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
        elif query.startswith("update fm_biometric_enrollment_photos"):
            enrollment_id, angle, key = params
            photo = next(
                item
                for item in self.store.photos
                if item["enrollment_id"] == enrollment_id
                and item["angle"] == angle
                and item["r2_key"] == key
            )
            photo["storage_state"] = "delete_pending"
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
                    and photo["storage_state"] == "quarantine"
                    for photo in self.store.photos
                )
            }
        elif query.startswith(
            "update fm_biometric_enrollments set liveness_nonce_digest"
        ):
            nonce_digest, enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            row["liveness_nonce_digest"] = nonce_digest
        elif query.startswith(
            "update fm_biometric_enrollments set liveness_session_digest"
        ):
            session_digest, enrollment_id, user_id, nonce_digest = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id
                and item["user_id"] == user_id
                and item["liveness_nonce_digest"] == nonce_digest
            )
            row["liveness_session_digest"] = session_digest
            row.setdefault("provider_versions", {})["faceLiveness"] = (
                "aws-rekognition-us-east-1"
            )
        elif query.startswith("update fm_biometric_enrollments set status = 'processing'"):
            enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            if row["status"] == "liveness_pending":
                row["status"] = "processing"
        elif query.startswith("select count(*) as recent_failures"):
            enrollment_id = params[0]
            current = next(
                item for item in self.store.enrollments if item["id"] == enrollment_id
            )
            self.result = {
                "recent_failures": sum(
                    row["status"] == "failed"
                    and row.get("completed_at")
                    and row["completed_at"] >= NOW - timedelta(minutes=3)
                    and row.get("reason") in facemarket_enrollment.TERMINAL_REASONS
                    and (
                        row["user_id"] == current["user_id"]
                        or row["device_digest"] == current["device_digest"]
                    )
                    for row in self.store.enrollments
                )
            }
        elif query.startswith(
            "update fm_biometric_enrollments set status = 'failed'"
        ):
            reason, cooldown_until, enrollment_id = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            row.update(
                status="failed",
                decision="failed",
                reason=reason,
                completed_at=row.get("completed_at") or NOW,
                cooldown_until=cooldown_until or row.get("cooldown_until"),
            )
        elif query.startswith("update fm_biometric_enrollments set status = 'expired'"):
            enrollment_id = params[0]
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            row.update(
                status="expired",
                decision="failed",
                reason="enrollment_expired",
                completed_at=row.get("completed_at") or NOW,
            )
        elif query.startswith("update fm_biometric_enrollments set status = 'liveness_pending'"):
            enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            if row["status"] == "photos_pending":
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
            self.conn.store.terminal_cleanup_loads += 1
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
                    and photo["storage_state"] in {"quarantine", "delete_pending"}
                ]
                self.many = (
                    [
                        {
                            "status": enrollment["status"],
                            "angle": photo["angle"],
                            "r2_key": photo["r2_key"],
                            "storage_state": photo["storage_state"],
                        }
                        for photo in photos
                    ]
                    or [{"status": enrollment["status"], "angle": None, "r2_key": None}]
                )
        elif "as remaining" in query and query.startswith("select"):
            enrollment_id = params[0]
            self.result = {
                "remaining": sum(
                    photo["enrollment_id"] == enrollment_id
                    and photo["storage_state"] in {"quarantine", "delete_pending"}
                    for photo in self.store.photos
                )
                + sum(
                    row["enrollment_id"] == enrollment_id
                    for row in self.store.cleanup
                )
            }
        elif (
            query.startswith("update fm_biometric_enrollments set raw_deletion_evidence")
            and "oacxportraitreleased" in query
        ):
            if self.store.fail_raw_release_evidence:
                raise RuntimeError("release evidence unavailable")
            portrait, liveness, embeddings, enrollment_id = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            row["raw_deletion_evidence"].update(
                oacxPortraitReleased=portrait,
                livenessReferenceReleased=liveness,
                temporaryEmbeddingsReleased=embeddings,
            )
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
        elif query.startswith("select id::text as id, user_id::text as user_id from fm_models"):
            ci_hash = params[0]
            model = next(
                (row for row in self.store.models if row.get("ci_hash") == ci_hash),
                None,
            )
            self.result = (
                {"id": model["id"], "user_id": model["user_id"]} if model else None
            )
        elif query.startswith("update fm_models set ci_hash"):
            ci_hash, display_name, user_id, model_id = params
            model = next(row for row in self.store.models if row["id"] == model_id)
            model.update(ci_hash=ci_hash, display_name=display_name, user_id=user_id)
        elif query.startswith("insert into fm_models"):
            user_id, display_name, ci_hash = params
            model = {
                "id": f"model-{len(self.store.models) + 1}",
                "user_id": user_id,
                "display_name": display_name,
                "status": "pending",
                "ci_hash": ci_hash,
                "assets_status": "none",
                "current_enrollment_id": None,
            }
            self.store.models.append(model)
            self.result = {"id": model["id"]}
        elif query.startswith("insert into fm_identity_verifications"):
            model_id, token_digest, fields = params
            if any(row.get("cx_tx_id") == token_digest for row in self.store.identities):
                raise UniqueViolation("duplicate cx_tx_id")
            self.store.identities.append(
                {
                    "model_id": model_id,
                    "cx_tx_id": token_digest,
                    "fields": _json_value(fields),
                }
            )
        elif query.startswith("update fm_models set assets_status = 'building'"):
            enrollment_id, model_id = params
            model = next(row for row in self.store.models if row["id"] == model_id)
            model.update(assets_status="building", current_enrollment_id=enrollment_id)
        elif query.startswith("update fm_biometric_enrollments set model_id"):
            model_id, token_digest, policy_version, provider_versions, enrollment_id = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            row.update(
                model_id=model_id,
                status="asset_building",
                decision="passed",
                reason=None,
                completed_at=NOW,
                oacx_tx_digest=token_digest,
                match_policy_version=policy_version,
            )
            row.setdefault("provider_versions", {}).update(_json_value(provider_versions))
        elif query.startswith("insert into jobs"):
            _user_id, payload = params
            self.store.jobs.append(
                {"kind": "fm_model_asset_build", "payload": _json_value(payload)}
            )
        else:
            raise AssertionError(f"unexpected SQL: {query}")

    async def fetchone(self):
        return self.result

    async def fetchall(self):
        return self.many


class FakeConn:
    def __init__(self, pool):
        self.pool = pool
        self.store = pool.store
        self.working = self._snapshot()
        self.cleanup_adds = {}
        self.cleanup_deletes = set()
        self.advisory_locks = set()
        self.closed = False

    def _snapshot(self):
        working = EnrollmentStore()
        for name in (
            "enrollments",
            "photos",
            "models",
            "licenses",
            "cleanup",
            "identities",
            "jobs",
        ):
            setattr(working, name, copy.deepcopy(getattr(self.store, name)))
        working.now = self.store.now
        working.fail_photo_upsert = self.store.fail_photo_upsert
        working.fail_raw_release_evidence = self.store.fail_raw_release_evidence
        return working

    def cursor(self):
        if self.closed:
            raise RuntimeError("connection closed")
        return FakeCursor(self)

    async def commit(self):
        if self.closed:
            raise RuntimeError("connection closed")
        self.store.commit_attempts += 1
        if self.store.commit_attempts in self.store.fail_commit_attempts:
            self.working = self._snapshot()
            self.cleanup_adds.clear()
            self.cleanup_deletes.clear()
            raise RuntimeError("commit unavailable")
        for name in ("enrollments", "photos", "models", "licenses", "identities", "jobs"):
            target = getattr(self.store, name)
            target[:] = copy.deepcopy(getattr(self.working, name))
        cleanup = {
            (row["enrollment_id"], row["r2_key"]): copy.deepcopy(row)
            for row in self.store.cleanup
        }
        cleanup.update(copy.deepcopy(self.cleanup_adds))
        for cleanup_key in self.cleanup_deletes:
            cleanup.pop(cleanup_key, None)
        self.store.cleanup[:] = list(cleanup.values())
        self.working = self._snapshot()
        self.cleanup_adds.clear()
        self.cleanup_deletes.clear()

    async def rollback(self):
        if self.closed:
            raise RuntimeError("connection closed")
        self.working = self._snapshot()
        self.cleanup_adds.clear()
        self.cleanup_deletes.clear()

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


class FakePool:
    def __init__(self, store):
        self.store = store
        self.fail_on_nested = False
        self.nested_attempts = 0
        self.max_checkout_depth = 0
        self.active_checkouts = 0
        self.max_active_checkouts = 0
        self.failed_try_locks = 0
        self._checkout_depths = {}

    def connection(self):
        @contextlib.asynccontextmanager
        async def connection():
            task = asyncio.current_task()
            depth = self._checkout_depths.get(task, 0)
            if depth:
                self.nested_attempts += 1
                if self.fail_on_nested:
                    raise RuntimeError("nested pool checkout")
            self._checkout_depths[task] = depth + 1
            self.max_checkout_depth = max(self.max_checkout_depth, depth + 1)
            self.active_checkouts += 1
            self.max_active_checkouts = max(
                self.max_active_checkouts, self.active_checkouts
            )
            conn = FakeConn(self)
            try:
                yield conn
            except Exception:
                await conn.rollback()
                raise
            finally:
                self.active_checkouts -= 1
                if depth:
                    self._checkout_depths[task] = depth
                else:
                    self._checkout_depths.pop(task, None)

        return connection()


class FakeR2:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.deletes = []
        self.fail_delete_for = set()
        self.fail_next_delete = False
        self.not_found_for = set()
        self.retain_on_delete_for = set()
        self.heads = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))
        self.objects[key] = (data, mime)

    def get_bytes(self, key):
        data, _mime = self.objects[key]
        return data

    def delete(self, key):
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("r2 unavailable")
        if key in self.fail_delete_for:
            raise RuntimeError("r2 unavailable")
        if key in self.not_found_for:
            error = RuntimeError("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        self.deletes.append(key)
        if key not in self.retain_on_delete_for:
            self.objects.pop(key, None)

    def head(self, key):
        self.heads.append(key)
        value = self.objects.get(key)
        if value is None:
            return None
        data, mime = value
        return {"size": len(data), "mime": mime}


def _enrollment_db_view(row):
    return {
        "id": row["id"],
        "model_id": row["model_id"],
        "status": row["status"],
        "decision": row["decision"],
        "reason": row["reason"],
        "cooldown_until": row["cooldown_until"],
        "expires_at": row["expires_at"],
        "liveness_session_digest": row.get("liveness_session_digest"),
    }


@pytest.fixture()
def enrollment_store():
    return EnrollmentStore()


@pytest.fixture()
def fake_r2():
    return FakeR2()


@pytest.fixture()
def fake_pool(enrollment_store):
    return FakePool(enrollment_store)


@pytest.fixture()
def fake_rekognition():
    return FakeRekognition()


@pytest.fixture()
def fake_sts():
    return FakeSts()


@pytest.fixture()
def enrollment_client(
    keypair,
    monkeypatch,
    enrollment_store,
    fake_r2,
    fake_pool,
    fake_rekognition,
    fake_sts,
):
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
        fm_ci_pepper="pep",
        fm_face_qc_enabled=True,
    )
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (fake_rekognition, fake_sts),
    )

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        async with fake_pool.connection() as conn:
            yield conn

    monkeypatch.setattr(facemarket_enrollment, "get_conn", fake_get_conn, raising=False)
    app = create_app(settings)
    app.state.jwt_key_resolver = lambda _token: public_key
    app.state.r2_face = fake_r2
    app.state.pool = fake_pool
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


def create_ready_enrollment(client, auth, store):
    enrollment_id = create_enrollment(client, auth)
    store.enrollments[0]["status"] = "liveness_pending"
    store.photos.extend(
        {
            "enrollment_id": enrollment_id,
            "angle": angle,
            "r2_key": f"private/{angle}.jpg",
            "image_digest": f"sha256-{angle}",
            "mime_type": "image/jpeg",
            "byte_size": 10,
            "qc_status": "passed",
            "storage_state": "quarantine",
            "uploaded_at": NOW,
        }
        for angle in ("front", "angle45", "side")
    )
    return enrollment_id


def create_complete_ready_enrollment(client, auth, store, fake_r2, fake_rekognition):
    enrollment_id = create_ready_enrollment(client, auth, store)
    store.enrollments[0]["liveness_session_digest"] = hashlib.sha256(
        fake_rekognition.session_id.encode()
    ).hexdigest()
    for angle in ("front", "angle45", "side"):
        fake_r2.objects[f"private/{angle}.jpg"] = (f"{angle}-bytes".encode(), "image/jpeg")
    return enrollment_id


def dev_trans(**patch):
    trans = {
        "ci": "dev-ci-value",
        "birth": "19900102",
        "nm": "홍길동",
        "txId": "tx-dev-1",
        "idPortraitBase64": base64.b64encode(b"portrait-bytes").decode(),
        "idPortraitMime": "image/jpeg",
        "issuedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    trans.update(patch)
    return trans


class RecordingFaceQc:
    def __init__(self, scores=(0.46, 0.41, 0.42, 0.43), fail=False):
        self.scores = list(scores)
        self.fail = fail
        self.calls = []

    def one_to_one_similarity(self, reference, candidate):
        labels = {
            b"portrait-bytes": "id",
            b"front-bytes": "front",
            b"angle45-bytes": "angle45",
            b"side-bytes": "side",
            b"live-reference": "live",
        }
        self.calls.append((labels.get(bytes(reference)), labels.get(bytes(candidate))))
        if self.fail:
            raise RuntimeError("qc unavailable")
        return self.scores.pop(0)


@pytest.fixture()
def completion_fakes(monkeypatch):
    state = {
        "trans": dev_trans(),
        "face_qc": RecordingFaceQc(),
    }

    async def fake_fetch_trans(_base_url, token):
        if token == "timeout-token":
            raise cx_identity.CxIdentityError("cx timeout")
        return copy.deepcopy(state["trans"])

    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch_trans)
    monkeypatch.setattr(
        facemarket_enrollment,
        "load_face_qc",
        lambda _settings, *, required=False: state["face_qc"],
    )
    return state


def complete_enrollment(client, auth, enrollment_id, session_id, token="oacx-token-used-only-now"):
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json={"sessionId": session_id, "token": token},
        headers=auth(),
    )


def assert_completion_failure(response, store, reason, *, retryable):
    assert response.status_code == 200, response.text
    assert response.json() == {
        "passed": False,
        "retryable": retryable,
        "reason": reason,
        "status": "failed",
    }
    row = store.enrollments[0]
    assert row["status"] == "failed"
    assert row["decision"] == "failed"
    assert row["reason"] == reason
    assert store.jobs == []
    assert all(photo["storage_state"] != "quarantine" for photo in store.photos)
    assert "score" not in response.text.lower()
    assert "private/" not in response.text


def test_complete_uses_distinct_thresholds_and_queues_bound_asset_job(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "passed": True,
        "retryable": False,
        "reason": None,
        "status": "asset_building",
        "modelId": "model-1",
    }
    assert completion_fakes["face_qc"].calls == [
        ("id", "live"),
        ("front", "live"),
        ("angle45", "live"),
        ("side", "live"),
    ]
    assert enrollment_store.jobs == [{
        "kind": "fm_model_asset_build",
        "payload": {"modelId": "model-1", "enrollmentId": enrollment_id},
    }]
    stored = enrollment_store.enrollments[0]
    assert stored["status"] == "asset_building"
    assert stored["decision"] == "passed"
    assert stored["model_id"] == "model-1"
    assert stored["provider_versions"] == {
        "faceLiveness": "aws-rekognition-face-liveness",
        "oacx": "dev-mock-v1",
        "faceMatch": "sface-one-to-one",
    }
    assert stored["raw_deletion_evidence"] == {
        "oacxPortraitReleased": True,
        "livenessReferenceReleased": True,
        "temporaryEmbeddingsReleased": True,
    }
    serialized = enrollment_store.serialized()
    for secret in (
        "oacx-token-used-only-now",
        "portrait-bytes",
        "live-reference",
        fake_rekognition.session_id,
        "0.46",
    ):
        assert secret not in serialized
    assert "private/" not in response.text
    assert "private/" not in json.dumps(enrollment_store.jobs)


def test_complete_expires_liveness_pending_without_provider_oacx_or_job(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    enrollment_store.enrollments[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "passed": False,
        "retryable": False,
        "reason": "enrollment_expired",
        "status": "expired",
    }
    row = enrollment_store.enrollments[0]
    assert row["status"] == "expired"
    assert row["decision"] == "failed"
    assert row["reason"] == "enrollment_expired"
    assert fake_rekognition.result_calls == []
    assert completion_fakes["face_qc"].calls == []
    assert enrollment_store.jobs == []
    assert all(photo["storage_state"] != "quarantine" for photo in enrollment_store.photos)
    assert enrollment_store.terminal_cleanup_loads >= 1


def test_complete_surfaces_raw_release_evidence_write_failure(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    enrollment_store.fail_raw_release_evidence = True

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "enrollment_unavailable"
    assert enrollment_store.enrollments[0]["raw_deletion_evidence"] == {}
    assert "oacx-token-used-only-now" not in response.text
    assert fake_rekognition.session_id not in response.text


def test_complete_waits_for_raw_release_evidence_before_cancellation_exits(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    monkeypatch,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    release_started = asyncio.Event()
    release_can_finish = asyncio.Event()
    released = asyncio.Event()

    async def slow_release(_request, _enrollment_id, **_evidence):
        release_started.set()
        await release_can_finish.wait()
        released.set()

    monkeypatch.setattr(facemarket_enrollment, "record_raw_release_evidence", slow_release)

    async def run_and_cancel():
        task = asyncio.create_task(
            facemarket_enrollment.process_enrollment_completion(
                types.SimpleNamespace(app=enrollment_client.app),
                enrollment_id=enrollment_id,
                user_id="user-1",
                session_id=fake_rekognition.session_id,
                token="oacx-token-used-only-now",
            )
        )
        await release_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_can_finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert released.is_set()

    asyncio.run(run_and_cancel())


def test_complete_hmacs_owned_ci_buffer_without_immutable_copy(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    monkeypatch,
):
    seen_message_types = []
    original_hmac_new = hmac.new

    def track_hmac_new(key, msg=None, digestmod=""):
        seen_message_types.append(type(msg))
        return original_hmac_new(key, msg, digestmod)

    monkeypatch.setattr(facemarket_enrollment.hmac, "new", track_hmac_new)
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert response.status_code == 202, response.text
    assert bytearray in seen_message_types


def test_complete_rejects_session_digest_mismatch_without_provider_call(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )

    response = complete_enrollment(
        enrollment_client,
        auth,
        enrollment_id,
        "00000000-0000-0000-0000-000000000099",
    )

    assert_completion_failure(
        response, enrollment_store, "liveness_retry", retryable=True
    )
    assert fake_rekognition.result_calls == []
    assert completion_fakes["face_qc"].calls == []
    assert enrollment_store.enrollments[0]["cooldown_until"] is None


def test_complete_rejects_same_oacx_token_replay(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    token = "already-used-oacx-token"
    enrollment_store.identities.append(
        {"cx_tx_id": f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"}
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id, token
    )

    assert_completion_failure(
        response, enrollment_store, "identity_replay", retryable=False
    )


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"idPortraitBase64": None}, "id_portrait_unavailable"),
        ({"birth": "20100102"}, "minor_blocked"),
    ],
)
def test_complete_maps_oacx_portrait_and_minor_failures(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    patch,
    reason,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    completion_fakes["trans"] = dev_trans(**patch)

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert_completion_failure(
        response,
        enrollment_store,
        reason,
        retryable=reason == "id_portrait_unavailable",
    )
    assert enrollment_store.enrollments[0]["cooldown_until"] is None


def test_complete_maps_expired_oacx_contract_before_minor_birth(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    completion_fakes["trans"] = dev_trans(
        birth="20100102",
        issuedAt="2026-08-21T02:54:59Z",
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert_completion_failure(
        response, enrollment_store, "id_portrait_unavailable", retryable=True
    )


@pytest.mark.parametrize(
    "scores,calls",
    [
        ((None, 0.9, 0.9, 0.9), [("id", "live")]),
        ((0.4499, 0.9, 0.9, 0.9), [("id", "live")]),
        ((0.9, 0.3999, 0.9, 0.9), [("id", "live"), ("front", "live")]),
        (
            (0.9, 0.9, 0.3999, 0.9),
            [("id", "live"), ("front", "live"), ("angle45", "live")],
        ),
        (
            (0.9, 0.9, 0.9, 0.3999),
            [("id", "live"), ("front", "live"), ("angle45", "live"), ("side", "live")],
        ),
    ],
)
def test_complete_fails_closed_on_each_distinct_threshold(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    scores,
    calls,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    completion_fakes["face_qc"] = RecordingFaceQc(scores=scores)

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert_completion_failure(
        response, enrollment_store, "face_match_failed", retryable=False
    )
    assert completion_fakes["face_qc"].calls == calls


def test_complete_requires_identity_recovery_for_ci_owned_by_another_account(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    enrollment_store.models.append(
        {
            "id": "other-model",
            "user_id": "other-user",
            "display_name": "다른 사람",
            "status": "verified",
            "ci_hash": hmac.new(
                b"pep", b"dev-ci-value", hashlib.sha256
            ).hexdigest(),
            "assets_status": "ready",
            "current_enrollment_id": None,
        }
    )

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert_completion_failure(
        response, enrollment_store, "identity_recovery_required", retryable=False
    )


@pytest.mark.parametrize(
    "setup,reason,retryable",
    [
        (lambda rekognition, fakes: setattr(rekognition, "failures", 1), "liveness_unavailable", True),
        (lambda rekognition, fakes: setattr(rekognition, "result", {"Status": "IN_PROGRESS"}), "liveness_retry", True),
        (lambda rekognition, fakes: fakes.update(face_qc=RecordingFaceQc(fail=True)), "qc_unavailable", True),
    ],
)
def test_complete_provider_and_qc_failures_are_retryable_without_cooldown(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    setup,
    reason,
    retryable,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    setup(fake_rekognition, completion_fakes)

    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )

    assert_completion_failure(response, enrollment_store, reason, retryable=retryable)
    assert enrollment_store.enrollments[0]["cooldown_until"] is None


def test_complete_terminal_biometric_failures_set_cooldown_after_five(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
):
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    completion_fakes["face_qc"] = RecordingFaceQc(scores=(0.1, 0.9, 0.9, 0.9))
    for index in range(4):
        enrollment_store.enrollments.append(
            {
                "id": f"00000000-0000-0000-0000-00000000000{index}",
                "user_id": "user-1",
                "model_id": None,
                "device_digest": enrollment_store.enrollments[0]["device_digest"],
                "consent_version": "2026-08-v1",
                "status": "failed",
                "decision": "failed",
                "reason": "face_match_failed",
                "cooldown_until": None,
                "expires_at": NOW,
                "completed_at": NOW - timedelta(minutes=1),
                "raw_deletion_evidence": {},
            }
        )

    before = datetime.now(timezone.utc) + timedelta(minutes=45)
    response = complete_enrollment(
        enrollment_client, auth, enrollment_id, fake_rekognition.session_id
    )
    after = datetime.now(timezone.utc) + timedelta(minutes=45)

    assert_completion_failure(
        response, enrollment_store, "face_match_failed", retryable=False
    )
    assert before <= enrollment_store.enrollments[0]["cooldown_until"] <= after


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
                "cooldown_until": datetime.now(timezone.utc) + timedelta(minutes=30),
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
        TEST_ENROLLMENT_ID, "angle45", "jpg"
    ) == f"facemarket/enrollments/{TEST_ENROLLMENT_ID}/quarantine/angle45.jpg"
    assert r2.enrollment_original_key(
        TEST_MODEL_ID, TEST_ENROLLMENT_ID, "front", "png"
    ) == (
        f"facemarket/models/{TEST_MODEL_ID}/enrollments/"
        f"{TEST_ENROLLMENT_ID}/originals/front.png"
    )
    assert r2.model_asset_key(
        TEST_MODEL_ID, TEST_ENROLLMENT_ID, "face_front", "webp"
    ) == (
        f"facemarket/models/{TEST_MODEL_ID}/enrollments/"
        f"{TEST_ENROLLMENT_ID}/assets/face_front.webp"
    )


def test_replacement_quarantine_keys_are_versioned():
    first = r2.enrollment_quarantine_key(
        TEST_ENROLLMENT_ID, "front", "jpg", version="upload-1"
    )
    second = r2.enrollment_quarantine_key(
        TEST_ENROLLMENT_ID, "front", "jpg", version="upload-2"
    )

    assert first == (
        f"facemarket/enrollments/{TEST_ENROLLMENT_ID}/quarantine/front/upload-1.jpg"
    )
    assert second == (
        f"facemarket/enrollments/{TEST_ENROLLMENT_ID}/quarantine/front/upload-2.jpg"
    )
    assert first != second


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
    assert fake_r2.puts[0][0].startswith(
        f"facemarket/enrollments/{enrollment_id}/quarantine/angle45/"
    )
    assert fake_r2.puts[0][0].endswith(".jpg")
    assert "quarantine" not in response.text


def test_upload_canonicalizes_uppercase_enrollment_uuid(
    enrollment_client, auth, fake_r2, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id.upper()}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 201, response.text
    assert fake_r2.puts[0][0].startswith(
        f"facemarket/enrollments/{enrollment_id}/quarantine/front/"
    )


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


@pytest.mark.parametrize(
    "status",
    [
        "processing",
        "asset_building",
        "license_pending",
        "vc_pending",
        "passed",
        "failed",
        "cancelled",
        "expired",
    ],
)
def test_photo_mutation_rejects_post_liveness_and_terminal_states(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch, status
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_store.enrollments[0]["status"] = status
    puts_before = list(fake_r2.puts)

    upload = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    delete = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert upload.status_code == delete.status_code == 409
    assert upload.json()["error"]["code"] == "invalid_enrollment_state"
    assert fake_r2.puts == puts_before
    assert fake_r2.deletes == []


def test_issued_liveness_session_blocks_photo_mutation(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_store.enrollments[0].update(
        status="liveness_pending", liveness_session_digest="sha256-session"
    )

    upload = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    delete = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert upload.status_code == delete.status_code == 409
    assert fake_r2.puts == []
    assert fake_r2.deletes == []


def test_pre_session_liveness_photo_delete_returns_to_photos_pending(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    for angle in ("front", "angle45", "side"):
        enrollment_client.post(
            f"/v1/facemarket/enrollments/{enrollment_id}/photos",
            data={"angle": angle},
            files={"photo": (f"{angle}.jpg", b"image", "image/jpeg")},
            headers=auth(),
        )
    assert enrollment_store.enrollments[0]["status"] == "liveness_pending"

    response = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert response.status_code == 204
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"


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


def test_other_user_same_angle_upload_never_touches_owner_object_or_row(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    owner = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"owner-image", "image/jpeg")},
        headers=auth(),
    )
    assert owner.status_code == 201
    owner_photo = copy.deepcopy(enrollment_store.photos[0])
    owner_object = fake_r2.objects[owner_photo["r2_key"]]
    puts_before_attack = list(fake_r2.puts)

    attack = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"attacker-image", "image/jpeg")},
        headers=auth(sub="other-user"),
    )

    assert attack.status_code == 404
    assert fake_r2.puts == puts_before_attack
    assert enrollment_store.photos == [owner_photo]
    assert fake_r2.objects == {owner_photo["r2_key"]: owner_object}


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
    assert fake_r2.deletes == [fake_r2.puts[0][0]]
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
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

    fake_r2.fail_delete_for.clear()
    retry = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )
    assert retry.status_code == 204
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


def test_delete_prepare_commit_failure_does_not_touch_r2(
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
    photo = copy.deepcopy(enrollment_store.photos[0])
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 1)

    response = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert response.status_code == 503
    assert enrollment_store.photos == [photo]
    assert photo["r2_key"] in fake_r2.objects
    assert fake_r2.deletes == []
    assert enrollment_store.cleanup == []


def test_delete_finalize_commit_failure_is_retryable_after_r2_delete(
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
    key = enrollment_store.photos[0]["r2_key"]
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 2)

    first = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert first.status_code == 503
    assert key not in fake_r2.objects
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

    retry = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )
    assert retry.status_code == 204
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


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

    old_key = fake_r2.puts[0][0]
    new_key = fake_r2.puts[1][0]
    assert old_key != new_key
    assert fake_r2.deletes == [old_key]
    assert enrollment_store.photos[0]["r2_key"] == new_key
    assert enrollment_store.cleanup == []


def test_superseded_photo_cleanup_failure_remains_referenced_until_retry(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"first", "image/jpeg")},
        headers=auth(),
    )
    first_key = enrollment_store.photos[0]["r2_key"]
    fake_r2.fail_delete_for.add(first_key)

    replacement = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.png", b"second", "image/png")},
        headers=auth(),
    )
    second_key = enrollment_store.photos[0]["r2_key"]

    assert replacement.status_code == 201
    assert second_key != first_key
    assert first_key in fake_r2.objects
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [first_key]

    fake_r2.fail_delete_for.clear()
    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.png", b"third", "image/png")},
        headers=auth(),
    )

    assert retry.status_code == 201
    assert first_key not in fake_r2.objects
    assert second_key not in fake_r2.objects
    assert enrollment_store.cleanup == []


def test_superseded_cleanup_finalize_commit_failure_retries_without_orphan(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"first", "image/jpeg")},
        headers=auth(),
    )
    first_key = enrollment_store.photos[0]["r2_key"]
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 3)

    replacement = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"second", "image/jpeg")},
        headers=auth(),
    )

    assert replacement.status_code == 201
    assert first_key not in fake_r2.objects
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [first_key]

    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"third", "image/jpeg")},
        headers=auth(),
    )
    assert retry.status_code == 201
    assert enrollment_store.cleanup == []


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

    key = fake_r2.puts[0][0]
    assert response.status_code == 503
    assert key not in response.text
    assert "digest" not in response.text.lower()
    assert fake_r2.deletes == [key]
    assert key not in fake_r2.objects


def test_same_extension_replacement_db_failure_preserves_owner_photo(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"owner-image", "image/jpeg")},
        headers=auth(),
    )
    assert first.status_code == 201
    owner_photo = copy.deepcopy(enrollment_store.photos[0])
    owner_object = fake_r2.objects[owner_photo["r2_key"]]
    enrollment_store.fail_photo_upsert = True

    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"replacement", "image/jpeg")},
        headers=auth(),
    )

    assert failed.status_code == 503
    assert enrollment_store.photos == [owner_photo]
    assert fake_r2.objects == {owner_photo["r2_key"]: owner_object}
    assert enrollment_store.cleanup == []


def test_replacement_commit_failure_rolls_back_switch_and_cleans_new_object(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"owner-image", "image/jpeg")},
        headers=auth(),
    )
    owner_photo = copy.deepcopy(enrollment_store.photos[0])
    owner_object = fake_r2.objects[owner_photo["r2_key"]]
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 2)

    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"replacement", "image/jpeg")},
        headers=auth(),
    )

    assert failed.status_code == 503
    assert enrollment_store.photos == [owner_photo]
    assert fake_r2.objects == {owner_photo["r2_key"]: owner_object}
    assert enrollment_store.cleanup == []


def test_failed_replacement_cleanup_is_tracked_and_retried_on_next_upload(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_store.fail_photo_upsert = True
    fake_r2.fail_next_delete = True

    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"orphan-candidate", "image/jpeg")},
        headers=auth(),
    )
    orphan_key = fake_r2.puts[-1][0]

    assert failed.status_code == 503
    assert orphan_key in fake_r2.objects
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [orphan_key]

    enrollment_store.fail_photo_upsert = False
    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"good-image", "image/jpeg")},
        headers=auth(),
    )

    assert retry.status_code == 201
    assert orphan_key not in fake_r2.objects
    assert enrollment_store.cleanup == []


def test_upload_replacement_and_failure_cleanup_never_nest_pool_checkouts(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    fake_pool.fail_on_nested = True
    enrollment_id = create_enrollment(enrollment_client, auth)

    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"first", "image/jpeg")},
        headers=auth(),
    )
    assert first.status_code == 201, first.text

    replacement = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"second", "image/jpeg")},
        headers=auth(),
    )
    assert replacement.status_code == 201, replacement.text

    enrollment_store.fail_photo_upsert = True
    fake_r2.fail_next_delete = True
    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"failed", "image/jpeg")},
        headers=auth(),
    )
    assert failed.status_code == 503

    enrollment_store.fail_photo_upsert = False
    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"retry", "image/jpeg")},
        headers=auth(),
    )

    assert retry.status_code == 201, retry.text
    assert fake_pool.nested_attempts == 0
    assert fake_pool.max_checkout_depth == 1
    assert fake_pool.max_active_checkouts == 1
    assert fake_pool.active_checkouts == 0


def test_equivalent_uuid_spellings_contend_on_one_photo_fence(
    enrollment_client, auth, fake_pool
):
    enrollment_id = create_enrollment(enrollment_client, auth)

    async def scenario():
        async with fake_pool.connection() as owner:
            assert await facemarket_enrollment._try_photo_fence(
                owner, enrollment_id.upper()
            )
            try:
                async with fake_pool.connection() as contender:
                    contended = not await facemarket_enrollment._try_photo_fence(
                        contender, enrollment_id
                    )
            finally:
                await facemarket_enrollment._unlock_photo_fence(
                    owner, enrollment_id.upper()
                )

        assert contended is True
        assert fake_pool.failed_try_locks == 1
        assert fake_pool.store.advisory_lock_owners == {}

    asyncio.run(scenario())


def test_cancellation_during_photo_fence_unlock_waits_for_release(
    enrollment_client, auth, fake_pool, enrollment_store
):
    enrollment_id = create_enrollment(enrollment_client, auth)

    async def scenario():
        async with fake_pool.connection() as conn:
            assert await facemarket_enrollment._try_photo_fence(conn, enrollment_id)
            enrollment_store.unlock_started = asyncio.Event()
            enrollment_store.allow_unlock = asyncio.Event()
            release = asyncio.create_task(
                facemarket_enrollment._unlock_photo_fence(conn, enrollment_id)
            )
            await enrollment_store.unlock_started.wait()
            release.cancel()
            await asyncio.sleep(0)
            cancellation_waited_for_unlock = not release.done()
            enrollment_store.allow_unlock.set()
            with pytest.raises(asyncio.CancelledError):
                await release

        assert cancellation_waited_for_unlock is True
        assert enrollment_store.advisory_lock_owners == {}

    asyncio.run(scenario())


def test_photo_fence_unlock_failure_closes_connection(
    enrollment_client, auth, fake_pool, enrollment_store
):
    enrollment_id = create_enrollment(enrollment_client, auth)

    async def scenario():
        async with fake_pool.connection() as conn:
            assert await facemarket_enrollment._try_photo_fence(conn, enrollment_id)
            enrollment_store.fail_unlock = True
            with pytest.raises(RuntimeError, match="unlock unavailable"):
                await facemarket_enrollment._unlock_photo_fence(conn, enrollment_id)

        assert conn.closed is True
        assert enrollment_store.advisory_lock_owners == {}

    asyncio.run(scenario())


def test_connection_death_mid_put_keeps_upload_orphan_until_object_is_deleted(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    put_started = threading.Event()
    allow_put = threading.Event()
    original_put = fake_r2.put_bytes

    def wait_then_put(key, data, mime, cache=None):
        put_started.set()
        if not allow_put.wait(timeout=3):
            raise RuntimeError("test barrier timed out")
        original_put(key, data, mime, cache)

    monkeypatch.setattr(fake_r2, "put_bytes", wait_then_put)

    async def scenario():
        request = facemarket_enrollment.Request(
            {"type": "http", "app": enrollment_client.app}
        )
        photo = facemarket_enrollment.UploadFile(
            io.BytesIO(b"image"),
            filename="front.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
        upload = asyncio.create_task(
            facemarket_enrollment.upload_enrollment_photo(
                request, enrollment_id, "front", photo, "user-1"
            )
        )
        assert await asyncio.to_thread(put_started.wait, 3)
        new_key = enrollment_store.cleanup[0]["r2_key"]
        owner = enrollment_store.advisory_lock_owners[
            (PHOTO_FENCE_NAMESPACE, enrollment_id)
        ]
        await owner.close()

        absent_result = await facemarket_enrollment._drain_photo_cleanup(
            enrollment_client.app,
            enrollment_id=enrollment_id,
            key=new_key,
        )
        intent_survived_absence = [
            row["r2_key"] for row in enrollment_store.cleanup
        ] == [new_key]
        deletes_while_absent = list(fake_r2.deletes)

        allow_put.set()
        with pytest.raises(facemarket_enrollment.HTTPException) as exc_info:
            await upload

        assert absent_result == (0, 0)
        assert intent_survived_absence is True
        assert deletes_while_absent == []
        assert exc_info.value.status_code == 503
        assert new_key not in fake_r2.objects
        assert enrollment_store.cleanup == []
        assert fake_pool.max_checkout_depth == 1

    asyncio.run(scenario())


def test_upload_orphan_cleanup_requires_strict_post_delete_absence(
    enrollment_client, auth, fake_r2, enrollment_store
):
    enrollment_id = create_enrollment(enrollment_client, auth)
    key = f"facemarket/enrollments/{enrollment_id}/quarantine/front/orphan.jpg"
    fake_r2.objects[key] = (b"orphan", "image/jpeg")
    fake_r2.retain_on_delete_for.add(key)
    enrollment_store.cleanup.append(
        {
            "enrollment_id": enrollment_id,
            "angle": "front",
            "r2_key": key,
            "reason": "upload_orphan",
            "created_at": enrollment_store.now,
            "not_before": enrollment_store.now,
        }
    )

    result = asyncio.run(
        facemarket_enrollment._drain_photo_cleanup(
            enrollment_client.app,
            enrollment_id=enrollment_id,
            key=key,
        )
    )

    assert result == (0, 1)
    assert key in fake_r2.objects
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]
    assert fake_r2.heads == [key, key]


def test_upload_fence_blocks_due_orphan_cleanup_beyond_old_lease(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    object_stored = threading.Event()
    allow_metadata = threading.Event()
    original_put = fake_r2.put_bytes

    def put_then_wait(key, data, mime, cache=None):
        original_put(key, data, mime, cache)
        object_stored.set()
        if not allow_metadata.wait(timeout=3):
            raise RuntimeError("test barrier timed out")

    monkeypatch.setattr(fake_r2, "put_bytes", put_then_wait)

    async def scenario():
        request = facemarket_enrollment.Request(
            {"type": "http", "app": enrollment_client.app}
        )
        photo = facemarket_enrollment.UploadFile(
            io.BytesIO(b"image"),
            filename="front.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
        upload = asyncio.create_task(
            facemarket_enrollment.upload_enrollment_photo(
                request, enrollment_id, "front", photo, "user-1"
            )
        )
        assert await asyncio.to_thread(object_stored.wait, 3)
        new_key = fake_r2.puts[-1][0]
        enrollment_store.now += timedelta(minutes=5, seconds=1)

        drained = asyncio.create_task(
            facemarket_enrollment._drain_photo_cleanup(
                enrollment_client.app,
                enrollment_id=enrollment_id,
                key=new_key,
            )
        )
        assert await drained == (0, 0)
        assert new_key in fake_r2.objects
        assert [row["r2_key"] for row in enrollment_store.cleanup] == [new_key]
        assert new_key not in fake_r2.deletes
        assert fake_pool.failed_try_locks == 1
        assert fake_pool.max_checkout_depth == 1

        allow_metadata.set()
        result = await upload
        assert result.angle == "front"
        assert enrollment_store.photos[0]["r2_key"] == new_key
        assert new_key in fake_r2.objects
        assert enrollment_store.cleanup == []

    asyncio.run(scenario())


def test_cancelled_upload_keeps_fence_until_put_finishes_then_cleans_orphan(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    put_started = threading.Event()
    allow_put = threading.Event()
    put_finished = threading.Event()
    original_put = fake_r2.put_bytes

    def wait_then_put(key, data, mime, cache=None):
        put_started.set()
        if not allow_put.wait(timeout=3):
            raise RuntimeError("test barrier timed out")
        original_put(key, data, mime, cache)
        put_finished.set()

    monkeypatch.setattr(fake_r2, "put_bytes", wait_then_put)

    async def scenario():
        request = facemarket_enrollment.Request(
            {"type": "http", "app": enrollment_client.app}
        )
        photo = facemarket_enrollment.UploadFile(
            io.BytesIO(b"image"),
            filename="front.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
        upload = asyncio.create_task(
            facemarket_enrollment.upload_enrollment_photo(
                request, enrollment_id, "front", photo, "user-1"
            )
        )
        assert await asyncio.to_thread(put_started.wait, 3)
        new_key = enrollment_store.cleanup[0]["r2_key"]

        upload.cancel()
        await asyncio.sleep(0)

        drained = asyncio.create_task(
            facemarket_enrollment._drain_photo_cleanup(
                enrollment_client.app,
                enrollment_id=enrollment_id,
                key=new_key,
            )
        )
        assert await drained == (0, 0)
        assert [row["r2_key"] for row in enrollment_store.cleanup] == [new_key]
        assert new_key not in fake_r2.objects
        assert new_key not in fake_r2.deletes
        assert fake_pool.failed_try_locks == 1

        allow_put.set()
        assert await asyncio.to_thread(put_finished.wait, 3)
        with pytest.raises(asyncio.CancelledError):
            await upload
        assert new_key in fake_r2.objects
        assert [row["r2_key"] for row in enrollment_store.cleanup] == [new_key]

        assert await facemarket_enrollment._drain_photo_cleanup(
            enrollment_client.app,
            enrollment_id=enrollment_id,
            key=new_key,
        ) == (1, 0)
        assert new_key not in fake_r2.objects
        assert enrollment_store.cleanup == []
        assert fake_pool.max_checkout_depth == 1

    asyncio.run(scenario())


def test_due_upload_orphan_cleanup_resumes_after_fence_connection_dies(
    enrollment_client,
    fake_pool,
    fake_r2,
    enrollment_store,
    auth,
):
    enrollment_id = create_enrollment(enrollment_client, auth)
    key = f"facemarket/enrollments/{enrollment_id}/quarantine/front/orphan.jpg"
    fake_r2.objects[key] = (b"orphan", "image/jpeg")
    enrollment_store.cleanup.append(
        {
            "enrollment_id": enrollment_id,
            "angle": "front",
            "r2_key": key,
            "reason": "upload_orphan",
            "created_at": enrollment_store.now,
            "not_before": enrollment_store.now,
        }
    )

    async def scenario():
        async with fake_pool.connection() as dead_owner:
            async with dead_owner.cursor() as cur:
                await cur.execute(
                    "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
                    (PHOTO_FENCE_NAMESPACE, enrollment_id),
                )
                assert (await cur.fetchone())["locked"] is True

            skipped = asyncio.create_task(
                facemarket_enrollment._drain_photo_cleanup(
                    enrollment_client.app,
                    enrollment_id=enrollment_id,
                    key=key,
                )
            )
            assert await asyncio.wait_for(skipped, timeout=0.2) == (0, 0)
            assert fake_r2.deletes == []
            assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

            await dead_owner.close()
            cleaned = asyncio.create_task(
                facemarket_enrollment._drain_photo_cleanup(
                    enrollment_client.app,
                    enrollment_id=enrollment_id,
                    key=key,
                )
            )
            assert await asyncio.wait_for(cleaned, timeout=0.2) == (1, 0)

        assert fake_r2.deletes == [key]
        assert enrollment_store.cleanup == []
        assert fake_pool.failed_try_locks == 1
        assert fake_pool.max_checkout_depth == 1

    asyncio.run(scenario())


def test_delete_photo_skips_immediately_while_upload_owns_fence(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"first", "image/jpeg")},
        headers=auth(),
    )
    assert first.status_code == 201
    put_started = threading.Event()
    allow_put = threading.Event()
    original_put = fake_r2.put_bytes

    def wait_then_put(key, data, mime, cache=None):
        put_started.set()
        if not allow_put.wait(timeout=3):
            raise RuntimeError("test barrier timed out")
        original_put(key, data, mime, cache)

    monkeypatch.setattr(fake_r2, "put_bytes", wait_then_put)

    async def scenario():
        request = facemarket_enrollment.Request(
            {"type": "http", "app": enrollment_client.app}
        )
        photo = facemarket_enrollment.UploadFile(
            io.BytesIO(b"replacement"),
            filename="front.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
        upload = asyncio.create_task(
            facemarket_enrollment.upload_enrollment_photo(
                request, enrollment_id, "front", photo, "user-1"
            )
        )
        assert await asyncio.to_thread(put_started.wait, 3)
        try:
            deletion = asyncio.create_task(
                facemarket_enrollment.delete_enrollment_photo(
                    request, enrollment_id, "front", "user-1"
                )
            )
            with pytest.raises(facemarket_enrollment.HTTPException) as exc_info:
                await asyncio.wait_for(deletion, timeout=0.2)
            assert exc_info.value.status_code == 409
            assert enrollment_store.photos[0]["storage_state"] == "quarantine"
            assert fake_r2.deletes == []
            assert fake_pool.failed_try_locks == 1
        finally:
            allow_put.set()
            await upload

        assert fake_pool.max_checkout_depth == 1

    asyncio.run(scenario())


def test_terminal_cleanup_skips_before_row_lock_while_upload_owns_fence(
    enrollment_client,
    auth,
    fake_pool,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    put_started = threading.Event()
    allow_put = threading.Event()
    original_put = fake_r2.put_bytes

    def wait_then_put(key, data, mime, cache=None):
        put_started.set()
        if not allow_put.wait(timeout=3):
            raise RuntimeError("test barrier timed out")
        original_put(key, data, mime, cache)

    monkeypatch.setattr(fake_r2, "put_bytes", wait_then_put)

    async def scenario():
        request = facemarket_enrollment.Request(
            {"type": "http", "app": enrollment_client.app}
        )
        photo = facemarket_enrollment.UploadFile(
            io.BytesIO(b"image"),
            filename="front.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
        upload = asyncio.create_task(
            facemarket_enrollment.upload_enrollment_photo(
                request, enrollment_id, "front", photo, "user-1"
            )
        )
        assert await asyncio.to_thread(put_started.wait, 3)
        enrollment_store.enrollments[0]["status"] = "cancelled"

        cleanup = asyncio.create_task(
            facemarket_enrollment.cleanup_terminal_enrollment(
                enrollment_client.app, enrollment_id=enrollment_id
            )
        )
        assert await asyncio.wait_for(cleanup, timeout=0.2) is False
        assert enrollment_store.terminal_cleanup_loads == 0
        assert fake_r2.deletes == []
        assert fake_pool.failed_try_locks == 1

        upload.cancel()
        allow_put.set()
        with pytest.raises(asyncio.CancelledError):
            await upload
        assert await facemarket_enrollment._drain_photo_cleanup(
            enrollment_client.app,
            enrollment_id=enrollment_id,
        ) == (1, 0)
        assert fake_pool.max_checkout_depth == 1

    asyncio.run(scenario())


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


def test_cancel_cleanup_failure_remains_delete_pending_until_retry(
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
    key = enrollment_store.photos[0]["r2_key"]
    fake_r2.fail_delete_for.add(key)

    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert first.status_code == 200
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]
    assert key in fake_r2.objects

    fake_r2.fail_delete_for.clear()
    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )
    assert retry.status_code == 200
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []
    assert key not in fake_r2.objects


def test_cancel_commit_failure_keeps_active_photo_usable_and_untouched(
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
    photo = copy.deepcopy(enrollment_store.photos[0])
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 1)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert response.status_code == 503
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"
    assert enrollment_store.photos == [photo]
    assert photo["r2_key"] in fake_r2.objects
    assert fake_r2.deletes == []


def test_cancel_cleanup_finalize_commit_failure_is_retryable(
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
    key = enrollment_store.photos[0]["r2_key"]
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 3)

    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert first.status_code == 200
    assert first.json()["photos"] == []
    assert key not in fake_r2.objects
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

    retry = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )
    assert retry.status_code == 200
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


def test_terminal_cleanup_prepare_commit_failure_never_deletes_usable_object(
    enrollment_client, fake_r2, enrollment_store, auth, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    photo = copy.deepcopy(enrollment_store.photos[0])
    enrollment_store.enrollments[0]["status"] = "failed"
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 1)

    complete = asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            enrollment_client.app, enrollment_id=enrollment_id
        )
    )

    assert complete is False
    assert enrollment_store.photos == [photo]
    assert photo["r2_key"] in fake_r2.objects
    assert fake_r2.deletes == []


def test_terminal_cleanup_finalize_commit_failure_retries_after_object_is_gone(
    enrollment_client, fake_r2, enrollment_store, auth, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    key = enrollment_store.photos[0]["r2_key"]
    enrollment_store.enrollments[0]["status"] = "expired"
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 2)

    first = asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            enrollment_client.app, enrollment_id=enrollment_id
        )
    )

    assert first is False
    assert key not in fake_r2.objects
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

    retry = asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            enrollment_client.app, enrollment_id=enrollment_id
        )
    )
    assert retry is True
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


def test_terminal_cleanup_r2_failure_stays_referenced_until_retry(
    enrollment_client, fake_r2, enrollment_store, auth, monkeypatch
):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    key = enrollment_store.photos[0]["r2_key"]
    enrollment_store.enrollments[0]["status"] = "failed"
    fake_r2.fail_delete_for.add(key)

    first = asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            enrollment_client.app, enrollment_id=enrollment_id
        )
    )

    assert first is False
    assert key in fake_r2.objects
    assert enrollment_store.photos[0]["storage_state"] == "delete_pending"
    assert [row["r2_key"] for row in enrollment_store.cleanup] == [key]

    fake_r2.fail_delete_for.clear()
    retry = asyncio.run(
        facemarket_enrollment.cleanup_terminal_enrollment(
            enrollment_client.app, enrollment_id=enrollment_id
        )
    )
    assert retry is True
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


def test_delete_treats_r2_not_found_as_success(
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
    key = enrollment_store.photos[0]["r2_key"]
    fake_r2.objects.pop(key)
    fake_r2.not_found_for.add(key)

    response = enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=auth()
    )

    assert response.status_code == 204
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


def test_liveness_session_is_bound_to_owner_nonce_and_three_photos(
    enrollment_client,
    auth,
    enrollment_store,
    fake_rekognition,
    fake_sts,
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    nonce = "browser-nonce-with-at-least-32-bytes"

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": nonce},
        headers=auth(),
    )

    assert response.status_code == 201, response.text
    assert response.json()["region"] == "us-east-1"
    assert response.json()["sessionId"] == fake_rekognition.session_id
    assert set(response.json()["credentials"]) == {
        "accessKeyId",
        "secretAccessKey",
        "sessionToken",
        "expiration",
    }
    stored = enrollment_store.enrollments[0]
    assert stored["liveness_nonce_digest"] == hashlib.sha256(
        nonce.encode()
    ).hexdigest()
    assert stored["liveness_session_digest"] == hashlib.sha256(
        fake_rekognition.session_id.encode()
    ).hexdigest()
    assert fake_rekognition.session_id not in enrollment_store.serialized()
    assert "temporary-secret-key" not in enrollment_store.serialized()
    assert len(fake_rekognition.calls) == len(fake_sts.calls) == 1


def test_liveness_session_is_issued_only_once_per_enrollment(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    first = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "first-browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    second = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "second-browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_enrollment_state"
    assert len(fake_rekognition.calls) == 1


def test_liveness_session_requires_all_three_quarantine_photos(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    enrollment_store.photos.pop()

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "photos_required"
    assert fake_rekognition.calls == []


def test_liveness_session_rejects_short_nonce_before_provider_call(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "too-short"},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_nonce"
    assert fake_rekognition.calls == []


def test_liveness_session_rejects_repeated_nonce_before_provider_call(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    nonce = "browser-nonce-with-at-least-32-bytes"
    enrollment_store.enrollments[0]["liveness_nonce_digest"] = hashlib.sha256(
        nonce.encode()
    ).hexdigest()

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": nonce},
        headers=auth(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nonce_replayed"
    assert fake_rekognition.calls == []


def test_liveness_session_rejects_nonce_used_by_another_enrollment(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    nonce = "browser-nonce-with-at-least-32-bytes"
    enrollment_store.enrollments.append(
        {
            "id": "00000000-0000-0000-0000-000000000099",
            "user_id": "former-user",
            "model_id": None,
            "device_digest": "former-device-digest",
            "consent_version": "2026-08-v1",
            "status": "failed",
            "decision": "failed",
            "reason": "liveness_failed",
            "cooldown_until": None,
            "expires_at": NOW,
            "completed_at": NOW,
            "raw_deletion_evidence": {},
            "liveness_nonce_digest": hashlib.sha256(nonce.encode()).hexdigest(),
        }
    )

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": nonce},
        headers=auth(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nonce_replayed"
    assert fake_rekognition.calls == []


def test_liveness_session_hides_other_owners_enrollment(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(sub="other-user"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert fake_rekognition.calls == []


def test_liveness_session_enforces_active_cooldown(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    enrollment_store.enrollments[0]["cooldown_until"] = datetime.now(
        timezone.utc
    ) + timedelta(minutes=1)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "liveness_cooldown"
    assert fake_rekognition.calls == []


@pytest.mark.parametrize("failed_provider", ["rekognition", "sts"])
def test_liveness_provider_failure_is_sanitized_and_not_a_biometric_failure(
    enrollment_client,
    auth,
    enrollment_store,
    fake_rekognition,
    fake_sts,
    failed_provider,
):
    enrollment_id = create_ready_enrollment(
        enrollment_client, auth, enrollment_store
    )
    nonce = "browser-nonce-with-at-least-32-bytes"
    provider = fake_rekognition if failed_provider == "rekognition" else fake_sts
    provider.failures = 1

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": nonce},
        headers=auth(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "liveness_unavailable"
    assert fake_rekognition.session_id not in response.text
    assert "temporary-access-key" not in response.text
    assert "temporary-secret-key" not in response.text
    stored = enrollment_store.enrollments[0]
    assert stored["decision"] is None
    assert stored["completed_at"] is None
    assert stored.get("liveness_session_digest") is None
    assert stored["liveness_nonce_digest"] == hashlib.sha256(
        nonce.encode()
    ).hexdigest()
