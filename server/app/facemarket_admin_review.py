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
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from . import admin_guard
from .auth import require_user
from .db import get_conn
from .facemarket_enrollment import (
    _readable_photo as readable_photo,
    EnrollmentMappedError,
    _assert_account_open,
    _reject_cutover_closed,
    _wake_dispatcher,
    bind_model_and_enqueue_asset_build,
    notify_enrollment_decision,
    required_slots_for_consent,
)
from .facemarket_id_document import purge_id_document
from .facemarket_photos import PHOTO_SLOTS, lighting_of, photo_slot_candidates
from .models import CamelModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket/admin", tags=["FaceMarket admin review"])

REVIEW_STATUSES = ("pending", "approved", "rejected")
# 화이트리스트 — 절대 클라이언트 문자열을 그대로 R2 키에 꽂지 않는다.
# 심사 화면의 이름은 그대로 둔다(정면·45도·측면). 실제 행 이름은 등록 회차마다 다르므로
# photo_slot_candidates 로 풀어 쓴다 — 16칸 스펙은 sh_front·sh_34·sh_side 다.
PHOTO_ANGLES = ("front", "angle45", "side")
#: 전체 사진 확인(2026-09-15) — 18칸을 그대로 열람한다. 학습 전에 사람이 품질을 보는 자리다.
#: 옛 이름 3개(front·angle45·side)는 그대로 둔다 — 기존 심사 화면이 그 이름으로 부른다.
FULL_PHOTO_KINDS: tuple[str, ...] = tuple(PHOTO_SLOTS)
IMAGE_KINDS = ("id_document",) + PHOTO_ANGLES + FULL_PHOTO_KINDS

# 이 라우터가 볼 수 있는 등록의 범위. `review_status` 가 채워진 행 = 실제로 사람 심사에
# 들어온 등록뿐이다. 이 술어가 없으면 관리자 카드·이미지 라우트가 **등록 id 하나만 알면
# 모든 등록의 생체 사진 3장**(mid 경로 포함)을 스트리밍한다 — 이 브랜치 이전엔 관리자에게
# 등록 사진을 주는 라우트가 아예 없었고, 라우터는 FM_IDENTITY_METHODS 가 아니라
# fm_biometric_enrollment_enabled 로 마운트돼 간편인증이 꺼진 프로덕션에서도 살아 있다
# (최종리뷰 I6). 열람 자체도 감사 기록을 남긴다(처리방침 §접속기록).
REVIEW_SCOPE = " and review_status is not null"

#: 전체 18칸을 열 수 있는 범위. **이것도 범위 술어다** — 아무 등록 id 로나 사진이 흐르면 안 된다.
#: 열리는 때: 통과한 등록(decision='passed') 중 사진 확인이 아직 안 끝났거나
#: (pending·reshoot_requested), 그 모델에 켜진 LoRA 가 아직 없을 때(= 학습 대기·학습 중).
#: 확인이 끝나고 LoRA 가 살아 있으면 다시 3장으로 좁아진다 — 그 뒤의 열람은 심사가 아니라 구경이다.
#:
#: REVIEW_SCOPE(review_status is not null)를 쓰지 않는 이유: 표준인증(mid) 등록은 사람 심사를
#: 거치지 않아 review_status 가 null 이다. 그 범위로 묶으면 프로덕션 주 경로(mid)의 사진을
#: 관리자가 **아예 못 보고**, 확인이 안 되니 학습 내보내기도 영원히 막힌다.
PHOTO_REVIEW_PREDICATE = """(
        {a}decision = 'passed'
        and (
            coalesce({a}photo_review_status, 'pending') in ('pending', 'reshoot_requested')
            or not exists (
                select 1 from fm_model_loras l
                where l.model_id = {a}model_id and l.enabled and l.status = 'ready'
            )
        )
    )"""
FULL_PHOTO_SCOPE = "\n    and " + PHOTO_REVIEW_PREDICATE.format(a="e.") + "\n"
#: 카드 조회 범위 — 심사 큐에 들어온 등록(간편인증) **또는** 사진 확인 대상(mid 포함).
CARD_SCOPE = (" and (review_status is not null or "
              + PHOTO_REVIEW_PREDICATE.format(a="fm_biometric_enrollments.") + ")")
#: 프런트가 "지금 전체 칸을 볼 수 있는가"를 알아야 한다 — 볼 수 없는 칸을 그려 놓고 404 를
#: 받으면 "사진이 파기됐다" 와 구분이 안 된다. 범위 술어와 **같은 식**을 컬럼으로 낸다.
FULL_PHOTO_VISIBLE_COLUMN = (PHOTO_REVIEW_PREDICATE.format(a="fm_biometric_enrollments.")
                             + " as full_photos_visible")

ENROLLMENT_CARD_COLUMNS = """
    id::text as id, user_id::text as user_id, model_id::text as model_id,
    identity_method, review_status, status, match_scores,
    application_id::text as application_id,
    identity_name_masked, identity_birth_year, mask_mode,
    consent_version,
    coalesce(photo_review_status, 'pending') as photo_review_status,
    photo_reviewed_at, reshoot_slots,
    """ + FULL_PHOTO_VISIBLE_COLUMN + """,
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


class ReshootSlot(CamelModel):
    """다시 찍어야 할 칸 하나 — 관리자가 고르고, 모델 화면이 그대로 읽는다."""
    slot: str
    reason: str = ""


def _reshoot_slots(raw) -> list["ReshootSlot"]:
    """jsonb 컬럼 → 모델. 모양이 깨진 행은 조용히 버린다(화면이 죽는 것보다 낫다)."""
    out: list[ReshootSlot] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "")
        if slot not in PHOTO_SLOTS:
            continue
        out.append(ReshootSlot(slot=slot, reason=str(item.get("reason") or "")))
    return out


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
    # 지원서 이름·생년월일이 신분증과 몇 번 안 맞았는지(facemarket_enrollment.py 의
    # 지원서 대조 블록, :1170 근처) — identity_method 로 갈리지 않는다: 게이트는
    # settings.fm_application_required and application_id 로만 걸려 있어 simple_auth
    # 등록도 이 카운터가 오른다. 이미 한두 번 어긋난 신원 주장은 사람 심사가 봐야 할
    # 신호라 카드에 낸다(fix round 1).
    identity_mismatch_count: int = 0
    # 프런트가 지원서 프로필 사진을 무턱대고 요청했다가 404 를 "정상적인 없음"으로
    # 다루지 않게 한다 — AdminApplications.jsx 의 hasProfileImage 게이트(ApplicantPhoto)
    # 와 같은 관례: 있을 때만 fetch 를 건다(fix round 1).
    has_profile_image: bool = False


class AdminReviewQueueRow(CamelModel):
    id: str
    identity_method: str
    review_status: str | None = None
    status: str
    created_at: datetime


class PhotoReviewQueueRow(CamelModel):
    """학습 전 사진 확인 큐 한 줄. 심사 큐(review_status)와 **다른 축**이다 —
    표준인증(mid) 등록은 사람 심사를 안 거쳐 review_status 가 null 이지만 사진 확인은
    똑같이 받아야 한다."""
    id: str
    identity_method: str
    status: str
    photo_review_status: str
    reshoot_slot_count: int = 0
    completed_at: datetime | None = None
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
    # 지원서 사진(profile-image)을 화면에서 보여주려면 프런트가 기존 관리자 지원서
    # 사진 라우트(GET /admin/applications/{id}/profile-image, facemarket_applications.py)
    # 를 직접 부를 수 있어야 한다 — 새 이미지 라우트를 만들지 않고 그 라우트를 재사용한다
    # (같은 admin_guard.require_admin, 같은 private/no-store). application 이 없으면 null.
    application_id: str | None = None
    # 캐리어가 증명한 신원(Task6) — 지원서 자기신고(application.applicantName/birthdate)
    # 와 다르다: 이 값은 위조할 수 없는 본인확인 결과다. "이 카드가 방금 인증된 그
    # 사람 것인가"라는 심사 질문에 필요한 건 자기신고가 아니라 이 값이라 카드 사진
    # 옆에 나란히 낸다(Task9). 개인정보 최소화로 이미 이름은 마스킹, 생일은 연도만이다.
    identity_name_masked: str | None = None
    identity_birth_year: str | None = None
    # 신분증 마스킹 기하 검증(facemarket_id_mask_verify)의 판정. 'auto' 는 서버가 마스킹
    # 위치를 확인함, 'manual'/None 은 확인하지 못했거나(shadow 라 업로드 자체는 막지
    # 않음) 애초에 검사가 안 돌았음(FM_ID_MASK_VERIFY=off, 또는 이 컬럼이 생기기 전 행) —
    # 어느 쪽이든 "확인됨"이 아니므로 프런트는 이 둘을 하나로 묶어 배지를 낸다(Task9).
    mask_mode: str | None = None
    #: 이 등록이 동의한 판(2026-09-v2=16칸, v3=18칸) 기준 칸 목록 — 화면이 그릴 순서 그대로.
    consent_version: str | None = None
    photo_slots: list[str] = []
    #: 학습 전 사진 확인. pending | approved | reshoot_requested.
    photo_review_status: str = "pending"
    photo_reviewed_at: datetime | None = None
    reshoot_slots: list["ReshootSlot"] = []
    #: 지금 전체 칸을 스트리밍할 수 있는가(FULL_PHOTO_SCOPE 와 같은 식).
    full_photos_visible: bool = False
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
            "phone, categories, identity_mismatch_count, profile_image_r2_key "
            "from fm_model_applications where id = %s",
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
        identity_mismatch_count=row.get("identity_mismatch_count") or 0,
        has_profile_image=bool(row.get("profile_image_r2_key")),
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
        application_id=row.get("application_id"),
        identity_name_masked=row.get("identity_name_masked"),
        identity_birth_year=row.get("identity_birth_year"),
        mask_mode=row.get("mask_mode"),
        consent_version=row.get("consent_version"),
        photo_slots=list(required_slots_for_consent(row.get("consent_version"))),
        photo_review_status=row.get("photo_review_status") or "pending",
        photo_reviewed_at=row.get("photo_reviewed_at"),
        reshoot_slots=_reshoot_slots(row.get("reshoot_slots")),
        full_photos_visible=bool(row.get("full_photos_visible")),
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
        # `review_status` 만 보면 **취소·만료된 행이 대기 큐에 영원히 남는다**:
        # cancel_enrollment 는 review_pending 을 받아 주면서 review_status 를 안 지웠고
        # (이제는 지운다), 그 행에 승인을 누르면 상태 가드 UPDATE 가 0-row → 409 다.
        # 지금은 두 겹으로 막는다 — 취소 시 review_status 를 null 로 만들고, 대기 큐는
        # status='review_pending' 인 행만 센다(최종리뷰 I5). 승인·거절 필터는 결정 이후의
        # 상태(processing/asset_building/… , failed)가 다양하므로 상태를 걸지 않는다.
        pending_only = " and status = 'review_pending'" if review == "pending" else ""
        async with conn.cursor() as cur:
            # created_at desc — 마이그레이션의 fm_biometric_review_queue 부분 인덱스와 정렬을 맞춘다.
            await cur.execute(
                f"""
                select id::text as id, identity_method, review_status, status, created_at
                from fm_biometric_enrollments
                where review_status = %s{pending_only}
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


PHOTO_REVIEW_FILTERS = {
    # 확인이 남은 것 — 대기 + 재촬영 요청(모델이 다시 올리길 기다리는 중)을 한 줄로 본다.
    "awaiting": "coalesce(photo_review_status, 'pending') in ('pending', 'reshoot_requested')",
    "approved": "photo_review_status = 'approved'",
}


@router.get("/enrollments/photo-review", response_model=list[PhotoReviewQueueRow])
async def list_photo_review_queue(
    request: Request,
    status: str = Query("awaiting", description="awaiting|approved"),
    user_id: str = Depends(require_user),
):
    """학습 전 사진 확인 큐. 통과한 등록(decision='passed')만 — 아직 진행 중이거나 실패한
    등록의 사진에는 확인 도장을 찍을 이유가 없다.

    라우트 순서 주의: `/enrollments/{enrollment_id}` 보다 **위**에 있어야 한다. 아래에 두면
    'photo-review' 가 enrollment_id 로 잡혀 404 가 된다.
    """
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        where = PHOTO_REVIEW_FILTERS.get(status)
        if where is None:
            raise _err("invalid_review_filter", "사진 확인 필터가 올바르지 않습니다.")
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                select id::text as id, identity_method, status,
                       coalesce(photo_review_status, 'pending') as photo_review_status,
                       coalesce(jsonb_array_length(reshoot_slots), 0) as reshoot_slot_count,
                       completed_at, created_at
                from fm_biometric_enrollments
                where decision = 'passed' and {where}
                order by completed_at desc nulls last, created_at desc
                limit 200
                """,
            )
            rows = await cur.fetchall()
    return [
        PhotoReviewQueueRow(
            id=row["id"],
            identity_method=row.get("identity_method") or "mid",
            status=row["status"],
            photo_review_status=row["photo_review_status"],
            reshoot_slot_count=row.get("reshoot_slot_count") or 0,
            completed_at=row.get("completed_at"),
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/enrollments/{enrollment_id}", response_model=AdminReviewCard)
async def get_review_card(
    request: Request, enrollment_id: str, user_id: str = Depends(require_user)
):
    """심사 대상 등록의 카드. **심사에 들어온 등록만** 조회할 수 있다 —
    `REVIEW_SCOPE` 주석 참조(최종리뷰 I6)."""
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {ENROLLMENT_CARD_COLUMNS} from fm_biometric_enrollments "
                f"where id = %s{CARD_SCOPE}",
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
    프록시·CDN·브라우저 캐시 어디에도 남지 않게).

    범위는 `REVIEW_SCOPE`(심사에 들어온 등록)로 제한하고, **열람할 때마다 감사 행을
    남긴다** — 이 라우트는 등록자의 신분증·얼굴 사진을 사람이 볼 수 있는 유일한 자리다.
    승인·거절은 감사 기록을 남기는데 열람만 안 남기면, 누가 무엇을 봤는지 아무 데도 없다
    (최종리뷰 I6, 처리방침 §접속기록 2년)."""
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        if kind not in IMAGE_KINDS:
            raise _err("invalid_image_kind", "이미지 종류가 올바르지 않습니다.")
        async with conn.cursor() as cur:
            if kind == "id_document":
                await cur.execute(
                    "select id_document_r2_key from fm_biometric_enrollments "
                    f"where id = %s{REVIEW_SCOPE}",
                    (enrollment_id,),
                )
                found = await cur.fetchone()
                key = found.get("id_document_r2_key") if found else None
                mime_hint = None
            else:
                candidates = list(photo_slot_candidates(kind))
                # 옛 이름 3장은 기존 심사 범위 그대로. 나머지 칸은 **학습 전 확인 범위**에서만 열린다.
                scope = (" and e.review_status is not null" if kind in PHOTO_ANGLES
                         else FULL_PHOTO_SCOPE)
                await cur.execute(
                    "select p.r2_key, p.normalized_r2_key, p.mime_type "
                    "from fm_biometric_enrollment_photos p "
                    "join fm_biometric_enrollments e on e.id = p.enrollment_id "
                    "where p.enrollment_id = %s and p.angle = any(%s) "
                    f"{scope} "
                    "order by array_position(%s, p.angle) limit 1",
                    (enrollment_id, candidates, candidates),
                )
                found = await cur.fetchone()
                # 정규화본을 낸다 — 원본이 HEIC 면 브라우저가 못 그려서 심사자는 빈 칸을 본다.
                key, mime_hint = readable_photo(found) if found else (None, None)
        # 키를 못 찾아도(파기됨·범위 밖) 시도 자체를 남긴다 — "무엇을 보려 했는가" 도 기록이다.
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="enrollment_review_image_view",
            target_type="enrollment",
            target_id=enrollment_id,
            note=kind,
        )
        await conn.commit()
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
        await _bind_and_enqueue_on_conn(conn, settings, enrollment_id)


async def _bind_and_enqueue_on_conn(conn, settings, enrollment_id: str) -> None:
    """요청 컨텍스트 없이도 쓸 수 있는 본체(재조정 스윕이 같은 코드를 재사용한다)."""
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


class ReshootRequest(CamelModel):
    slots: list[ReshootSlot]


class PhotoReviewResult(CamelModel):
    photo_review_status: str
    reshoot_slots: list[ReshootSlot] = []


@router.post("/enrollments/{enrollment_id}/photos/approve", response_model=PhotoReviewResult)
async def approve_enrollment_photos(
    request: Request, enrollment_id: str, user_id: str = Depends(require_user)
):
    """사진 확인 완료 — 이 뒤로 학습 내보내기가 열린다(fm_export_training_set 게이트).

    전체 18칸 열람도 여기서 닫힌다(FULL_PHOTO_SCOPE). 확인이 끝났는데 계속 열어 두면
    그 뒤의 열람은 심사가 아니라 구경이다.
    """
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set photo_review_status = 'approved', photo_reviewed_by = %s,
                    photo_reviewed_at = now(), reshoot_slots = null
                where id = %s and decision = 'passed' 
                returning coalesce(photo_review_status, 'pending') as photo_review_status
                """,
                (user_id, enrollment_id),
            )
            updated = await cur.fetchone()
        if updated is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="enrollment_photo_review_approve",
            target_type="enrollment",
            target_id=enrollment_id,
            after={"photoReviewStatus": "approved"},
        )
        await conn.commit()
    return PhotoReviewResult(photo_review_status="approved", reshoot_slots=[])


@router.post("/enrollments/{enrollment_id}/photos/reshoot", response_model=PhotoReviewResult)
async def request_enrollment_reshoot(
    request: Request, enrollment_id: str, body: ReshootRequest,
    user_id: str = Depends(require_user),
):
    """다시 찍어야 할 칸을 지정한다. 모델 화면이 그 칸만 다시 받는다.

    칸 이름은 **화이트리스트**(PHOTO_SLOTS)만 — 클라이언트 문자열이 그대로 저장돼 화면에
    뿌려지면 안 된다. 사유는 모델이 읽는 문장이라 길이만 자른다.
    """
    slots = list(body.slots or [])
    if not slots:
        raise _err("invalid_reshoot_slots", "다시 찍을 칸을 하나 이상 골라 주세요.")
    unknown = [item.slot for item in slots if item.slot not in PHOTO_SLOTS]
    if unknown:
        raise _err("invalid_reshoot_slots", "알 수 없는 사진 칸입니다.")
    payload = [{"slot": item.slot, "reason": str(item.reason or "")[:200]} for item in slots]
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        enrollment_id = _canonical_id(enrollment_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set photo_review_status = 'reshoot_requested', photo_reviewed_by = %s,
                    photo_reviewed_at = now(), reshoot_slots = %s::jsonb
                where id = %s and decision = 'passed' 
                returning coalesce(photo_review_status, 'pending') as photo_review_status
                """,
                (user_id, json.dumps(payload, ensure_ascii=False), enrollment_id),
            )
            updated = await cur.fetchone()
        if updated is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="enrollment_photo_review_reshoot",
            target_type="enrollment",
            target_id=enrollment_id,
            after={"photoReviewStatus": "reshoot_requested",
                   "slots": [item["slot"] for item in payload]},
        )
        await conn.commit()
    return PhotoReviewResult(photo_review_status="reshoot_requested",
                             reshoot_slots=[ReshootSlot(**item) for item in payload])


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
                    status = 'processing',
                    -- expires_at 은 생성 + 24h 다. 사람 심사는 그보다 늦게 끝나는 게 정상이라
                    -- 승인하는 순간 이미 만료 시각을 지났고, 'processing' 은 만료 스윕의
                    -- 대상 상태다 — 손대지 않으면 승인된 등록이 ≤60초 안에 expired 로
                    -- 뒤집히고 격리 사진까지 지워진다(최종리뷰 I4). 자산 빌드가 붙잡을
                    -- 시간을 준다.
                    expires_at = greatest(expires_at, now() + interval '1 hour')
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
    # 심사 대기 화면이 "결과는 메일로 알려 드려요" 라고 약속한다 — 그 화면은 폴링하지
    # 않으므로 이게 유일한 통지 경로다(최종리뷰 I2). best-effort: 결정은 이미 커밋됐다.
    await notify_enrollment_decision(
        request.app, enrollment_id=enrollment_id, email_type="enrollment_review_approved"
    )
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
    # 거절도 반드시 알린다 — 알리지 않으면 사용자는 "검수 중" 화면에서 영영 기다린다.
    await notify_enrollment_decision(
        request.app,
        enrollment_id=enrollment_id,
        email_type="enrollment_review_rejected",
        reject_reason=reason,
    )
    return JSONResponse(
        content=AdminReviewDecisionResult(
            id=enrollment_id, review_status="rejected", status="failed"
        ).model_dump(by_alias=True)
    )


# --- 재조정 스윕 ---------------------------------------------------------------------


# 승인 직후 자산빌드 재개가 실패하면(레이스·identity_replay·일시 장애) 행은
# status='processing' + review_status='approved' 로 멈춘다. 응답·ERROR 로그·감사 행으로
# 보이게는 해 뒀지만 **아무도 다시 시도하지 않았다** — 사람이 로그를 읽을 때쯤이면
# 신분증은 이미 파기됐고 사진도 만료 스윕이 지운 뒤다(최종리뷰 I4). 이 스윕이 그 행을
# 주기적으로 다시 집는다. 승인 UPDATE 가 expires_at 을 1시간 뒤로 밀어 두므로 그 안에
# 몇 번은 재시도된다.
REVIEW_RESUME_RETRY_AFTER = "2 minutes"


async def sweep_stalled_review_approvals(app, *, limit: int = 20) -> int:
    """승인됐는데 자산빌드가 안 걸린 등록을 다시 집어 bind/enqueue 를 시도한다.

    반환값은 이번 tick 에 성공적으로 재개한 건수. 실패는 다음 tick 이 다시 본다 —
    영구 실패(identity_replay 등)는 매번 WARNING 을 남기므로 알람이 걸린다.
    """
    pool = getattr(app.state, "pool", None)
    if pool is None:
        return 0
    settings = app.state.settings
    limit = max(1, min(int(limit), 100))
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                select e.id::text as id
                from fm_biometric_enrollments e
                where e.status = 'processing' and e.review_status = 'approved'
                  and e.reviewed_at < now() - interval '{REVIEW_RESUME_RETRY_AFTER}'
                -- jobs 테이블을 뒤지는 anti-join 은 일부러 안 건다: 바인딩 UPDATE(status를
                -- 'asset_building' 으로)와 잡 INSERT 가 **같은 트랜잭션**이라, 잡이 있으면
                -- status 는 이미 'processing' 이 아니다. 위 술어만으로 중복 큐잉이 막히고,
                -- 인덱스 없는 jobs 전체 스캔을 60초마다 도는 일도 없다.
                order by e.reviewed_at
                limit %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()
        await conn.commit()

    resumed = 0
    for row in rows:
        enrollment_id = row["id"]
        try:
            async with pool.connection() as conn:
                await _bind_and_enqueue_on_conn(conn, settings, enrollment_id)
            resumed += 1
            logger.info("enrollment_review_resume_reconciled enrollment=%s", enrollment_id)
        except Exception as exc:
            logger.warning(
                "enrollment_review_resume_retry_failed enrollment=%s error_type=%s",
                enrollment_id, type(exc).__name__,
            )
    return resumed
