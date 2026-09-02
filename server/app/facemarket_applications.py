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

import asyncio
import logging
import uuid

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
# 지원 사진 4종(레퍼런스 정합): 프로필(정면 헤드샷)·클로즈업(측면/3/4)·상반신·전신. 전부 필수.
# 'profile' 이 관리자 썸네일·카탈로그 커버(profile_image_r2_key)로 승격된다.
PHOTO_KINDS = ("profile", "closeup", "waist_up", "full_length")
# 제출에 필수인 종류 — 프로필 1장만(2026-09-02 사용자 결정). 나머지는 올리면 저장하되 요구하지 않는다.
REQUIRED_PHOTO_KINDS = ("profile",)
EXPERIENCE_LEVELS = {"none", "beginner", "intermediate", "professional"}
# 제출 시 확인 서명 3종(전부 true 여야 함) — 에이전시 미소속·성인/진실·사진 본인·최신·무보정.
# 에이전시 관련 확인(noAgency)은 뺐다(2026-09-02 사용자 결정) — 옛 지원서 행에 남아 있어도 무시한다.
ATTESTATION_KEYS = ("adultAndTruthful", "photosAreMine")
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
    phone: str | None = None
    experience_level: str | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None
    # 확인 서명 3종 — 전부 true 필수(에이전시 미소속·성인/진실·사진 본인).
    attestations: dict[str, bool] = {}
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
    phone: str | None = None
    experience_level: str | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None
    # 보유 사진 종류(재지원 시 어떤 슬롯이 이전 사진으로 채워지는지).
    photo_kinds: list[str] = []


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
    phone: str | None = None
    experience_level: str | None = None
    agency_contracted: bool = False
    categories: list[str] = []
    portfolio_url: str | None = None
    sns_url: str | None = None
    bio: str | None = None
    has_profile_image: bool = False
    photo_kinds: list[str] = []
    attestations: dict[str, bool] = {}
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


def _staging_key(user_id: str, kind: str, ext: str) -> str:
    # 종류별 1슬롯이지만 키에 uuid 를 넣어 교체 시 옛 오브젝트를 명시적으로 지운다.
    return f"private/fm-application/staging/{user_id}/{kind}-{uuid.uuid4().hex}.{ext}"


def _application_photo_key(application_id: str, kind: str, ext: str) -> str:
    return f"private/fm-application/{application_id}/{kind}.{ext}"


def _photo_keys(row: dict) -> dict:
    keys = row.get("photo_keys") or {}
    if not isinstance(keys, dict):
        return {}
    # 구버전 행(사진 1장 시절)은 profile_image_r2_key 만 있다 — profile 로 승격해 노출.
    if not keys and row.get("profile_image_r2_key"):
        return {"profile": row["profile_image_r2_key"]}
    return keys


def _mime_for_key(key: str) -> str:
    """저장 키 확장자 → mime(스테이징 없이 이전 지원서 사진을 복사할 때 필요)."""
    if key.endswith(".png"):
        return "image/png"
    if key.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


async def _sweep_orphan_staging_objects(r2, pool, *, max_age_hours: int, limit: int) -> int:
    """스테이징 접두사에서 **DB 행이 참조하지 않는** 오래된 객체를 지운다.

    나열이 실패하면 0을 돌려주고 조용히 넘어간다(다음 주기에 재시도) — 이 스윕은 보조 경로이고,
    실패로 주 sweep 을 깨뜨릴 이유가 없다. 나열은 버킷 전체가 아니라 스테이징 접두사만 훑는다.
    """
    lister = getattr(r2, "list_prefix_aged", None)
    if lister is None:
        return 0
    try:
        keys = await asyncio.to_thread(
            lister, "private/fm-application/staging/", older_than_seconds=max_age_hours * 3600,
        )
    except Exception:
        logger.warning("application staging orphan scan failed")
        return 0
    if not keys:
        return 0
    keys = keys[: limit * 4]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select r2_key from fm_model_application_photo_staging where r2_key = any(%s)",
                (keys,),
            )
            referenced = {r["r2_key"] for r in await cur.fetchall()}
        await conn.commit()
    removed = 0
    for key in keys:
        if key in referenced:
            continue
        try:
            await asyncio.to_thread(r2.delete, key)
            removed += 1
        except Exception:
            logger.warning("orphan application staging object delete failed")
    if removed:
        logger.info("fm application staging orphan sweep removed=%d", removed)
    return removed


async def sweep_application_photo_staging(app, *, max_age_hours: int = 24, limit: int = 100) -> int:
    """미제출 스테이징 사진 회수(스펙 9/E11). 제출 시 스테이징 행은 지워지므로 오래 남은 행은
    지원서를 안 내고 나간 사용자의 사진이다. dispatcher 의 주기 sweep 에서 호출된다."""
    r2 = getattr(app.state, "r2_face", None)
    pool = getattr(app.state, "pool", None)
    if r2 is None or pool is None:
        return 0
    # 순서가 중요하다: **행을 먼저 지우고 커밋한 뒤** R2 객체를 지운다.
    #  · skip locked 를 유지해야 in-flight 제출(스테이징 행을 for update 로 잠근 채 r2.copy 를
    #    도는 중)이 건너뛰어진다. 잠금을 안 보면 sweep 이 그 원본을 지워 copy 가 NoSuchKey 로
    #    터지고 제출이 500 으로 실패한다(행까지 사라져 사진을 다시 올려야 한다).
    #  · 삭제를 한 문장으로 끝내 잠금 구간에 네트워크 I/O 를 넣지 않는다(r2.py §5).
    #  · 행을 먼저 지우므로 R2 삭제가 실패하면 객체가 남지만, 그건 계정 삭제 파기의 접두사
    #    스윕이 회수한다. 반대 순서(객체 먼저)는 제출과 경합해 사진을 잃을 수 있어 더 나쁘다.
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                delete from fm_model_application_photo_staging
                 where ctid in (
                    select ctid from fm_model_application_photo_staging
                     where created_at < now() - make_interval(hours => %s)
                     order by created_at limit %s
                     for update skip locked
                 )
                 returning r2_key
                """,
                (max_age_hours, limit),
            )
            rows = await cur.fetchall()
        await conn.commit()

    removed = 0
    for row in rows:
        try:
            await asyncio.to_thread(r2.delete, row["r2_key"])
            removed += 1
        except Exception:
            logger.warning("stale application staging photo delete failed")  # 키는 남기지 않는다(user_id 포함)

    # 행이 아예 없는 고아도 회수한다. 업로드는 트랜잭션 밖에서 먼저 일어나므로(이벤트 루프를
    # 막지 않으려면 그래야 한다), 커넥션 확보 실패·같은 kind 동시 업로드 경합에서는 객체만
    # 남고 행이 없는 상태가 생긴다. 그건 행 기반 스캔으로 영영 못 잡는다.
    # 나이 기준(max_age_hours)을 넘긴 것만 본다 — 진행 중 업로드를 지우지 않기 위해서다.
    removed += await _sweep_orphan_staging_objects(r2, pool, max_age_hours=max_age_hours, limit=limit)
    if removed:
        logger.info("fm application staging sweep removed=%d", removed)
    return removed


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
        phone=row.get("phone"),
        experience_level=row.get("experience_level"),
        agency_contracted=row.get("agency_contracted", False),
        categories=list(row.get("categories") or []),
        portfolio_url=row.get("portfolio_url"),
        sns_url=row.get("sns_url"),
        bio=row.get("bio"),
        photo_kinds=sorted(_photo_keys(row).keys(), key=PHOTO_KINDS.index),
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
        phone=row.get("phone"),
        experience_level=row.get("experience_level"),
        agency_contracted=row.get("agency_contracted", False),
        categories=list(row.get("categories") or []),
        portfolio_url=row.get("portfolio_url"),
        sns_url=row.get("sns_url"),
        bio=row.get("bio"),
        has_profile_image=bool(row.get("profile_image_r2_key")),
        photo_kinds=sorted(_photo_keys(row).keys(), key=PHOTO_KINDS.index),
        attestations=dict(row.get("attestations") or {}),
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
    birthdate, region, gender, height_cm, phone, experience_level, agency_contracted,
    categories, portfolio_url, sns_url, bio, profile_image_r2_key, photo_keys, attestations,
    identity_mismatch_count, reviewed_by::text as reviewed_by, reviewed_at, reject_reason,
    created_at, updated_at
"""


async def _load_current(conn, user_id: str) -> dict | None:
    """유저의 활성 지원서(있으면), 없으면 가장 최근 터미널 지원서(상태 허브·재지원 프리필용).

    30일 sweep 으로 익명화된 행은 **프리필 후보에서 뺀다**. 안 빼면 재지원 화면에 성 '삭제된',
    이름 '지원자', 이메일 purged@invalid, 생년월일 1900-01-01 이 미리 채워지고, 그대로 내면
    invalid_email 로 막힌다 — 자기가 넣은 적 없는 값 때문에 제출이 실패하는 화면이 된다.
    활성 지원서는 익명화 대상이 아니므로(터미널만 스윕) 상태 허브 표시에는 영향이 없다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            select {_APPLICATION_COLUMNS} from fm_model_applications
            where user_id = %s and contact_email <> %s
            order by (status in ('under_review','approved')) desc, created_at desc
            limit 1
            """,
            (user_id, PURGED_EMAIL),
        )
        return await cur.fetchone()


async def _require_admin(conn, user_id: str) -> None:
    if not await repo.is_admin(conn, user_id):
        raise _err("forbidden", "관리자만 가능해요.", status=403)


# --- 지원자 엔드포인트 -------------------------------------------------------


@router.get("/applications/config")
async def application_config(request: Request):
    """지원서 게이트 활성 여부 — 프론트가 신규 진입을 /model/apply 로 보낼지 판정.
    생체등록 라우터(/config)와 독립적으로 항상 제공된다(지원서는 생체 스택과 무관)."""
    return {"applicationRequired": _settings(request).fm_application_required}


@router.post("/applications/photo-staging", status_code=201)
async def stage_application_photo(
    request: Request,
    image: UploadFile = File(...),
    kind: str = Form("profile"),
    user_id: str = Depends(require_user),
):
    """제출 전 지원 사진을 종류별(profile/closeup/waist_up/full_length)로 임시 저장 — 슬롯당 1장,
    재업로드 시 교체. 미제출 시 orphan cleanup 회수(E11)."""
    kind = (kind or "").strip().lower()
    if kind not in PHOTO_KINDS:
        raise _err("invalid_photo_kind", "사진 종류가 올바르지 않습니다.")
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
    new_key = _staging_key(user_id, kind, ext)
    # 업로드는 트랜잭션 **밖**에서 끝낸다 — 25MB 업로드를 FOR UPDATE 를 쥔 채로 하면 그 시간만큼
    # 행이 잠기고, 동기 boto3 라 to_thread 없이는 이벤트 루프까지 멈춘다(2026-08-26 ECS 동결).
    # 업로드가 성공했는데 아래 INSERT 가 실패하면(풀 고갈로 커넥션을 못 잡는 경우 등) 행 없는
    # 객체가 남는다. 그건 아래 _sweep_orphan_staging_objects 가 나이 기준으로 회수하고,
    # 계정 삭제 파기의 접두사 스윕도 같은 경로를 훑는다.
    await asyncio.to_thread(r2.put_bytes, new_key, data, mime)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select r2_key from fm_model_application_photo_staging "
                "where user_id = %s and kind = %s for update",
                (user_id, kind),
            )
            existing = await cur.fetchone()
            await cur.execute(
                """
                insert into fm_model_application_photo_staging (user_id, kind, r2_key, mime_type, byte_size)
                values (%s, %s, %s, %s, %s)
                on conflict (user_id, kind) do update
                  set r2_key = excluded.r2_key, mime_type = excluded.mime_type,
                      byte_size = excluded.byte_size, created_at = now()
                """,
                (user_id, kind, new_key, mime, len(data)),
            )
        await conn.commit()
        # 옛 스테이징 오브젝트는 커밋 후 정리(실패해도 orphan cleanup 이 나중에 회수).
        if existing and existing["r2_key"] and existing["r2_key"] != new_key:
            try:
                await asyncio.to_thread(r2.delete, existing["r2_key"])
            except Exception:
                # 키에는 user_id 가 들어 있다(_staging_key) — 카운트/사실만 남긴다.
                logger.warning("stale application staging photo not deleted")
    return {"staged": True, "kind": kind}


PII_RETENTION_DAYS = 30
PURGED_NAME = "삭제된 지원자"
PURGED_EMAIL = "purged@invalid"
# birthdate·region 은 NOT NULL 이다(마이그 20260902120000). 익명화는 null 이 아니라 센티널로
# 밀어야 한다 — null 로 밀면 NotNullViolation 으로 sweep 전체가 실패하고 PII 가 그대로 남는다.
PURGED_BIRTHDATE = "1900-01-01"
PURGED_REGION = "-"


async def sweep_terminal_application_pii(app, *, retention_days: int = PII_RETENTION_DAYS,
                                         limit: int = 100) -> int:
    """터미널 지원서(거절·취소)의 PII 를 30일 뒤 익명화하고 사진을 지운다(스펙 11 / 3A).

    승인된 지원서는 운영 데이터로 남긴다 — 지우는 건 rejected·cancelled 뿐이다. 계정 삭제 경로
    (biometric_purge)와는 별개다: 저쪽은 '이 사람 것 전부', 이쪽은 '심사가 끝나고 시간이 지난 건'.

    이 sweep 이 없던 동안 실명·생년월일·연락처가 무기한 남았다. 마이그레이션이 이 스캔을 위해
    부분 인덱스(fm_model_applications_terminated_due)를 미리 만들어 뒀다.
    행을 지우지 않고 익명화하는 이유는 처리 이력(검토 건수)과 유니크 제약을 깨지 않기 위해서다.
    """
    r2 = getattr(app.state, "r2_face", None)
    pool = getattr(app.state, "pool", None)
    if pool is None:
        return 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id::text as id, photo_keys, profile_image_r2_key
                  from fm_model_applications
                 where status in ('rejected', 'cancelled')
                   and terminated_at is not null
                   and terminated_at < now() - make_interval(days => %s)
                   and contact_email <> %s
                 order by terminated_at
                 limit %s
                """,
                (retention_days, PURGED_EMAIL, limit),
            )
            rows = await cur.fetchall()
        await conn.commit()
    if not rows:
        return 0

    # R2 삭제는 트랜잭션 밖에서, 동기 boto3 라 to_thread 로(r2.py §5). 삭제가 실패해도 익명화는
    # 진행한다 — 텍스트 PII 를 남기는 것보다 낫고, 남은 오브젝트는 계정 삭제 파기의 접두사
    # 스윕이 회수한다. 키는 로그에 남기지 않는다(user_id·지원서 id 가 들어 있다).
    for row in rows:
        keys = set(_photo_keys(row).values())
        if row.get("profile_image_r2_key"):
            keys.add(row["profile_image_r2_key"])
        for key in keys:
            if not key or r2 is None:
                continue
            try:
                await asyncio.to_thread(r2.delete, key)
            except Exception:
                logger.warning("terminal application photo delete failed")

    anonymized = 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_model_applications
                   set contact_email = %s, applicant_name = %s,
                       birthdate = %s, region = %s,
                       phone = null, bio = null,
                       portfolio_url = null, sns_url = null,
                       profile_image_r2_key = null, photo_keys = '{}'::jsonb
                 where id = any(%s)
                """,
                (PURGED_EMAIL, PURGED_NAME, PURGED_BIRTHDATE, PURGED_REGION,
                 [r["id"] for r in rows]),
            )
            anonymized = cur.rowcount or 0
        await conn.commit()
    if anonymized:
        logger.info("fm terminal application pii sweep anonymized=%d", anonymized)
    return anonymized


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
    if body.experience_level is not None and body.experience_level not in EXPERIENCE_LEVELS:
        raise _err("invalid_experience", "경력 수준 값이 올바르지 않습니다.")
    phone = _clean_text(body.phone, "전화번호", required=False, max_len=40)
    # 확인 서명 3종(레퍼런스 정합): 에이전시 미소속·성인/진실·사진 본인. 전부 동의해야 제출.
    for key in ATTESTATION_KEYS:
        if not body.attestations.get(key):
            raise _err("attestation_required", "제출 전 확인 항목에 모두 동의해 주세요.")
    attestations = {key: True for key in ATTESTATION_KEYS}

    r2 = _r2_face(request)
    auto_approved = settings.fm_application_auto_approve
    if auto_approved:
        logger.warning(
            "fm_application_auto_approve ON — 관리자 검토 우회(데모/리허설). user=%s", user_id
        )
    new_id = str(uuid.uuid4())

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 사진 4종 전부 필수(레퍼런스 정합). 종류별 원본 = 스테이징, 없으면 재지원 프리필
            # (스펙 5): 최근 터미널(거절/취소) 지원서의 같은 종류 사진을 30일 내면 새 키로 복사한다 —
            # 이전 지원서의 30일 익명화가 새 지원서 사진을 지우지 않게 수명을 분리한다.
            await cur.execute(
                "select kind, r2_key, mime_type from fm_model_application_photo_staging "
                "where user_id = %s for update",
                (user_id,),
            )
            staged = {r["kind"]: r for r in await cur.fetchall()}
            await cur.execute(
                "select photo_keys, profile_image_r2_key from fm_model_applications "
                "where user_id = %s and status in ('rejected', 'cancelled') "
                "and terminated_at >= now() - interval '30 days' "
                "order by terminated_at desc limit 1",
                (user_id,),
            )
            prev_row = await cur.fetchone()
            prev_keys = _photo_keys(prev_row) if prev_row else {}
            sources: dict[str, tuple[str, str]] = {}
            for kind in PHOTO_KINDS:
                if kind in staged:
                    sources[kind] = (staged[kind]["r2_key"], staged[kind]["mime_type"])
                elif prev_keys.get(kind):
                    sources[kind] = (prev_keys[kind], _mime_for_key(prev_keys[kind]))
            missing = [k for k in REQUIRED_PHOTO_KINDS if k not in sources]
            if missing:
                raise _err(
                    "profile_image_required",
                    "프로필 사진을 올려 주세요.",
                    missing=missing,
                )
            photo_keys: dict[str, str] = {}
            for kind, (src_key, src_mime) in sources.items():
                dst = _application_photo_key(new_id, kind, ext_for_mime(src_mime))
                # 원본(스테이징 또는 이전 지원서) → application 귀속 키로 복사. 행보다 먼저 만들어야
                # INSERT 가 성공한 순간 사진이 이미 제자리에 있다. 동기 boto3 라 to_thread 로 감싼다.
                await asyncio.to_thread(r2.copy, src_key, dst, src_mime)
                photo_keys[kind] = dst
            try:
                await cur.execute(
                    """
                    insert into fm_model_applications (
                        id, user_id, status, contact_email, applicant_name, birthdate,
                        region, gender, height_cm, phone, experience_level, agency_contracted,
                        categories, portfolio_url, sns_url, bio, profile_image_r2_key, photo_keys,
                        attestations, privacy_consent_version, reviewed_at
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_id, user_id, "approved" if auto_approved else "under_review",
                        contact_email, applicant_name, body.birthdate, region, body.gender,
                        body.height_cm, phone, body.experience_level, body.agency_contracted,
                        Json(categories), portfolio_url, sns_url, bio, photo_keys["profile"],
                        Json(photo_keys), Json(attestations), consent.document_version,
                        datetime.now(timezone.utc) if auto_approved else None,
                    ),
                )
            except UniqueViolation:
                # 동시 이중 제출. 이 행은 안 생기므로 방금 복사한 사본은 참조가 없다 — 바로 지운다.
                # 안 지우면 어떤 파기 경로도 도달 못 하는 고아 얼굴 사진이 된다.
                for orphan in photo_keys.values():
                    try:
                        await asyncio.to_thread(r2.delete, orphan)
                    except Exception:
                        logger.warning("orphan application photo not deleted after 409")
                raise _err(
                    "application_active",
                    "이미 검토 중이거나 승인된 지원서가 있습니다.",
                    status=409,
                )
            # 스테이징 행 제거. 오브젝트 원본은 커밋 뒤 아래에서 지운다.
            await cur.execute(
                "delete from fm_model_application_photo_staging where user_id = %s",
                (user_id,),
            )
        await conn.commit()
        row = await _load_current(conn, user_id)
    # 커밋 후: 복사에 쓴 스테이징 **원본**을 지운다. r2.copy 는 copy_object 라 원본이 남는데,
    # 위에서 스테이징 행을 지웠으므로 sweep 은 그 오브젝트에 영영 도달하지 못한다(행 기반 스캔).
    # 여기서 안 지우면 제출 한 건마다 얼굴 사진이 R2 에 영구 고아로 쌓인다.
    # 실패해도 제출은 유효하다 — 남은 고아는 파기 경로가 접두사 스윕으로 회수한다.
    for staged_row in staged.values():
        try:
            await asyncio.to_thread(r2.delete, staged_row["r2_key"])
        except Exception:
            logger.warning("staged source photo not deleted after submit")
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
               a.phone, a.experience_level,
               a.agency_contracted, a.categories, a.portfolio_url, a.sns_url, a.bio,
               a.profile_image_r2_key, a.photo_keys, a.attestations, a.identity_mismatch_count,
               a.reviewed_by::text as reviewed_by, a.reviewed_at, a.reject_reason,
               a.created_at, em.last_email_status, em.last_email_type
        from fm_model_applications a
        left join lateral (
            -- 오래 pending 인 행은 '미발송'으로 본다. 원장은 pending 으로 넣고 발송 뒤 sent/failed 로
            -- 바꾸는데, 그 사이에 태스크가 죽거나 요청이 취소되면(CancelledError 는 BaseException 이라
            -- except Exception 이 못 잡는다) pending 으로 굳는다. 그러면 대시보드에 '미발송' 뱃지도
            -- 재발송 버튼도 안 떠서 메일이 안 갔다는 사실 자체가 보이지 않는다.
            select case when status = 'pending' and created_at < now() - interval '2 minutes'
                        then 'failed' else status end as last_email_status,
                   email_type as last_email_type
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
                """
                select a.status, a.contact_email, a.reject_reason,
                       (select email_type from fm_model_application_emails e
                        where e.application_id = a.id order by e.created_at desc limit 1)
                       as last_email_type
                from fm_model_applications a where a.id = %s
                """,
                (application_id,),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "지원서를 찾을 수 없습니다.", status=404)
    if row["status"] not in ("approved", "rejected"):
        raise _err("no_decision_email", "발송할 결정 메일이 없는 상태입니다.", status=409)
    # 종류는 원장의 마지막 메일을 따른다. status 로만 고르면 신분증 대조 3회 실패로 자동 거절된
    # 지원서(auto_rejected)의 재발송이 관리자 거절 템플릿으로 바뀌어, 사유가 없는데 사유 자리가
    # 빈 메일이 나간다.
    email_type = row.get("last_email_type") or row["status"]
    if email_type not in ("approved", "rejected", "auto_rejected"):
        email_type = row["status"]
    await _dispatch_decision_email(
        request, application_id=application_id, to=row["contact_email"],
        email_type=email_type, reject_reason=row.get("reject_reason"),
    )
    return {"resent": True}


@router.get("/admin/applications/{application_id}/profile-image")
async def admin_application_photo(
    request: Request,
    application_id: str,
    kind: str = "profile",
    user_id: str = Depends(require_user),
):
    """관리자 지원 사진 스트림. ?kind=profile|closeup|waist_up|full_length (기본 profile)."""
    application_id = _canonical_id(application_id)
    if kind not in PHOTO_KINDS:
        raise _err("invalid_photo_kind", "사진 종류가 올바르지 않습니다.")
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select profile_image_r2_key, photo_keys from fm_model_applications where id = %s",
                (application_id,),
            )
            row = await cur.fetchone()
    key = _photo_keys(row).get(kind) if row else None
    if not key:
        raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
    r2 = _r2_face(request)
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
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
