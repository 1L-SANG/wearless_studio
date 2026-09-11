"""FaceMarket 관리자 육안 심사 API — 큐 · 카드 · 이미지 열람 · 승인 · 거절.

간편인증(simple_auth) 경로의 앵커는 사용자가 손에 들고 촬영한 신분증이다 — 위조 가능한
증거라 기계 점수(match_scores)가 어느 방향으로도 신뢰 판정을 내리지 못한다(위조된 카드도
진짜 얼굴을 담고, 반사광 하나로 진짜 카드의 점수가 떨어진다). 그래서 Task7 은 점수를
"정보"로만 남기고 `review_pending` 에서 멈춘다. 이 API 가 그 정보를 사람에게 보여주고
결정을 받는 유일한 창구다 — 이게 없으면 등록이 `review_pending` 에 영원히 쌓인다.

동시에 이 API 는 등록자의 신분증 원본을 볼 수 있는 **유일한** 자리다. 그래서 라우트
5개 전부 `admin_guard.require_admin`(기기 게이트 포함)을 맨 앞에 두고, 이미지는
`Cache-Control: private, no-store`로 어떤 중간 캐시에도 앉지 않게 하며, 승인·거절
직후 신분증 촬영본을 즉시 파기한다(더 볼 사람이 없어진 순간이 곧 보관 이유가 사라지는
순간이다).

승인 뒤 자산빌드 재개: `bind_model_and_enqueue_asset_build`(facemarket_enrollment.py)는
정상 완료 경로(`process_enrollment_completion`)의 모델 바인딩 tail 을 그대로 추출한
공유 함수다. 심사 승인은 그 함수를 재사용해 모델을 만들고(또는 재사용) `fm_model_asset_build`
잡을 큐잉한다 — 매칭을 다시 하지 않는다(신분증은 이미 파기됐고, Task7 이 advisory 로
계산해 저장해 둔 match_scores 를 그대로 감사 기록에 남긴다).
"""

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from . import admin_guard
from .auth import require_user
from .db import get_conn
from .facemarket_enrollment import (
    EnrollmentMappedError,
    _assert_account_open,
    _reject_cutover_closed,
    _wake_dispatcher,
    bind_model_and_enqueue_asset_build,
)
from .facemarket_id_document import purge_id_document
from .models import CamelModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket/admin", tags=["FaceMarket admin review"])

REVIEW_STATUSES = ("pending", "approved", "rejected")
# 화이트리스트 — 절대 클라이언트 문자열을 그대로 R2 키에 꽂지 않는다.
PHOTO_ANGLES = ("front", "angle45", "side")
IMAGE_KINDS = ("id_document",) + PHOTO_ANGLES

ENROLLMENT_CARD_COLUMNS = """
    id::text as id, user_id::text as user_id, model_id::text as model_id,
    identity_method, review_status, status, match_scores,
    application_id::text as application_id,
    reviewed_by::text as reviewed_by, reviewed_at, review_reason, created_at
"""


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _canonical_id(enrollment_id: str) -> str:
    try:
        return str(uuid.UUID(str(enrollment_id)))
    except (AttributeError, TypeError, ValueError):
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)


async def _require_admin(conn, user_id: str, request: Request) -> None:
    """호출부 이름은 그대로 두고 판정만 admin_guard 로 넘긴다(facemarket_applications.py 패턴)."""
    await admin_guard.require_admin(conn, user_id, request)


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "얼굴 저장소를 사용할 수 없습니다.", status=503)
    return client


def _image_urls(enrollment_id: str) -> dict[str, str]:
    base = f"/v1/facemarket/admin/enrollments/{enrollment_id}/images"
    return {kind: f"{base}/{kind}" for kind in IMAGE_KINDS}


class AdminReviewApplication(CamelModel):
    """심사 카드용 지원서 요약 — 전부 optional. mid/simple_auth 모두 지원서 없이도
    등록이 존재할 수 있고(application_id nullable), 값이 일부만 채워진 경우도 있다."""
    applicant_name: str | None = None
    birthdate: str | None = None
    region: str | None = None
    gender: str | None = None
    height_cm: int | None = None
    weight_kg: int | None = None
    phone: str | None = None
    categories: list[str] = []


class AdminReviewQueueRow(CamelModel):
    id: str
    identity_method: str
    review_status: str | None = None
    status: str
    created_at: datetime


class AdminReviewCard(CamelModel):
    id: str
    user_id: str
    identity_method: str
    review_status: str | None = None
    status: str
    # 원시 코사인 그대로(백분율 변환은 표시층=Task12 소관). thresholds/anchor/skipped 등
    # Task7 이 이미 만든 구조를 그대로 통과시킨다 — 여기서 재가공하지 않는다.
    match_scores: dict | None = None
    application: AdminReviewApplication | None = None
    images: dict[str, str]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    created_at: datetime


class AdminReviewDecisionResult(CamelModel):
    id: str
    review_status: str
    status: str
    # 승인 재개(자산빌드)가 실패했을 때만 채워진다 — None 이면 성공(또는 거절이라 해당 없음).
    # 관리자가 "승인은 됐는데 왜 안 만들어지지"를 응답 하나로 바로 알 수 있어야 한다
    # (fix round 1, IMPORTANT B) — ERROR 로그 + admin_audit_log 감사행과 함께 3중 가시성.
    asset_build_error: str | None = None


class AdminRejectBody(CamelModel):
    reason: str


async def _load_application_summary(conn, application_id: str | None) -> AdminReviewApplication | None:
    if not application_id:
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            "select applicant_name, birthdate, region, gender, height_cm, weight_kg, "
            "phone, categories from fm_model_applications where id = %s",
            (application_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return AdminReviewApplication(
        applicant_name=row.get("applicant_name"),
        birthdate=str(row["birthdate"]) if row.get("birthdate") else None,
        region=row.get("region"),
        gender=row.get("gender"),
        height_cm=row.get("height_cm"),
        weight_kg=row.get("weight_kg"),
        phone=row.get("phone"),
        categories=list(row.get("categories") or []),
    )


def _card_view(row: dict, application: AdminReviewApplication | None) -> AdminReviewCard:
    return AdminReviewCard(
        id=row["id"],
        user_id=row["user_id"],
        identity_method=row.get("identity_method") or "mid",
        review_status=row.get("review_status"),
        status=row["status"],
        match_scores=row.get("match_scores"),
        application=application,
        images=_image_urls(row["id"]),
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row.get("reviewed_at"),
        review_reason=row.get("review_reason"),
        created_at=row["created_at"],
    )


# --- 큐 · 카드 · 이미지 --------------------------------------------------------------


@router.get("/enrollments", response_model=list[AdminReviewQueueRow])
async def list_review_queue(
    request: Request,
    review: str = Query(..., description="pending|approved|rejected"),
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        # 관리자 판정이 먼저 답한다 — 요청 모양 검증(잘못된 review 값)보다 늦게 하면
        # 비관리자가 400 과 403 을 구분해 필터 값 스캐닝에 쓸 수 있다(fix round 1, minor).
        await _require_admin(conn, user_id, request)
        if review not in REVIEW_STATUSES:
            raise _err("invalid_review_filter", "심사 상태 필터가 올바르지 않습니다.")
        async with conn.cursor() as cur:
            # created_at desc — 마이그레이션의 fm_biometric_review_queue 부분 인덱스와 정렬을 맞춘다.
            await cur.execute(
                """
                select id::text as id, identity_method, review_status, status, created_at
                from fm_biometric_enrollments
                where review_status = %s
                order by created_at desc
                limit 200
                """,
                (review,),
            )
            rows = await cur.fetchall()
    return [
        AdminReviewQueueRow(
            id=row["id"],
            identity_method=row.get("identity_method") or "mid",
            review_status=row.get("review_status"),
            status=row["status"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/enrollments/{enrollment_id}", response_model=AdminReviewCard)
async def get_review_card(
    request: Request, enrollment_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {ENROLLMENT_CARD_COLUMNS} from fm_biometric_enrollments where id = %s",
                (enrollment_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        application = await _load_application_summary(conn, row.get("application_id"))
    return _card_view(row, application)


@router.get("/enrollments/{enrollment_id}/images/{kind}")
async def get_review_image(
    request: Request, enrollment_id: str, kind: str, user_id: str = Depends(require_user)
):
    """신분증·등록 사진 스트림. `kind` 는 화이트리스트만 허용 — 클라이언트 문자열을
    R2 키에 절대 그대로 끼워 넣지 않는다. 응답은 항상 private·no-store(생체 이미지가
    프록시·CDN·브라우저 캐시 어디에도 남지 않게)."""
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        if kind not in IMAGE_KINDS:
            raise _err("invalid_image_kind", "이미지 종류가 올바르지 않습니다.")
        async with conn.cursor() as cur:
            if kind == "id_document":
                await cur.execute(
                    "select id_document_r2_key from fm_biometric_enrollments where id = %s",
                    (enrollment_id,),
                )
                found = await cur.fetchone()
                key = found.get("id_document_r2_key") if found else None
                mime_hint = None
            else:
                await cur.execute(
                    "select r2_key, mime_type from fm_biometric_enrollment_photos "
                    "where enrollment_id = %s and angle = %s",
                    (enrollment_id, kind),
                )
                found = await cur.fetchone()
                key = found.get("r2_key") if found else None
                mime_hint = found.get("mime_type") if found else None
    if not key:
        raise _err("not_found", "이미지를 찾을 수 없습니다.", status=404)
    r2 = _r2_face(request)
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        # 키/바이트를 로그에 남기지 않는다 — 원시 PII 미저장 규율.
        logger.warning("admin_review_image_fetch_failed enrollment=%s kind=%s", enrollment_id, kind)
        raise _err("not_found", "이미지를 찾을 수 없습니다.", status=404)
    mime = mime_hint or "image/jpeg"
    if not mime_hint:
        if key.endswith(".png"):
            mime = "image/png"
        elif key.endswith(".webp"):
            mime = "image/webp"
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store"})


# --- 승인 · 거절 ---------------------------------------------------------------------


async def _purge_id_document_best_effort(request: Request, enrollment_id: str) -> None:
    """심사가 끝나면 신분증은 더 쓸 데가 없다 — 즉시 파기. 실패해도 결정은 이미 커밋됐으니
    요청을 막지 않는다(경고 로그만). 7일 배치 스윕이 상한."""
    try:
        r2 = _r2_face(request)
        async with get_conn(request) as conn:
            await purge_id_document(r2, conn, enrollment_id)
            await conn.commit()
    except Exception:
        logger.warning("id_document_purge_failed enrollment=%s", enrollment_id, exc_info=True)


class _ResumeRaceLost(Exception):
    """승인 UPDATE 가 방금 확정한 (status='processing', review_status='approved') 조합을
    재개-select 가 다시 못 찾았다 — 극히 드문 레이스(예: 다른 프로세스가 그 사이 행을
    바꿈)의 신호일 뿐 이 요청 자체의 버그가 아니다. 일반 Exception 과 분리해서 로그
    문구를 정확히 남긴다."""


async def _bind_and_enqueue_locked(request: Request, enrollment_id: str) -> None:
    """`_resume_asset_build` 의 실제 작업. 성공하면 조용히 리턴하고, 실패하면 예외를
    던진다(호출자가 로그·가시성·응답 표기를 전담) — 이 함수 자체는 실패를 삼키지 않는다."""
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id::text as id, user_id::text as user_id, model_id::text as model_id,
                       identity_method, match_scores, identity_ci_hash, identity_tx_digest,
                       identity_name_masked, identity_birth_year, identity_contract_version,
                       profile_image_r2_key, height_bucket, body_type
                from fm_biometric_enrollments
                where id = %s and status = 'processing' and review_status = 'approved'
                for update
                """,
                (enrollment_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise _ResumeRaceLost()
            # 정상 완료 경로(process_enrollment_completion)와 동일한 두 관문 — 승인
            # 시점과 재개 시점 사이(짧지만 0 은 아닌 창) 계정이 닫히거나 컷오버가
            # 시작됐다면, 재개는 여기서 멈춰야 한다(fix round 1, IMPORTANT E).
            await _assert_account_open(conn, row["user_id"])
            await _reject_cutover_closed(conn)
            method = row.get("identity_method") or "mid"
            match_snapshot = row.get("match_scores") or {}
            await bind_model_and_enqueue_asset_build(
                cur,
                user_id=row["user_id"],
                enrollment_id=enrollment_id,
                row=row,
                match_snapshot=match_snapshot,
                method=method,
                identity_contract_version=row.get("identity_contract_version"),
                # 재개 시점엔 원래 라이브니스 프레임이 이미 사라졌다 — 리뷰 대상은 항상
                # fm_liveness_enabled=False 조합이라(Task7) 실질적 정보 손실은 없다.
                liveness_provider_version="disabled_resume",
                match_policy_version=settings.fm_match_policy_version,
            )
        await conn.commit()


async def _resume_asset_build(
    request: Request, enrollment_id: str, *, actor_user_id: str
) -> tuple[str, str | None]:
    """승인 뒤 모델 바인딩 + 자산빌드 잡 큐잉을 재개한다.

    `process_enrollment_completion`(정상 완료 경로)이 심사가 필요 없을 때 쓰는 것과
    **동일한 tail**(`bind_model_and_enqueue_asset_build`)을 재사용한다 — 매칭을 다시
    하지 않는다: 신분증은 이미 파기됐고, Task7 이 advisory 로 계산해 저장해 둔
    match_scores 를 그대로 넘긴다.

    실패(레이스로 행을 못 잠그거나, identity_replay/identity_recovery_required 같은
    드문 충돌, 혹은 계정 폐쇄·컷오버)해도 승인 결정 자체(review_status='approved')는
    이미 커밋·감사된 뒤라 이 요청을 실패시키지 않는다 — 다만 실패를 **조용히 삼키지
    않는다**(fix round 1, IMPORTANT B): ERROR 로그(enrollment id + 원인) 남기고,
    별도 감사 행(`enrollment_review_resume_failed`)을 써서 admin_audit_log 에서도
    찾을 수 있게 하고, 반환값(`error_code`)을 호출자가 응답 바디에 그대로 실어 보내
    승인 버튼을 누른 바로 그 관리자가 즉시 알게 한다. 이 코드베이스엔 아직
    status='processing'+review_status='approved' 로 멈춘 행을 스캔해 자동 재시도하는
    스윕이 없다 — 그래서 "찾을 수 있게" 가 곧 "복구할 수 있게" 는 아니라는 게 남은 한계다.

    반환값: (최종 status, error_code). error_code 는 성공 시 None.
    """
    error_code: str
    try:
        await _bind_and_enqueue_locked(request, enrollment_id)
        _wake_dispatcher(request)
        return "asset_building", None
    except _ResumeRaceLost:
        error_code = "race_lost"
    except HTTPException as exc:
        error_code = (
            exc.detail.get("code") if isinstance(exc.detail, dict) else "resume_blocked"
        )
    except EnrollmentMappedError as exc:
        error_code = exc.reason
    except Exception as exc:
        logger.error(
            "enrollment_resume_failed enrollment=%s error_type=%s",
            enrollment_id, type(exc).__name__, exc_info=True,
        )
        error_code = "unexpected_error"

    logger.error(
        "enrollment_resume_failed enrollment=%s reason=%s", enrollment_id, error_code
    )
    try:
        async with get_conn(request) as conn:
            await admin_guard.write_audit(
                conn,
                actor_user_id=actor_user_id,
                action="enrollment_review_resume_failed",
                target_type="enrollment",
                target_id=enrollment_id,
                note=error_code,
            )
            await conn.commit()
    except Exception:
        logger.error(
            "enrollment_resume_failure_audit_failed enrollment=%s", enrollment_id, exc_info=True
        )
    return "processing", error_code


@router.post("/enrollments/{enrollment_id}/approve", response_model=AdminReviewDecisionResult)
async def approve_enrollment(
    request: Request, enrollment_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        async with conn.cursor() as cur:
            # 상태 가드 UPDATE — 다른 관리자가 이미 처리했으면 0-row(레이스에서 진 쪽은 409,
            # 둘 다 "이겼다"고 믿는 일이 없다).
            await cur.execute(
                """
                update fm_biometric_enrollments
                set review_status = 'approved', reviewed_by = %s, reviewed_at = now(),
                    status = 'processing'
                where id = %s and status = 'review_pending' and review_status = 'pending'
                returning id::text as id
                """,
                (user_id, enrollment_id),
            )
            updated = await cur.fetchone()
        if updated is None:
            raise _err("invalid_review_state", "심사 대기 상태가 아닙니다.", status=409)
        await conn.commit()

    # 커밋 뒤: 파기(best-effort) → 감사(필수) → 자산빌드 재개(best-effort) 순서.
    await _purge_id_document_best_effort(request, enrollment_id)
    async with get_conn(request) as conn:
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="enrollment_review_approve",
            target_type="enrollment",
            target_id=enrollment_id,
            before={"reviewStatus": "pending", "status": "review_pending"},
            after={"reviewStatus": "approved", "status": "processing"},
        )
        await conn.commit()
    final_status, resume_error = await _resume_asset_build(
        request, enrollment_id, actor_user_id=user_id
    )
    return JSONResponse(
        content=AdminReviewDecisionResult(
            id=enrollment_id, review_status="approved", status=final_status,
            asset_build_error=resume_error,
        ).model_dump(by_alias=True)
    )


@router.post("/enrollments/{enrollment_id}/reject", response_model=AdminReviewDecisionResult)
async def reject_enrollment(
    request: Request,
    enrollment_id: str,
    body: AdminRejectBody,
    user_id: str = Depends(require_user),
):
    # body 자체가 JSON 스키마와 안 맞으면(reason 이 없거나 타입이 다르면) FastAPI 가 여기
    # 도달하기 전에 422 를 낸다 — 그건 우리 관할이 아니다. 여기서 잡는 건 "모양은 맞는데
    # 의미가 비어 있다"(빈 문자열/공백)는 400 이다. 두 상태 코드가 다른 건 의도적이다.
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        reason = (body.reason or "").strip()
        if not reason:
            raise _err("reason_required", "거절 사유를 입력해 주세요.")
        if len(reason) > 1000:
            raise _err("reason_too_long", "거절 사유가 너무 깁니다.")
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set review_status = 'rejected', reviewed_by = %s, reviewed_at = now(),
                    review_reason = %s, status = 'failed', reason = 'review_rejected',
                    completed_at = now()
                where id = %s and status = 'review_pending' and review_status = 'pending'
                returning id::text as id
                """,
                (user_id, reason, enrollment_id),
            )
            updated = await cur.fetchone()
        if updated is None:
            raise _err("invalid_review_state", "심사 대기 상태가 아닙니다.", status=409)
        await conn.commit()

    await _purge_id_document_best_effort(request, enrollment_id)
    async with get_conn(request) as conn:
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="enrollment_review_reject",
            target_type="enrollment",
            target_id=enrollment_id,
            before={"reviewStatus": "pending", "status": "review_pending"},
            after={"reviewStatus": "rejected", "status": "failed"},
            note=reason,
        )
        await conn.commit()
    return JSONResponse(
        content=AdminReviewDecisionResult(
            id=enrollment_id, review_status="rejected", status="failed"
        ).model_dump(by_alias=True)
    )
