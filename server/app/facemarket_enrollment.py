"""Fail-closed FaceMarket biometric enrollment and quarantine lifecycle."""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import boto3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

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
ANGLES = ("front", "angle45", "side")
MAX_FACE_BYTES = 25 * 1024 * 1024
ALLOWED_FACE_MIME = {"image/png", "image/jpeg", "image/webp"}


class BiometricConsent(CamelModel):
    accepted: bool
    document_version: str


class CreateEnrollmentBody(CamelModel):
    device_id: str
    biometric_consent: BiometricConsent


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


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "얼굴 저장소를 사용할 수 없습니다.", status=503)
    return client


async def _load_owned_enrollment(conn, enrollment_id: str, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at
            from fm_biometric_enrollments e
            where e.id = %s and e.user_id = %s
            """,
            (enrollment_id, user_id),
        )
        return await cur.fetchone()


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
            where p.enrollment_id = %s
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
    if angle not in ANGLES:
        raise _err("invalid_angle", "사진 각도를 확인해 주세요.")
    mime = (photo.content_type or "").lower()
    if mime not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    r2 = _r2_face(request)
    data = await photo.read()
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
        new_key = enrollment_quarantine_key(enrollment_id, angle, ext)
        try:
            await asyncio.to_thread(r2.put_bytes, new_key, data, mime)
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

        old_key = None
        try:
            async with get_conn(request) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        select e.id::text as id, e.status
                        from fm_biometric_enrollments e
                        where e.id = %s and e.user_id = %s and e.status in (
                            'photos_pending', 'liveness_pending', 'processing',
                            'asset_building', 'license_pending', 'vc_pending'
                        ) for update
                        """,
                        (enrollment_id, user_id),
                    )
                    if await cur.fetchone() is None:
                        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
                    await cur.execute(
                        """
                        select r2_key from fm_biometric_enrollment_photos
                        where enrollment_id = %s and angle = %s
                        """,
                        (enrollment_id, angle),
                    )
                    old = await cur.fetchone()
                    old_key = old["r2_key"] if old else None
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
                        (enrollment_id, angle, new_key, sha256_sri(data), mime, len(data)),
                    )
                    uploaded_at = (await cur.fetchone())["uploaded_at"]
                    await cur.execute(
                        """
                        select count(*) as passed_count
                        from fm_biometric_enrollment_photos
                        where enrollment_id = %s and qc_status = 'passed'
                        """,
                        (enrollment_id,),
                    )
                    if (await cur.fetchone())["passed_count"] == len(ANGLES):
                        await cur.execute(
                            """
                            update fm_biometric_enrollments set status = 'liveness_pending'
                            where id = %s and user_id = %s
                            """,
                            (enrollment_id, user_id),
                        )
                await conn.commit()
        except Exception as db_error:
            try:
                await asyncio.to_thread(r2.delete, new_key)
            except Exception as exc:
                logger.warning(
                    "facemarket_enrollment_failed_upload_cleanup_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "angle": angle,
                        "error_type": type(exc).__name__,
                    },
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

        if old_key and old_key != new_key:
            try:
                await asyncio.to_thread(r2.delete, old_key)
            except Exception as exc:
                logger.warning(
                    "facemarket_enrollment_old_photo_cleanup_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "angle": angle,
                        "error_type": type(exc).__name__,
                    },
                )
        return EnrollmentPhotoView(angle=angle, qc_status="passed", uploaded_at=uploaded_at)
    finally:
        data = b""


@router.delete("/enrollments/{enrollment_id}/photos/{angle}", status_code=204)
async def delete_enrollment_photo(
    request: Request,
    enrollment_id: str,
    angle: str,
    user_id: str = Depends(require_user),
):
    if angle not in ANGLES:
        raise _err("invalid_angle", "사진 각도를 확인해 주세요.")
    r2 = _r2_face(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.id::text as id, e.status
                from fm_biometric_enrollments e
                where e.id = %s and e.user_id = %s and e.status in (
                    'photos_pending', 'liveness_pending', 'processing',
                    'asset_building', 'license_pending', 'vc_pending'
                ) for update
                """,
                (enrollment_id, user_id),
            )
            if await cur.fetchone() is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            await cur.execute(
                """
                select r2_key from fm_biometric_enrollment_photos
                where enrollment_id = %s and angle = %s
                """,
                (enrollment_id, angle),
            )
            photo = await cur.fetchone()
            if photo is None:
                return Response(status_code=204)
            try:
                await asyncio.to_thread(r2.delete, photo["r2_key"])
            except Exception:
                raise _err(
                    "storage_unavailable",
                    "얼굴 저장소를 사용할 수 없습니다.",
                    status=503,
                )
            await cur.execute(
                """
                delete from fm_biometric_enrollment_photos
                where enrollment_id = %s and angle = %s and r2_key = %s
                """,
                (enrollment_id, angle, photo["r2_key"]),
            )
            await cur.execute(
                """
                update fm_biometric_enrollments set status = 'photos_pending'
                where id = %s and user_id = %s and status = 'liveness_pending'
                """,
                (enrollment_id, user_id),
            )
        await conn.commit()
    return Response(status_code=204)


async def cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return False

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.status, p.angle, p.r2_key
                from fm_biometric_enrollments e
                left join fm_biometric_enrollment_photos p
                  on p.enrollment_id = e.id and p.storage_state = 'quarantine'
                where e.id = %s and e.status in ('failed', 'cancelled', 'expired')
                """,
                (enrollment_id,),
            )
            rows = await cur.fetchall()
    if not rows:
        return False

    deleted_count = 0
    failed_count = 0
    for row in rows:
        if row.get("r2_key") is None:
            continue
        try:
            await asyncio.to_thread(r2.delete, row["r2_key"])
        except Exception as exc:
            failed_count += 1
            logger.warning(
                "facemarket_enrollment_quarantine_cleanup_failed",
                extra={
                    "enrollment_id": enrollment_id,
                    "angle": row["angle"],
                    "error_type": type(exc).__name__,
                    "retry_count": failed_count,
                },
            )
            continue
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    delete from fm_biometric_enrollment_photos
                    where enrollment_id = %s and angle = %s and r2_key = %s
                      and storage_state = 'quarantine'
                    """,
                    (enrollment_id, row["angle"], row["r2_key"]),
                )
            await conn.commit()
        deleted_count += 1

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select count(*) as remaining
                from fm_biometric_enrollment_photos
                where enrollment_id = %s and storage_state = 'quarantine'
                """,
                (enrollment_id,),
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
    return remaining == 0


@router.post("/enrollments/{enrollment_id}/cancel", response_model=EnrollmentView)
async def cancel_enrollment(
    request: Request,
    enrollment_id: str,
    user_id: str = Depends(require_user),
):
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

    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    async with get_conn(request) as conn:
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)


def validate_biometric_settings(settings: Settings) -> None:
    if not settings.fm_biometric_enrollment_enabled:
        return
    if not settings.facemarket_enabled:
        raise RuntimeError("FACEMARKET_ENABLED is required for biometric enrollment")
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
    rekognition = boto3.client("rekognition", region_name="us-east-1")
    sts = boto3.client("sts", region_name="us-east-1")
    return rekognition, sts
