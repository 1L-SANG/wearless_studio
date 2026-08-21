"""Fail-closed FaceMarket biometric enrollment and quarantine lifecycle."""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from . import cx_identity
from .agents.face_qc import QcFailed, load_face_qc
from .auth import require_user
from .config import Settings
from .db import get_conn
from .models import CamelModel
from .personalization_qc import FaceQcUnavailable, evaluate_face_qc, qc_reason_message
from .r2 import enrollment_quarantine_key, ext_for_mime, sha256_sri

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket biometric enrollment"])

BIOMETRIC_CONSENT_VERSION = "2026-08-v1"
ENROLLMENT_TTL = timedelta(hours=24)
_PHOTO_FENCE_NAMESPACE = 0x464D5048
_MODEL_ASSET_FENCE_NAMESPACE = 0x464D4D41
ANGLES = ("front", "angle45", "side")
MAX_FACE_BYTES = 25 * 1024 * 1024
ALLOWED_FACE_MIME = {"image/png", "image/jpeg", "image/webp"}
START_LIVENESS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "rekognition:StartFaceLivenessSession",
            "Resource": "*",
            "Condition": {
                "StringEquals": {"aws:RequestedRegion": "us-east-1"}
            },
        }
    ],
}
AWS_LIVENESS_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"mode": "standard", "max_attempts": 3},
)


class BiometricProviderError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EnrollmentMappedError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EnrollmentExpiredError(RuntimeError):
    pass


@dataclass(slots=True)
class LivenessResult:
    reference_image: bytearray
    confidence: float
    provider_version: str = "aws-rekognition-face-liveness"


@dataclass(frozen=True, slots=True)
class EnrollmentDecision:
    passed: bool
    retryable: bool
    reason: str | None
    status: str
    model_id: str | None = None


RETRYABLE_REASONS = {
    "liveness_retry",
    "liveness_unavailable",
    "qc_unavailable",
    "id_portrait_unavailable",
}
TERMINAL_REASONS = {
    "minor_blocked",
    "liveness_failed",
    "face_match_failed",
    "identity_replay",
    "identity_recovery_required",
}


def create_liveness_session(rekognition, *, client_request_token: str) -> str:
    response = rekognition.create_face_liveness_session(
        ClientRequestToken=client_request_token,
        Settings={"AuditImagesLimit": 0},
    )
    session_id = response.get("SessionId")
    try:
        return str(uuid.UUID(str(session_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise BiometricProviderError("liveness_unavailable") from exc


def get_liveness_result(
    rekognition, *, session_id: str, minimum_confidence: float
) -> LivenessResult:
    try:
        response = rekognition.get_face_liveness_session_results(SessionId=session_id)
    except Exception as exc:
        raise BiometricProviderError("liveness_unavailable") from exc
    if response.get("Status") != "SUCCEEDED":
        raise BiometricProviderError("liveness_retry")
    reference = (response.get("ReferenceImage") or {}).get("Bytes")
    if not reference:
        raise BiometricProviderError("liveness_retry")
    try:
        confidence = float(response.get("Confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise BiometricProviderError("liveness_failed") from exc
    if not math.isfinite(confidence) or confidence < minimum_confidence:
        raise BiometricProviderError("liveness_failed")
    return LivenessResult(bytearray(reference), confidence)


def assume_liveness_browser_credentials(
    sts, *, role_arn: str, session_name: str
) -> dict:
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=900,
        Policy=json.dumps(START_LIVENESS_POLICY, separators=(",", ":")),
    )
    credentials = response["Credentials"]
    return {
        "accessKeyId": credentials["AccessKeyId"],
        "secretAccessKey": credentials["SecretAccessKey"],
        "sessionToken": credentials["SessionToken"],
        "expiration": credentials["Expiration"],
    }


class BiometricConsent(CamelModel):
    accepted: bool
    document_version: str


class CreateEnrollmentBody(CamelModel):
    device_id: str
    biometric_consent: BiometricConsent


class LivenessSessionBody(CamelModel):
    nonce: str


class CompleteEnrollmentBody(CamelModel):
    session_id: str
    token: str


class EnrollmentPhotoView(CamelModel):
    angle: str
    qc_status: str
    uploaded_at: datetime


class EnrollmentView(CamelModel):
    id: str
    model_id: str | None = None
    status: str
    photos: list[EnrollmentPhotoView] = []
    required_angles: list[str] = list(ANGLES)
    passed: bool | None = None
    retryable: bool | None = None
    reason: str | None = None
    expires_at: datetime


def _err(code: str, message: str, status: int = 400, **extra) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **extra},
    )


def _canonical_enrollment_id(enrollment_id: str) -> str:
    try:
        return str(uuid.UUID(str(enrollment_id)))
    except (AttributeError, TypeError, ValueError):
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "얼굴 저장소를 사용할 수 없습니다.", status=503)
    return client


def _wake_dispatcher(request: Request) -> None:
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()


async def _load_owned_enrollment(conn, enrollment_id: str, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at,
                   e.liveness_session_digest
            from fm_biometric_enrollments e
            where e.id = %s and e.user_id = %s
            """,
            (enrollment_id, user_id),
        )
        return await cur.fetchone()


def _validate_photo_mutation_enrollment(row: dict | None) -> dict:
    if row is None:
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
    if row["status"] == "photos_pending":
        return row
    if row["status"] == "liveness_pending" and not row.get(
        "liveness_session_digest"
    ):
        return row
    raise _err(
        "invalid_enrollment_state",
        "현재 등록 단계에서는 사진을 변경할 수 없습니다.",
        status=409,
    )


async def _lock_photo_mutation_enrollment(
    conn, enrollment_id: str, user_id: str
) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.status, e.liveness_session_digest
            from fm_biometric_enrollments e
            where e.id = %s and e.user_id = %s
            for update
            """,
            (enrollment_id, user_id),
        )
        row = await cur.fetchone()
    return _validate_photo_mutation_enrollment(row)


def _is_r2_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    return isinstance(error, dict) and str(error.get("Code")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


async def _try_photo_fence(conn, enrollment_id: str) -> bool:
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
            (_PHOTO_FENCE_NAMESPACE, enrollment_id),
        )
        return bool((await cur.fetchone())["locked"])


async def _unlock_photo_fence_once(conn, enrollment_id: str) -> None:
    await conn.rollback()
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_unlock(%s, hashtext(%s)) as unlocked",
            (_PHOTO_FENCE_NAMESPACE, enrollment_id),
        )
        if not (await cur.fetchone())["unlocked"]:
            raise RuntimeError("photo fence was not owned by this connection")
    await conn.rollback()


async def _unlock_photo_fence(conn, enrollment_id: str) -> None:
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    task = asyncio.create_task(_unlock_photo_fence_once(conn, enrollment_id))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            await conn.close()
        raise
    except Exception:
        await conn.close()
        raise


async def _run_r2_call_until_done(call, *args):
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def _drain_photo_cleanup_locked(
    conn,
    r2,
    *,
    enrollment_id: str,
    angle: str | None = None,
    key: str | None = None,
    reason: str | None = None,
) -> tuple[int, int]:
    clauses = ["c.enrollment_id = %s", "c.not_before <= now()"]
    params: list[str] = [enrollment_id]
    if angle is not None:
        clauses.append("c.angle = %s")
        params.append(angle)
    if key is not None:
        clauses.append("c.r2_key = %s")
        params.append(key)
    if reason is not None:
        clauses.append("c.reason = %s")
        params.append(reason)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                select c.angle, c.r2_key, c.reason, p.storage_state as current_state,
                       a.view as current_asset_view
                from fm_biometric_enrollment_photo_cleanup c
                left join fm_biometric_enrollment_photos p
                  on p.enrollment_id = c.enrollment_id
                 and p.angle = c.angle and p.r2_key = c.r2_key
                left join fm_model_assets a
                  on a.source_enrollment_id = c.enrollment_id
                 and a.r2_key = c.r2_key
                where {' and '.join(clauses)}
                order by c.created_at
                """,
                tuple(params),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        await conn.rollback()
        logger.warning(
            "facemarket_enrollment_photo_cleanup_load_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        return 0, 1

    deleted_count = 0
    failed_count = 0
    for row in rows:
        if row.get("current_state") in {"quarantine", "approved"} or row.get("current_asset_view"):
            delete_object = False
        else:
            delete_object = True
            try:
                if row["reason"] == "upload_orphan":
                    if (
                        await _run_r2_call_until_done(r2.head, row["r2_key"])
                        is None
                    ):
                        continue
                try:
                    await _run_r2_call_until_done(r2.delete, row["r2_key"])
                except Exception as exc:
                    if not _is_r2_not_found(exc):
                        raise
                if row["reason"] == "upload_orphan" and (
                    await _run_r2_call_until_done(r2.head, row["r2_key"])
                    is not None
                ):
                    raise RuntimeError("R2 object remained after delete")
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    "facemarket_enrollment_photo_cleanup_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "angle": row["angle"],
                        "error_type": type(exc).__name__,
                    },
                )
                continue
        try:
            async with conn.cursor() as cur:
                if delete_object:
                    await cur.execute(
                        """
                        delete from fm_biometric_enrollment_photos
                        where enrollment_id = %s and angle = %s and r2_key = %s
                          and storage_state = 'delete_pending'
                        """,
                        (enrollment_id, row["angle"], row["r2_key"]),
                    )
                await cur.execute(
                    """
                    delete from fm_biometric_enrollment_photo_cleanup
                    where enrollment_id = %s and r2_key = %s
                    """,
                    (enrollment_id, row["r2_key"]),
                )
            await conn.commit()
        except Exception as exc:
            await conn.rollback()
            failed_count += 1
            logger.warning(
                "facemarket_enrollment_photo_cleanup_commit_failed",
                extra={
                    "enrollment_id": enrollment_id,
                    "angle": row["angle"],
                    "error_type": type(exc).__name__,
                },
            )
            continue
        deleted_count += int(delete_object)
    return deleted_count, failed_count


async def _drain_photo_cleanup(
    app,
    *,
    enrollment_id: str,
    angle: str | None = None,
    key: str | None = None,
    reason: str | None = None,
) -> tuple[int, int]:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return 0, 1
    try:
        enrollment_id = _canonical_enrollment_id(enrollment_id)
        async with pool.connection() as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                return 0, 0
            try:
                return await _drain_photo_cleanup_locked(
                    conn,
                    r2,
                    enrollment_id=enrollment_id,
                    angle=angle,
                    key=key,
                    reason=reason,
                )
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_photo_cleanup_load_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        return 0, 1


async def _drain_model_asset_cleanup(
    app, *, limit: int = 100, model_id: str | None = None
) -> int:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return 0
    limit = max(1, min(int(limit), 100))
    clauses = ["c.not_before <= now()"]
    params: list[object] = []
    if model_id is not None:
        clauses.append("c.model_id = %s")
        params.append(model_id)
    params.append(limit)
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    select c.model_id::text as model_id, c.r2_key
                    from fm_model_asset_cleanup c
                    where {' and '.join(clauses)}
                    order by c.created_at
                    for update skip locked
                    limit %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                resolved = 0
                for row in rows:
                    await cur.execute(
                        "select pg_try_advisory_xact_lock(%s, hashtext(%s)) as locked",
                        (_MODEL_ASSET_FENCE_NAMESPACE, row["model_id"].lower()),
                    )
                    if not (await cur.fetchone())["locked"]:
                        continue
                    await cur.execute(
                        "select 1 from fm_model_assets where model_id=%s and r2_key=%s limit 1",
                        (row["model_id"], row["r2_key"]),
                    )
                    if await cur.fetchone() is None:
                        try:
                            await _run_r2_call_until_done(r2.delete, row["r2_key"])
                        except Exception as exc:
                            if not _is_r2_not_found(exc):
                                await cur.execute(
                                    """
                                    update fm_model_asset_cleanup
                                    set not_before = now() + interval '5 minutes'
                                    where model_id=%s and r2_key=%s
                                    """,
                                    (row["model_id"], row["r2_key"]),
                                )
                                logger.warning(
                                    "facemarket_model_asset_cleanup_failed",
                                    extra={
                                        "model_id": row["model_id"],
                                        "error_type": type(exc).__name__,
                                    },
                                )
                                continue
                    await cur.execute(
                        "delete from fm_model_asset_cleanup where model_id=%s and r2_key=%s",
                        (row["model_id"], row["r2_key"]),
                    )
                    resolved += 1
            await conn.commit()
            return resolved
    except Exception as exc:
        logger.warning(
            "facemarket_model_asset_cleanup_sweep_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0


async def _load_current_enrollment(conn, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at
            from fm_biometric_enrollments e
            where e.user_id = %s and e.status in (
                'photos_pending', 'liveness_pending', 'processing', 'asset_building',
                'license_pending', 'vc_pending'
            )
            order by e.created_at desc limit 1
            """,
            (user_id,),
        )
        return await cur.fetchone()


async def _enrollment_view(conn, row: dict) -> EnrollmentView:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select p.angle, p.qc_status, p.uploaded_at
            from fm_biometric_enrollment_photos p
            where p.enrollment_id = %s and p.storage_state = 'quarantine'
            order by case p.angle when 'front' then 1 when 'angle45' then 2 else 3 end
            """,
            (row["id"],),
        )
        photos = await cur.fetchall()
    decision = row.get("decision")
    cooldown_until = row.get("cooldown_until")
    retryable = None
    if decision == "failed":
        retryable = cooldown_until is None or cooldown_until <= datetime.now(timezone.utc)
    return EnrollmentView(
        id=str(row["id"]),
        model_id=str(row["model_id"]) if row.get("model_id") else None,
        status=row["status"],
        photos=photos,
        passed=True if decision == "passed" else False if decision == "failed" else None,
        retryable=retryable,
        reason=row.get("reason"),
        expires_at=row["expires_at"],
    )


@router.post("/enrollments", response_model=EnrollmentView, status_code=201)
async def create_enrollment(
    request: Request,
    body: CreateEnrollmentBody,
    user_id: str = Depends(require_user),
):
    device_id = body.device_id.strip()
    consent = body.biometric_consent
    if len(device_id) < 32:
        raise _err("invalid_device", "기기 식별자를 확인할 수 없습니다.")
    if not consent.accepted:
        raise _err("biometric_consent_required", "생체정보 처리 동의가 필요합니다.")
    if consent.document_version != BIOMETRIC_CONSENT_VERSION:
        raise _err("stale_consent_version", "최신 생체정보 처리 동의를 확인해 주세요.")
    device_digest = hashlib.sha256(device_id.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + ENROLLMENT_TTL
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select count(*) filter (
                         where status = 'failed' and completed_at >= now() - interval '3 minutes'
                       ) as recent_failures,
                       max(cooldown_until) as cooldown_until
                from fm_biometric_enrollments
                where user_id = %s or device_digest = %s
                """,
                (user_id, device_digest),
            )
            rate = await cur.fetchone()
            if rate["recent_failures"] >= 5 or (
                rate["cooldown_until"] is not None and rate["cooldown_until"] > now
            ):
                raise _err(
                    "liveness_cooldown",
                    "잠시 후 생체 인증을 다시 시도해 주세요.",
                    status=429,
                )
            await cur.execute(
                """
                select id::text as id, status from fm_models
                where user_id = %s order by created_at desc limit 1 for update
                """,
                (user_id,),
            )
            model = await cur.fetchone()
            model_id = model["id"] if model else None
            if model and model["status"] == "verified":
                await cur.execute(
                    """
                    update fm_models
                    set status = 'reverification_required', assets_status = 'none',
                        current_enrollment_id = null
                    where id = %s
                    """,
                    (model_id,),
                )
                await cur.execute(
                    """
                    update fm_licenses set status = 'reverification_required'
                    where model_id = %s and status = 'active'
                    """,
                    (model_id,),
                )
            await cur.execute(
                """
                insert into fm_biometric_enrollments
                    (user_id, model_id, device_digest, consent_version, expires_at)
                values (%s, %s, %s, %s, %s)
                on conflict (user_id) where status in (
                    'photos_pending', 'liveness_pending', 'processing', 'asset_building',
                    'license_pending', 'vc_pending'
                ) do nothing
                returning id::text as id
                """,
                (user_id, model_id, device_digest, consent.document_version, expires_at),
            )
            inserted = await cur.fetchone()
            if inserted:
                enrollment_id = inserted["id"]
            else:
                await cur.execute(
                    """
                    select id::text as id from fm_biometric_enrollments
                    where user_id = %s and status in (
                        'photos_pending', 'liveness_pending', 'processing', 'asset_building',
                        'license_pending', 'vc_pending'
                    ) order by created_at desc limit 1
                    """,
                    (user_id,),
                )
                enrollment_id = (await cur.fetchone())["id"]
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


@router.get("/enrollments/current", response_model=EnrollmentView)
async def get_current_enrollment(
    request: Request,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        row = await _load_current_enrollment(conn, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        return await _enrollment_view(conn, row)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentView)
async def get_enrollment(
    request: Request,
    enrollment_id: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with get_conn(request) as conn:
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        return await _enrollment_view(conn, row)


@router.post(
    "/enrollments/{enrollment_id}/photos",
    response_model=EnrollmentPhotoView,
    status_code=201,
)
async def upload_enrollment_photo(
    request: Request,
    enrollment_id: str,
    angle: str = Form(...),
    photo: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    if angle not in ANGLES:
        raise _err("invalid_angle", "사진 각도를 확인해 주세요.")
    mime = (photo.content_type or "").lower()
    if mime not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    r2 = _r2_face(request)
    data = await photo.read()
    new_key = None
    try:
        if not data:
            raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
        if len(data) > MAX_FACE_BYTES:
            raise _err("file_too_large", "이미지는 25MB 이하만 가능합니다.", status=413)
        try:
            qc = await evaluate_face_qc(
                request.app.state.settings,
                image_bytes=data,
                mime=mime,
                angle=angle,
            )
        except FaceQcUnavailable:
            raise _err(
                "qc_unavailable",
                "얼굴 검사를 지금 수행할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                status=503,
            )
        if not qc.passed:
            raise _err(
                "face_quality",
                qc_reason_message(qc.reasons),
                reasons=qc.reasons,
            )
        ext = ext_for_mime(mime)
        new_key = enrollment_quarantine_key(
            enrollment_id, angle, ext, version=uuid.uuid4().hex
        )
        old_key = None
        intent_committed = False
        try:
            async with get_conn(request) as conn:
                row = await _load_owned_enrollment(conn, enrollment_id, user_id)
                _validate_photo_mutation_enrollment(row)
            await _drain_photo_cleanup(
                request.app,
                enrollment_id=enrollment_id,
                angle=angle,
                reason="upload_orphan",
            )
            async with get_conn(request) as conn:
                if not await _try_photo_fence(conn, enrollment_id):
                    raise _err(
                        "photo_cleanup_pending",
                        "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                        status=409,
                    )
                try:
                    await _lock_photo_mutation_enrollment(conn, enrollment_id, user_id)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select r2_key, storage_state
                            from fm_biometric_enrollment_photos
                            where enrollment_id = %s and angle = %s
                            """,
                            (enrollment_id, angle),
                        )
                        old = await cur.fetchone()
                        if old and old["storage_state"] == "delete_pending":
                            raise _err(
                                "photo_cleanup_pending",
                                "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                                status=409,
                            )
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photo_cleanup
                                (enrollment_id, angle, r2_key, reason)
                            values (%s, %s, %s, 'upload_orphan')
                            on conflict (enrollment_id, r2_key) do nothing
                            """,
                            (enrollment_id, angle, new_key),
                        )
                    await conn.commit()
                    intent_committed = True
                    try:
                        await _run_r2_call_until_done(r2.put_bytes, new_key, data, mime)
                    except Exception as exc:
                        logger.warning(
                            "facemarket_enrollment_photo_store_failed",
                            extra={
                                "enrollment_id": enrollment_id,
                                "angle": angle,
                                "error_type": type(exc).__name__,
                            },
                        )
                        raise _err(
                            "storage_unavailable",
                            "얼굴 저장소를 사용할 수 없습니다.",
                            status=503,
                        )

                    await _lock_photo_mutation_enrollment(conn, enrollment_id, user_id)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select r2_key, storage_state
                            from fm_biometric_enrollment_photos
                            where enrollment_id = %s and angle = %s
                            """,
                            (enrollment_id, angle),
                        )
                        old = await cur.fetchone()
                        if old and old["storage_state"] == "delete_pending":
                            raise _err(
                                "photo_cleanup_pending",
                                "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                                status=409,
                            )
                        old_key = old["r2_key"] if old else None
                        if old_key and old_key != new_key:
                            await cur.execute(
                                """
                                insert into fm_biometric_enrollment_photo_cleanup
                                    (enrollment_id, angle, r2_key, reason)
                                values (%s, %s, %s, 'superseded')
                                on conflict (enrollment_id, r2_key) do update
                                set reason = 'superseded'
                                """,
                                (enrollment_id, angle, old_key),
                            )
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photos
                                (enrollment_id, angle, r2_key, image_digest, mime_type, byte_size)
                            values (%s, %s, %s, %s, %s, %s)
                            on conflict (enrollment_id, angle) do update set
                                r2_key = excluded.r2_key,
                                image_digest = excluded.image_digest,
                                mime_type = excluded.mime_type,
                                byte_size = excluded.byte_size,
                                qc_status = 'passed',
                                storage_state = 'quarantine',
                                uploaded_at = now(),
                                approved_at = null
                            returning uploaded_at
                            """,
                            (
                                enrollment_id,
                                angle,
                                new_key,
                                sha256_sri(data),
                                mime,
                                len(data),
                            ),
                        )
                        uploaded_at = (await cur.fetchone())["uploaded_at"]
                        await cur.execute(
                            """
                            delete from fm_biometric_enrollment_photo_cleanup
                            where enrollment_id = %s and r2_key = %s
                            """,
                            (enrollment_id, new_key),
                        )
                        await cur.execute(
                            """
                            select count(*) as passed_count
                            from fm_biometric_enrollment_photos
                            where enrollment_id = %s and qc_status = 'passed'
                              and storage_state = 'quarantine'
                            """,
                            (enrollment_id,),
                        )
                        if (await cur.fetchone())["passed_count"] == len(ANGLES):
                            await cur.execute(
                                """
                                update fm_biometric_enrollments set status = 'liveness_pending'
                                where id = %s and user_id = %s and status = 'photos_pending'
                                """,
                                (enrollment_id, user_id),
                            )
                    await conn.commit()
                    intent_committed = False
                finally:
                    await _unlock_photo_fence(conn, enrollment_id)
        except Exception as db_error:
            if intent_committed:
                await _drain_photo_cleanup(
                    request.app, enrollment_id=enrollment_id, key=new_key
                )
            if isinstance(db_error, HTTPException):
                raise
            logger.warning(
                "facemarket_enrollment_photo_metadata_store_failed",
                extra={
                    "enrollment_id": enrollment_id,
                    "angle": angle,
                    "error_type": type(db_error).__name__,
                },
            )
            raise _err(
                "db_unavailable",
                "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
                status=503,
            )

        await _drain_photo_cleanup(
            request.app,
            enrollment_id=enrollment_id,
            angle=angle,
            reason="superseded",
        )
        return EnrollmentPhotoView(angle=angle, qc_status="passed", uploaded_at=uploaded_at)
    finally:
        data = b""


@router.post("/enrollments/{enrollment_id}/liveness-session", status_code=201)
async def start_enrollment_liveness(
    request: Request,
    enrollment_id: str,
    body: LivenessSessionBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    nonce = body.nonce.strip()
    nonce_bytes = nonce.encode()
    if not 32 <= len(nonce_bytes) <= 512:
        raise _err("invalid_nonce", "인증 세션을 시작할 수 없습니다.")
    nonce_digest = hashlib.sha256(nonce_bytes).hexdigest()

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.status, e.cooldown_until, e.liveness_nonce_digest,
                       e.liveness_session_digest
                from fm_biometric_enrollments e
                where e.id = %s and e.user_id = %s
                for update
                """,
                (enrollment_id, user_id),
            )
            enrollment = await cur.fetchone()
            if enrollment is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if (
                enrollment["status"] != "liveness_pending"
                or enrollment.get("liveness_session_digest") is not None
            ):
                raise _err(
                    "invalid_enrollment_state",
                    "현재 등록 단계에서는 인증 세션을 시작할 수 없습니다.",
                    status=409,
                )
            cooldown_until = enrollment.get("cooldown_until")
            if cooldown_until is not None and cooldown_until > datetime.now(timezone.utc):
                raise _err(
                    "liveness_cooldown",
                    "잠시 후 생체 인증을 다시 시도해 주세요.",
                    status=429,
                )
            if enrollment.get("liveness_nonce_digest") == nonce_digest:
                raise _err(
                    "nonce_replayed",
                    "새 인증 세션으로 다시 시도해 주세요.",
                    status=409,
                )
            await cur.execute(
                """
                select exists(
                    select 1 from fm_biometric_enrollments
                    where liveness_nonce_digest = %s
                ) as replayed
                """,
                (nonce_digest,),
            )
            if (await cur.fetchone())["replayed"]:
                raise _err(
                    "nonce_replayed",
                    "새 인증 세션으로 다시 시도해 주세요.",
                    status=409,
                )
            await cur.execute(
                """
                select count(*) as passed_count
                from fm_biometric_enrollment_photos
                where enrollment_id = %s and qc_status = 'passed'
                  and storage_state = 'quarantine'
                  and angle in ('front', 'angle45', 'side')
                """,
                (enrollment_id,),
            )
            if (await cur.fetchone())["passed_count"] != len(ANGLES):
                raise _err(
                    "photos_required",
                    "정면, 45도, 측면 사진을 모두 등록해 주세요.",
                    status=409,
                )
            await cur.execute(
                """
                update fm_biometric_enrollments
                set liveness_nonce_digest = %s
                where id = %s and user_id = %s and status = 'liveness_pending'
                """,
                (nonce_digest, enrollment_id, user_id),
            )

        try:
            session_id = await asyncio.to_thread(
                create_liveness_session,
                request.app.state.fm_rekognition,
                client_request_token=nonce_digest,
            )
            credentials = await asyncio.to_thread(
                assume_liveness_browser_credentials,
                request.app.state.fm_sts,
                role_arn=request.app.state.settings.fm_liveness_browser_role_arn,
                session_name=f"fm-live-{enrollment_id.replace('-', '')[:12]}",
            )
        except Exception as exc:
            await conn.commit()
            logger.warning(
                "facemarket_liveness_provider_unavailable",
                extra={"provider": "aws_liveness", "error_type": type(exc).__name__},
            )
            raise _err(
                "liveness_unavailable",
                "생체 인증을 지금 시작할 수 없습니다. "
                "잠시 후 다시 시도해 주세요.",
                status=503,
            )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set liveness_session_digest = %s,
                    provider_versions = provider_versions
                      || jsonb_build_object('faceLiveness', 'aws-rekognition-us-east-1')
                where id = %s and user_id = %s and status = 'liveness_pending'
                  and liveness_nonce_digest = %s
                """,
                (
                    hashlib.sha256(session_id.encode()).hexdigest(),
                    enrollment_id,
                    user_id,
                    nonce_digest,
                ),
            )
        await conn.commit()

    return {
        "sessionId": session_id,
        "region": "us-east-1",
        "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
        "credentials": credentials,
    }


@router.delete("/enrollments/{enrollment_id}/photos/{angle}", status_code=204)
async def delete_enrollment_photo(
    request: Request,
    enrollment_id: str,
    angle: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    if angle not in ANGLES:
        raise _err("invalid_angle", "사진 각도를 확인해 주세요.")
    r2 = _r2_face(request)
    try:
        async with get_conn(request) as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                raise _err(
                    "photo_cleanup_pending",
                    "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                    status=409,
                )
            try:
                await _lock_photo_mutation_enrollment(conn, enrollment_id, user_id)
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        select r2_key, storage_state
                        from fm_biometric_enrollment_photos
                        where enrollment_id = %s and angle = %s
                        """,
                        (enrollment_id, angle),
                    )
                    photo = await cur.fetchone()
                    if photo is not None:
                        if photo["storage_state"] not in {
                            "quarantine",
                            "delete_pending",
                        }:
                            raise _err(
                                "invalid_enrollment_state",
                                "현재 등록 단계에서는 사진을 변경할 수 없습니다.",
                                status=409,
                            )
                        await cur.execute(
                            """
                            update fm_biometric_enrollment_photos
                            set storage_state = 'delete_pending'
                            where enrollment_id = %s and angle = %s and r2_key = %s
                            """,
                            (enrollment_id, angle, photo["r2_key"]),
                        )
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photo_cleanup
                                (enrollment_id, angle, r2_key, reason)
                            values (%s, %s, %s, 'delete')
                            on conflict (enrollment_id, r2_key) do update
                            set reason = 'delete'
                            """,
                            (enrollment_id, angle, photo["r2_key"]),
                        )
                        await cur.execute(
                            """
                            update fm_biometric_enrollments set status = 'photos_pending'
                            where id = %s and user_id = %s
                              and status = 'liveness_pending'
                              and liveness_session_digest is null
                            """,
                            (enrollment_id, user_id),
                        )
                await conn.commit()
                _, failed_count = await _drain_photo_cleanup_locked(
                    conn,
                    r2,
                    enrollment_id=enrollment_id,
                    angle=angle,
                )
                if failed_count:
                    raise _err(
                        "storage_unavailable",
                        "얼굴 저장소를 사용할 수 없습니다.",
                        status=503,
                    )
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_photo_delete_prepare_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        raise _err(
            "db_unavailable",
            "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
            status=503,
        )
    return Response(status_code=204)


async def cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return False

    try:
        enrollment_id = _canonical_enrollment_id(enrollment_id)
        async with pool.connection() as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                return False
            try:
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select e.status, p.angle, p.r2_key, p.storage_state
                            from fm_biometric_enrollments e
                            left join fm_biometric_enrollment_photos p
                              on p.enrollment_id = e.id
                             and p.storage_state in ('quarantine', 'delete_pending')
                            where e.id = %s
                              and e.status in ('failed', 'cancelled', 'expired')
                            for update of e
                            """,
                            (enrollment_id,),
                        )
                        rows = await cur.fetchall()
                        if not rows:
                            return False
                        for row in rows:
                            if row.get("r2_key") is None:
                                continue
                            await cur.execute(
                                """
                                update fm_biometric_enrollment_photos
                                set storage_state = 'delete_pending'
                                where enrollment_id = %s and angle = %s and r2_key = %s
                                """,
                                (enrollment_id, row["angle"], row["r2_key"]),
                            )
                            await cur.execute(
                                """
                                insert into fm_biometric_enrollment_photo_cleanup
                                    (enrollment_id, angle, r2_key, reason)
                                values (%s, %s, %s, 'delete')
                                on conflict (enrollment_id, r2_key) do update
                                set reason = 'delete'
                                """,
                                (enrollment_id, row["angle"], row["r2_key"]),
                            )
                    await conn.commit()
                except Exception as exc:
                    await conn.rollback()
                    logger.warning(
                        "facemarket_enrollment_cleanup_prepare_failed",
                        extra={
                            "enrollment_id": enrollment_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return False

                deleted_count, failed_count = await _drain_photo_cleanup_locked(
                    conn,
                    r2,
                    enrollment_id=enrollment_id,
                )
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select (
                                select count(*) from fm_biometric_enrollment_photos
                                where enrollment_id = %s
                                  and storage_state in ('quarantine', 'delete_pending')
                            ) + (
                                select count(*) from fm_biometric_enrollment_photo_cleanup
                                where enrollment_id = %s
                            ) as remaining
                            """,
                            (enrollment_id, enrollment_id),
                        )
                        remaining = (await cur.fetchone())["remaining"]
                        await cur.execute(
                            """
                            update fm_biometric_enrollments
                            set raw_deletion_evidence = coalesce(raw_deletion_evidence, '{}'::jsonb)
                                || jsonb_build_object(
                                    'quarantineDeleted', %s,
                                    'quarantineDeletedCount',
                                        coalesce((raw_deletion_evidence->>'quarantineDeletedCount')::int, 0) + %s,
                                    'quarantineDeleteFailedCount',
                                        coalesce((raw_deletion_evidence->>'quarantineDeleteFailedCount')::int, 0) + %s,
                                    'quarantineCleanupAt', now()
                                )
                            where id = %s and status in ('failed', 'cancelled', 'expired')
                            """,
                            (remaining == 0, deleted_count, failed_count, enrollment_id),
                        )
                    await conn.commit()
                except Exception as exc:
                    await conn.rollback()
                    logger.warning(
                        "facemarket_enrollment_cleanup_evidence_failed",
                        extra={
                            "enrollment_id": enrollment_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return False
                return remaining == 0
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cleanup_prepare_failed",
            extra={
                "enrollment_id": enrollment_id,
                "error_type": type(exc).__name__,
            },
        )
        return False


async def sweep_terminal_enrollments(app, *, limit: int = 100) -> int:
    pool = getattr(app.state, "pool", None)
    if pool is None:
        return 0
    limit = max(1, min(int(limit), 100))
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    with due as (
                        select id from fm_biometric_enrollments
                        where expires_at <= now()
                          and status in ('photos_pending', 'liveness_pending', 'processing')
                        order by expires_at
                        for update skip locked
                        limit %s
                    )
                    update fm_biometric_enrollments e
                    set status='expired', decision='failed',
                        reason='enrollment_expired', completed_at=now()
                    from due where e.id=due.id
                    returning e.id::text as id
                    """,
                    (limit,),
                )
                await cur.fetchall()
            await conn.commit()

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.status in ('failed', 'cancelled', 'expired')
                      and coalesce(
                            (e.raw_deletion_evidence->>'quarantineDeleted')::boolean,
                            false
                          ) is not true
                    order by e.completed_at nulls first, e.created_at
                    for update skip locked
                    limit %s
                    """,
                    (limit,),
                )
                candidates = await cur.fetchall()
            await conn.commit()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.status = 'license_pending'
                      and exists (
                          select 1
                          from fm_biometric_enrollment_photo_cleanup c
                          where c.enrollment_id = e.id
                            and c.not_before <= now()
                      )
                    order by e.id
                    for update skip locked
                    limit %s
                    """,
                    (limit,),
                )
                cleanup_candidates = await cur.fetchall()
            await conn.commit()
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cleanup_sweep_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0

    cleaned = 0
    for row in candidates:
        cleaned += int(
            await cleanup_terminal_enrollment(app, enrollment_id=row["id"])
        )
    for row in cleanup_candidates:
        deleted, failed = await _drain_photo_cleanup(
            app, enrollment_id=row["id"], reason="delete"
        )
        cleaned += int(deleted > 0 and failed == 0)
    cleaned += await _drain_model_asset_cleanup(app, limit=limit)
    return cleaned


def _decision_body(decision: EnrollmentDecision) -> dict:
    body = {
        "passed": decision.passed,
        "retryable": decision.retryable,
        "reason": decision.reason,
        "status": decision.status,
    }
    if decision.model_id is not None:
        body["modelId"] = decision.model_id
    return body


def _assert_match(score: float, threshold: float) -> None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        raise EnrollmentMappedError("face_match_failed") from None
    if not math.isfinite(value) or value < threshold:
        raise EnrollmentMappedError("face_match_failed")


async def record_raw_release_evidence(
    request: Request, enrollment_id: str, **evidence: bool
) -> None:
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set raw_deletion_evidence = coalesce(raw_deletion_evidence, '{}'::jsonb)
                  || jsonb_build_object(
                    'oacxPortraitReleased', %s,
                    'livenessReferenceReleased', %s,
                    'temporaryEmbeddingsReleased', %s
                  )
                where id = %s
                """,
                (
                    evidence.get("oacx_portrait_released", False),
                    evidence.get("liveness_reference_released", False),
                    evidence.get("temporary_embeddings_released", False),
                    enrollment_id,
                ),
            )
        await conn.commit()


async def _fail_enrollment(
    request: Request,
    *,
    enrollment_id: str,
    reason: str,
    retryable: bool,
) -> EnrollmentDecision:
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            cooldown_until = None
            if not retryable:
                await cur.execute(
                    """
                    select count(*) as recent_failures
                    from fm_biometric_enrollments current
                    join fm_biometric_enrollments prior
                      on (prior.user_id = current.user_id
                          or prior.device_digest = current.device_digest)
                    where current.id = %s
                      and prior.status = 'failed'
                      and prior.completed_at >= now() - interval '3 minutes'
                      and prior.reason in ('minor_blocked', 'liveness_failed',
                                           'face_match_failed', 'identity_replay',
                                           'identity_recovery_required')
                    """,
                    (enrollment_id,),
                )
                recent = int((await cur.fetchone() or {}).get("recent_failures") or 0)
                if recent + 1 >= 5:
                    cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=45)
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'failed', decision = 'failed', reason = %s,
                    completed_at = coalesce(completed_at, now()),
                    cooldown_until = coalesce(%s, cooldown_until)
                where id = %s
                """,
                (reason, cooldown_until, enrollment_id),
            )
        await conn.commit()
    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    return EnrollmentDecision(False, retryable, reason, "failed")


async def _initial_completion_checks(
    request: Request, *, enrollment_id: str, user_id: str, session_id: str, token: str
) -> tuple[dict, list[dict]]:
    token_digest = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.id::text as id, e.user_id::text as user_id,
                       e.model_id::text as model_id, e.status, e.cooldown_until,
                       e.expires_at, e.liveness_session_digest, e.device_digest
                from fm_biometric_enrollments e
                where e.id = %s and e.user_id = %s
                for update
                """,
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if row["status"] != "liveness_pending":
                raise _err(
                    "invalid_enrollment_state",
                    "현재 등록 단계에서는 인증을 완료할 수 없습니다.",
                    status=409,
                )
            if row.get("cooldown_until") and row["cooldown_until"] > datetime.now(timezone.utc):
                raise _err("liveness_cooldown", "잠시 후 다시 시도해 주세요.", status=429)
            if row["expires_at"] <= datetime.now(timezone.utc):
                await cur.execute(
                    """
                    update fm_biometric_enrollments
                    set status = 'expired', decision = 'failed',
                        reason = 'enrollment_expired',
                        completed_at = coalesce(completed_at, now())
                    where id = %s
                    """,
                    (enrollment_id,),
                )
                await conn.commit()
                raise EnrollmentExpiredError
            if row["liveness_session_digest"] != hashlib.sha256(session_id.encode()).hexdigest():
                await conn.commit()
                raise EnrollmentMappedError("liveness_retry")
            await cur.execute(
                """
                select exists(
                  select 1 from fm_identity_verifications
                  where cx_tx_id = %s and cx_tx_id_format = 'sha256-v1'
                  union all
                  select 1 from fm_biometric_enrollments
                  where oacx_tx_digest = %s
                ) as replayed
                """,
                (token_digest, token_digest),
            )
            if (await cur.fetchone())["replayed"]:
                await conn.commit()
                raise EnrollmentMappedError("identity_replay")
            await cur.execute(
                """
                select angle, r2_key, mime_type
                from fm_biometric_enrollment_photos
                where enrollment_id = %s and qc_status = 'passed'
                  and storage_state = 'quarantine'
                  and angle in ('front', 'angle45', 'side')
                order by case angle when 'front' then 1 when 'angle45' then 2 else 3 end
                """,
                (enrollment_id,),
            )
            photos = await cur.fetchall()
            if [row["angle"] for row in photos] != list(ANGLES):
                raise _err("photos_required", "사진 세 장이 필요합니다.", status=409)
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'processing'
                where id = %s and user_id = %s and status = 'liveness_pending'
                """,
                (enrollment_id, user_id),
            )
        await conn.commit()
    return row, photos


async def process_enrollment_completion(
    request: Request,
    *,
    enrollment_id: str,
    user_id: str,
    session_id: str,
    token: str,
) -> EnrollmentDecision:
    settings = request.app.state.settings
    liveness = None
    evidence = None
    photo_buffers: list[bytearray] = []
    try:
        row, photos = await _initial_completion_checks(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
            token=token,
        )
        try:
            liveness = await asyncio.to_thread(
                get_liveness_result,
                request.app.state.fm_rekognition,
                session_id=session_id,
                minimum_confidence=settings.fm_liveness_confidence_threshold,
            )
        except BiometricProviderError as exc:
            raise EnrollmentMappedError(exc.reason) from None

        try:
            trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
            evidence = cx_identity.parse_oacx_biometric_evidence(
                trans,
                contract=cx_identity.get_oacx_biometric_contract(settings),
            )
        except EnrollmentMappedError:
            raise
        except cx_identity.OacxBiometricError as exc:
            raise EnrollmentMappedError(exc.reason) from None
        except Exception:
            raise EnrollmentMappedError("id_portrait_unavailable") from None

        r2 = _r2_face(request)
        for photo in photos:
            try:
                photo_buffers.append(bytearray(await asyncio.to_thread(r2.get_bytes, photo["r2_key"])))
            except Exception:
                raise EnrollmentMappedError("id_portrait_unavailable") from None

        try:
            qc = load_face_qc(settings, required=True)
            _assert_match(
                qc.one_to_one_similarity(evidence.portrait, liveness.reference_image),
                settings.fm_id_live_threshold,
            )
            for buffer in photo_buffers:
                _assert_match(
                    qc.one_to_one_similarity(buffer, liveness.reference_image),
                    settings.fm_retouched_live_threshold,
                )
        except EnrollmentMappedError:
            raise
        except QcFailed as exc:
            reason = exc.reason if exc.reason == "qc_unavailable" else "face_match_failed"
            raise EnrollmentMappedError(reason) from None
        except Exception:
            raise EnrollmentMappedError("qc_unavailable") from None

        token_digest = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"
        ci_hash = hmac.new(
            settings.fm_ci_pepper.encode(), evidence.ci, hashlib.sha256
        ).hexdigest()
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.id = %s and e.user_id = %s
                      and e.status = 'processing'
                      and e.liveness_session_digest = %s
                    for update
                    """,
                    (
                        enrollment_id,
                        user_id,
                        hashlib.sha256(session_id.encode()).hexdigest(),
                    ),
                )
                if await cur.fetchone() is None:
                    raise _err(
                        "invalid_enrollment_state",
                        "현재 등록 단계에서는 인증을 완료할 수 없습니다.",
                        status=409,
                    )
                await cur.execute(
                    "select id::text as id, user_id::text as user_id from fm_models where ci_hash = %s for update",
                    (ci_hash,),
                )
                model = await cur.fetchone()
                if model and model["user_id"] != user_id:
                    raise EnrollmentMappedError("identity_recovery_required")
                if model:
                    model_id = model["id"]
                elif row.get("model_id"):
                    model_id = row["model_id"]
                    await cur.execute(
                        """
                        update fm_models
                        set ci_hash = %s, display_name = %s, user_id = %s
                        where id = %s
                        """,
                        (ci_hash, evidence.name_masked, user_id, model_id),
                    )
                else:
                    await cur.execute(
                        """
                        insert into fm_models (user_id, display_name, status, ci_hash)
                        values (%s, %s, 'pending', %s)
                        returning id::text as id
                        """,
                        (user_id, evidence.name_masked, ci_hash),
                    )
                    model_id = (await cur.fetchone())["id"]
                try:
                    await cur.execute(
                        """
                        insert into fm_identity_verifications
                            (model_id, cx_tx_id, cx_tx_id_format, fields)
                        values (%s, %s, 'sha256-v1', %s)
                        """,
                        (
                            model_id,
                            token_digest,
                            Json({
                                "nameMasked": evidence.name_masked,
                                "birthYear": evidence.birth[:4],
                                "biometric": True,
                            }),
                        ),
                    )
                except UniqueViolation:
                    raise EnrollmentMappedError("identity_replay")
                await cur.execute(
                    """
                    update fm_models
                    set assets_status = 'building', current_enrollment_id = %s
                    where id = %s
                    """,
                    (enrollment_id, model_id),
                )
                await cur.execute(
                    """
                    update fm_biometric_enrollments
                    set model_id = %s, status = 'asset_building', decision = 'passed',
                        reason = null, completed_at = now(), oacx_tx_digest = %s,
                        match_policy_version = %s,
                        provider_versions = provider_versions || %s::jsonb
                    where id = %s
                    """,
                    (
                        model_id,
                        token_digest,
                        settings.fm_match_policy_version,
                        Json({
                            "faceLiveness": liveness.provider_version,
                            "oacx": evidence.contract_version,
                            "faceMatch": "sface-one-to-one",
                        }),
                        enrollment_id,
                    ),
                )
                await cur.execute(
                    """
                    insert into jobs (user_id, project_id, kind, status, payload, credits_reserved, metadata)
                    values (%s, null, 'fm_model_asset_build', 'pending', %s, 0, '{}'::jsonb)
                    """,
                    (
                        user_id,
                        Json({"modelId": model_id, "enrollmentId": enrollment_id}),
                    ),
                )
            await conn.commit()
        return EnrollmentDecision(True, False, None, "asset_building", model_id)
    except EnrollmentExpiredError:
        await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
        return EnrollmentDecision(False, False, "enrollment_expired", "expired")
    except EnrollmentMappedError as exc:
        return await _fail_enrollment(
            request,
            enrollment_id=enrollment_id,
            reason=exc.reason,
            retryable=exc.reason in RETRYABLE_REASONS,
        )
    finally:
        if evidence is not None:
            cx_identity.wipe_bytearray(evidence.ci)
            cx_identity.wipe_bytearray(evidence.portrait)
        if liveness is not None:
            cx_identity.wipe_bytearray(liveness.reference_image)
        for buffer in photo_buffers:
            cx_identity.wipe_bytearray(buffer)
        photo_buffers.clear()
        release_task = asyncio.create_task(
            record_raw_release_evidence(
                request,
                enrollment_id,
                oacx_portrait_released=True,
                liveness_reference_released=True,
                temporary_embeddings_released=True,
            )
        )
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            await release_task
            raise


@router.post("/enrollments/{enrollment_id}/complete")
async def complete_enrollment(
    request: Request,
    enrollment_id: str,
    body: CompleteEnrollmentBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    try:
        session_id = str(uuid.UUID(str(body.session_id)))
    except (AttributeError, TypeError, ValueError):
        raise _err("invalid_liveness_session", "인증 세션을 확인할 수 없습니다.")
    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")
    try:
        decision = await process_enrollment_completion(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
            token=token,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_complete_failed",
            extra={"enrollment_id": enrollment_id, "error_type": type(exc).__name__},
        )
        raise _err("enrollment_unavailable", "등록을 완료할 수 없습니다.", status=503)
    if decision.passed:
        _wake_dispatcher(request)
    return JSONResponse(
        status_code=202 if decision.passed else 200,
        content=_decision_body(decision),
    )


@router.post("/enrollments/{enrollment_id}/cancel", response_model=EnrollmentView)
async def cancel_enrollment(
    request: Request,
    enrollment_id: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    try:
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update fm_biometric_enrollments e
                    set status = 'cancelled', completed_at = coalesce(completed_at, now())
                    where e.id = %s and e.user_id = %s and e.status in (
                        'photos_pending', 'liveness_pending', 'processing', 'asset_building',
                        'license_pending', 'vc_pending', 'cancelled'
                    )
                    returning e.id::text as id
                    """,
                    (enrollment_id, user_id),
                )
                if await cur.fetchone() is None:
                    raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            await conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cancel_commit_failed",
            extra={
                "enrollment_id": enrollment_id,
                "error_type": type(exc).__name__,
            },
        )
        raise _err(
            "db_unavailable",
            "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
            status=503,
        )

    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    async with get_conn(request) as conn:
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


def validate_biometric_settings(settings: Settings) -> None:
    if not settings.fm_biometric_enrollment_enabled:
        return
    if not settings.facemarket_enabled:
        raise RuntimeError("FACEMARKET_ENABLED is required for biometric enrollment")
    if not settings.opendid_holder_url:
        raise RuntimeError("OPENDID_HOLDER_URL is required for biometric enrollment")
    if settings.fm_liveness_region != "us-east-1":
        raise RuntimeError("Face Liveness region must be us-east-1")
    if not settings.fm_liveness_browser_role_arn:
        raise RuntimeError("FM_LIVENESS_BROWSER_ROLE_ARN is required")
    if not settings.fm_face_qc_enabled:
        raise RuntimeError("FM_FACE_QC_ENABLED is required")
    thresholds = (
        settings.fm_liveness_confidence_threshold,
        settings.fm_id_live_threshold,
        settings.fm_retouched_live_threshold,
        settings.fm_match_policy_version,
    )
    if any(value is None for value in thresholds):
        raise RuntimeError("calibrated biometric thresholds and policy version are required")
    if settings.fm_oacx_contract_mode == "dev-mock-v1" and settings.app_env != "dev":
        raise RuntimeError("verified OACX biometric contract is required outside dev")
    if settings.fm_oacx_contract_mode != "dev-mock-v1":
        raise RuntimeError("verified OACX biometric contract is required")


def build_biometric_aws_clients(settings: Settings):
    rekognition = boto3.client(
        "rekognition", region_name="us-east-1", config=AWS_LIVENESS_CONFIG
    )
    sts = boto3.client("sts", region_name="us-east-1", config=AWS_LIVENESS_CONFIG)
    return rekognition, sts
