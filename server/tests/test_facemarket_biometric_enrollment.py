import asyncio
import contextlib
import copy
import hashlib
import hmac
import inspect
import io
import json
import re
import threading
import types
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from botocore.exceptions import EndpointConnectionError
from psycopg.errors import UniqueViolation
from starlette.datastructures import Headers

from PIL import Image

from app import cx_identity, facemarket_enrollment, facemarket_photos, r2
from app.facemarket import _gender_from_trans
from app.main import create_app
from app.personalization_qc import FaceQcResult


def _uploaded_pair(fake_r2, nth=0):
    """nth 번째 업로드가 만든 두 객체 키 [원본, 정규화본].

    2026-09-15 부터 한 번의 업로드가 R2 객체를 **둘** 만든다 — 원본은 사용자가 올린 그대로
    (HEIC 일 수 있다), 정규화본은 그걸 읽을 수 있게 만든 무손실 PNG. 그래서 puts 는 짝으로
    쌓이고, 지울 때도 짝으로 지워진다(r2.normalized_sibling_key 가 그 둘을 잇는다).
    """
    original = fake_r2.puts[nth * 2][0]
    return [original, r2.normalized_sibling_key(original)]


def _uploaded_key(fake_r2, nth=0):
    """nth 번째 업로드의 **원본** 키."""
    return fake_r2.puts[nth * 2][0]


def _last_uploaded_key(fake_r2):
    """가장 최근 업로드의 **원본** 키. puts 의 마지막은 정규화본이라 그대로 쓰면 안 된다 —
    정리 원장(cleanup)이 추적하는 건 원본 키 하나뿐이다."""
    return next(key for key, *_ in reversed(fake_r2.puts)
                if not key.endswith(r2.NORMALIZED_SUFFIX))

from conftest import make_settings


def _select_aliases(sql_text: str) -> tuple[str, ...]:
    """`select ... from` 사이의 컬럼 목록에서 결과 dict 의 키(별칭)만 뽑는다.

    `dict_row` 커서는 **select 한 컬럼만** 돌려준다. 가짜 커서가 손으로 적은 dict 를 주면
    그 사실이 사라져, 프로덕션 SELECT 에 없는 컬럼을 코드가 읽어도 테스트가 전부 통과한다
    — 최종리뷰 C1(간편인증 완료 경로가 프로덕션에서만 죽어 있던 버그)이 정확히 그 틈으로
    빠져나갔다. 그래서 가짜 커서도 프로덕션 SQL 이 실제로 뽑는 컬럼만 돌려주게 한다.
    """
    body = re.search(r"select\s(.*?)\sfrom\s", sql_text, re.S | re.I)
    assert body, "select ... from 절을 찾지 못했다"
    # SQL 주석(-- ...) 은 컬럼이 아니다.
    columns = re.sub(r"--[^\n]*", "", body.group(1))
    aliases = []
    for item in columns.split(","):
        item = " ".join(item.split())
        if not item:
            continue
        lowered = item.lower()
        if " as " in lowered:
            aliases.append(item[lowered.rindex(" as ") + 4:].strip())
        else:
            aliases.append(item.rsplit(".", 1)[-1].strip())
    return tuple(aliases)


def completion_check_columns() -> tuple[str, ...]:
    """`_initial_completion_checks` 의 완료-체크 SELECT 가 실제로 투영하는 컬럼들."""
    source = inspect.getsource(facemarket_enrollment._initial_completion_checks)
    return _select_aliases(source[source.index("select e.id::text"):])


# 컬럼 목록은 프로덕션 SQL 에서 뽑지만(손으로 적으면 "코드는 읽는데 SELECT 엔 없는" 컬럼을
# 가짜 커서가 대신 채워 줘 프로덕션에서만 죽는다), 값의 기본형은 DB 스키마를 따라야 한다 —
# photo_revision 은 `integer not null default 0` 이라 실서버에서 절대 NULL 이 아니다.
# 여기서 None 을 주면 실서버엔 없는 NoneType 비교 오류가 테스트에서만 난다.
COMPLETION_COLUMN_DEFAULTS = {"photo_revision": 0}


NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
DEVICE_ID = "device-id-with-at-least-32-characters"
# D1: 초상은 trans 가 아니라 클라가 릴레이하는 OACX RESULT-step dlphotoimage(hex) 에서 온다.
# 진짜 JPEG 매직바이트(ffd8ff)로 시작하는 최소 픽스처 — 실제 신분증 사진이 아니라 포맷 검증용.
PORTRAIT_JPEG_BYTES = b"\xff\xd8\xff" + b"portrait-bytes"
PORTRAIT_HEX = PORTRAIT_JPEG_BYTES.hex()
ACTIVE_STATUSES = {
    "identity_pending",
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
        self.consent_events = []
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
        self.cutover_closed_sequence = []
        self.account_closed = False

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
        # 라우트가 보는 값 — 상태 가드 UPDATE 는 rowcount 로 승패를 가른다(신분증 업로드의
        # 보상 삭제가 그 위에 걸려 있다). 분기가 안 잡히면 0 이 남아 "졌다"로 읽힌다.
        self.rowcount = 0

        if (
            "kind = 'personalization_purge'" in query
            and "status in ('pending', 'running')" in query
            and "ready_for_identity_delete" in query
        ):
            self.result = {"closed": self.store.account_closed}
        elif query.startswith("select pg_try_advisory_lock"):
            lock_key = tuple(params)
            owner = self.conn.store.advisory_lock_owners.get(lock_key)
            locked = owner is None or owner is self.conn
            if locked:
                self.conn.store.advisory_lock_owners[lock_key] = self.conn
                self.conn.advisory_locks.add(lock_key)
            else:
                self.conn.pool.failed_try_locks += 1
            self.result = {"locked": locked}
        elif query.startswith("select pg_try_advisory_xact_lock"):
            self.result = {"locked": True}
        elif query.startswith("select view, r2_key from fm_model_assets where source_enrollment_id"):
            self.many = []
        elif query.startswith("delete from fm_model_assets where source_enrollment_id"):
            self.result = None
        elif query.startswith("select pg_advisory_unlock"):
            if self.conn.store.unlock_started is not None:
                self.conn.store.unlock_started.set()
                await self.conn.store.allow_unlock.wait()
            if self.conn.store.fail_unlock:
                raise RuntimeError("unlock unavailable")
            lock_key = tuple(params)
            unlocked = self.conn.release_advisory_lock(lock_key)
            self.result = {"unlocked": unlocked}
        elif query.startswith("select id::text as id, model_id::text as model_id, status, photo_revision"):
            eid, uid = params
            row = next((row for row in self.store.enrollments if row["id"] == eid and row["user_id"] == uid), None)
            self.result = {**row, "photo_revision": row.get("photo_revision", 0)} if row else None
        elif "as has_license" in query:
            self.result = {"has_license": any(row.get("enrollment_id") == params[0] for row in self.store.licenses)}
        elif "as identity_recorded" in query:
            self.result = {"identity_recorded": any(row["model_id"] == params[0] and row["cx_tx_id"] == params[1] for row in self.store.identities)}
        elif query.startswith("update fm_biometric_enrollments set status = %s, photo_revision"):
            status, expires, eid, uid = params
            row = next(row for row in self.store.enrollments if row["id"] == eid and row["user_id"] == uid)
            row.update(status=status, expires_at=expires, photo_revision=row.get("photo_revision", 0) + 1, decision=None, reason=None, completed_at=None, liveness_session_digest=None, liveness_nonce_digest=None)
        elif query.startswith("update fm_models set assets_status = 'none'"):
            mid, uid, eid = params if len(params) == 3 else (params[0], None, params[1])
            for row in self.store.models:
                if row["id"] == mid and (uid is None or row["user_id"] == uid) and row.get("current_enrollment_id") == eid:
                    row.update(assets_status="none", assets_source_hash=None)
        elif query.startswith("select angle, qc_status, storage_state"):
            self.many = [photo.copy() for photo in self.store.photos if photo["enrollment_id"] == params[0]]
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
            # 컬럼 목록을 손으로 적지 않는다 — 프로덕션 SELECT 에서 그대로 읽어 온다.
            # 손으로 적으면 "코드는 읽는데 SELECT 엔 없는" 컬럼을 이 가짜 커서가 대신
            # 채워 줘서, 프로덕션에서만 죽는 경로가 초록불로 통과한다(최종리뷰 C1).
            self.result = (
                {
                    column: row.get(column, COMPLETION_COLUMN_DEFAULTS.get(column))
                    for column in completion_check_columns()
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
        elif query.startswith("select exists(") and "identity_tx_digest" in query:
            # 신분증-먼저 게이트(/identity)의 replay 차단 — identity_tx_digest 컬럼을 본다
            # (complete 경로의 oacx_tx_digest replay 와 별개의 SQL 이라 먼저 분기).
            token_digest = params[0]
            self.result = {
                "replayed": any(
                    row.get("cx_tx_id") == token_digest for row in self.store.identities
                )
                or any(
                    row.get("identity_tx_digest") == token_digest
                    for row in self.store.enrollments
                )
            }
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
            # 지원서 게이트(E5): insert 에 application_id 컬럼이 추가됐다(플래그 off 면 None).
            (
                user_id, model_id, device_digest, consent_version, expires_at, application_id,
                terms_consent_version, overseas_consent_version,
            ) = params
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
                    "terms_consent_version": terms_consent_version,
                    "overseas_consent_version": overseas_consent_version,
                    # Task1 migration: 새 등록의 status DEFAULT 는 identity_pending.
                    "status": "identity_pending",
                    "decision": None,
                    "reason": None,
                    "provider_versions": {},
                    "cooldown_until": None,
                    "expires_at": expires_at,
                    "completed_at": None,
                    "raw_deletion_evidence": {},
                    # Task1 evidence 컬럼(신분증-먼저 게이트가 채운다) — 기본 None.
                    "identity_ci_hash": None,
                    "identity_name_masked": None,
                    "identity_birth_year": None,
                    "identity_tx_digest": None,
                    "identity_contract_version": None,
                    "application_id": application_id,
                }
                self.store.enrollments.append(row)
                self.result = {"id": row["id"]}
        elif (
            query.startswith("with due as ( select id from fm_biometric_enrollments")
            and "review_status = 'pending'" in query
        ):
            # 심사 기한(REVIEW_DEADLINE_DAYS) 초과 종료(최종리뷰 I3). 만료 스윕과 같은
            # `with due as (...)` 로 시작하므로 **이 분기가 먼저**여야 한다.
            limit = params[0]
            deadline = self.store.now - timedelta(
                days=facemarket_enrollment.REVIEW_DEADLINE_DAYS
            )
            due = sorted(
                [
                    row
                    for row in self.store.enrollments
                    if row["status"] == "review_pending"
                    and row.get("review_status") == "pending"
                    and row.get("created_at", self.store.now) <= deadline
                ],
                key=lambda row: row.get("created_at", self.store.now),
            )[:limit]
            self.many = [{"id": row["id"]} for row in due]
            for row in due:
                row.update(
                    status="failed",
                    decision="failed",
                    reason="review_timeout",
                    review_status=None,
                    completed_at=self.store.now,
                )
        elif query.startswith("with due as ( select id from fm_biometric_enrollments"):
            limit = params[0]
            due = sorted(
                [
                    row
                    for row in self.store.enrollments
                    if row["expires_at"] <= self.store.now
                    and row["status"]
                    in {
                        "identity_pending",
                        "photos_pending",
                        "liveness_pending",
                        "processing",
                    }
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
        elif query.startswith("select pg_advisory_xact_lock"):
            self.result = {"?column?": None}
        elif "from fm_cutover_batches" in query and "status = any" in query:
            closed = (
                self.conn.store.cutover_closed_sequence.pop(0)
                if self.conn.store.cutover_closed_sequence
                else False
            )
            self.result = {"closed": closed}
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
        elif query.startswith("update fm_biometric_enrollments set consent_version"):
            biometric, terms, overseas, enrollment_id, user_id = params
            row = next(
                item for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            row.update(
                consent_version=biometric,
                terms_consent_version=terms,
                overseas_consent_version=overseas,
            )
        elif query.startswith("insert into fm_enrollment_consent_events"):
            enrollment_id, user_id, biometric, terms, overseas = params
            self.store.consent_events.append({
                "enrollment_id": enrollment_id,
                "user_id": user_id,
                "biometric_version": biometric,
                "terms_version": terms,
                "overseas_version": overseas,
            })
        elif (
            query.startswith("select e.id::text as id from fm_biometric_enrollments e")
            and "e.status = 'processing'" in query
            and "e.liveness_session_digest = %s" in query
        ):
            enrollment_id, user_id, session_digest = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["user_id"] == user_id
                    and item["status"] == "processing"
                    and item.get("liveness_session_digest") == session_digest
                ),
                None,
            )
            self.result = {"id": row["id"]} if row else None
        elif (
            query.startswith("select e.id::text as id from fm_biometric_enrollments e")
            and "e.status = 'processing'" in query
            and "e.liveness_session_digest" not in query
        ):
            # 라이브니스 off 2차 lock — 세션 다이제스트 예측어 없이 processing 만 확인.
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["user_id"] == user_id
                    and item["status"] == "processing"
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
                candidates.append(row)
            candidates.sort(
                key=lambda row: (
                    row.get("completed_at") is not None,
                    row.get("completed_at") or self.store.now,
                )
            )
            self.many = [{"id": row["id"]} for row in candidates[:limit]]
        elif (
            query.startswith("select e.id::text as id")
            and "exists (" in query
            and "fm_biometric_enrollment_photo_cleanup" in query
        ):
            limit = params[0]
            due_ids = []
            for cleanup in self.store.cleanup:
                if cleanup["not_before"] > self.store.now:
                    continue
                row = next(
                    (
                        item for item in self.store.enrollments
                        if item["id"] == cleanup["enrollment_id"]
                    ),
                    None,
                )
                if row and row["status"] == "license_pending" and row["id"] not in due_ids:
                    due_ids.append(row["id"])
            self.many = [{"id": enrollment_id} for enrollment_id in due_ids[:limit]]
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
            model_gender = None
            if row and row.get("model_id") and "m.gender" in query:
                # Task4 round1: _load_current_enrollment 도 fm_models 를 조인해
                # model_gender 를 본다(/enrollments/current 가 physique 를 반영하도록).
                model = next(
                    (m for m in self.store.models if m["id"] == row["model_id"]), None
                )
                model_gender = model.get("gender") if model else None
            self.result = (
                _enrollment_db_view(row, model_gender=model_gender) if row else None
            )
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
            model_gender = None
            if row and row.get("model_id") and "m.gender" in query:
                # Task4: _load_owned_enrollment 가 fm_models 를 조인해 model_gender 를 본다.
                model = next(
                    (m for m in self.store.models if m["id"] == row["model_id"]), None
                )
                model_gender = model.get("gender") if model else None
            self.result = (
                _enrollment_db_view(row, model_gender=model_gender) if row else None
            )
        elif query.startswith("select p.angle, p.qc_status, p.uploaded_at"):
            enrollment_id = params[0]
            self.many = [
                {
                    "angle": photo["angle"],
                    "qc_status": photo["qc_status"],
                    "uploaded_at": photo["uploaded_at"],
                    "storage_state": photo["storage_state"],
                }
                for photo in self.store.photos
                if photo["enrollment_id"] == enrollment_id
            ]
        elif query.startswith("select angle, r2_key, mime_type"):
            enrollment_id = params[0]
            slots = params[1] if len(params) > 1 else ["front", "angle45", "side"]
            order = {slot: index for index, slot in enumerate(slots)}
            self.many = sorted(
                [
                    {
                        "angle": photo["angle"],
                        "r2_key": photo["r2_key"],
                        "mime_type": photo["mime_type"],
                        "qc_status": photo["qc_status"],
                        "storage_state": photo["storage_state"],
                    }
                    for photo in self.store.photos
                    if photo["enrollment_id"] == enrollment_id
                    and photo["angle"] in order
                ],
                key=lambda row: order[row["angle"]],
            )
        elif query.startswith("select angle from fm_biometric_enrollment_photos"):
            enrollment_id = params[0]
            self.many = [
                {"angle": photo["angle"]}
                for photo in self.store.photos
                if photo["enrollment_id"] == enrollment_id
                and photo["qc_status"] == "passed"
                and photo["storage_state"] == "quarantine"
            ]
        elif query.startswith("select r2_key, storage_state"):
            # 업로드 1차 통과(고아 예약 직전)의 좁은 조회 — storage_state 만 본다.
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
                {"r2_key": photo["r2_key"], "storage_state": photo["storage_state"]}
                if photo else None
            )
        elif query.startswith("select r2_key, normalized_r2_key, mime_type"):
            enrollment_id, angle = params
            photo = next(
                (
                    item for item in self.store.photos
                    if item["enrollment_id"] == enrollment_id
                    and item["angle"] == angle
                ),
                None,
            )
            self.result = (
                {"r2_key": photo["r2_key"],
                 "normalized_r2_key": photo.get("normalized_r2_key"),
                 "mime_type": photo["mime_type"],
                 "storage_state": photo["storage_state"]}
                if photo else None
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
            (enrollment_id, angle, key, digest, mime, byte_size,
             normalized_key, normalized_bytes, normalized_w, normalized_h) = params
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
                "normalized_r2_key": normalized_key,
                "normalized_byte_size": normalized_bytes,
                "normalized_width": normalized_w,
                "normalized_height": normalized_h,
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
            reason, cooldown_until, enrollment_id, expected_status = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            if row["status"] == expected_status:
                row.update(
                    status="failed",
                    decision="failed",
                    reason=reason,
                    completed_at=row.get("completed_at") or NOW,
                    cooldown_until=cooldown_until or row.get("cooldown_until"),
                )
                self.result = {"status": "failed"}
        elif query.startswith(
            "select identity_method from fm_biometric_enrollments where id"
        ):
            # /identity 게이트가 계약 선택을 위해 fetch_trans 앞에서 락 없이 읽는 조회
            # (Task6). mid 로 만들어진 행은 identity_method 키 자체가 없다 — 그대로
            # None 을 돌려줘 라우트가 'mid' 로 폴백하게 한다(NULL=mid 와 동일한 모양).
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id and item["user_id"] == user_id
                ),
                None,
            )
            self.result = {"identity_method": row.get("identity_method")} if row else None
        elif (
            query.startswith("select status, application_id")
            and "fm_biometric_enrollments where id" in query
            and "for update" in query
        ):
            # /identity 게이트의 소유·상태 잠금 조회(id + user_id + for update). 대조용 application_id 포함(E5).
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
                {"status": row["status"], "application_id": row.get("application_id")}
                if row
                else None
            )
        elif query.startswith("select status from fm_biometric_enrollments where id"):
            enrollment_id = params[0]
            row = next(
                (item for item in self.store.enrollments if item["id"] == enrollment_id),
                None,
            )
            self.result = {"status": row["status"]} if row else None
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
        elif (
            query.startswith("update fm_biometric_enrollments set status = 'photos_pending'")
            and "identity_ci_hash" in query
        ):
            # /identity 게이트: identity_pending → photos_pending + CI 증거 컬럼 저장.
            (
                ci_hash,
                name_masked,
                birth_year,
                tx_digest,
                contract_version,
                enrollment_id,
                user_id,
            ) = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            if row["status"] == "identity_pending":
                row.update(
                    status="photos_pending",
                    identity_ci_hash=ci_hash,
                    identity_name_masked=name_masked,
                    identity_birth_year=birth_year,
                    identity_tx_digest=tx_digest,
                    identity_contract_version=contract_version,
                )
        elif query.startswith("update fm_biometric_enrollments set status = 'photos_pending'"):
            enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            if row["status"] == "liveness_pending":
                row["status"] = "photos_pending"
        elif query.startswith(
            "update fm_biometric_enrollments set status = 'identity_pending', "
            "id_document_r2_key"
        ):
            # 신분증 업로드 성공 전이(status 가드 포함) — 라우트는 rowcount 로 승패를 본다.
            key, document_type, enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["user_id"] == user_id
                    and item["status"] == "id_capture_pending"
                ),
                None,
            )
            if row is not None:
                row.update(
                    status="identity_pending",
                    id_document_r2_key=key,
                    id_document_type=document_type,
                    id_document_uploaded_at=self.store.now,
                    id_document_purged_at=None,
                )
                self.rowcount = 1
        elif query.startswith("update fm_biometric_enrollments e set status = 'cancelled'"):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id
                    and item["user_id"] == user_id
                    # 프로덕션 cancel SQL 의 상태 목록을 따른다 — ACTIVE_STATUSES 는 mid
                    # 시절의 7개라 간편인증 신규 두 상태(id_capture_pending·review_pending)가
                    # 빠져 있다. 그대로 쓰면 취소 경로가 이 페이크에서만 404 가 된다.
                    and (
                        item["status"] in ACTIVE_STATUSES
                        or item["status"] in {"cancelled", "id_capture_pending", "review_pending"}
                    )
                ),
                None,
            )
            if row:
                row["status"] = "cancelled"
                row["completed_at"] = row.get("completed_at") or NOW
                # 취소한 등록이 관리자 대기 큐에 남지 않게 review_status='pending' 만 비운다
                # (이미 내려진 승인·거절 기록은 그대로 둔다).
                if row.get("review_status") == "pending":
                    row["review_status"] = None
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
                unsigned = not any(row.get("enrollment_id") == enrollment_id for row in self.store.licenses)
                photos = [
                    photo
                    for photo in self.store.photos
                    if photo["enrollment_id"] == enrollment_id
                    and (photo["storage_state"] in {"quarantine", "delete_pending"} or (unsigned and photo["storage_state"] == "approved"))
                ]
                self.many = (
                    [
                        {
                            "status": enrollment["status"],
                            "angle": photo["angle"],
                            "r2_key": photo["r2_key"],
                            "storage_state": photo["storage_state"],
                            "model_id": enrollment.get("model_id"),
                            "unsigned": unsigned,
                        }
                        for photo in photos
                    ]
                    or [{"status": enrollment["status"], "angle": None, "r2_key": None, "model_id": enrollment.get("model_id"), "unsigned": unsigned}]
                )
        # --- Task9: cleanup_terminal_enrollment 이 부르는 purge_id_document 의 SELECT/UPDATE.
        # mid 경로는 id_document_r2_key 가 애초에 None 이라 이 UPDATE 가 타도 관측 가능한
        # 사진 정리/만료 동작은 그대로다(id_document_purged_at 은 API 로 노출되지 않는다). ---
        elif query.startswith("select id_document_r2_key from fm_biometric_enrollments"):
            (enrollment_id,) = params
            row = next(
                (item for item in self.store.enrollments if item["id"] == enrollment_id),
                None,
            )
            self.result = {"id_document_r2_key": row.get("id_document_r2_key")} if row else None
        elif "id_document_purged_at = now()" in query:
            (enrollment_id,) = params
            row = next(
                (item for item in self.store.enrollments if item["id"] == enrollment_id),
                None,
            )
            if row is not None:
                row["id_document_r2_key"] = None
                row["id_document_purged_at"] = NOW
        elif "as remaining" in query and query.startswith("select"):
            enrollment_id = params[0]
            unsigned = not any(row.get("enrollment_id") == enrollment_id for row in self.store.licenses)
            self.result = {
                "remaining": sum(
                    photo["enrollment_id"] == enrollment_id
                    and (photo["storage_state"] in {"quarantine", "delete_pending"} or (unsigned and photo["storage_state"] == "approved"))
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
        elif query.startswith(
            "update fm_biometric_enrollments set profile_image_r2_key"
        ):
            key, enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            row["profile_image_r2_key"] = key
        elif query.startswith(
            "update fm_biometric_enrollments set height_bucket"
        ):
            height_bucket, body_type, enrollment_id, user_id = params
            row = next(
                item
                for item in self.store.enrollments
                if item["id"] == enrollment_id and item["user_id"] == user_id
            )
            row["height_bucket"] = height_bucket
            row["body_type"] = body_type
        elif query.startswith("select user_id::text as user_id from fm_models"):
            # /identity 게이트의 교차유저 CI 충돌 조회(ci_hash → 소유 user_id).
            ci_hash = params[0]
            model = next(
                (row for row in self.store.models if row.get("ci_hash") == ci_hash),
                None,
            )
            self.result = {"user_id": model["user_id"]} if model else None
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
        elif query.startswith("update fm_models set cover_image_url"):
            cover_image_url, model_id = params
            model = next(row for row in self.store.models if row["id"] == model_id)
            model["cover_image_url"] = cover_image_url
        elif query.startswith("update fm_models set height_bucket = coalesce"):
            # Task5: 바인딩때 키·체형을 모델로 승격. gender는 없으면 버킷서 유도(coalesce).
            height_bucket, body_type, derived_gender, model_id = params
            model = next(row for row in self.store.models if row["id"] == model_id)
            if height_bucket is not None:
                model["height_bucket"] = height_bucket
            if body_type is not None:
                model["body_type"] = body_type
            if not model.get("gender") and derived_gender is not None:
                model["gender"] = derived_gender
        elif query.startswith(
            "update fm_biometric_enrollments set provider_versions = provider_versions ||"
        ):
            # 기준 4장 합의도 기록 — 완료 판정에 쓰이지 않는다(기록 전용).
            merged, enrollment_id = params
            row = next(item for item in self.store.enrollments if item["id"] == enrollment_id)
            row.setdefault("provider_versions", {}).update(_json_value(merged))
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
            "consent_events",
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
        for name in (
            "enrollments", "photos", "models", "licenses", "identities", "jobs",
            "consent_events",
        ):
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


def _enrollment_db_view(row, *, model_gender=None):
    return {
        "id": row["id"],
        "model_id": row["model_id"],
        "status": row["status"],
        "photo_revision": row.get("photo_revision", 0),
        "decision": row["decision"],
        "reason": row["reason"],
        "cooldown_until": row["cooldown_until"],
        "expires_at": row["expires_at"],
        "liveness_session_digest": row.get("liveness_session_digest"),
        # Task4: physique(체형·키) + fm_models 조인으로 얻는 모델 성별.
        "height_bucket": row.get("height_bucket"),
        "body_type": row.get("body_type"),
        "model_gender": model_gender,
        "consent_version": row.get("consent_version"),
        "terms_consent_version": row.get("terms_consent_version"),
        "overseas_consent_version": row.get("overseas_consent_version"),
        "license_id": None,
        "license_allowed_use": None,
        "license_valid_until": None,
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
        fm_face_match_enabled=True,
        fm_liveness_enabled=True,
        fm_photo_slots=("sh_front", "sh_34", "sh_side"),
        fm_required_slot_count=3,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version="dev-gold-v1",
        fm_ci_pepper="pep",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
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


IDENTITY_TOKEN = "oacx-identity-token-1"


def verify_identity(client, auth, enrollment_id, *, token=IDENTITY_TOKEN):
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/identity",
        json={"token": token},
        headers=auth(),
    )


def _fast_forward_identity(store, enrollment_id):
    # 컨트롤러 결정 1: 기존 사진/liveness/complete 테스트(~35개)는 cx_identity.fetch_trans 를
    # 스텁하지 않으므로, 실 /identity HTTP 를 태우면 깨진다. 대신 store 를 직접 변이해
    # identity 게이트를 통과(photos_pending)시키고, Task3 의 바인딩이 읽을 identity_* 증거
    # 컬럼을 더미로 채운다. 실 엔드포인트는 전용 identity-gate 테스트만 태운다.
    enrollment = next(
        row for row in store.enrollments if row["id"] == enrollment_id
    )
    enrollment["status"] = "photos_pending"
    enrollment["identity_ci_hash"] = "identity-ci-hash-dummy"
    enrollment["identity_name_masked"] = "홍*동"
    enrollment["identity_birth_year"] = "1990"
    enrollment["identity_tx_digest"] = "cxsha256:identity-dummy"
    enrollment["identity_contract_version"] = "dev-mock-v1"


def create_enrollment(client, auth, *, device_id=DEVICE_ID, verify_identity=True):
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
    enrollment_id = response.json()["id"]
    if verify_identity:
        _fast_forward_identity(client.app.state.pool.store, enrollment_id)
    return enrollment_id


def test_identity_verify_advances_to_photos_pending(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"
    res = verify_identity(enrollment_client, auth, eid)
    assert res.status_code == 200, res.text
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"
    # ci_hash 저장, 원시 CI 미저장
    row = enrollment_store.enrollments[0]
    assert row["identity_ci_hash"] and "dev-ci-value" not in enrollment_store.serialized()


def test_identity_verify_blocks_minor(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    async def minor_trans(*_a, **_k):
        return {"ci": "dev-ci-value", "birth": "20200101", "nm": "홍길동", "txId": "tx-m"}

    monkeypatch.setattr(facemarket_enrollment.cx_identity, "fetch_trans", minor_trans)
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    res = verify_identity(enrollment_client, auth, eid)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "minor_blocked"
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"


def test_photos_require_identity_first(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    # QC 를 통과시켜, 거절이 QC 가 아니라 상태 게이트(identity_pending)에서 나옴을 확인한다.
    # (upload_enrollment_photo 는 QC 를 먼저 돌리고 그 뒤 _validate_photo_mutation_enrollment 로 상태를 본다.)
    stub_qc(monkeypatch)
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_enrollment_state"


def create_ready_enrollment(client, auth, store, *, slots=("front", "angle45", "side")):
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
        for angle in slots
    )
    return enrollment_id


def create_complete_ready_enrollment(
    client, auth, store, fake_r2, fake_rekognition, *, slots=("front", "angle45", "side")
):
    enrollment_id = create_ready_enrollment(client, auth, store, slots=slots)
    store.enrollments[0]["liveness_session_digest"] = hashlib.sha256(
        fake_rekognition.session_id.encode()
    ).hexdigest()
    for angle in slots:
        fake_r2.objects[f"private/{angle}.jpg"] = (f"{angle}-bytes".encode(), "image/jpeg")
    return enrollment_id


def dev_trans(**patch):
    trans = {
        "ci": "dev-ci-value",
        "birth": "19900102",
        "nm": "홍길동",
        "txId": "tx-dev-1",
    }
    trans.update(patch)
    return trans


class RecordingFaceQc:
    #: 신원 앵커 — 이쪽이 낀 호출만 "본인확인 매칭"이다.
    ANCHORS = ("id", "live")

    def __init__(self, scores=(0.46, 0.41, 0.42, 0.43), fail=False):
        self.scores = list(scores)
        self.fail = fail
        self.calls = []
        #: 기준 4장끼리의 합의도(refset_agreement) — 매칭과 **다른 경로**라 따로 센다.
        #: 같은 목록에 섞으면 "매칭 안 했다" 는 검증이 거짓이 된다.
        self.refset_calls = []

    @staticmethod
    def label(data):
        raw = bytes(data)
        if raw == PORTRAIT_JPEG_BYTES:
            return "id"
        if raw == b"live-reference":
            return "live"
        text = raw.decode("utf-8", "ignore")
        return text[: -len("-bytes")] if text.endswith("-bytes") else None

    def one_to_one_similarity(self, reference, candidate):
        pair = (self.label(reference), self.label(candidate))
        if not any(side in self.ANCHORS for side in pair):
            self.refset_calls.append(pair)
            return 0.85
        self.calls.append(pair)
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


def complete_enrollment(
    client,
    auth,
    enrollment_id,
    session_id,
    id_photo_hex=PORTRAIT_HEX,
):
    # Task3: /complete 는 더 이상 OACX token 을 받지 않는다(신분증 게이트는 /identity 가 전담).
    body = {"sessionId": session_id}
    if id_photo_hex is not None:
        body["idPhotoHex"] = id_photo_hex
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json=body,
        headers=auth(),
    )


# ── FM_LIVENESS_ENABLED=false: 라이브니스 없이 신분증 초상 앵커 매칭 ──────────────────────

def _make_liveness_off_settings():
    # 라이브니스 off — browser role·confidence·id_live 는 None 이어도 기동검증을 통과해야 한다.
    return make_settings(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_liveness_enabled=False,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_browser_role_arn=None,
        fm_liveness_confidence_threshold=None,
        fm_id_live_threshold=None,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version="dev-gold-v1",
        fm_ci_pepper="pep",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
    )


@pytest.fixture()
def liveness_off_client(
    keypair, monkeypatch, enrollment_store, fake_r2, fake_pool, fake_rekognition, fake_sts
):
    _private_key, public_key = keypair
    settings = _make_liveness_off_settings()
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
    app.state.fm_rekognition = None  # 라이브니스 off — 클라 없음
    app.state.fm_sts = None
    return TestClient(app)


def test_startup_requires_the_detector_even_with_face_matching_off(monkeypatch):
    """촬영 스펙 검사는 fm_face_match_enabled 와 무관하게 항상 돈다.

    잡는 회귀: 검출기 가중치 없이 뜬 배포는 사진 업로드가 전부 503 이라 등록이 통째로 막힌다.
    옛 검증은 얼굴 매칭이 켜져 있을 때만 가중치를 봤다 — 운영 기본값은 off 다.
    """
    settings = _make_liveness_off_settings()
    assert settings.fm_face_match_enabled is False
    monkeypatch.setattr(
        facemarket_enrollment, "weight_paths", lambda _s: ("/nonexistent/yunet.onnx", __file__)
    )
    with pytest.raises(RuntimeError, match="YuNet"):
        facemarket_enrollment.validate_biometric_settings(settings)


def test_liveness_disabled_validate_passes_without_liveness_settings(monkeypatch):
    # 기동검증: 라이브니스 off 면 role/confidence/id_live 가 None 이어도 통과해야 한다.
    settings = _make_liveness_off_settings()
    monkeypatch.setattr(
        facemarket_enrollment, "weight_paths", lambda _s: (__file__, __file__)
    )
    facemarket_enrollment.validate_biometric_settings(settings)  # raise 없어야 함


def test_config_reports_liveness_not_required(liveness_off_client):
    res = liveness_off_client.get("/v1/facemarket/config")
    assert res.status_code == 200, res.text
    # applicationRequired 는 기본 off(기존 즉시 등록). 지원서 게이트는 별도 플래그로 켠다.
    assert res.json() == {
        "payoutBanks": [
            {"code": "shinhan", "name": "신한은행"}, {"code": "kb", "name": "국민은행"},
            {"code": "woori", "name": "우리은행"}, {"code": "hana", "name": "하나은행"},
            {"code": "nh", "name": "NH농협은행"}, {"code": "ibk", "name": "IBK기업은행"},
            {"code": "kakao", "name": "카카오뱅크"}, {"code": "toss", "name": "토스뱅크"},
        ],
        "photoSlots": list(facemarket_photos.PHOTO_SLOTS),
        # 2026-09-15: 옆모습 오른쪽·뒷모습이 더해져 18칸. **새로 시작하는** 등록이 보는 값이다
        # (옛 동의로 시작한 등록은 16칸 그대로 — required_slots_for_consent).
        "requiredSlotCount": 18,
        "faceMatchEnabled": False,
        "livenessRequired": False,
        "applicationRequired": False,
        # 프론트가 이 값으로 동의 화면을 띄우고 그대로 돌려보낸다 — **현재 버전**이어야 한다.
        "consentDocumentVersion": facemarket_enrollment.BIOMETRIC_CONSENT_VERSION,
    }


def test_config_reports_valid_custom_slot_prefix(liveness_off_client):
    settings = replace(
        liveness_off_client.app.state.settings,
        fm_photo_slots=("sh_front", "sh_34", "sh_side", "sl_front", "bl_34"),
        fm_required_slot_count=3,
    )
    liveness_off_client.app.state.settings = settings
    response = liveness_off_client.get("/v1/facemarket/config")
    assert response.status_code == 200
    assert response.json()["photoSlots"] == [
        "sh_front", "sh_34", "sh_side", "sl_front", "bl_34"
    ]
    assert response.json()["requiredSlotCount"] == 3


def test_mixed_legacy_rows_normalize_to_distinct_required_slots():
    rows = [
        {"angle": "front", "r2_key": "old-front"},
        {"angle": "face01", "r2_key": "new-front"},
        {"angle": "angle45", "r2_key": "old-angle"},
        {"angle": "side", "r2_key": "old-side"},
    ]
    normalized = facemarket_enrollment.resolve_photo_rows(
        rows, ("face01", "face03", "face05")
    )
    assert [row["angle"] for row in normalized] == ["face01", "angle45", "side"]
    assert normalized[0]["r2_key"] == "new-front"


def test_new_consent_version_requires_terms_consent_but_not_overseas(
    enrollment_client, auth, enrollment_store
):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {"accepted": True, "documentVersion": facemarket_enrollment.BIOMETRIC_CONSENT_VERSION},
        },
        headers=auth(),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "consent_required"


def test_a_previous_consent_version_is_still_accepted_while_deploys_are_skewed(
    enrollment_client, auth, enrollment_store
):
    """프론트(Vercel)와 백엔드(CI) 배포 시점이 어긋나는 동안 옛 동의문으로도 등록이 시작된다.

    그때는 terms 를 함께 요구하지 않는다 — 옛 화면은 그 필드를 보내지 않는다.
    """
    previous = "2026-09-v1"
    assert previous in facemarket_enrollment.ACCEPTED_CONSENT_VERSIONS
    assert previous != facemarket_enrollment.BIOMETRIC_CONSENT_VERSION
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": {"accepted": True, "documentVersion": previous},
        },
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    # **본 적 없는 버전을 기록하면 안 된다** — 기록은 클라이언트가 보여 준 문서의 버전이다.
    assert enrollment_store.enrollments[-1]["consent_version"] == previous


def test_existing_enrollment_records_explicit_new_reconsent(
    enrollment_client, auth, enrollment_store
):
    eid = create_enrollment(enrollment_client, auth)
    current = facemarket_enrollment.BIOMETRIC_CONSENT_VERSION
    consent = {"accepted": True, "documentVersion": current}
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": DEVICE_ID,
            "biometricConsent": consent,
            "termsConsent": consent,
        },
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["id"] == eid
    assert response.json()["consentDocumentVersion"] == current
    assert response.json()["termsConsentVersion"] == current
    assert response.json()["overseasConsentVersion"] == facemarket_enrollment.OVERSEAS_NOTICE_VERSION
    # 동의 이벤트 테이블에 **새 값**이 기록돼야 한다 — 누가 어느 본문에 동의했는지의 증거다.
    assert len(enrollment_store.consent_events) == 1
    assert enrollment_store.consent_events[0]["biometric_version"] == current


def test_owner_can_reload_private_enrollment_photo(
    enrollment_client, auth, enrollment_store, fake_r2
):
    eid = create_enrollment(enrollment_client, auth)
    key = "private/face01.jpg"
    enrollment_store.photos.append({
        "enrollment_id": eid, "angle": "face01", "r2_key": key,
        "mime_type": "image/jpeg", "qc_status": "passed",
        "storage_state": "quarantine", "uploaded_at": NOW,
    })
    fake_r2.objects[key] = (b"photo-bytes", "image/jpeg")

    response = enrollment_client.get(
        f"/v1/facemarket/enrollments/{eid}/photos/face01", headers=auth()
    )
    assert response.status_code == 200
    assert response.content == b"photo-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"


def test_physique_accepts_new_body_type_without_height(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    eid = create_enrollment(enrollment_client, auth)
    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/physique",
        json={"bodyType": "delicate"},
        headers=auth(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["bodyType"] == "delicate"


def test_every_slot_uploads_and_completes_without_face_matching(
    liveness_off_client, auth, enrollment_store, fake_r2, completion_fakes
):
    eid = create_enrollment(liveness_off_client, auth)
    for slot in facemarket_photos.PHOTO_SLOTS:
        response = liveness_off_client.post(
            f"/v1/facemarket/enrollments/{eid}/photos",
            data={"slot": slot},
            files={"photo": (f"{slot}.jpg", f"{slot}-bytes".encode(), "image/jpeg")},
            headers=auth(),
        )
        assert response.status_code == 201, response.text
        assert response.json()["slot"] == slot

    assert enrollment_store.enrollments[0]["status"] == "liveness_pending"
    response = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/complete", json={}, headers=auth()
    )
    assert response.status_code == 202, response.text
    assert response.json()["passed"] is True
    assert completion_fakes["face_qc"].calls == []


def test_legacy_angle_upload_maps_to_new_slot(
    liveness_off_client, auth, monkeypatch
):
    eid = create_enrollment(liveness_off_client, auth)
    response = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"front-bytes", "image/jpeg")},
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["slot"] == "sh_front"
    assert response.json()["angle"] == "front"


def test_liveness_session_rejected_when_disabled(
    liveness_off_client, auth, enrollment_store
):
    eid = create_ready_enrollment(liveness_off_client, auth, enrollment_store)
    res = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/liveness-session",
        json={"nonce": "n" * 40},
        headers=auth(),
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "liveness_disabled"


def test_complete_without_liveness_skips_face_matching_by_default(
    liveness_off_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    eid = create_complete_ready_enrollment(
        liveness_off_client, auth, enrollment_store, fake_r2, fake_rekognition,
        slots=facemarket_enrollment.PHOTO_SLOTS,
    )
    # 세션 없이 완료 — 신분증 초상 앵커.
    res = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/complete",
        json={"idPhotoHex": PORTRAIT_HEX},
        headers=auth(),
    )
    assert res.status_code == 202, res.text
    assert res.json()["passed"] is True
    # AWS Rekognition 호출 0.
    assert fake_rekognition.calls == []
    assert fake_rekognition.result_calls == []
    assert completion_fakes["face_qc"].calls == []


def test_complete_binds_using_stored_identity_without_token(
    enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    eid = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    res = complete_enrollment(enrollment_client, auth, eid, fake_rekognition.session_id)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["passed"] is True and body["status"] == "asset_building"
    # 바인딩된 모델 display_name 이 저장된 identity_name_masked 에서 온다(재조회 없이).
    assert enrollment_store.models[0]["display_name"]


def test_profile_image_upload_is_non_gating(
    enrollment_client, auth, enrollment_store, fake_r2, completion_fakes
):
    eid = create_enrollment(enrollment_client, auth)  # photos_pending
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/profile-image",
        files={"image": ("cover.jpg", b"cover-bytes", "image/jpeg")},
        headers=auth(),
    )
    assert res.status_code == 201, res.text
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"  # 불변
    assert enrollment_store.enrollments[0]["profile_image_r2_key"]
    assert len(fake_r2.objects) >= 1


def test_profile_image_rejects_bad_mime(enrollment_client, auth, completion_fakes):
    eid = create_enrollment(enrollment_client, auth)
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/profile-image",
        files={"image": ("x.txt", b"nope", "text/plain")},
        headers=auth(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "unsupported_type"


def _seed_model_with_gender(store, gender, *, user_id="user-1"):
    # Task3: 성별은 fm_models.gender(OACX 신원확인이 세팅) — physique 검증이 이걸 읽는다.
    # create_enrollment 는 유저의 기존(가장 최근) fm_models 행을 골라 model_id 로 붙이므로,
    # 여기서 미리 심어두면 이어서 만든 등록이 이 모델·성별에 자동으로 연결된다.
    model_id = f"model-{gender}-seed"
    store.models.append(
        {
            "id": model_id,
            "user_id": user_id,
            "display_name": "테스트 모델",
            "status": "pending",
            "ci_hash": f"ci-hash-{gender}",
            "assets_status": "none",
            "current_enrollment_id": None,
            "gender": gender,
        }
    )
    return model_id


def test_physique_saves_and_returns(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    _seed_model_with_gender(enrollment_store, "female")
    eid = create_enrollment(enrollment_client, auth)  # photos_pending, model_id→여성 모델

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/physique",
        json={"heightBucket": "f_165_170", "bodyType": "toned"},
        headers=auth(),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["heightBucket"] == "f_165_170"
    assert body["bodyType"] == "toned"
    assert body["gender"] == "female"
    assert enrollment_store.enrollments[0]["height_bucket"] == "f_165_170"
    assert enrollment_store.enrollments[0]["body_type"] == "toned"


def test_physique_rejects_gender_mismatch(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    # 모델 gender=female 인 등록에 male 전용 키 구간을 저장하려 하면 400 invalid_physique.
    _seed_model_with_gender(enrollment_store, "female")
    eid = create_enrollment(enrollment_client, auth)

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/physique",
        json={"heightBucket": "m_180_185"},
        headers=auth(),
    )

    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "invalid_physique"
    assert enrollment_store.enrollments[0].get("height_bucket") is None


def test_physique_round_trips_through_current_enrollment(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    # Round1 회귀: /enrollments/current 도 /enrollments/{id} 와 동일하게
    # physique(height_bucket/body_type) + fm_models 조인 gender 를 반영해야 한다
    # (위저드 리로드/복원 경로가 마운트 시 getCurrentEnrollment() 를 호출한다).
    _seed_model_with_gender(enrollment_store, "female")
    eid = create_enrollment(enrollment_client, auth)
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/physique",
        json={"heightBucket": "f_165_170", "bodyType": "toned"},
        headers=auth(),
    )
    assert res.status_code == 200, res.text

    current = enrollment_client.get(
        "/v1/facemarket/enrollments/current", headers=auth()
    )
    status = enrollment_client.get(
        f"/v1/facemarket/enrollments/{eid}", headers=auth()
    )

    assert current.status_code == 200, current.text
    assert status.status_code == 200, status.text
    assert current.json() == status.json()
    body = current.json()
    assert body["heightBucket"] == "f_165_170"
    assert body["bodyType"] == "toned"
    assert body["gender"] == "female"


def test_complete_promotes_profile_image_to_model_cover(
    enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    # Task4: 대표이미지가 저장돼 있으면 바인딩 시 fm_models.cover_image_url 로 승격된다.
    eid = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/profile-image",
        files={"image": ("cover.jpg", b"cover-bytes", "image/jpeg")},
        headers=auth(),
    )
    assert res.status_code == 201, res.text
    profile_key = enrollment_store.enrollments[0]["profile_image_r2_key"]
    assert profile_key
    res = complete_enrollment(enrollment_client, auth, eid, fake_rekognition.session_id)
    assert res.status_code == 202, res.text
    assert enrollment_store.models[0]["cover_image_url"] == profile_key


def test_complete_promotes_physique_to_model(
    enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    # Task5: 등록 중 입력받은 키·체형이 있으면 바인딩 시 fm_models 로 승격된다.
    eid = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )
    # 등록에 키·체형을 설정한다.
    enrollment_store.enrollments[0]["height_bucket"] = "f_165_170"
    enrollment_store.enrollments[0]["body_type"] = "toned"

    res = complete_enrollment(enrollment_client, auth, eid, fake_rekognition.session_id)
    assert res.status_code == 202, res.text

    # 바인딩 시 model로 승격된 것을 확인한다.
    assert enrollment_store.models[0]["height_bucket"] == "f_165_170"
    assert enrollment_store.models[0]["body_type"] == "toned"


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
        "payload": {"modelId": "model-1", "enrollmentId": enrollment_id, "photoRevision": 0},
    }]
    stored = enrollment_store.enrollments[0]
    assert stored["status"] == "asset_building"
    assert stored["decision"] == "passed"
    assert stored["model_id"] == "model-1"
    assert stored["provider_versions"] == {
        "faceLiveness": "aws-rekognition-face-liveness",
        "oacx": "dev-mock-v1",
        "faceMatch": "sface-one-to-one",
        # 이 등록은 자산 소스 3칸만 요구하는 구성이라 기준 4장이 없다 — 상태만 남는다.
        "refset": {"status": "absent", "photos": 0},
    }
    assert stored["raw_deletion_evidence"] == {
        "oacxPortraitReleased": True,
        "livenessReferenceReleased": True,
        "temporaryEmbeddingsReleased": True,
    }
    serialized = enrollment_store.serialized()
    for secret in (
        "portrait-bytes",
        "live-reference",
        fake_rekognition.session_id,
        "0.46",
    ):
        assert secret not in serialized
    assert "private/" not in response.text
    assert "private/" not in json.dumps(enrollment_store.jobs)


def test_cancel_wins_before_completion_finalization_without_resurrection(
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
    request = types.SimpleNamespace(app=enrollment_client.app)
    original_get_conn = facemarket_enrollment.get_conn
    finalization_started = asyncio.Event()
    allow_finalization = asyncio.Event()
    checkout_counts = {}
    completion_task = None

    @contextlib.asynccontextmanager
    async def gated_get_conn(inner_request):
        task = asyncio.current_task()
        checkout_counts[task] = checkout_counts.get(task, 0) + 1
        if task is completion_task and checkout_counts[task] == 2:
            finalization_started.set()
            await allow_finalization.wait()
        async with original_get_conn(inner_request) as conn:
            yield conn

    monkeypatch.setattr(facemarket_enrollment, "get_conn", gated_get_conn)

    async def run_race():
        nonlocal completion_task
        completion_task = asyncio.create_task(
            facemarket_enrollment.process_enrollment_completion(
                request,
                enrollment_id=enrollment_id,
                user_id="user-1",
                session_id=fake_rekognition.session_id,
                id_photo_hex=PORTRAIT_HEX,
            )
        )
        await finalization_started.wait()
        cancelled = await facemarket_enrollment.cancel_enrollment(
            request, enrollment_id, "user-1"
        )
        assert cancelled.status == "cancelled"
        allow_finalization.set()
        with pytest.raises(HTTPException) as exc_info:
            await completion_task
        assert exc_info.value.status_code == 409

    asyncio.run(run_race())

    assert enrollment_store.enrollments[0]["status"] == "cancelled"
    assert enrollment_store.identities == []
    assert enrollment_store.models == []
    assert enrollment_store.jobs == []


def test_cancel_wins_before_provider_failure_without_cooldown_or_resurrection(
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
    provider_started = threading.Event()
    release_provider = threading.Event()

    def fail_after_cancel(*_args, **_kwargs):
        provider_started.set()
        assert release_provider.wait(timeout=2)
        raise facemarket_enrollment.BiometricProviderError("liveness_failed")

    monkeypatch.setattr(facemarket_enrollment, "get_liveness_result", fail_after_cancel)
    request = types.SimpleNamespace(app=enrollment_client.app)

    async def run_race():
        completion = asyncio.create_task(
            facemarket_enrollment.process_enrollment_completion(
                request,
                enrollment_id=enrollment_id,
                user_id="user-1",
                session_id=fake_rekognition.session_id,
                id_photo_hex=PORTRAIT_HEX,
            )
        )
        assert await asyncio.to_thread(provider_started.wait, 2)
        assert enrollment_store.enrollments[0]["status"] == "processing"
        try:
            cancelled = await facemarket_enrollment.cancel_enrollment(
                request, enrollment_id, "user-1"
            )
            assert cancelled.status == "cancelled"
        finally:
            release_provider.set()
        return await completion

    decision = asyncio.run(run_race())

    assert decision == facemarket_enrollment.EnrollmentDecision(
        False, False, "liveness_failed", "cancelled"
    )
    current = enrollment_store.enrollments[0]
    assert current["status"] == "cancelled"
    assert current["cooldown_until"] is None
    assert enrollment_store.identities == []
    assert enrollment_store.models == []
    assert enrollment_store.jobs == []


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
                id_photo_hex=PORTRAIT_HEX,
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


def test_identity_hmacs_owned_ci_buffer_without_immutable_copy(
    enrollment_client, auth, enrollment_store, completion_fakes, monkeypatch
):
    # Task3: 원시 CI 의 HMAC 계산은 /complete 가 아니라 앞단 /identity 로 이동했다. CI 는 폐기
    # 가능한 가변 버퍼(bytearray)에서 바로 해시돼야 한다 — 불변 복사본이 생기면 wipe 가 무의미.
    seen_message_types = []
    original_hmac_new = hmac.new

    def track_hmac_new(key, msg=None, digestmod=""):
        seen_message_types.append(type(msg))
        return original_hmac_new(key, msg, digestmod)

    monkeypatch.setattr(facemarket_enrollment.hmac, "new", track_hmac_new)
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)

    response = verify_identity(enrollment_client, auth, eid)

    assert response.status_code == 200, response.text
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


# Task3 참고: 토큰 재사용(replay)·미성년 차단·교차유저 CI 검증은 이제 /complete 가 아니라
# 앞단 /identity 게이트가 전담한다. 아래 두 테스트가 실 /identity 엔드포인트에서 replay 와
# 교차유저 CI 를 각각 검증한다(미성년은 test_identity_verify_blocks_minor 가 커버).


def test_identity_verify_rejects_replayed_token(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    # 같은 OACX token 이 이미 소비됐다면(digest 가 fm_identity_verifications 에 존재) 재사용 거절.
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    token_digest = f"cxsha256:{hashlib.sha256(IDENTITY_TOKEN.encode()).hexdigest()}"
    enrollment_store.identities.append({"cx_tx_id": token_digest})

    res = verify_identity(enrollment_client, auth, eid)

    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "identity_replay"
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"


def test_identity_verify_rejects_cross_user_ci(
    enrollment_client, auth, enrollment_store, completion_fakes
):
    # CI 가 다른 유저 모델에 이미 묶여 있으면(소유권 불일치) 회복 플로우로 보낸다.
    # ci_hash 는 엔드포인트와 동일하게 계산: hmac(pepper="pep", evidence.ci=b"dev-ci-value").
    enrollment_store.models.append(
        {
            "id": "other-model",
            "user_id": "other-user",
            "display_name": "다른 사람",
            "status": "verified",
            "ci_hash": hmac.new(b"pep", b"dev-ci-value", hashlib.sha256).hexdigest(),
            "assets_status": "ready",
            "current_enrollment_id": None,
        }
    )
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)

    res = verify_identity(enrollment_client, auth, eid)

    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "identity_recovery_required"
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"


@pytest.mark.parametrize(
    "id_photo_hex",
    [
        None,  # 클라가 아예 보내지 않음(구버전 클라·통신 실패 등)
        "",
        "not-hex-zz",
        (b"\x89PNG\r\n\x1a\n" + b"not-a-jpeg").hex(),  # PNG 매직바이트 — JPEG 아님
    ],
)
def test_complete_fails_closed_on_missing_or_invalid_photo_hex(
    enrollment_client,
    auth,
    enrollment_store,
    fake_r2,
    fake_rekognition,
    completion_fakes,
    id_photo_hex,
):
    # D1: 초상은 이제 trans 가 아니라 클라 릴레이 HEX 에서 온다 — 없거나 JPEG 이 아니면
    # SFace 매치를 절대 시도하지 않고 여기서 fail-closed 해야 한다(얼굴 없는 통과 금지).
    enrollment_id = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )

    response = complete_enrollment(
        enrollment_client,
        auth,
        enrollment_id,
        fake_rekognition.session_id,
        id_photo_hex=id_photo_hex,
    )

    assert_completion_failure(
        response, enrollment_store, "id_portrait_unavailable", retryable=True
    )
    assert completion_fakes["face_qc"].calls == []
    assert enrollment_store.enrollments[0]["cooldown_until"] is None


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
    # Task3: /complete 는 저장된 identity_ci_hash 를 그대로 읽어 소유권을 재확인한다(방어선).
    # _fast_forward_identity 가 채운 더미 ci_hash 와 같은 값을 다른 유저 모델에 심어 충돌을 유발.
    enrollment_store.models.append(
        {
            "id": "other-model",
            "user_id": "other-user",
            "display_name": "다른 사람",
            "status": "verified",
            "ci_hash": enrollment_store.enrollments[0]["identity_ci_hash"],
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
    # 신분증-먼저 재배치(Task1 DEFAULT): 새 등록은 identity_pending 부터 시작한다.
    assert response.json()["status"] == "identity_pending"
    assert response.json()["requiredAngles"] == ["sh_front", "sh_34", "sh_side"]
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
        # Task4: physique(체형·키) + 검증용 모델 성별.
        "heightBucket",
        "bodyType",
        "gender",
        # Task5: 인증 수단 분기 + 심사 상태.
        "identityMethod",
        "reviewStatus",
        "photoCount",
        "photoRevision",
        "consentDocumentVersion",
        "licenseId",
        "licenseTerms",
        "termsConsentVersion",
        "overseasConsentVersion",
        # 2026-09-15: 학습 전 관리자 사진 확인. 재촬영 요청이 오면 등록 화면이 그 칸만 다시 받는다.
        "photoReviewStatus",
        "reshootSlots",
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
        f"facemarket/enrollments/{enrollment_id}/quarantine/sh_34/"
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
        f"facemarket/enrollments/{enrollment_id}/quarantine/sh_front/"
    )


# ── 기준 4장 합의도(기록만) ──────────────────────────────────────────────────
class _FakeQc:
    def __init__(self, scores=None, raise_on=()):
        self.scores, self.raise_on, self.pairs = scores or {}, set(raise_on), []

    def one_to_one_similarity(self, a, b):
        key = tuple(sorted((bytes(a).decode(), bytes(b).decode())))
        self.pairs.append(key)
        if key in self.raise_on:
            raise RuntimeError("no face")
        return self.scores.get(key, 0.85)


def _refset_items(*slots):
    return [(slot, bytearray(slot.encode())) for slot in slots]


def test_refset_agreement_scores_every_pair_of_the_three(monkeypatch):
    qc = _FakeQc()
    monkeypatch.setattr(facemarket_enrollment, "load_face_qc", lambda settings, required: qc)
    items = _refset_items(*facemarket_photos.REFSET_SLOTS, "sh_front", "sh_side")

    summary = facemarket_enrollment.refset_agreement(object(), items)

    assert len(qc.pairs) == 3, "3장 → 3쌍. 학습컷·측면은 기준이 아니다"
    assert summary == {"status": "ok", "pairs": 3, "median": 0.85, "min": 0.85,
                       "rule": "median>=0.8 and min>=0.7", "photos": 3}


def test_refset_agreement_records_a_weak_set_without_blocking(monkeypatch):
    pair = tuple(sorted(("sh_front2", "sh_gaze_left")))
    monkeypatch.setattr(facemarket_enrollment, "load_face_qc",
                        lambda settings, required: _FakeQc({pair: 0.41}))

    summary = facemarket_enrollment.refset_agreement(
        object(), _refset_items(*facemarket_photos.REFSET_SLOTS))

    assert summary["status"] == "weak" and summary["min"] == 0.41


def test_refset_agreement_never_raises_when_the_detector_is_missing(monkeypatch):
    def boom(settings, required):
        raise FileNotFoundError("face QC weights missing")

    monkeypatch.setattr(facemarket_enrollment, "load_face_qc", boom)
    summary = facemarket_enrollment.refset_agreement(
        object(), _refset_items(*facemarket_photos.REFSET_SLOTS))
    assert summary == {"status": "unavailable", "photos": 3, "error": "FileNotFoundError"}


def test_refset_agreement_survives_a_pair_that_cannot_be_scored(monkeypatch):
    """3쌍 중 하나를 못 재면 표본이 모자라다 — 0 으로 세지 않고 판정을 보류한다."""
    bad = tuple(sorted(("sh_front2", "sh_gaze_right")))
    monkeypatch.setattr(facemarket_enrollment, "load_face_qc",
                        lambda settings, required: _FakeQc(raise_on={bad}))

    summary = facemarket_enrollment.refset_agreement(
        object(), _refset_items(*facemarket_photos.REFSET_SLOTS))

    assert summary["pairs"] == 2 and summary["unscored"] == 1
    assert summary["status"] == "insufficient"


def test_refset_agreement_on_a_legacy_enrollment_has_nothing_to_compare(monkeypatch):
    """옛 3장 등록엔 기준 4장이 없다 — 판정 대신 상태만 남긴다."""
    monkeypatch.setattr(facemarket_enrollment, "load_face_qc",
                        lambda settings, required: pytest.fail("불러선 안 된다"))
    assert facemarket_enrollment.refset_agreement(
        object(), _refset_items("sh_front", "sh_34", "sh_side")) == {
            "status": "absent", "photos": 0}


# ── 촬영 스펙 검사 배선 ──────────────────────────────────────────────────────
# 판정 자체는 tests/test_facemarket_photo_check.py(v7 실사진 16장의 숫자 픽스처)가 본다.
# 여기서 보는 건 "라우트가 그 판정을 실제로 부르고, 막았을 때 아무것도 저장하지 않는가" 다.
def _upload_one(client, auth, eid, slot="sh_front"):
    return client.post(
        f"/v1/facemarket/enrollments/{eid}/photos",
        data={"slot": slot},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )


@pytest.mark.real_photo_check
def test_a_badly_framed_photo_is_rejected_before_anything_is_stored(
    liveness_off_client, auth, fake_r2, monkeypatch
):
    eid = create_enrollment(liveness_off_client, auth)
    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo",
                        lambda data, slot, **kw: ("face_too_small", {"face_w": 120.0}))

    response = _upload_one(liveness_off_client, auth, eid)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "photo_framing"
    assert response.json()["error"]["reasons"] == ["face_too_small"]
    assert "한 걸음 다가가서" in response.json()["error"]["message"]
    assert fake_r2.puts == []


@pytest.mark.real_photo_check
def test_the_shot_check_runs_even_when_face_matching_is_off(
    liveness_off_client, auth, monkeypatch
):
    """본인확인 QC(fm_face_match_enabled)와 별개다 — 등록 사진이 곧 학습셋이라 항상 본다."""
    assert liveness_off_client.app.state.settings.fm_face_match_enabled is False
    eid = create_enrollment(liveness_off_client, auth)
    seen = []
    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo",
                        lambda data, slot, **kw: (seen.append(slot), (None, {}))[1])

    assert _upload_one(liveness_off_client, auth, eid, slot="sl_34").status_code == 201
    assert seen == ["sl_34"], "정식 슬롯 이름으로 검사한다"


@pytest.mark.real_photo_check
def test_a_legacy_angle_is_checked_under_its_canonical_slot(
    liveness_off_client, auth, monkeypatch
):
    eid = create_enrollment(liveness_off_client, auth)
    seen = []
    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo",
                        lambda data, slot, **kw: (seen.append(slot), (None, {}))[1])

    response = liveness_off_client.post(
        f"/v1/facemarket/enrollments/{eid}/photos",
        data={"angle": "angle45"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    assert seen == ["sh_34"], "옛 angle45 도 3/4 창으로 잰다"


@pytest.mark.real_photo_check
def test_a_detector_outage_is_not_the_users_fault(
    liveness_off_client, auth, fake_r2, monkeypatch
):
    """가중치 부재·cv2 오류는 503 — 사진을 다시 찍으라고 하면 안 된다(qc_unavailable 과 같은 규칙)."""
    eid = create_enrollment(liveness_off_client, auth)

    def boom(data, slot, **kw):
        raise facemarket_enrollment.PhotoCheckUnavailable("FileNotFoundError")

    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo", boom)

    response = _upload_one(liveness_off_client, auth, eid)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "qc_unavailable"
    assert fake_r2.puts == []


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


def test_upload_rejects_file_over_the_face_cap(enrollment_client, auth, fake_r2, monkeypatch):
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"x" * (facemarket_enrollment.MAX_FACE_BYTES + 1), "image/jpeg")},
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


def test_angle_mismatch_now_blocks_upload(
    enrollment_client, auth, fake_r2, monkeypatch
):
    # angle_mismatch 는 차단이다(front↔turned 불일치). 측면 칸에 정면 등 방향 어긋난 사진은
    # 저장하지 않고 거절한다 — 45˚/측면 구분은 QC 가 안 하므로 이 케이스는 오탐이 아니다.
    stub_qc(monkeypatch, "reject", ["angle_mismatch"])
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "face_quality"
    assert response.json()["error"]["reasons"] == ["angle_mismatch"]
    assert fake_r2.puts == []


def test_blocking_qc_rejects_with_all_blocking_reasons(
    enrollment_client, auth, fake_r2, monkeypatch
):
    # occlusion + angle_mismatch 둘 다 차단 사유라 거절하고, 두 사유 모두 노출한다.
    stub_qc(monkeypatch, "reject", ["angle_mismatch", "occlusion"])
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "face_quality"
    assert set(response.json()["error"]["reasons"]) == {"occlusion", "angle_mismatch"}
    assert fake_r2.puts == []


def test_three_legacy_angles_complete_custom_three_slot_configuration(
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
        "sh_front",
        "sh_34",
        "sh_side",
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
    for angle in facemarket_enrollment.PHOTO_SLOTS:
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
    # 주인의 사진은 원본·정규화본 **한 짝**이다. "남의 업로드가 이걸 건드리지 않았다" 를
    # 보려면 두 객체 모두가 그대로 있어야 한다.
    owner_objects = {key: fake_r2.objects[key]
                     for key in (owner_photo["r2_key"],
                                 r2.normalized_sibling_key(owner_photo["r2_key"]))
                     if key in fake_r2.objects}
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
    assert fake_r2.objects == owner_objects


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
    # 원본과 정규화본이 짝으로 지워진다 — 하나만 지우면 얼굴이 R2 에 그대로 남는다.
    assert fake_r2.deletes == _uploaded_pair(fake_r2)
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

    old_key = _uploaded_key(fake_r2, 0)
    new_key = _uploaded_key(fake_r2, 1)
    assert old_key != new_key
    assert fake_r2.deletes == _uploaded_pair(fake_r2, 0)
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
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 4)

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

    key = _uploaded_key(fake_r2, 0)
    assert response.status_code == 503
    assert key not in response.text
    assert "digest" not in response.text.lower()
    assert fake_r2.deletes == _uploaded_pair(fake_r2, 0)
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
    # 주인의 사진은 원본·정규화본 **한 짝**이다. "남의 업로드가 이걸 건드리지 않았다" 를
    # 보려면 두 객체 모두가 그대로 있어야 한다.
    owner_objects = {key: fake_r2.objects[key]
                     for key in (owner_photo["r2_key"],
                                 r2.normalized_sibling_key(owner_photo["r2_key"]))
                     if key in fake_r2.objects}
    enrollment_store.fail_photo_upsert = True

    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"replacement", "image/jpeg")},
        headers=auth(),
    )

    assert failed.status_code == 503
    assert enrollment_store.photos == [owner_photo]
    assert fake_r2.objects == owner_objects
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
    # 주인의 사진은 원본·정규화본 **한 짝**이다. "남의 업로드가 이걸 건드리지 않았다" 를
    # 보려면 두 객체 모두가 그대로 있어야 한다.
    owner_objects = {key: fake_r2.objects[key]
                     for key in (owner_photo["r2_key"],
                                 r2.normalized_sibling_key(owner_photo["r2_key"]))
                     if key in fake_r2.objects}
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 2)

    failed = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"replacement", "image/jpeg")},
        headers=auth(),
    )

    assert failed.status_code == 503
    assert enrollment_store.photos == [owner_photo]
    assert fake_r2.objects == owner_objects
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
    orphan_key = _last_uploaded_key(fake_r2)

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


def test_cutover_close_between_photo_preflight_and_fence_writes_no_r2_or_link(
    enrollment_client,
    auth,
    fake_r2,
    enrollment_store,
    monkeypatch,
):
    """Break caught: close after preflight but before photo fence could still put R2."""
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    enrollment_store.cutover_closed_sequence = [False, True]

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "facemarket_cutover_in_progress"
    assert fake_r2.puts == []
    assert fake_r2.objects == {}
    assert enrollment_store.photos == []
    assert enrollment_store.cleanup == []


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
        new_key = _last_uploaded_key(fake_r2)
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

        # 정리는 짝으로 움직인다 — 원본 키 하나만 추적하고, 지울 때 형제(정규화본)를 함께
        # 지운다. 형제가 없어도 R2 delete 는 무해한 no-op 이라 옛 객체에도 안전하다.
        assert fake_r2.deletes == [key, r2.normalized_sibling_key(key)]
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


def test_cancel_purges_id_document_from_r2_and_clears_column(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    """review fix round1: 기존 cancel/expire 회귀 테스트는 전부 id_document_r2_key 가
    None 인 fixture 라 purge_id_document 의 'key 없음' no-op 분기만 타고 있었다 —
    실제로 문서가 있을 때 취소가 R2 delete 를 부르고 컬럼을 지우는지는 아무 테스트도
    확인하지 않았다. simple_auth 가 심었을 법한 실제 신분증 키를 fixture 에 직접 심고
    취소 경로(cancel_enrollment → cleanup_terminal_enrollment)로 몰아 이 갭을 메운다."""
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    key = r2.enrollment_id_document_key(enrollment_id, "jpg", version="test-version")
    enrollment_store.enrollments[0]["id_document_r2_key"] = key
    fake_r2.objects[key] = (b"masked-id-bytes", "image/jpeg")

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert response.status_code == 200, response.text
    assert key in fake_r2.deletes
    row = enrollment_store.enrollments[0]
    assert row["id_document_r2_key"] is None
    assert row["id_document_purged_at"] is not None


def test_cancel_allows_identity_pending_enrollment(
    enrollment_client, auth, enrollment_store
):
    # 신분증-먼저 재배치(Task1): identity_pending 도 사용자의 단일 활성 슬롯을 차지하므로
    # 취소 가능해야 한다(아니면 슬롯을 영구 점유해 재등록이 막힌다).
    enrollment_id = create_enrollment(enrollment_client, auth, verify_identity=False)
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert enrollment_store.enrollments[0]["status"] == "cancelled"


def test_sweep_expires_identity_pending_enrollment(
    enrollment_client, auth, enrollment_store
):
    # identity_pending 도 만료 스윕 대상이어야 한다 — 아니면 만료돼도 활성 슬롯을 영구 점유한다.
    enrollment_id = create_enrollment(enrollment_client, auth, verify_identity=False)
    row = enrollment_store.enrollments[0]
    assert row["status"] == "identity_pending"
    row["expires_at"] = NOW - timedelta(seconds=1)

    asyncio.run(
        facemarket_enrollment.sweep_terminal_enrollments(enrollment_client.app)
    )

    swept = enrollment_store.enrollments[0]
    assert swept["id"] == enrollment_id
    assert swept["status"] == "expired"
    assert swept["reason"] == "enrollment_expired"


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
    # Task9: cleanup_terminal_enrollment 이 이제 사진 드레인 전에 purge_id_document
    # 커밋을 하나 더 낸다(카운트 +1) — 노리는 대상은 여전히 드레인 커밋(R2 는 이미
    # 지웠는데 그 사실을 기록하는 커밋만 실패)이라 오프셋을 그만큼 밀어야 한다.
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 4)

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
    # Task9: cleanup_terminal_enrollment 이 이제 사진 드레인 전에 purge_id_document
    # 커밋을 하나 더 낸다(카운트 +1) — 노리는 대상은 여전히 드레인 커밋이라 오프셋을
    # 그만큼 밀어야 한다.
    enrollment_store.fail_commit_attempts.add(enrollment_store.commit_attempts + 3)

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


def test_liveness_session_can_be_reissued_with_new_nonce_for_retry(
    enrollment_client, auth, enrollment_store, fake_rekognition
):
    # 라이브니스 에러/취소 후 신분증·사진 재입력 없이 라이브 인증만 다시 시도할 수 있어야 한다 —
    # liveness_pending 이면 새 nonce 로 세션을 재발급받는다(같은 nonce 재사용은 아래 replay 테스트가 막음).
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
    assert second.status_code == 201
    assert len(fake_rekognition.calls) == 2


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


@pytest.mark.parametrize("raw,expected", [
    ({"gender": "M"}, "male"),
    ({"gender": "F"}, "female"),
    ({"sexCd": "1"}, "male"),
    ({"sexCd": "2"}, "female"),
    ({"gender": "male"}, "male"),
    ({}, None),
    ({"gender": "x"}, None),
])
def test_gender_from_trans(raw, expected):
    assert _gender_from_trans(raw) == expected


# ── opendid prewarm 훅 ─────────────────────────────────────────────────────────
# VC 발급(holder)은 scale-to-zero 라 첫 요청이 콜드부트 ~2분을 그대로 사용자에게 물린다.
# 발급 버튼보다 먼저, 발급이 사실상 확정되는 두 지점(라이브니스 시작·등록 완료)에서 깨운다.


class _PrewarmSpy:
    """opendid autoscaler 대역 — 라우트는 prewarm_soon 만 부른다(sam 훅과 같은 계약)."""

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def prewarm_soon(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("aws down")


def test_liveness_session_prewarms_opendid(
    enrollment_client, auth, enrollment_store, fake_rekognition, fake_sts
):
    scaler = _PrewarmSpy()
    enrollment_client.app.state.opendid_autoscaler = scaler
    eid = create_ready_enrollment(enrollment_client, auth, enrollment_store)

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert res.status_code == 201, res.text
    assert scaler.calls == 1


def test_rejected_liveness_session_does_not_prewarm_opendid(
    enrollment_client, auth, enrollment_store, fake_rekognition, fake_sts
):
    # 거절된 요청(짧은 nonce)은 발급으로 이어지지 않는다 — 태스크를 띄우지 않는다.
    scaler = _PrewarmSpy()
    enrollment_client.app.state.opendid_autoscaler = scaler
    eid = create_ready_enrollment(enrollment_client, auth, enrollment_store)

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/liveness-session",
        json={"nonce": "too-short"},
        headers=auth(),
    )

    assert res.status_code == 400, res.text
    assert scaler.calls == 0


def test_liveness_session_succeeds_even_when_the_prewarm_hook_raises(
    enrollment_client, auth, enrollment_store, fake_rekognition, fake_sts
):
    enrollment_client.app.state.opendid_autoscaler = _PrewarmSpy(fail=True)
    eid = create_ready_enrollment(enrollment_client, auth, enrollment_store)

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert res.status_code == 201, res.text


def test_complete_prewarms_opendid_when_enrollment_passes(
    enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    scaler = _PrewarmSpy()
    enrollment_client.app.state.opendid_autoscaler = scaler
    eid = create_complete_ready_enrollment(
        enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition
    )

    res = complete_enrollment(enrollment_client, auth, eid, fake_rekognition.session_id)

    assert res.status_code == 202, res.text
    assert res.json()["passed"] is True
    assert scaler.calls == 1


def test_prewarm_hook_is_optional_on_app_state(
    enrollment_client, auth, enrollment_store, fake_rekognition, fake_sts
):
    # 오토스케일러가 state 에 없어도(로컬·테스트) 라우트는 그대로 동작해야 한다.
    if hasattr(enrollment_client.app.state, "opendid_autoscaler"):
        del enrollment_client.app.state.opendid_autoscaler
    eid = create_ready_enrollment(enrollment_client, auth, enrollment_store)

    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )

    assert res.status_code == 201, res.text


def test_photo_upload_prewarms_opendid(
    enrollment_client, auth, fake_r2, monkeypatch
):
    """사진 업로드가 홀더를 깨우는 가장 이른 지점이다. prod 실측(2026-09-01): 사진 3장에
    3분 26초 — 그동안 홀더(~2분 부팅)를 띄우면 발급 시점엔 따뜻하다. 라이브니스 훅만으로는
    발급까지 1분도 안 남아 콜드부트를 못 가렸다."""
    calls = []

    class _Scaler:
        def prewarm_soon(self):
            calls.append(True)

    stub_qc(monkeypatch)
    enrollment_client.app.state.opendid_autoscaler = _Scaler()
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "front"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )

    assert response.status_code == 201, response.text
    assert calls, "첫 사진 업로드에서 홀더를 깨워야 한다"


@pytest.mark.parametrize("application_required", [False, True])
def test_create_enrollment_rejects_awaiting_confirmation_without_side_effects(
    enrollment_client, auth, enrollment_store, fake_r2, application_required
):
    enrollment_client.app.state.settings = replace(
        enrollment_client.app.state.settings, fm_application_required=application_required
    )
    enrollment_store.models.append({
        "id": "model-1", "user_id": "user-1", "status": "awaiting_confirm",
        "assets_status": "ready", "current_enrollment_id": "old-enrollment",
    })
    enrollment_store.licenses.append({"id": "license-1", "model_id": "model-1", "status": "active"})
    before = enrollment_store.serialized()
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={"deviceId": DEVICE_ID, "biometricConsent": {
            "accepted": True, "documentVersion": "2026-08-v1",
        }},
        headers=auth(),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_confirmation_required"
    assert enrollment_store.serialized() == before
    assert enrollment_store.commit_attempts == 0
    assert fake_r2.puts == [] and fake_r2.deletes == []


def test_completion_select_projects_every_column_read():
    """`/complete` 가 row 에서 읽는 컬럼은 전부 완료-체크 SELECT 에 있어야 한다.

    `dict_row` 커서는 select 한 컬럼만 담은 dict 를 준다 — 목록에 없는 컬럼을 읽으면
    KeyError 가 아니라 조용한 None 이다. 최종리뷰 C1 이 정확히 그 사건이었다:
    `identity_method`/`id_document_r2_key` 가 SELECT 에 없어 항상 None 이 되었고,
    `row.get("identity_method") or "mid"` 가 그 None 을 mid 로 접어 **간편인증 완료
    경로 전체가 프로덕션에서만 죽어 있었다**(테스트는 전부 통과). 컬럼을 지우거나
    읽는 쪽에 새 컬럼이 생기면 여기서 먼저 터진다.
    """
    projected = set(completion_check_columns())
    read_keys: set[str] = set()
    for reader in (
        facemarket_enrollment.process_enrollment_completion,
        # 완료 경로의 tail — 같은 row 를 그대로 받아 읽는다.
        facemarket_enrollment.bind_model_and_enqueue_asset_build,
    ):
        source = inspect.getsource(reader)
        read_keys |= set(re.findall(r"""row(?:\.get\(|\[)["']([a-z0-9_]+)["']""", source))
    assert read_keys, "row 를 읽는 코드를 하나도 못 찾았다 — 정규식이 낡았다"
    assert "identity_method" in read_keys and "id_document_r2_key" in read_keys
    missing = sorted(read_keys - projected)
    assert not missing, (
        f"_initial_completion_checks 의 SELECT 에 없는 컬럼을 읽는다: {missing}. "
        "dict_row 라 그 값은 항상 None 이 된다 — SELECT 목록에 추가해야 한다."
    )


def test_cancel_clears_pending_review_status(enrollment_client, auth, enrollment_store):
    """취소한 등록은 관리자 대기 큐에서도 사라져야 한다(최종리뷰 I5).

    `cancel_enrollment` 는 review_pending 을 받아 주면서 review_status 를 안 지웠다 —
    큐는 review_status 로만 필터하므로 그 행이 영원히 대기 목록에 남고, 심사자가 승인을
    누르면 상태 가드 UPDATE 가 0-row → 409 다. 지울 수도 처리할 수도 없는 유령 항목이다.
    """
    enrollment_id = create_enrollment(enrollment_client, auth, verify_identity=False)
    row = enrollment_store.enrollments[0]
    row["status"] = "review_pending"
    row["review_status"] = "pending"

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert response.status_code == 200, response.text
    # 커밋이 store 를 새 dict 로 갈아끼우므로 요청 뒤에 다시 읽는다.
    cancelled = enrollment_store.enrollments[0]
    assert cancelled["status"] == "cancelled"
    assert cancelled["review_status"] is None


def test_cancel_keeps_a_decided_review_status(enrollment_client, auth, enrollment_store):
    """이미 내려진 승인·거절 기록까지 지우지는 않는다 — 'pending' 일 때만 비운다."""
    enrollment_id = create_enrollment(enrollment_client, auth, verify_identity=False)
    row = enrollment_store.enrollments[0]
    row["status"] = "processing"
    row["review_status"] = "approved"

    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/cancel", headers=auth()
    )

    assert response.status_code == 200, response.text
    assert enrollment_store.enrollments[0]["review_status"] == "approved"


def test_review_pending_expires_after_the_review_deadline(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    """심사 대기는 5일 뒤 failed('review_timeout') 로 닫히고 통지된다(최종리뷰 I3).

    일반 만료 스윕에서 review_pending 을 뺀 건 "심사가 밀렸다고 24시간 만에 자동 탈락시키지
    않는다"는 뜻이지 "영원히 기다린다"가 아니다 — 신분증 촬영본은 7일이면 배치 스윕이
    DB 와 무관하게 지우므로 그 뒤엔 심사 자체가 불가능하고, 행은 사용자의 단일 활성 등록
    슬롯을 영구 점유한다(= 재등록 불가). 기한은 7일보다 **짧아야** 증거가 살아 있는 동안
    심사가 끝난다.
    """
    notified = []

    async def fake_notify(_app, *, enrollment_id, email_type, reject_reason=None):
        notified.append((enrollment_id, email_type))
        return True

    monkeypatch.setattr(facemarket_enrollment, "notify_enrollment_decision", fake_notify)

    enrollment_id = create_enrollment(enrollment_client, auth, verify_identity=False)
    row = enrollment_store.enrollments[0]
    row["status"] = "review_pending"
    row["review_status"] = "pending"
    row["created_at"] = NOW - timedelta(days=facemarket_enrollment.REVIEW_DEADLINE_DAYS, hours=1)

    asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(enrollment_client.app))

    swept = enrollment_store.enrollments[0]
    assert swept["id"] == enrollment_id
    assert swept["status"] == "failed"
    assert swept["reason"] == "review_timeout"
    # 관리자 대기 큐에서도 내려간다 — 처리할 수 없는 행이 큐에 남으면 안 된다.
    assert swept["review_status"] is None
    assert notified == [(enrollment_id, "enrollment_review_timeout")]


def test_review_pending_within_the_deadline_is_left_alone(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    """기한 안이면 건드리지 않는다 — 심사가 하루 밀렸다고 지원자를 떨어뜨리지 않는다."""
    monkeypatch.setattr(
        facemarket_enrollment,
        "notify_enrollment_decision",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("통지하면 안 된다")),
    )
    create_enrollment(enrollment_client, auth, verify_identity=False)
    row = enrollment_store.enrollments[0]
    row["status"] = "review_pending"
    row["review_status"] = "pending"
    row["created_at"] = NOW - timedelta(days=facemarket_enrollment.REVIEW_DEADLINE_DAYS - 1)
    # 일반 만료 스윕이 review_pending 을 집지 않는다는 사실도 함께 지킨다.
    row["expires_at"] = NOW - timedelta(hours=1)

    asyncio.run(facemarket_enrollment.sweep_terminal_enrollments(enrollment_client.app))

    assert enrollment_store.enrollments[0]["status"] == "review_pending"


def test_review_deadline_is_shorter_than_the_id_document_sweep():
    """기한은 신분증 촬영본 배치 스윕(7일)보다 반드시 짧아야 한다.

    이게 뒤집히면 "심사하러 갔더니 증거가 이미 파기됨" 상태가 다시 생긴다 — 두 상수가
    서로 다른 파일에 있어서 한쪽만 바뀌기 쉽다.
    """
    import inspect as _inspect

    from app import facemarket_id_document

    sweep_default = _inspect.signature(
        facemarket_id_document.sweep_stale_id_documents
    ).parameters["older_than_seconds"].default
    assert facemarket_enrollment.REVIEW_DEADLINE_DAYS * 86400 < sweep_default


# ── 원본 그대로 받기 + 정규화본 (2026-09-15) ──────────────────────────────────
#
# 등록 사진이 곧 LoRA 학습셋인데, 프런트가 셀러 상품 사진용 규칙(긴 변 4000px·JPEG 0.85)으로
# 다시 인코딩해 올리고 있었다 — 48MP 원본이 12MP 손실본으로 학습에 들어갔다. 이제 받은
# 바이트를 그대로 저장하고, **읽을 것**(QC·관리자 열람·학습 내보내기)은 서버가 만든
# 정규화본(EXIF 적용 무손실 PNG)을 쓴다.


def _real_normalize(monkeypatch):
    """conftest 의 정규화 스텁을 끄고 진짜 변환을 쓴다(이 구역 전용)."""
    from app import facemarket_photo_normalize

    monkeypatch.setattr(facemarket_enrollment, "normalize_png",
                        facemarket_photo_normalize.normalize_png)


def _real_jpeg(width=900, height=1200, *, orientation=None) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), (80, 120, 200))
    image.paste((240, 40, 40), (0, 0, width, max(1, height // 4)))
    if orientation is None:
        image.save(buffer, "JPEG", quality=95)
    else:
        exif = image.getexif()
        exif[274] = orientation
        image.save(buffer, "JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def _upload(client, auth_headers, enrollment_id, body, *, filename="front.jpg",
            content_type="image/jpeg", slot="front"):
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": slot},
        files={"photo": (filename, body, content_type)},
        headers=auth_headers,
    )


def test_the_original_bytes_are_stored_unchanged(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    """★ 이 PR 의 요점. 올린 바이트가 한 비트도 안 바뀌어야 학습이 원본 화질을 본다."""
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    body = _real_jpeg()

    response = _upload(enrollment_client, auth(), enrollment_id, body)

    assert response.status_code == 201, response.text
    original_key = _uploaded_key(fake_r2, 0)
    stored, mime = fake_r2.objects[original_key]
    assert stored == body, "원본이 재인코딩되면 그만큼이 영영 사라진다"
    assert mime == "image/jpeg"
    row = enrollment_store.photos[0]
    assert row["byte_size"] == len(body)
    # 무결성 해시는 원본 기준이다 — 증서(VC)가 가리키는 게 사용자가 올린 그 파일이어야 한다.
    assert row["image_digest"] == r2.sha256_sri(body)


def test_a_normalized_png_is_stored_next_to_the_original(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    # orientation=6 = 시계방향 90도. 픽셀에 적용되면 가로·세로가 뒤집힌다.
    assert _upload(enrollment_client, auth(), enrollment_id,
                   _real_jpeg(900, 1200, orientation=6)).status_code == 201

    original_key, normalized_key = _uploaded_pair(fake_r2, 0)
    assert normalized_key in fake_r2.objects, "정규화본이 없으면 cv2 가 HEIC 를 못 읽는다"
    data, mime = fake_r2.objects[normalized_key]
    assert mime == "image/png"
    with Image.open(io.BytesIO(data)) as out:
        assert out.format == "PNG"
        assert out.size == (1200, 900), "EXIF 회전이 픽셀에 적용돼야 한다"
        assert out.getexif().get(274) in (None, 1)
    row = enrollment_store.photos[0]
    assert row["normalized_r2_key"] == normalized_key
    assert (row["normalized_width"], row["normalized_height"]) == (1200, 900)
    assert row["normalized_byte_size"] == len(data)
    # 형제 규칙 — 정리·파기가 이 계산으로 둘을 잇는다.
    assert r2.normalized_sibling_key(original_key) == normalized_key


def test_a_huge_photo_keeps_its_original_but_the_copy_is_capped(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    """★ 상한은 **읽기용 사본에만** 걸린다 — 원본이 줄면 이 PR 의 요점이 사라진다.

    48MP(8064×6048) 실측: 정규화본 51.0MB/2.80s → 4096 상한에서 17.6MB/1.41s. api 태스크가
    작은 vCPU 를 셀러 API·헬스체크와 나눠 쓰므로 그 1.4초가 그냥 비용이다.
    """
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    # 설정은 frozen dataclass 다 — 기본값(4096)을 그대로 쓰고, 그 값이 기본임을 여기서 잠근다.
    assert enrollment_client.app.state.settings.fm_normalized_max_edge == 4096
    enrollment_id = create_enrollment(enrollment_client, auth)
    body = _real_jpeg(8064, 6048)

    assert _upload(enrollment_client, auth(), enrollment_id, body).status_code == 201

    original_key, normalized_key = _uploaded_pair(fake_r2, 0)
    assert fake_r2.objects[original_key][0] == body, "원본은 한 비트도 안 바뀐다"
    with Image.open(io.BytesIO(fake_r2.objects[normalized_key][0])) as copy:
        assert copy.size == (4096, 3072)
    row = enrollment_store.photos[0]
    assert (row["normalized_width"], row["normalized_height"]) == (4096, 3072)
    assert row["byte_size"] == len(body), "행에 남는 원본 크기도 원본 기준이다"


def test_a_photo_under_the_cap_is_not_resized(
    enrollment_client, auth, fake_r2, enrollment_store, monkeypatch
):
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    assert _upload(enrollment_client, auth(), enrollment_id,
                   _real_jpeg(900, 1200)).status_code == 201

    row = enrollment_store.photos[0]
    assert (row["normalized_width"], row["normalized_height"]) == (900, 1200)


def test_the_quality_check_sees_the_normalized_bytes(
    enrollment_client, auth, monkeypatch
):
    """HEIC 는 cv2 가 아예 못 읽는다 — 검사에 원본을 넘기면 전부 'unreadable' 이 된다."""
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    seen = {}

    def spy(data, slot, **kwargs):
        seen["data"] = data
        return None, {"spy": True}

    monkeypatch.setattr(facemarket_enrollment, "check_enrollment_photo", spy)
    enrollment_id = create_enrollment(enrollment_client, auth)
    body = _real_jpeg()

    assert _upload(enrollment_client, auth(), enrollment_id, body).status_code == 201

    assert seen["data"] != body
    with Image.open(io.BytesIO(seen["data"])) as checked:
        assert checked.format == "PNG"


@pytest.mark.parametrize("brand", [b"heic", b"mif1"])
@pytest.mark.parametrize("declared", ["", "application/octet-stream", "image/heic"])
def test_heic_is_accepted_whatever_the_browser_calls_it(
    enrollment_client, auth, fake_r2, monkeypatch, brand, declared
):
    """iOS 는 HEIC 의 content-type 을 비워 보내기도 한다 — 확장자도 .HEIC/.hif 로 제각각이다.

    여기서는 매직바이트 판정만 본다(진짜 HEIC 디코드는 test_face_photo_normalize 가 본다).
    """
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    body = b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 64

    response = _upload(enrollment_client, auth(), enrollment_id, body,
                       filename="IMG_0001.HEIC", content_type=declared)

    assert response.status_code == 201, response.text
    assert _uploaded_key(fake_r2, 0).endswith(".heic" if brand == b"heic" else ".heif")


def test_an_obviously_wrong_type_is_refused_before_any_gate(
    enrollment_client, auth, fake_r2, monkeypatch
):
    """★ 형식 검사는 **계정·단계 게이트보다 먼저**다.

    매직바이트로 판정하려면 바이트를 읽어야 해서 검사를 뒤로 미루기 쉬운데, 그러면 PDF 업로드가
    400(요청이 틀림) 대신 403/409(권한·단계)를 받는다 — 클라이언트가 "내 파일이 잘못됐다" 와
    "지금은 못 올린다" 를 구분하지 못한다. 실제로 2026-09-15 personalization 쪽에서 이 순서가
    뒤집혀 CI 가 잡았다(403 == 400).

    게이트가 도는지 여부와 무관함을 보이려고 DB 접근 자체를 폭탄으로 만든다 — 그래도 400 이면
    그 검사가 DB 앞에 있다는 뜻이다.
    """
    enrollment_id = create_enrollment(enrollment_client, auth)

    @contextlib.asynccontextmanager
    async def boom(_request):
        raise AssertionError("형식 검사가 DB 게이트보다 뒤에 있다")
        yield  # pragma: no cover

    monkeypatch.setattr(facemarket_enrollment, "get_conn", boom)
    response = _upload(enrollment_client, auth(), enrollment_id, b"%PDF-1.4",
                       filename="x.pdf", content_type="application/pdf")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_type"
    assert fake_r2.puts == []


def test_a_video_wearing_the_same_box_header_is_refused(
    enrollment_client, auth, fake_r2, monkeypatch
):
    """mp4 도 ftyp 박스다 — 브랜드를 안 보면 동영상이 얼굴 사진 자리에 들어온다."""
    stub_qc(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = _upload(enrollment_client, auth(), enrollment_id,
                       b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64,
                       filename="clip.mp4", content_type="video/mp4")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_type"
    assert fake_r2.puts == []


def test_an_unreadable_upload_never_reaches_storage(
    enrollment_client, auth, fake_r2, monkeypatch
):
    """읽을 수 없는 사진을 받아 두면, 학습 내보내기 날에야 깨진 파일을 발견한다."""
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)

    response = _upload(enrollment_client, auth(), enrollment_id, b"\xff\xd8\xff not-a-jpeg")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "photo_framing"
    assert fake_r2.puts == []


def test_the_model_sees_the_normalized_bytes_back(
    enrollment_client, auth, fake_r2, monkeypatch
):
    """미리보기로 원본(HEIC)을 내려주면 브라우저가 못 그려 빈 칸이 된다."""
    stub_qc(monkeypatch)
    _real_normalize(monkeypatch)
    enrollment_id = create_enrollment(enrollment_client, auth)
    assert _upload(enrollment_client, auth(), enrollment_id, _real_jpeg()).status_code == 201

    response = enrollment_client.get(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/sh_front", headers=auth())

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    _original, normalized_key = _uploaded_pair(fake_r2, 0)
    assert response.content == fake_r2.objects[normalized_key][0]
