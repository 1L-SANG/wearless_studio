"""FaceMarket 모델 지원서·관리자 검토 (리뉴얼).

지원 → 관리자 검토(승인/거절) → 승인 지원서가 create_enrollment 게이트 통과 근거.
생체 enrollment 와 별개 aggregate(fm_model_applications). 설계·결정:
docs/designs/facemarket-application-renewal.md

  상태 머신 (fm_model_applications.status):
    under_review ──승인──▶ approved ──(enrollment 생성 근거, E5 FK)
         │                    │
      거절│(관리자)         취소│(사용자)     신분증 대조 3회 실패(E2) ──▶ rejected
         ▼                    ▼
    rejected(터미널)      cancelled(터미널)  ── 30일 후 PII 익명화(3A, 데모 후 sweep)
    활성 = {under_review, approved}, 유저당 1개(E9). 터미널이면 재지원 허용.
"""

import logging
import uuid

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from . import facemarket_notify, repo
from .auth import require_user
from .config import Settings
from .db import get_conn
from .models import CamelModel
from .r2 import ext_for_mime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket model applications"])

# 지원서 카테고리(활동하고 싶은 모델 분야). 앱 레벨 검증(DB 는 jsonb).
CATEGORY_VALUES = {"fashion", "commercial", "fitness", "lifestyle"}
# 개인정보 수집·이용 동의(생체 동의와 별개, E3). 문구 변경 시 버전을 올린다.
PRIVACY_CONSENT_VERSION = "2026-09-v1"
ACCEPTED_PRIVACY_VERSIONS = ("2026-09-v1",)
# 신분증 대조 실패 상한(지원서에 누적, 초과 시 자동 거절). E2/5A.
MAX_IDENTITY_MISMATCH = 3
MAX_PHOTO_BYTES = 25 * 1024 * 1024
ALLOWED_PHOTO_MIME = {"image/png", "image/jpeg", "image/webp"}
# 활성으로 간주되는 상태(유저당 1개 unique 와 동일 집합, E9).
ACTIVE_STATUSES = ("under_review", "approved")
_MAX_TEXT = 2000
_MAX_URL = 500


class ApplicationConsent(CamelModel):
    accepted: bool
    document_version: str


class ApplicationSubmitBody(CamelModel):
    contact_email: str
    applicant_name: str
    birthdate: date
    region: str
    gender: str | None = None
    height_cm: int | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None
    privacy_consent: ApplicationConsent


class ApplicationView(CamelModel):
    """지원자 본인이 ModelHub 상태 허브에서 보는 뷰(+재지원 프리필용 필드)."""
    id: str
    status: str
    reject_reason: str | None = None
    identity_mismatch_count: int = 0
    has_profile_image: bool = False
    created_at: datetime
    reviewed_at: datetime | None = None
    # 재지원 프리필(E11: 사진은 별도, 30일 내 복사)
    contact_email: str
    applicant_name: str
    birthdate: date
    region: str
    gender: str | None = None
    height_cm: int | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None


class AdminApplicationCard(CamelModel):
    """관리자 대시보드 카드. 프로필 사진은 별도 게이트 라우트로 스트림."""
    id: str
    user_id: str
    status: str
    contact_email: str
    applicant_name: str
    birthdate: date
    region: str
    gender: str | None = None
    height_cm: int | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None
    has_profile_image: bool = False
    identity_mismatch_count: int = 0
    reject_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    # 최근 결정 메일 발송 상태(pending/sent/failed/None) — 대시보드 '미발송' 뱃지·재발송용(2A).
    last_email_status: str | None = None
    last_email_type: str | None = None


class AdminRejectBody(CamelModel):
    reason: str


def _err(code: str, message: str, status: int = 400, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "저장소를 사용할 수 없습니다.", status=503)
    return client


def _canonical_id(application_id: str) -> str:
    try:
        return str(uuid.UUID(str(application_id)))
    except (AttributeError, TypeError, ValueError):
        raise _err("not_found", "지원서를 찾을 수 없습니다.", status=404)


def _staging_key(user_id: str, ext: str) -> str:
    # 사용자당 1슬롯이지만 키에 uuid 를 넣어 교체 시 옛 오브젝트를 명시적으로 지운다.
    return f"private/fm-application/staging/{user_id}/{uuid.uuid4().hex}.{ext}"


def _application_photo_key(application_id: str, ext: str) -> str:
    return f"private/fm-application/{application_id}/profile.{ext}"


def _validate_categories(values: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in values:
        v = (raw or "").strip().lower()
        if v not in CATEGORY_VALUES:
            raise _err("invalid_category", "지원 가능한 카테고리가 아닙니다.")
        if v not in seen:
            seen.append(v)
    if not seen:
        raise _err("categories_required", "활동하고 싶은 카테고리를 하나 이상 선택해 주세요.")
    return seen


def _clean_url(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > _MAX_URL:
        raise _err("invalid_url", f"{field} 링크가 너무 깁니다.")
    if not (v.startswith("http://") or v.startswith("https://")):
        raise _err("invalid_url", f"{field} 링크는 http(s) 주소만 사용할 수 있습니다.")
    return v


def _clean_text(value: str | None, field: str, *, required: bool, max_len: int = _MAX_TEXT) -> str | None:
    v = (value or "").strip()
    if not v:
        if required:
            raise _err("field_required", f"{field}을(를) 입력해 주세요.")
        return None
    if len(v) > max_len:
        raise _err("field_too_long", f"{field}이(가) 너무 깁니다.")
    return v


def _application_view(row: dict) -> ApplicationView:
    return ApplicationView(
        id=row["id"],
        status=row["status"],
        reject_reason=row.get("reject_reason"),
        identity_mismatch_count=row.get("identity_mismatch_count", 0),
        has_profile_image=bool(row.get("profile_image_r2_key")),
        created_at=row["created_at"],
        reviewed_at=row.get("reviewed_at"),
        contact_email=row["contact_email"],
        applicant_name=row["applicant_name"],
        birthdate=row["birthdate"],
        region=row["region"],
        gender=row.get("gender"),
        height_cm=row.get("height_cm"),
        agency_contracted=row.get("agency_contracted", False),
        categories=list(row.get("categories") or []),
        portfolio_url=row.get("portfolio_url"),
        sns_url=row.get("sns_url"),
        bio=row.get("bio"),
    )


def _admin_card(row: dict) -> AdminApplicationCard:
    return AdminApplicationCard(
        id=row["id"],
        user_id=row["user_id"],
        status=row["status"],
        contact_email=row["contact_email"],
        applicant_name=row["applicant_name"],
        birthdate=row["birthdate"],
        region=row["region"],
        gender=row.get("gender"),
        height_cm=row.get("height_cm"),
        agency_contracted=row.get("agency_contracted", False),
        categories=list(row.get("categories") or []),
        portfolio_url=row.get("portfolio_url"),
        sns_url=row.get("sns_url"),
        bio=row.get("bio"),
        has_profile_image=bool(row.get("profile_image_r2_key")),
        identity_mismatch_count=row.get("identity_mismatch_count", 0),
        reject_reason=row.get("reject_reason"),
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row.get("reviewed_at"),
        created_at=row["created_at"],
        last_email_status=row.get("last_email_status"),
        last_email_type=row.get("last_email_type"),
    )


_APPLICATION_COLUMNS = """
    id::text as id, user_id::text as user_id, status, contact_email, applicant_name,
    birthdate, region, gender, height_cm, agency_contracted, categories,
    portfolio_url, sns_url, bio, profile_image_r2_key, identity_mismatch_count,
    reviewed_by::text as reviewed_by, reviewed_at, reject_reason,
    created_at, updated_at
"""


async def _load_current(conn, user_id: str) -> dict | None:
    """유저의 활성 지원서(있으면), 없으면 가장 최근 터미널 지원서(상태 허브·재지원 프리필용)."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            select {_APPLICATION_COLUMNS} from fm_model_applications
            where user_id = %s
            order by (status in ('under_review','approved')) desc, created_at desc
            limit 1
            """,
            (user_id,),
        )
        return await cur.fetchone()


async def _require_admin(conn, user_id: str) -> None:
    if not await repo.is_admin(conn, user_id):
        raise _err("forbidden", "관리자만 가능해요.", status=403)


# --- 지원자 엔드포인트 -------------------------------------------------------


@router.post("/applications/photo-staging", status_code=201)
async def stage_application_photo(
    request: Request,
    image: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    """제출 전 프로필 사진을 임시 저장(사용자당 1슬롯, 재업로드 시 교체). 미제출 시 orphan cleanup 회수(E11)."""
    mime = (image.content_type or "").lower()
    if mime not in ALLOWED_PHOTO_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    data = await image.read()
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
    if len(data) > MAX_PHOTO_BYTES:
        raise _err("file_too_large", "이미지는 25MB 이하만 가능합니다.", status=413)
    r2 = _r2_face(request)
    ext = ext_for_mime(mime)
    new_key = _staging_key(user_id, ext)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select r2_key from fm_model_application_photo_staging where user_id = %s for update",
                (user_id,),
            )
            existing = await cur.fetchone()
            r2.put_bytes(new_key, data, mime)
            await cur.execute(
                """
                insert into fm_model_application_photo_staging (user_id, r2_key, mime_type, byte_size)
                values (%s, %s, %s, %s)
                on conflict (user_id) do update
                  set r2_key = excluded.r2_key, mime_type = excluded.mime_type,
                      byte_size = excluded.byte_size, created_at = now()
                """,
                (user_id, new_key, mime, len(data)),
            )
        await conn.commit()
        # 옛 스테이징 오브젝트는 커밋 후 정리(실패해도 orphan cleanup 이 나중에 회수).
        if existing and existing["r2_key"] and existing["r2_key"] != new_key:
            try:
                r2.delete(existing["r2_key"])
            except Exception:
                logger.warning("stale application staging photo not deleted: %s", existing["r2_key"])
    return {"staged": True}


@router.post("/applications", response_model=ApplicationView, status_code=201)
async def submit_application(
    request: Request,
    body: ApplicationSubmitBody,
    user_id: str = Depends(require_user),
):
    settings = _settings(request)
    consent = body.privacy_consent
    if not consent.accepted:
        raise _err("privacy_consent_required", "개인정보 수집·이용 동의가 필요합니다.")
    if consent.document_version not in ACCEPTED_PRIVACY_VERSIONS:
        raise _err("stale_consent_version", "최신 개인정보 처리 동의를 확인해 주세요.")

    contact_email = _clean_text(body.contact_email, "이메일", required=True, max_len=254)
    if "@" not in contact_email or "." not in contact_email.split("@")[-1]:
        raise _err("invalid_email", "올바른 이메일 주소를 입력해 주세요.")
    applicant_name = _clean_text(body.applicant_name, "이름", required=True, max_len=100)
    region = _clean_text(body.region, "지역", required=True, max_len=100)
    bio = _clean_text(body.bio, "자기소개", required=False)
    portfolio_url = _clean_url(body.portfolio_url, "포트폴리오")
    sns_url = _clean_url(body.sns_url, "SNS")
    categories = _validate_categories(body.categories)
    if body.gender is not None and body.gender not in ("male", "female"):
        raise _err("invalid_gender", "성별 값이 올바르지 않습니다.")
    if body.height_cm is not None and not (100 <= body.height_cm <= 250):
        raise _err("invalid_height", "키는 100~250cm 범위로 입력해 주세요.")

    r2 = _r2_face(request)
    auto_approved = settings.fm_application_auto_approve
    if auto_approved:
        logger.warning(
            "fm_application_auto_approve ON — 관리자 검토 우회(데모/리허설). user=%s", user_id
        )
    new_id = str(uuid.uuid4())

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 스테이징 사진을 지원서 키로 승격(제출 원자성, E11). 사진은 필수.
            await cur.execute(
                "select r2_key, mime_type from fm_model_application_photo_staging "
                "where user_id = %s for update",
                (user_id,),
            )
            staged = await cur.fetchone()
            if not staged:
                raise _err("profile_image_required", "프로필 사진을 먼저 업로드해 주세요.")
            photo_ext = ext_for_mime(staged["mime_type"])
            photo_key = _application_photo_key(new_id, photo_ext)
            # 스테이징 → application 귀속 키로 복사(수명 분리, E11). 원자성 위해 커밋 전.
            r2.copy(staged["r2_key"], photo_key, staged["mime_type"])
            try:
                await cur.execute(
                    """
                    insert into fm_model_applications (
                        id, user_id, status, contact_email, applicant_name, birthdate,
                        region, gender, height_cm, agency_contracted, categories,
                        portfolio_url, sns_url, bio, profile_image_r2_key,
                        privacy_consent_version, reviewed_at
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_id, user_id, "approved" if auto_approved else "under_review",
                        contact_email, applicant_name, body.birthdate, region, body.gender,
                        body.height_cm, body.agency_contracted, Json(categories),
                        portfolio_url, sns_url, bio, photo_key,
                        consent.document_version,
                        datetime.now(timezone.utc) if auto_approved else None,
                    ),
                )
            except UniqueViolation:
                raise _err(
                    "application_active",
                    "이미 검토 중이거나 승인된 지원서가 있습니다.",
                    status=409,
                )
            # 스테이징 행 제거(오브젝트는 photo_key 로 복사됨 — 원본은 orphan cleanup 이 회수).
            await cur.execute(
                "delete from fm_model_application_photo_staging where user_id = %s",
                (user_id,),
            )
        await conn.commit()
        row = await _load_current(conn, user_id)
    # post-commit: 새 지원서 Slack 알림 — auto-approve(관리자 검토 우회)면 스킵.
    if not auto_approved:
        await _dispatch_new_application_slack(
            request, categories=categories, region=region
        )
    return _application_view(row)


@router.get("/applications/current", response_model=ApplicationView)
async def get_current_application(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        row = await _load_current(conn, user_id)
    if row is None:
        raise _err("not_found", "지원서가 없습니다.", status=404)
    return _application_view(row)


@router.post("/applications/{application_id}/cancel", response_model=ApplicationView)
async def cancel_application(
    request: Request, application_id: str, user_id: str = Depends(require_user)
):
    application_id = _canonical_id(application_id)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_model_applications
                set status = 'cancelled', terminated_at = now()
                where id = %s and user_id = %s and status = 'under_review'
                """,
                (application_id, user_id),
            )
            if cur.rowcount == 0:
                raise _err(
                    "invalid_application_state",
                    "취소할 수 있는 지원서가 아닙니다.",
                    status=409,
                )
        await conn.commit()
        row = await _load_current(conn, user_id)
    return _application_view(row)


# --- 관리자 엔드포인트 -------------------------------------------------------


@router.get("/admin/applications", response_model=list[AdminApplicationCard])
async def admin_list_applications(
    request: Request,
    status: str | None = None,
    user_id: str = Depends(require_user),
):
    if status is not None and status not in (
        "under_review", "approved", "rejected", "cancelled"
    ):
        raise _err("invalid_status", "상태 필터가 올바르지 않습니다.")
    # 최근 결정 메일 상태를 lateral 로 붙인다(대시보드 '미발송' 뱃지·재발송, 2A).
    base = f"""
        select a.id::text as id, a.user_id::text as user_id, a.status, a.contact_email,
               a.applicant_name, a.birthdate, a.region, a.gender, a.height_cm,
               a.agency_contracted, a.categories, a.portfolio_url, a.sns_url, a.bio,
               a.profile_image_r2_key, a.identity_mismatch_count,
               a.reviewed_by::text as reviewed_by, a.reviewed_at, a.reject_reason,
               a.created_at, em.last_email_status, em.last_email_type
        from fm_model_applications a
        left join lateral (
            select status as last_email_status, email_type as last_email_type
            from fm_model_application_emails e
            where e.application_id = a.id order by e.created_at desc limit 1
        ) em on true
    """
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            if status:
                await cur.execute(
                    base + " where a.status = %s order by a.created_at desc limit 200",
                    (status,),
                )
            else:
                await cur.execute(base + " order by a.created_at desc limit 200")
            rows = await cur.fetchall()
    return [_admin_card(r) for r in rows]


@router.post("/admin/applications/{application_id}/approve", response_model=AdminApplicationCard)
async def admin_approve_application(
    request: Request, application_id: str, user_id: str = Depends(require_user)
):
    application_id = _canonical_id(application_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            # status 가드 UPDATE — 다른 관리자가 이미 처리했으면 0-row(409).
            await cur.execute(
                """
                update fm_model_applications
                set status = 'approved', reviewed_by = %s, reviewed_at = now(),
                    reject_reason = null
                where id = %s and status = 'under_review'
                returning 1
                """,
                (user_id, application_id),
            )
            if await cur.fetchone() is None:
                raise _err("already_processed", "이미 처리된 지원서입니다.", status=409)
            await cur.execute(
                f"select {_APPLICATION_COLUMNS} from fm_model_applications where id = %s",
                (application_id,),
            )
            row = await cur.fetchone()
        await conn.commit()
    # post-commit: 승인 메일. 상태는 이미 커밋됨(진실원천, 2A).
    await _dispatch_decision_email(
        request, application_id=application_id, to=row["contact_email"],
        email_type="approved", reject_reason=None,
    )
    return _admin_card(row)


@router.post("/admin/applications/{application_id}/reject", response_model=AdminApplicationCard)
async def admin_reject_application(
    request: Request,
    application_id: str,
    body: AdminRejectBody,
    user_id: str = Depends(require_user),
):
    application_id = _canonical_id(application_id)
    reason = _clean_text(body.reason, "거절 사유", required=True, max_len=1000)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_model_applications
                set status = 'rejected', reviewed_by = %s, reviewed_at = now(),
                    reject_reason = %s, terminated_at = now()
                where id = %s and status = 'under_review'
                returning 1
                """,
                (user_id, reason, application_id),
            )
            if await cur.fetchone() is None:
                raise _err("already_processed", "이미 처리된 지원서입니다.", status=409)
            await cur.execute(
                f"select {_APPLICATION_COLUMNS} from fm_model_applications where id = %s",
                (application_id,),
            )
            row = await cur.fetchone()
        await conn.commit()
    await _dispatch_decision_email(
        request, application_id=application_id, to=row["contact_email"],
        email_type="rejected", reject_reason=row.get("reject_reason"),
    )
    return _admin_card(row)


@router.post("/admin/applications/{application_id}/resend-email")
async def admin_resend_email(
    request: Request, application_id: str, user_id: str = Depends(require_user)
):
    """결정 메일 재발송(2A '메일 미발송' 복구). 현재 상태(approved/rejected)에 맞는 메일을 다시 보낸다."""
    application_id = _canonical_id(application_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select status, contact_email, reject_reason "
                "from fm_model_applications where id = %s",
                (application_id,),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "지원서를 찾을 수 없습니다.", status=404)
    if row["status"] not in ("approved", "rejected"):
        raise _err("no_decision_email", "발송할 결정 메일이 없는 상태입니다.", status=409)
    await _dispatch_decision_email(
        request, application_id=application_id, to=row["contact_email"],
        email_type=row["status"], reject_reason=row.get("reject_reason"),
    )
    return {"resent": True}


@router.get("/admin/applications/{application_id}/profile-image")
async def admin_application_photo(
    request: Request, application_id: str, user_id: str = Depends(require_user)
):
    application_id = _canonical_id(application_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select profile_image_r2_key from fm_model_applications where id = %s",
                (application_id,),
            )
            row = await cur.fetchone()
    if row is None or not row["profile_image_r2_key"]:
        raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
    key = row["profile_image_r2_key"]
    r2 = _r2_face(request)
    try:
        data = r2.get_bytes(key)
    except Exception:
        raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
    mime = "image/jpeg"
    if key.endswith(".png"):
        mime = "image/png"
    elif key.endswith(".webp"):
        mime = "image/webp"
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store"})


# --- post-commit 알림 (전부 best-effort — 이미 커밋된 뒤 호출, 절대 예외를 올리지 않는다) ----


async def _dispatch_new_application_slack(
    request: Request, *, categories: list[str], region: str | None
) -> None:
    settings = _settings(request)
    try:
        await facemarket_notify.notify_slack_new_application(
            settings, categories=categories, region=region
        )
    except Exception:
        logger.warning("slack dispatch failed", exc_info=True)


async def _dispatch_decision_email(
    request: Request, *, application_id: str, to: str, email_type: str,
    reject_reason: str | None,
) -> None:
    """메일 발송 원장(pending→sent/failed) + Resend 발송. 실패해도 상태는 유지(2A)."""
    settings = _settings(request)
    try:
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into fm_model_application_emails (application_id, email_type) "
                    "values (%s, %s) returning id::text as id",
                    (application_id, email_type),
                )
                email_id = (await cur.fetchone())["id"]
            await conn.commit()
        ok, message_id, error = await facemarket_notify.send_application_email(
            settings, to=to, email_type=email_type, reject_reason=reject_reason,
        )
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "update fm_model_application_emails "
                    "set status = %s, provider_message_id = %s, error = %s where id = %s",
                    ("sent" if ok else "failed", message_id, error, email_id),
                )
            await conn.commit()
    except Exception:
        logger.warning("email dispatch failed for %s", application_id, exc_info=True)
