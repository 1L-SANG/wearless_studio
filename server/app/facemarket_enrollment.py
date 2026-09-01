"""Fail-closed FaceMarket biometric enrollment and quarantine lifecycle."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from . import cx_identity, repo
from .agents.face_qc import QcFailed, load_face_qc, weight_paths
from .auth import require_user
from .config import Settings
from .db import get_conn
from .models import CamelModel
from .personalization_qc import FaceQcUnavailable, evaluate_face_qc, qc_reason_message
from .r2 import enrollment_quarantine_key, ext_for_mime, sha256_sri

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket biometric enrollment"])

BIOMETRIC_CONSENT_VERSION = "2026-08-v2"
# 동의문 텍스트를 바꾸면 버전을 올린다. 프론트(Vercel)·백엔드(CI) 배포 시점이 어긋나는
# 동안 stale_consent_version 400 으로 등록이 막히지 않게, 직전 버전도 함께 수락한다.
ACCEPTED_CONSENT_VERSIONS = ("2026-08-v2", "2026-08-v1")
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
    # 라이브니스 off 면 세션이 없으므로 optional. 실제 요구는 라우트가 flag 로 강제한다.
    session_id: str | None = None
    # Task3: 신분증(CI) 검증은 앞단 /identity 가 전담한다 — /complete 는 SFace 매칭만 하고
    # OACX token 을 더 이상 받지 않는다(저장된 identity_* 증거를 읽어 모델을 바인딩).
    # D1: OACX RESULT-step 신분증 초상(`data.dlphotoimage`, HEX JPEG) — 프론트가 위젯 콜백에서
    # 그대로 릴레이한다. 스키마 레벨에서는 optional(구버전 클라·계약 모드 무관하게 요청 자체는
    # 받아준다) — 실제 요구 여부는 process_enrollment_completion 이 fail-closed 로 강제한다.
    id_photo_hex: str | None = None


class IdentityVerifyBody(CamelModel):
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
    height_bucket: str | None = None
    body_type: str | None = None
    gender: str | None = None


class PhysiqueBody(CamelModel):
    height_bucket: str | None = None
    body_type: str | None = None


def _err(code: str, message: str, status: int = 400, **extra) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **extra},
    )


async def _assert_account_open(conn, user_id: str) -> None:
    if await repo.user_account_purge_closed(conn, user_id):
        raise _err("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", status=404)


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


def match_threshold_for_angle(settings: Settings, angle: str) -> float | None:
    """각도별 매칭 임계. 측면만 따로 둔다.

    정면 얼굴 인식기(YuNet 검출 + SFace)는 측면에서 유사도가 구조적으로 낮게 나온다.
    prod 실측(2026-09-01): 같은 사람 사진인데 front 0.1806 / angle45 0.2605 / side 0.14825 —
    측면만 공통 임계 0.15 에 0.0017 모자라 등록 전체가 face_match_failed 로 날아갔다(3회 반복,
    매칭이 결정적이라 같은 사진은 늘 같은 점수였다). 측면 전용 임계가 없으면 기존 값을 쓴다.
    """
    if angle == "side" and settings.fm_side_live_threshold is not None:
        return settings.fm_side_live_threshold
    return settings.fm_retouched_live_threshold


def _prewarm_opendid(request: Request) -> None:
    """VC 발급이 사실상 확정된 지점에서 holder(opendid)를 미리 깨운다.

    holder 는 scale-to-zero 라 콜드부트가 ~2분(4 JVM)이다. 발급 버튼에서 처음 깨우면
    사용자가 그 2분을 그대로 기다린다(#201 의 재시도 진행표시가 버텨줄 뿐이다). 라이브니스
    시작·등록 완료는 발급까지 몇 분 남은 가장 이른 확실한 신호라, 여기서 켜 두면 부팅이
    등록 뒷단계에 가려진다. 실패·중복은 무해 — reconciler 가 60초 안에 덮고, 오토스케일이
    off 면 prewarm_soon 이 즉시 return 한다."""
    scaler = getattr(request.app.state, "opendid_autoscaler", None)
    if scaler is not None:
        with contextlib.suppress(Exception):   # 훅은 등록 응답을 절대 막지 않는다
            scaler.prewarm_soon()


async def _load_owned_enrollment(conn, enrollment_id: str, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at,
                   e.liveness_session_digest, e.height_bucket, e.body_type,
                   m.gender as model_gender
            from fm_biometric_enrollments e
            left join fm_models m on m.id = e.model_id
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


async def _reject_cutover_closed(conn) -> None:
    await repo.lock_facemarket_writer_boundary(conn)
    if await repo.facemarket_writer_boundary_closed(conn):
        raise _err(
            "facemarket_cutover_in_progress",
            "실물 모델 보안 전환 중이라 잠시 후 다시 시도해 주세요.",
            status=409,
        )


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
                   e.decision, e.reason, e.cooldown_until, e.expires_at,
                   e.height_bucket, e.body_type, m.gender as model_gender
            from fm_biometric_enrollments e
            left join fm_models m on m.id = e.model_id
            where e.user_id = %s and e.status in (
                'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
                'asset_building', 'license_pending', 'vc_pending'
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
        height_bucket=row.get("height_bucket"),
        body_type=row.get("body_type"),
        gender=row.get("model_gender"),
    )


@router.get("/config")
async def facemarket_config(request: Request):
    """등록 위저드 런타임 설정 — 프론트가 라이브니스 단계를 렌더할지 판정한다(서버 authoritative).

    인증 불필요(민감정보 없음, boolean 플래그 하나). livenessRequired=false 면 프론트는
    라이브니스 세션/위젯을 건너뛰고 사진 → 완료로 직행한다.
    """
    settings: Settings = request.app.state.settings
    return {"livenessRequired": settings.fm_liveness_enabled}


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
    if consent.document_version not in ACCEPTED_CONSENT_VERSIONS:
        raise _err("stale_consent_version", "최신 생체정보 처리 동의를 확인해 주세요.")
    device_digest = hashlib.sha256(device_id.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + ENROLLMENT_TTL
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
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
                    'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
                    'asset_building', 'license_pending', 'vc_pending'
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
                        'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending'
                    ) order by created_at desc limit 1
                    """,
                    (user_id,),
                )
                enrollment_id = (await cur.fetchone())["id"]
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


@router.post("/enrollments/{enrollment_id}/identity", response_model=EnrollmentView)
async def verify_enrollment_identity(
    request: Request,
    enrollment_id: str,
    body: IdentityVerifyBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")
    settings: Settings = request.app.state.settings
    token_digest = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"
    contract = cx_identity.get_oacx_biometric_contract(settings)
    try:
        # CI·이름·생년월일은 trans/{token}(서버발 조회)에서만 온다 — 서버검증 완료.
        trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
        evidence = cx_identity.parse_oacx_biometric_evidence(trans, contract=contract)
    except cx_identity.OacxBiometricError as exc:
        raise _err(exc.reason, "본인확인에 실패했어요. 다시 시도해 주세요.")
    except cx_identity.CxIdentityError:
        raise _err("id_portrait_unavailable", "신분증 확인에 실패했어요. 다시 시도해 주세요.")
    try:
        # 원시 CI 는 HMAC(ci_hash)만 장기저장하고 raw 는 즉시 폐기한다.
        ci_hash = hmac.new(
            settings.fm_ci_pepper.encode(), evidence.ci, hashlib.sha256
        ).hexdigest()
    finally:
        cx_identity.wipe_bytearray(evidence.ci)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
        async with conn.cursor() as cur:
            # 소유·상태 검사(identity_pending 만 허용)
            await cur.execute(
                "select status from fm_biometric_enrollments "
                "where id = %s and user_id = %s for update",
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if row["status"] != "identity_pending":
                raise _err(
                    "invalid_enrollment_state",
                    "이미 본인확인이 완료됐거나 진행할 수 없는 상태입니다.",
                    status=409,
                )
            # replay(토큰 재사용) 차단
            await cur.execute(
                """
                select exists(
                  select 1 from fm_identity_verifications
                  where cx_tx_id = %s and cx_tx_id_format = 'sha256-v1'
                  union all
                  select 1 from fm_biometric_enrollments where identity_tx_digest = %s
                ) as replayed
                """,
                (token_digest, token_digest),
            )
            if (await cur.fetchone())["replayed"]:
                await conn.commit()
                raise _err("identity_replay", "이미 사용된 인증입니다. 새로 시작해 주세요.")
            # 교차유저 CI 충돌(다른 유저 모델이면 소유권 확인 필요)
            await cur.execute(
                "select user_id::text as user_id from fm_models where ci_hash = %s",
                (ci_hash,),
            )
            owner = await cur.fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise _err("identity_recovery_required", "기존 모델 소유권 확인이 필요해요.")
            # 증거 저장 + 상태 전이
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'photos_pending',
                    identity_ci_hash = %s, identity_name_masked = %s,
                    identity_birth_year = %s, identity_tx_digest = %s,
                    identity_contract_version = %s
                where id = %s and user_id = %s and status = 'identity_pending'
                """,
                (ci_hash, evidence.name_masked, evidence.birth[:4], token_digest,
                 evidence.contract_version, enrollment_id, user_id),
            )
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
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
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
            # 차단 사유만 노출(angle_mismatch advisory 는 여기 도달 못하지만, 혼재 시에도
            # 거절 카피에 각도 안내가 섞이지 않게 blocking_reasons 로 한정한다).
            raise _err(
                "face_quality",
                qc_reason_message(qc.blocking_reasons),
                reasons=qc.blocking_reasons,
            )
        ext = ext_for_mime(mime)
        new_key = enrollment_quarantine_key(
            enrollment_id, angle, ext, version=uuid.uuid4().hex
        )
        old_key = None
        intent_committed = False
        try:
            async with get_conn(request) as conn:
                await _assert_account_open(conn, user_id)
                row = await _load_owned_enrollment(conn, enrollment_id, user_id)
                _validate_photo_mutation_enrollment(row)
                await _reject_cutover_closed(conn)
                await conn.commit()
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
                    await _assert_account_open(conn, user_id)
                    await _reject_cutover_closed(conn)
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

                    await _reject_cutover_closed(conn)
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
        # 사진을 올리기 시작했다 = 발급까지 아직 몇 분 남았다. 여기서 깨우는 게 가장 이르다.
        # prod 실측(2026-09-01): 사진 3장에 3분 26초가 걸렸고, 그동안 홀더(~2분 부팅)를 띄우면
        # 발급 시점엔 이미 따뜻하다. 라이브니스 훅은 발급까지 1분도 안 남아 부팅을 못 가렸다.
        _prewarm_opendid(request)
        return EnrollmentPhotoView(angle=angle, qc_status="passed", uploaded_at=uploaded_at)
    finally:
        data = b""


@router.post(
    "/enrollments/{enrollment_id}/profile-image",
    response_model=EnrollmentView,
    status_code=201,
)
async def upload_profile_image(
    request: Request,
    enrollment_id: str,
    image: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    # 대표이미지(cover)는 표시용일 뿐 컷 파이프라인·상태머신을 게이팅하지 않는다 —
    # SFace/QC 없음, 상태 전이 없음. 바인딩 시 fm_models.cover_image_url 로 승격만 한다.
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    mime = (image.content_type or "").lower()
    if mime not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    data = await image.read()
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
    if len(data) > MAX_FACE_BYTES:
        raise _err("file_too_large", "이미지는 25MB 이하만 가능합니다.", status=413)
    r2 = _r2_face(request)
    ext = ext_for_mime(mime)
    key = f"private/fm-profile/{enrollment_id}.{ext}"
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        r2.put_bytes(key, data, mime)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set profile_image_r2_key = %s "
                "where id = %s and user_id = %s",
                (key, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


@router.post("/enrollments/{enrollment_id}/physique", response_model=EnrollmentView)
async def set_physique(
    request: Request,
    enrollment_id: str,
    body: PhysiqueBody,
    user_id: str = Depends(require_user),
):
    # 체형·키(physique)는 표시·프롬프트 문구용 메타데이터일 뿐 컷 파이프라인·상태머신을
    # 게이팅하지 않는다 — 검증은 값 자체(enum·성별 일치)만 본다(app.facemarket_physique 단일소스).
    from .facemarket_physique import PhysiqueError, validate_physique

    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        try:
            validate_physique(
                height_bucket=body.height_bucket,
                body_type=body.body_type,
                gender=row.get("model_gender"),
            )
        except PhysiqueError as e:
            raise _err(e.code, e.message, status=400)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set height_bucket = %s, body_type = %s "
                "where id = %s and user_id = %s",
                (body.height_bucket, body.body_type, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


@router.post("/enrollments/{enrollment_id}/liveness-session", status_code=201)
async def start_enrollment_liveness(
    request: Request,
    enrollment_id: str,
    body: LivenessSessionBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    settings: Settings = request.app.state.settings
    if not settings.fm_liveness_enabled:
        # 라이브니스 off — 세션을 만들 필요가 없다(stale 프론트 방어). 프론트는 /config 로
        # livenessRequired=false 를 보고 이 호출을 건너뛰어야 한다.
        raise _err("liveness_disabled", "라이브 인증이 필요하지 않습니다.", status=409)
    nonce = body.nonce.strip()
    nonce_bytes = nonce.encode()
    if not 32 <= len(nonce_bytes) <= 512:
        raise _err("invalid_nonce", "인증 세션을 시작할 수 없습니다.")
    nonce_digest = hashlib.sha256(nonce_bytes).hexdigest()

    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
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
            # status 만 검사한다 — 이미 세션을 한 번 발급받았어도(liveness_session_digest 존재)
            # 라이브니스 에러/취소 후 재시도를 허용해 신분증·사진 재입력 없이 라이브 인증만 다시
            # 진행하게 한다. 새 nonce 라 아래 nonce 재사용 검사는 여전히 새 세션을 강제한다.
            if enrollment["status"] != "liveness_pending":
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

    # 라이브 인증에 들어왔다 = 몇 분 뒤 VC 발급이다. holder 콜드부트를 지금 시작해 둔다.
    _prewarm_opendid(request)
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
                await _assert_account_open(conn, user_id)
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
                          and status in ('identity_pending', 'photos_pending', 'liveness_pending', 'processing')
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
    expected_status: str,
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
                where id = %s and status = %s
                returning status
                """,
                (reason, cooldown_until, enrollment_id, expected_status),
            )
            updated = await cur.fetchone()
            if updated is None:
                await cur.execute(
                    "select status from fm_biometric_enrollments where id = %s",
                    (enrollment_id,),
                )
                closed = await cur.fetchone()
                await conn.commit()
                return EnrollmentDecision(
                    False,
                    retryable,
                    reason,
                    (closed or {}).get("status") or "failed",
                )
        await conn.commit()
    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    return EnrollmentDecision(False, retryable, reason, "failed")


async def _initial_completion_checks(
    request: Request, *, enrollment_id: str, user_id: str, session_id: str | None
) -> tuple[dict, list[dict]]:
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.id::text as id, e.user_id::text as user_id,
                       e.model_id::text as model_id, e.status, e.cooldown_until,
                       e.expires_at, e.liveness_session_digest, e.device_digest,
                       e.identity_ci_hash, e.identity_name_masked, e.identity_birth_year,
                       e.identity_tx_digest, e.identity_contract_version,
                       e.profile_image_r2_key, e.height_bucket, e.body_type
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
            # Task3 방어선: 신분증 게이트(/identity)를 통과한 등록만 완료할 수 있다.
            # identity_tx_digest 가 없다면 앞단 CI 증거가 저장된 적이 없다는 뜻 → 완료 불가.
            if not row.get("identity_tx_digest"):
                raise _err(
                    "invalid_enrollment_state",
                    "본인확인을 먼저 완료해 주세요.",
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
            if settings.fm_liveness_enabled and (
                row["liveness_session_digest"] != hashlib.sha256(session_id.encode()).hexdigest()
            ):
                await conn.commit()
                raise EnrollmentMappedError("liveness_retry")
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
    id_photo_hex: str | None = None,
) -> EnrollmentDecision:
    settings = request.app.state.settings
    liveness = None
    portrait: bytearray | None = None
    photo_buffers: list[bytearray] = []
    processing_started = False
    try:
        row, photos = await _initial_completion_checks(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
        )
        processing_started = True
        if settings.fm_liveness_enabled:
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
            # Task3: CI·이름·생년월일 검증은 앞단 /identity 가 이미 마쳤다(저장 컬럼을 아래에서
            # 읽는다). 여기서는 SFace 매칭에 쓸 신분증 초상만 파싱한다 — trans 재조회 없음.
            contract = cx_identity.get_oacx_biometric_contract(settings)
            # 초상은 D1부터 trans 필드가 아니라 클라가 OACX RESULT-step(`data.dlphotoimage`)
            # 콜백에서 그대로 릴레이한 HEX 다 — cx_identity.parse_oacx_portrait_hex 의
            # 모듈 docstring 에 이 릴레이의 보안 경계(client-relayed, bounded)를 기록해 두었다.
            portrait = cx_identity.parse_oacx_portrait_hex(id_photo_hex, contract=contract)
        except EnrollmentMappedError:
            raise
        except cx_identity.OacxBiometricError as exc:
            raise EnrollmentMappedError(exc.reason) from None
        except Exception:
            raise EnrollmentMappedError("id_portrait_unavailable") from None

        r2 = _r2_face(request)
        photo_items: list[tuple[str, bytearray]] = []  # (angle, buffer)
        for photo in photos:
            try:
                buffer = bytearray(await asyncio.to_thread(r2.get_bytes, photo["r2_key"]))
            except Exception:
                raise EnrollmentMappedError("id_portrait_unavailable") from None
            photo_items.append((photo["angle"], buffer))
            photo_buffers.append(buffer)  # 아래 finally 에서 일괄 wipe

        try:
            qc = load_face_qc(settings, required=True)
            # 매칭 앵커: 라이브니스 on 이면 라이브 프레임, off 면 신분증 초상.
            #  - on: 신분증 초상 ↔ 라이브(신원 앵커, 차단) + 업로드 사진 ↔ 라이브(스왑 방지).
            #  - off: 업로드 사진 ↔ 신분증 초상. 신분증 초상이 앵커 = OACX 모바일신분증(실시간 폰
            #    인증)으로 실명검증된 본인. id↔live 는 라이브 프레임이 없으니 생략(신분증이 곧 앵커).
            match_anchor = liveness.reference_image if settings.fm_liveness_enabled else portrait
            if settings.fm_liveness_enabled:
                # 신원 앵커: 신분증 초상 ↔ 라이브 프레임(둘 다 정면 → SFace 유효). 차단.
                id_live_score = qc.one_to_one_similarity(portrait, match_anchor)
                # score 는 float 이거나 None(검출 실패) — %s 로 로깅해 None 도 안전하게 찍고,
                # None 판정(fail-closed)은 _assert_match 가 face_match_failed 로 처리한다.
                logger.info(
                    "fm_match_id_live score=%s threshold=%.4f",
                    id_live_score, settings.fm_id_live_threshold,
                )
                _assert_match(id_live_score, settings.fm_id_live_threshold)
            # 업로드 사진 ↔ 앵커: 정면 얼굴 인식기(YuNet 검출 + SFace)는 측면·프로필을
            # 신뢰성 있게 다루지 못한다 — 옆모습은 검출(YuNet) 자체가 실패한다. 그래서 정면 얼굴이
            # 잡히는 사진만 매칭해 "모델 사진 = 검증된 본인"을 확인하고, 검출 불가한 각도
            # (45/측면)는 자산용 앵글 소스로만 취급해 건너뛴다. 검출된 사진은 모두 매칭돼야 하고,
            # 최소 1장은 매칭돼야 한다(스왑 방지).
            matched_any = False
            for _angle, buffer in photo_items:
                try:
                    score = qc.one_to_one_similarity(buffer, match_anchor)
                except QcFailed as exc:
                    if exc.reason == "no_face_detected":
                        continue  # 정면 검출기가 못 잡는 각도(측면/프로필) — 매칭 대상 아님
                    raise
                threshold = match_threshold_for_angle(settings, _angle)
                logger.info(
                    "fm_match_photo_live angle=%s score=%s threshold=%.4f",
                    _angle, score, threshold,
                )
                _assert_match(score, threshold)
                matched_any = True
            if not matched_any:
                raise EnrollmentMappedError("face_match_failed")
        except EnrollmentMappedError:
            raise
        except QcFailed as exc:
            reason = exc.reason if exc.reason == "qc_unavailable" else "face_match_failed"
            raise EnrollmentMappedError(reason) from None
        except Exception:
            raise EnrollmentMappedError("qc_unavailable") from None

        # Task3: 바인딩 증거는 앞단 /identity 가 fm_biometric_enrollments 에 저장한 값을 읽는다.
        # CI 는 재계산할 원본 token 이 없다 — 저장된 HMAC(identity_ci_hash)을 그대로 쓴다.
        ci_hash = row["identity_ci_hash"]
        identity_tx_digest = row["identity_tx_digest"]
        identity_name_masked = row["identity_name_masked"]
        identity_birth_year = row["identity_birth_year"]
        identity_contract_version = row["identity_contract_version"]
        async with get_conn(request) as conn:
            await _assert_account_open(conn, user_id)
            await _reject_cutover_closed(conn)
            async with conn.cursor() as cur:
                # 라이브니스 off 면 세션 다이제스트가 없다(NULL) — 예측어를 빼야 잠금이 걸린다.
                if settings.fm_liveness_enabled:
                    _digest_predicate = "and e.liveness_session_digest = %s"
                    _lock_params = (
                        enrollment_id,
                        user_id,
                        hashlib.sha256(session_id.encode()).hexdigest(),
                    )
                else:
                    _digest_predicate = ""
                    _lock_params = (enrollment_id, user_id)
                await cur.execute(
                    f"""
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.id = %s and e.user_id = %s
                      and e.status = 'processing'
                      {_digest_predicate}
                    for update
                    """,
                    _lock_params,
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
                        (ci_hash, identity_name_masked, user_id, model_id),
                    )
                else:
                    await cur.execute(
                        """
                        insert into fm_models (user_id, display_name, status, ci_hash)
                        values (%s, %s, 'pending', %s)
                        returning id::text as id
                        """,
                        (user_id, identity_name_masked, ci_hash),
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
                            identity_tx_digest,
                            Json({
                                "nameMasked": identity_name_masked,
                                "birthYear": identity_birth_year,
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
                # Task4: 등록 중 올린 대표이미지가 있으면 바인딩 시 모델 커버로 승격한다.
                # cover_image_url 은 기존 관례상 별도 URL 변환 없이 그대로 읽히므로(facemarket.py
                # _MODEL_CARD_COLS 참조) R2 키를 그대로 저장한다 — 노출 URL화는 범위 밖.
                if row.get("profile_image_r2_key"):
                    await cur.execute(
                        "update fm_models set cover_image_url = %s where id = %s",
                        (row["profile_image_r2_key"], model_id),
                    )
                # Task5: 등록 중 입력받은 키·체형(height_bucket·body_type)이 있으면 바인딩 시
                # 모델로 승격한다. gender는 identity(OACX)에서 설정되지만, CX가 성별을 안 주면
                # NULL로 남으므로 — 모델이 고른 키 구간 접두사(m_/f_)에서 유도해 채운다(coalesce).
                if row.get("height_bucket") or row.get("body_type"):
                    from .facemarket_physique import bucket_gender

                    await cur.execute(
                        "update fm_models set height_bucket = coalesce(%s, height_bucket), "
                        "body_type = coalesce(%s, body_type), "
                        "gender = coalesce(gender, %s) where id = %s",
                        (
                            row.get("height_bucket"),
                            row.get("body_type"),
                            bucket_gender(row.get("height_bucket")),
                            model_id,
                        ),
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
                        identity_tx_digest,
                        settings.fm_match_policy_version,
                        Json({
                            "faceLiveness": (
                                liveness.provider_version
                                if liveness is not None else "disabled"
                            ),
                            "oacx": identity_contract_version,
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
            expected_status="processing" if processing_started else "liveness_pending",
        )
    finally:
        # Task3: 원시 CI(evidence.ci)는 앞단 /identity 가 이미 폐기했다 — 여기서 다룰 게 없다.
        if portrait is not None:
            cx_identity.wipe_bytearray(portrait)
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
    settings: Settings = request.app.state.settings
    if settings.fm_liveness_enabled:
        try:
            session_id = str(uuid.UUID(str(body.session_id)))
        except (AttributeError, TypeError, ValueError):
            raise _err("invalid_liveness_session", "인증 세션을 확인할 수 없습니다.")
    else:
        session_id = None  # 라이브니스 off — 세션 없이 신분증 초상 앵커로 매칭
    try:
        decision = await process_enrollment_completion(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
            id_photo_hex=body.id_photo_hex,
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
        _prewarm_opendid(request)   # 자산빌드(1~3분) 뒤가 발급 — 그 사이에 holder 를 띄운다
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
            await _assert_account_open(conn, user_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update fm_biometric_enrollments e
                    set status = 'cancelled', completed_at = coalesce(completed_at, now())
                    where e.id = %s and e.user_id = %s and e.status in (
                        'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending', 'cancelled'
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
    # 라이브니스 관련 요구는 on 일 때만 — off 면 매칭 앵커가 신분증 초상이라 리전·브라우저 role·
    # liveness confidence·id_live 임계가 쓰이지 않는다.
    if settings.fm_liveness_enabled:
        if settings.fm_liveness_region != "us-east-1":
            raise RuntimeError("Face Liveness region must be us-east-1")
        if not settings.fm_liveness_browser_role_arn:
            raise RuntimeError("FM_LIVENESS_BROWSER_ROLE_ARN is required")
    if not settings.fm_face_qc_enabled:
        raise RuntimeError("FM_FACE_QC_ENABLED is required")
    if not settings.fm_ci_pepper or not settings.fm_ci_pepper.strip():
        raise RuntimeError("FM_CI_PEPPER is required for biometric enrollment")
    det_path, rec_path = weight_paths(settings)
    if not (os.path.exists(det_path) and os.path.exists(rec_path)):
        raise RuntimeError(
            "SFace/YuNet face QC weight files are required for biometric enrollment"
        )
    # retouched_live·match_policy 는 항상 필요(사진 ↔ 앵커 매칭). liveness_confidence·id_live 는
    # 라이브니스 on 일 때만 필요.
    required = [
        settings.fm_retouched_live_threshold,
        settings.fm_match_policy_version,
    ]
    bounded_thresholds = [(settings.fm_retouched_live_threshold, 1.0)]
    if settings.fm_liveness_enabled:
        required += [
            settings.fm_liveness_confidence_threshold,
            settings.fm_id_live_threshold,
        ]
        bounded_thresholds += [
            (settings.fm_liveness_confidence_threshold, 100.0),
            (settings.fm_id_live_threshold, 1.0),
        ]
    if any(value is None for value in required):
        raise RuntimeError("calibrated biometric thresholds and policy version are required")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 < value <= upper
        for value, upper in bounded_thresholds
    ):
        raise RuntimeError(
            "calibrated biometric thresholds must be finite and within provider domains"
        )
    if settings.fm_oacx_contract_mode == "dev-mock-v1" and settings.app_env != "dev":
        raise RuntimeError("verified OACX biometric contract is required outside dev")
    # D1: prod-dlphoto-v1 은 실 프로덕션 계약(cx_identity.PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT)
    # 이라 dev-mock-v1 처럼 app_env=='dev' 로 가둘 필요가 없다 — prod 에서도 유효해야 한다.
    if settings.fm_oacx_contract_mode not in ("dev-mock-v1", "prod-dlphoto-v1"):
        raise RuntimeError("verified OACX biometric contract is required")


def build_biometric_aws_clients(settings: Settings):
    rekognition = boto3.client(
        "rekognition", region_name="us-east-1", config=AWS_LIVENESS_CONFIG
    )
    sts = boto3.client("sts", region_name="us-east-1", config=AWS_LIVENESS_CONFIG)
    return rekognition, sts
