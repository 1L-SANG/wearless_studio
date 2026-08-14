"""HTTP 라우트 — Phase 1~2 (backend_integration_plan §4).

읽기(/me/account, /projects?view=library) + projects CRUD + 자산 업로드(§3).
모든 라우트는 require_user로 JWT sub를 받고, repo가 그 user_id로 소유권을 스코프한다.
"""

import asyncio
import hashlib
import json
import contextlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from psycopg import errors

from . import facemarket, repo
from .agents import (
    color_harmony,
    content_roles,
    cut_generator,
    feature_copy,
    fit_axes,
    mannequin,
    mannequin_base_fidelity_qc,
    product_analyst,
    space_set_assets,
    style_affinity,
)
from .agents.gemini_image import InlineImage
from .agents.vision_llm import VisionError
from .services import (editor_garment_mask, garment_grid, input_qc,
                       mannequin_tone_render, matching, retrieval)
from .auth import require_user
from .db import get_conn
from .models import (
    Account,
    Asset,
    AssetCompleteRequest,
    CreditHistoryEntry,
    CreditSource,
    CustomMatchItemRequest,
    DraftSlotPutRequest,
    ErrorResponse,
    JobView,
    MannequinCut,
    PricingPlan,
    Product,
    ProductPatch,
    Project,
    ProjectPatch,
    ProjectSummary,
    RefundRequestBody,
    TopupPurchaseBody,
    UploadUrlRequest,
    UploadUrlResponse,
    ToneApplyRequest,
    ToneEditorState,
)
from .r2 import IMMUTABLE_CACHE, R2Client, derived_key, ext_for_mime, upload_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

# 상품 사진 상한 (업로드 실패 사유 표면화 §). 15MB → 25MB (2026-08-01): 요즘 아이폰 사진은
# HEIC 원본이 8MB 대이고, 클라이언트가 JPEG 로 변환하면(lib/imageTranscode.js) 더 커질 수 있다.
# 실제 업로드는 변환 단계에서 긴 변 4000px 로 줄여 3MB 안팎이라 이 값은 상한 가드다.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB
UPLOAD_URL_TTL = 300  # presigned PUT 만료(초)
DRAFT_SLOT_STORAGE_KEY = "draft-slot"

COMMON_RESPONSES = {
    401: {"model": ErrorResponse, "description": "인증 실패 (토큰 누락, 만료 또는 위변조)"},
    403: {"model": ErrorResponse, "description": "권한 없음 (타 사용자의 리소스 접근 시도 등)"},
    404: {"model": ErrorResponse, "description": "리소스를 찾을 수 없음"},
}



def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "프로젝트를 찾을 수 없습니다."},
    )


def _r2(request: Request) -> R2Client:
    r2 = getattr(request.app.state, "r2", None)
    if r2 is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "storage_unavailable", "message": "자산 저장소가 설정되지 않았습니다."},
        )
    return r2


def _wake_dispatcher(request: Request) -> None:
    """job 생성 직후 디스패처 즉시 기상 — 유휴 폴링 대기(최대 3초)를 건너뛰어 시작 지연을 없애고,
    같은 DB를 폴링하는 외부(구버전/타 env) dispatcher 와의 클레임 레이스를 사실상 제거한다
    (2026-07-12 사고: QC=true env 외부 프로세스가 사용자 잡을 가로채 생성 전멸)."""
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()


async def _fit_profile_snapshot(
    conn,
    user_id: str,
    project_id: str,
    requested: dict | None,
    *,
    validate_matching_fit: bool = False,
) -> dict:
    """잡 생성 시점 effective fitProfile 스냅샷 — 워커의 불변 입력 (fidelity 설계 D3).

    - profile: 카탈로그 정규화(fit_axes.normalize_fit_profile). 프로필이 없으면 명시적 None
      (auto 값 발명 금지). 실제 매칭 이미지가 없으면 matchCut/matchingFit 제거
      (없는 옷 지시 방지).
    - adjustedAxes: **서버 diff 로만 산출**(§E-2) — 직전 정규화 프로필 vs 요청 정규화 프로필.
      클라이언트가 보낸 조정 목록은 신뢰하지 않는다. generate/바디 없는 regenerate 는 [].
    """
    analysis = await repo.get_analysis(conn, project_id) or {}
    prev = fit_axes.normalize_fit_profile(analysis.get("fitProfile"))
    if requested is not None:
        profile = fit_axes.normalize_fit_profile(requested)
        adjusted = fit_axes.adjusted_axes_between(prev, profile)
    else:
        profile, adjusted = prev, []
    main_match_id = mannequin.main_match_item_id(analysis)
    matching_fit = profile.get("matchingFit") if profile else None
    match_cut = profile.get("matchCut") if profile else None
    matching_id_valid = bool(
        isinstance(matching_fit, dict)
        and matching_fit.get("clothingId") == main_match_id
    )
    if validate_matching_fit and matching_fit and not matching_id_valid:
        raise _bad_request(
            "invalid_matching_fit",
            "매칭 핏이 현재 선택된 메인 매칭 의류와 일치하지 않습니다.",
        )
    item_metadata = None
    if main_match_id and (match_cut is not None or matching_id_valid):
        item_metadata = await repo.get_matching_item_metadata(
            conn, main_match_id, user_id, project_id
        )
    authoritative_fit_category = matching.fit_category(item_metadata or {})
    if matching_fit:
        matching_category_valid = (
            matching_id_valid
            and authoritative_fit_category == matching_fit.get("fitCategory")
        )
        if validate_matching_fit and not matching_category_valid:
            raise _bad_request(
                "invalid_matching_fit",
                "매칭 핏 카테고리가 현재 선택된 매칭 의류와 일치하지 않습니다.",
            )
        if not matching_category_valid:
            profile = {k: v for k, v in profile.items() if k != "matchingFit"}
    if match_cut is not None and authoritative_fit_category != "pants":
        profile = {k: v for k, v in profile.items() if k != "matchCut"}
    if profile and (profile.get("matchCut") is not None or profile.get("matchingFit") is not None):
        has_match = bool(
            main_match_id and await repo.get_matching_item_asset(
                conn, main_match_id, user_id, project_id
            )
        )
        if not has_match:
            profile = {k: v for k, v in profile.items() if k not in ("matchCut", "matchingFit")}
    return {"version": 1, "profile": profile, "adjustedAxes": adjusted}


def _bad_request(code: str, message: str, meta: dict | None = None) -> HTTPException:
    detail = {"code": code, "message": message}
    if meta:
        detail["meta"] = meta
    return HTTPException(status_code=400, detail=detail)


def _mannequin_payload_matches(job: dict, requested_payload: dict) -> bool:
    """활성 마네킹 job 합류가 안전한지 의미 입력만 비교한다.

    regenerate 재시도 때 저장된 analysis가 이미 새 profile로 바뀌어 adjustedAxes가 []로
    재산출될 수 있으므로 그 파생 필드만 제외하고 mode와 스냅샷을 비교한다.
    """
    if job.get("status") not in ("pending", "running"):
        return True
    existing = job.get("payload") or {}
    existing_snapshot = existing.get("fitProfileSnapshot")
    requested_snapshot = requested_payload.get("fitProfileSnapshot")
    if isinstance(existing_snapshot, dict):
        existing_snapshot = {
            key: value for key, value in existing_snapshot.items() if key != "adjustedAxes"
        }
    if isinstance(requested_snapshot, dict):
        requested_snapshot = {
            key: value for key, value in requested_snapshot.items() if key != "adjustedAxes"
        }
    return (
        existing.get("mode") == requested_payload.get("mode")
        and existing_snapshot == requested_snapshot
    )


def _generation_in_progress() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "generation_in_progress",
            "message": "이미 다른 마네킹 생성이 진행 중이에요. 잠시 뒤 다시 시도해 주세요.",
        },
    )


def _custom_match_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _custom_match_metadata(expected_type: str) -> dict:
    """AI 없이 채우는 커스텀 매칭의류 메타데이터 (D10).

    아는 것만 쓴다 — 종류는 슬롯이 정본이고, 나머지는 '모른다'는 뜻의 중립값이다.
    category 를 큐레이션 어휘(팬츠·스커트…) 중 하나로 지어내면 matching.fit_category 가
    잘못된 핏 어휘를 열어 준다. 그래서 닫힌 어휘에 없는 '커스텀'을 쓴다.
    """
    return {
        "name": "내 상의" if expected_type == "top" else "내 하의",
        "clothingType": expected_type,
        "category": "커스텀",
        "length": "regular",
        "colorName": "커스텀",
        "colorGroup": "gray",
    }


def _matching_item_to_api(r2: R2Client, item: dict, *, compatible: bool = True) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "gender": item["gender"],
        "thumb": r2.public_url(item["thumb_key"]),
        "imageUrl": r2.public_url(item["image_key"]) if item.get("image_key") else None,
        "thumbnailUrl": r2.public_url(item["thumb_key"]),
        "clothingType": item.get("clothing_type"),
        "category": item.get("category"),
        "colorName": item.get("color_name"),
        "colorGroup": item.get("color_group"),
        "colorBrightness": item.get("color_brightness"),
        "fit": item.get("fit"),
        "length": item.get("length"),
        "fitCategory": matching.fit_category(item),
        "selected": False,
        "isCustom": bool(item.get("is_custom")),
        "isCompatible": compatible,
    }


def _matching_product_color(product: dict | None, analysis: dict | None = None) -> str | None:
    """서버 소유 상품색을 매칭 랭킹용 단일 스와치로 결정한다.

    기준 색 그룹(`isBase`, 레거시는 첫 그룹)의 swatchId가 정본이다. 없을 때만
    분석의 첫 swatchSuggestions를 사용한다. 새 색 문자열은 여기서 버리지 않고
    조화 조회부의 중립 0.5 폴백으로 보낸다.
    """
    colors = product.get("colors") if isinstance(product, dict) else None
    colors = colors if isinstance(colors, list) else []
    base = next(
        (color for color in colors if isinstance(color, dict) and color.get("isBase")),
        colors[0] if colors and isinstance(colors[0], dict) else None,
    )
    swatch_id = base.get("swatchId") if isinstance(base, dict) else None
    if isinstance(swatch_id, str) and swatch_id.strip():
        return swatch_id.strip()

    suggestions = analysis.get("swatchSuggestions") if isinstance(analysis, dict) else None
    suggestions = [s for s in suggestions if isinstance(s, dict)] if isinstance(suggestions, list) else []
    # 다색 상품에서 첫 제안이 기준 색이라는 보장이 없다 — 기준 색 그룹(colorGroupId)과
    # 연결된 제안을 먼저 찾고, 없을 때만 첫 제안으로 폴백한다(2026-08-12 리뷰 반영).
    base_group_id = base.get("id") if isinstance(base, dict) else None
    preferred = next(
        (s for s in suggestions if base_group_id and s.get("colorGroupId") == base_group_id),
        suggestions[0] if suggestions else None,
    )
    fallback = preferred.get("swatchId") if isinstance(preferred, dict) else None
    return fallback.strip() if isinstance(fallback, str) and fallback.strip() else None


def _analysis_with_added_custom(payload: dict, item: dict) -> dict:
    existing = [m for m in (payload.get("matchClothing") or []) if m.get("id") != item["id"]]
    return {**payload, "matchClothing": [{**item, "selected": False}, *existing]}


def _analysis_without_custom(payload: dict, item_id: str) -> dict:
    remaining = [m for m in (payload.get("matchClothing") or []) if m.get("id") != item_id]
    selected = sorted(
        (m for m in remaining if m.get("selected")),
        key=lambda m: m.get("selOrder") or 99,
    )[:1]
    order_by_id = {m.get("id"): index for index, m in enumerate(selected, start=1)}
    normalized = []
    for item in remaining:
        if item.get("id") in order_by_id:
            normalized.append({**item, "selected": True, "selOrder": order_by_id[item["id"]]})
        else:
            cleaned = {**item, "selected": False}
            cleaned.pop("selOrder", None)
            normalized.append(cleaned)

    next_payload = {**payload, "matchClothing": normalized}
    fit_profile = payload.get("fitProfile")
    matching_fit = fit_profile.get("matchingFit") if isinstance(fit_profile, dict) else None
    if isinstance(matching_fit, dict) and matching_fit.get("clothingId") == item_id:
        next_fit_profile = {**fit_profile}
        next_fit_profile.pop("matchingFit", None)
        next_payload["fitProfile"] = next_fit_profile
    return next_payload


async def _cleanup_custom_match_r2(r2: R2Client, assets: list[dict]) -> None:
    """Best-effort asynchronous cleanup; retry each key without changing the committed DB result."""
    for asset in assets:
        for attempt in range(3):
            try:
                await asyncio.to_thread(r2.delete, asset["r2_key"])
                break
            except Exception:
                if attempt == 2:
                    logger.warning(
                        "custom_match_r2_cleanup_failed",
                        extra={"asset_id": asset.get("id")},
                        exc_info=True,
                    )
                else:
                    await asyncio.sleep(0.25 * (attempt + 1))


async def _cleanup_draft_slot_r2(r2: R2Client, assets: list[dict]) -> None:
    """Best-effort R2 cleanup after the draft asset rows have been soft-deleted."""
    for asset in assets:
        for attempt in range(3):
            try:
                await asyncio.to_thread(r2.delete, asset["r2_key"])
                break
            except Exception:
                if attempt == 2:
                    logger.warning(
                        "draft_slot_r2_cleanup_failed",
                        extra={"asset_id": asset.get("id")},
                        exc_info=True,
                    )
                else:
                    await asyncio.sleep(0.25 * (attempt + 1))


def _draft_slot_images(payload: dict) -> list[dict]:
    product = payload.get("product") if isinstance(payload.get("product"), dict) else payload
    colors = product.get("colors") if isinstance(product, dict) else None
    if not isinstance(colors, list):
        return []
    return [
        image
        for color in colors
        if isinstance(color, dict) and isinstance(color.get("images"), list)
        for image in color["images"]
        if isinstance(image, dict)
    ]


def _draft_slot_asset_ids(payload: dict) -> set[str]:
    asset_ids: set[str] = set()
    for image in _draft_slot_images(payload):
        try:
            asset_ids.add(str(uuid.UUID(str(image.get("id")))))
        except (ValueError, TypeError, AttributeError):
            continue
    return asset_ids


def _draft_slot_meta(row: dict) -> dict:
    updated_at = row["updated_at"]
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()
    return {
        "updatedAt": updated_at,
        "deviceLabel": row.get("device_label"),
        "photoCount": len(_draft_slot_images(row.get("payload") or {})),
        "photosPending": bool(row.get("photos_pending")),
    }


def _draft_slot_expired(row: dict) -> bool:
    expires_at = row.get("expires_at")
    return isinstance(expires_at, datetime) and expires_at <= datetime.now(timezone.utc)


async def _remove_draft_slot(
    conn, user_id: str, row: dict, *, keep_asset_ids: set[str] | None = None
) -> list[dict]:
    asset_ids = _draft_slot_asset_ids(row.get("payload") or {}) - (keep_asset_ids or set())
    await repo.delete_draft_slot(conn, user_id)
    return await repo.soft_delete_unreferenced_draft_assets(
        conn, user_id, sorted(asset_ids)
    )


def _require_bg_examples_enabled(request: Request, value) -> None:
    """Fail before persistence/reservation while the bg-reference pilot is opt-in only."""
    if getattr(request.app.state.settings, "genexample_bg_enabled", False):
        return
    items = value if isinstance(value, list) else [value]
    is_storyboard = isinstance(value, list)
    requested = False
    for item in items:
        if (
            not isinstance(item, dict)
            or (item.get("refScope") or item.get("ref_scope")) != "bg"
            or not bool(item.get("exampleId") or item.get("example_id"))
        ):
            continue
        if is_storyboard:
            try:
                if (
                    space_set_assets.parse_space_set_group_id(
                        item.get("spaceGroupId") or item.get("space_group_id")
                    )
                    is not None
                ):
                    continue
            except space_set_assets.SpaceSetBindingError as exc:
                raise _bad_request(exc.code, exc.message) from exc
        requested = True
        break
    if requested:
        raise _bad_request(
            "genexample_bg_disabled",
            "배경만 생성예시는 파일럿 검증 중이라 현재 사용할 수 없어요.",
        )


def _credit_error(e: "repo.CreditError") -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@router.get(
    "/me/account",
    response_model=Account,
    responses={**COMMON_RESPONSES},
    tags=["User & Account"],
    summary="사용자 계정 정보 조회",
)
async def get_account(request: Request, user_id: str = Depends(require_user)):
    """인증된 사용자의 계정 정보(이름, 아바타, 사용 가능한 크레딧 잔액, 요금제 티어)를 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `401 Unauthorized`: 토큰이 누락되었거나 유효하지 않은 경우
      - `404 Not Found` (`account_not_found`): DB에 사용자 정보가 존재하지 않는 경우
    """
    async with get_conn(request) as conn:
        row = await repo.get_account(conn, user_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "account_not_found", "message": "계정 정보를 찾을 수 없습니다."},
        )
    return row


# ---------- 크레딧 (credit_system_design.md §6) ----------


@router.get(
    "/pricing-plans",
    response_model=list[PricingPlan],
    responses={**COMMON_RESPONSES},
    tags=["Credits"],
    summary="요금제 목록 조회",
)
async def get_pricing_plans(request: Request, user_id: str = Depends(require_user)):
    """사용 가능한 구독/크레딧 충전 요금제 목록을 조회합니다.

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        return await repo.list_pricing_plans(conn)


@router.get(
    "/credits/sources",
    response_model=list[CreditSource],
    responses={**COMMON_RESPONSES},
    tags=["Credits"],
    summary="사용자 활성 크레딧 원천 목록 조회",
)
async def get_credit_sources(request: Request, user_id: str = Depends(require_user)):
    """사용자가 보유한 충전/지급 크레딧 항목(원천)들을 조회합니다. (환불 요청 시 사용)

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        return await repo.list_credit_sources(conn, user_id)


@router.get(
    "/credits/history",
    response_model=list[CreditHistoryEntry],
    responses={**COMMON_RESPONSES},
    tags=["Credits"],
    summary="크레딧 트랜잭션 내역 조회",
)
async def get_credit_history(request: Request, user_id: str = Depends(require_user)):
    """사용자의 크레딧 충전, 사용, 환불 등 원장 거래 기록을 전체 조회합니다.

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        return await repo.list_credit_history(conn, user_id)


@router.post(
    "/credits/topups:purchase",
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Credits"],
    summary="요금제 구매 (크레딧 충전)",
)
async def purchase_topup(
    request: Request,
    body: TopupPurchaseBody,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """지정된 요금제 코드로 크레딧을 수동 충전합니다. **개발 환경 전용**(결제 없이 지급).

    - **Bearer Token**: 필수
    - **Header**: `Idempotency-Key` (선택, 동일 요청 중복 방지)
    - **에지 케이스**:
      - `404 Not Found`: 프로덕션에서는 라우트가 없는 것처럼 응답합니다.
      - `400 Bad Request`: 존재하지 않는 요금제 코드이거나 중복 충전 시도 시 발생
    """
    # 결제 없이 크레딧을 주는 경로다. PG(토스) 연동 전에는 임시 수단이었지만, 실 결제가 생긴 뒤로는
    # 프로덕션에 열려 있으면 '돈 내는 길'과 '공짜 길'이 공존하는 결제 우회 창구가 된다.
    # **fail-closed**: dev 일 때만 허용한다. 특정 값('prod')을 차단하는 방식은 실제 배포값
    # (copilot manifest 의 APP_ENV=production)을 놓쳐 구멍이 그대로 열린다. docs 게이트
    # (main.py 의 app_env == "dev")와 같은 화이트리스트 판정으로 맞춘다.
    if request.app.state.settings.app_env != "dev":
        raise _not_found()
    async with get_conn(request) as conn:
        try:
            result = await repo.purchase_topup(
                conn, user_id=user_id, plan_code=body.plan_code, idempotency_key=idempotency_key
            )
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.post(
    "/credits/refunds",
    status_code=201,
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Refunds"],
    summary="크레딧 환불 요청",
)
async def request_refund(
    request: Request, body: RefundRequestBody, user_id: str = Depends(require_user)
):
    """구매한 크레딧 패키지에 대한 환불 신청을 등록합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `400 Bad Request`: 이미 소모했거나 환불 진행 중인 크레딧 소스에 대해 요청 시 발생
    """
    async with get_conn(request) as conn:
        try:
            result = await repo.request_refund(
                conn, user_id=user_id, credit_source_id=body.credit_source_id, reason=body.reason
            )
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result, status_code=201)


@router.post(
    "/admin/refunds/{request_id}/approve",
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Admin & Refunds"],
    summary="관리자: 환불 요청 승인",
)
async def approve_refund(
    request: Request, request_id: str, user_id: str = Depends(require_user)
):
    """(관리자 전용) 등록된 환불 요청을 최종 승인 처리하고 잔액에서 크레딧을 회수합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `403 Forbidden`: 요청자가 관리자가 아닌 경우
      - `400 Bad Request`: 이미 처리되었거나 유효하지 않은 환불 요청인 경우
    """
    async with get_conn(request) as conn:
        if not await repo.is_admin(conn, user_id):
            raise HTTPException(403, detail={"code": "forbidden", "message": "관리자만 가능해요."})
        try:
            result = await repo.approve_refund(conn, request_id=request_id, resolved_by=user_id)
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.post(
    "/admin/refunds/{request_id}/reject",
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Admin & Refunds"],
    summary="관리자: 환불 요청 반려",
)
async def reject_refund(
    request: Request, request_id: str, user_id: str = Depends(require_user)
):
    """(관리자 전용) 등록된 환불 요청을 반려 처리합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `403 Forbidden`: 요청자가 관리자가 아닌 경우
      - `400 Bad Request`: 이미 처리된 환불 요청인 경우
    """
    async with get_conn(request) as conn:
        if not await repo.is_admin(conn, user_id):
            raise HTTPException(403, detail={"code": "forbidden", "message": "관리자만 가능해요."})
        try:
            result = await repo.reject_refund(conn, request_id=request_id, resolved_by=user_id)
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.get(
    "/projects",
    response_model=list[ProjectSummary],
    responses={**COMMON_RESPONSES},
    tags=["Projects"],
    summary="프로젝트 목록 (보관함) 조회",
)
async def get_library(
    request: Request,
    view: str = Query("library"),
    user_id: str = Depends(require_user),
):
    """현재 로그인한 사용자의 모든 프로젝트 요약 목록(보관함 카드 목록)을 조회합니다.

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        return await repo.list_library(conn, user_id)


@router.post(
    "/projects",
    response_model=Project,
    status_code=201,
    responses={**COMMON_RESPONSES},
    tags=["Projects"],
    summary="새 프로젝트 생성",
)
async def create_project(request: Request, user_id: str = Depends(require_user)):
    """새로운 프로젝트 초안(Draft)을 생성합니다.

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        row = await repo.create_project(conn, user_id)
        await conn.commit()
    return row


@router.get(
    "/projects/{project_id}",
    response_model=Project,
    responses={**COMMON_RESPONSES},
    tags=["Projects"],
    summary="프로젝트 상세 조회",
)
async def get_project(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """지정된 ID의 프로젝트 단건 상세 정보를 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 다른 사용자의 소유인 경우 발생
    """
    async with get_conn(request) as conn:
        row = await repo.get_project(conn, user_id, project_id)
    if row is None:
        raise _not_found()
    return row


@router.patch(
    "/projects/{project_id}",
    response_model=Project,
    responses={**COMMON_RESPONSES},
    tags=["Projects"],
    summary="프로젝트 설정 수정",
)
async def patch_project(
    request: Request,
    project_id: str,
    patch: ProjectPatch,
    user_id: str = Depends(require_user),
):
    """프로젝트의 설정(예: composeMode, copywriting, selectedMannequinId 등)을 업데이트합니다.

    - **Bearer Token**: 필수
    - **제한 사항**:
      - `adjustCount` 및 `status` 등 서버 제어 필드는 요청 본문에 실어 보내더라도 안전하게 무시됩니다.
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    # adjustCount·status 등은 모델에 없어 자동 무시 (계약 §6). exclude_unset = 보낸 필드만.
    fields = patch.model_dump(exclude_unset=True)
    async with get_conn(request) as conn:
        row = await repo.patch_project(conn, user_id, project_id, fields)
        await conn.commit()
    if row is None:
        raise _not_found()
    return row


# ---------- product (계약 §3.1) ----------


@router.get(
    "/projects/{project_id}/product",
    response_model=Product,
    responses={**COMMON_RESPONSES},
    tags=["Products"],
    summary="상품 정보 조회",
)
async def get_product(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """프로젝트에 등록된 상품의 정보(이름, 분류, 컬러 그룹, 측정 실측 치수 등)를 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
      - 만약 DB상에 product 행이 아직 없는 신규 프로젝트라면, 에러 대신 빈 기본 스키마를 반환합니다.
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        row = await repo.get_product(conn, project_id)  # 순수 read
    if row is None:
        # 레거시(product 행 없는 프로젝트) — 기본값 반환(쓰기 없음). saveProduct가 생성.
        return {
            "id": "", "projectId": project_id, "name": "", "clothingType": None,
            "colors": [], "measurements": [], "measurementsUnknown": False,
            "uploadComplete": False,
        }
    return row


@router.patch(
    "/projects/{project_id}/product",
    response_model=Product,
    responses={**COMMON_RESPONSES},
    tags=["Products"],
    summary="상품 정보 저장/수정",
)
async def save_product(
    request: Request,
    project_id: str,
    patch: ProductPatch,
    user_id: str = Depends(require_user),
):
    """프로젝트 내 상품의 물리적 사실(이름, 분류, 컬러 그룹, 측정 실측 치수 등)을 수정하거나 신규 등록합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    fields = patch.model_dump(exclude_unset=True)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        row = await repo.save_product(conn, project_id, user_id, fields)
        if (
            row
            and (row.get("clothingType") or row.get("clothing_type")) == "dress"
        ):
            analysis = await repo.get_analysis(conn, project_id) or {}
            if analysis and analysis.get("targetGenders") != ["women"]:
                await repo.save_analysis(
                    conn,
                    project_id,
                    {**analysis, "targetGenders": ["women"]},
                )
        await conn.commit()
    return row


# ---------- analysis (계약 §3.2) ----------


@router.patch(
    "/projects/{project_id}/analysis",
    responses={**COMMON_RESPONSES},
    tags=["Analysis"],
    summary="AI 상품 분석 결과 저장/수정",
)
async def save_analysis(
    request: Request,
    project_id: str,
    analysis: dict = Body(...),
    user_id: str = Depends(require_user),
):
    """AI 제안(추천 제품명, 핏, 소재 등) 및 사용자 조정을 거친 상품 분석 정보를 JSONB 페이로드로 통째로 갱신하여 저장합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    # 프론트 소유 shape를 유지하되 폐기된 레거시 핏 어휘는 신규 저장하지 않는다.
    analysis = fit_axes.normalize_analysis_fit(analysis)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        product = await repo.get_product(conn, project_id) or {}
        if (
            product.get("clothingType") or product.get("clothing_type")
        ) == "dress":
            analysis = {**analysis, "targetGenders": ["women"]}
        row = await repo.save_analysis(conn, project_id, analysis)
        await conn.commit()
    return {"projectId": row["project_id"], **(row["payload"] or {})}


@router.get(
    "/projects/{project_id}/analysis",
    responses={**COMMON_RESPONSES},
    tags=["Analysis"],
    summary="AI 상품 분석 결과 조회",
)
async def get_analysis(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """저장된 분석 payload(프론트 소유 JSONB)를 조회합니다. 하드 새로고침 후 매칭 선택 등 복원용.

    - **Bearer Token**: 필수
    - **에지 케이스**: `404` 프로젝트 없음/타인 소유. 분석 미저장이면 `{projectId}` 만 반환.
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        payload = fit_axes.normalize_analysis_fit(await repo.get_analysis(conn, project_id))
    return {"projectId": project_id, **(payload or {})}


@router.post(
    "/projects/{project_id}/wash-care:draft",
    responses={**COMMON_RESPONSES, 502: {"model": ErrorResponse}},
    tags=["Analysis"],
    summary="AI 세탁 관리법 초안 생성",
)
async def draft_wash_care(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """상품 종류·소재를 근거로 짧은 세탁 관리 문구를 생성합니다(무과금·동기). 반환: `{text}`.

    - **Bearer Token**: 필수
    - **에지 케이스**: `404` 프로젝트 없음/타인 소유. `502` LLM 생성 실패.
    """
    s = request.app.state.settings
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        product = await repo.get_product(conn, project_id) or {}
        analysis = await repo.get_analysis(conn, project_id) or {}
    try:
        raw, _provider = await product_analyst.draft_wash_care(s, product, analysis)
    except VisionError as e:
        raise HTTPException(status_code=502, detail={"code": "wash_care_failed", "message": str(e)})
    text = (raw.get("text") or "").strip()
    if not text:
        raise HTTPException(
            status_code=502,
            detail={"code": "wash_care_failed",
                    "message": "세탁 정보 생성에 실패했어요. 잠시 후 다시 시도해 주세요."})
    return JSONResponse({"text": text})


@router.post(
    "/projects/{project_id}/feature-copy:draft",
    responses={**COMMON_RESPONSES, 502: {"model": ErrorResponse}},
    tags=["Analysis"],
    summary="AI 특징 포인트 설명 초안 생성",
)
async def draft_feature_copy(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """강조특징마다 설명 한 줄을 생성합니다(무과금·동기). 반환: `{items:[{point,desc}]}`.

    상세페이지 생성 잡이 쓰는 것과 같은 경로다 — 부위·구조 사전을 먼저 보고 사전에 없는
    표현만 LLM 1콜로 넘긴다. 결과는 `analysis.featureCopy` 에 합쳐 저장하므로, 에디터가
    블록을 지을 때도 같은 문구를 쓴다. 셀러가 입력한 강조특징 자체는 읽기만 한다.

    - **Bearer Token**: 필수
    - **에지 케이스**: `404` 프로젝트 없음/타인 소유. `502` 한 줄도 만들지 못함.
    """
    s = request.app.state.settings
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        product = await repo.get_product(conn, project_id) or {}
        analysis = await repo.get_analysis(conn, project_id) or {}
    points = analysis.get("sellingPoints") or analysis.get("aiSuggestedPoints") or []
    items = await feature_copy.generate(s, points, product, analysis)
    if not items:
        raise HTTPException(
            status_code=502,
            detail={"code": "feature_copy_failed",
                    "message": "특징 설명을 만들지 못했어요. 잠시 후 다시 시도해 주세요."})
    # 쓰기 직전에 다시 읽는다 — 이 요청 사이에 셀러가 분석을 고쳤을 수 있다.
    async with get_conn(request) as conn:
        fresh = await repo.get_analysis(conn, project_id) or {}
        await repo.save_analysis(
            conn, project_id,
            {**fresh, "featureCopy": feature_copy.merge_stored(fresh.get("featureCopy"), items)})
        await conn.commit()
    return JSONResponse({"items": items})


@router.post(
    "/projects/{project_id}/analyze",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "상품 분석 작업이 대기열에 진입했습니다."},
    },
    tags=["Analysis"],
    summary="AI 상품 분석 작업 시작 (AG-01)",
)
async def analyze_product(
    request: Request,
    project_id: str,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """업로드된 상품 사진으로 AI 분석(색·핏·소재·스타일 등)을 수행하는 비동기 작업을 요청합니다.

    - **Bearer Token**: 필수
    - **무과금**: 분석은 크레딧을 차감하지 않습니다 (ai_agent_modules §3).
    - **멱등성**: 진행 중 동일 작업이 있으면 새로 띄우지 않고 기존 `jobId`로 합류합니다
      (더블클릭 시 LLM 2회 호출 방지). 완료된 분석은 재호출 시 재분석(무과금)됩니다.
    """
    scoped_key = f"{project_id}:analyze:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        # 무과금 → 예약/게이트 없이 job 생성(멱등/활성 합류는 create_job 이 원자 처리).
        job, _created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="analyze",
            payload={"mode": "analyze"}, idempotency_key=scoped_key,
            credits_reserved=0, metadata={})
        await conn.commit()
        # 캐노니컬 컷아웃 전처리도 같이 띄운다. 분석과 독립적으로 돌고(소스 사진만 있으면 된다),
        # 무과금이다. 여기서 거는 이유는 이 시점이 소스 사진이 확정된 첫 지점이고, 마네킹
        # 생성이 시작되기 전에 끝나 있을 가능성이 가장 높아서다.
        # 프로젝트당 1회 멱등(같은 키 재요청은 기존 잡에 합류) — create_job 이 원자 처리한다.
        #
        # **분석 커밋 뒤, 별도 트랜잭션에서, 예외를 삼키고** 건다. 한 트랜잭션에 묶었더니
        # 전처리 잡 INSERT 가 실패하는 순간 분석 잡까지 롤백돼 POST /analyze 가 통째로 500 이
        # 됐다(2026-08-12 로컬 QA — jobs_kind_check 에 sam_preprocess 가 없던 시점). 보조
        # 인프라가 본 기능을 죽이지 않는다는 건 주석이 아니라 트랜잭션 경계로 지켜야 한다.
        try:
            await repo.create_job(
                conn, user_id=user_id, project_id=project_id, kind="sam_preprocess",
                payload={"mode": "canonical_cutout"},
                idempotency_key=f"{project_id}:sam_preprocess",
                credits_reserved=0, metadata={})
            await conn.commit()
        except Exception:  # noqa: BLE001 - 전처리 큐잉 실패가 분석을 막지 않는다
            # 정리 코드가 다시 터져서 500 이 되면 위 보장이 무의미하다. rollback 실패까지 삼킨다.
            with contextlib.suppress(Exception):
                await conn.rollback()
            logger.warning("sam_preprocess enqueue failed for project %s", project_id,
                           exc_info=True)
    _wake_dispatcher(request)
    return JSONResponse(status_code=202, content={"jobId": job["id"]})


@router.post(
    "/projects/{project_id}/analyze:spike",
    responses={**COMMON_RESPONSES},
    tags=["Analysis"],
    summary="[임시] 분석 provider 관측 spike (flag 게이트)",
)
async def analyze_spike(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """provider(Gemini/GPT) 순응률·폴백·지연을 실측하는 **임시** 동기 harness. `ANALYSIS_SPIKE=on`
    일 때만 동작(기본 off → 403). production 경로는 `POST /analyze`(job). plan §7."""
    s = request.app.state.settings
    if s.analysis_spike != "on":
        raise HTTPException(
            status_code=403,
            detail={"code": "spike_disabled", "message": "분석 spike 가 비활성화되어 있어요."})
    r2 = _r2(request)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        product = await repo.get_product(conn, project_id) or {}
        assets = []
        for _slot, aid in mannequin.base_color_images(product):
            a = await repo.get_asset_for_user(conn, user_id, aid)
            if a:
                assets.append(a)
    if not assets:
        raise _bad_request("no_product_images", "상품 사진을 먼저 올려주세요.")
    images = [
        InlineImage(a["mime_type"], await asyncio.to_thread(r2.get_bytes, a["r2_key"]))
        for a in assets
    ]
    order = [p.strip() for p in (s.analysis_model_order or "").split(",") if p.strip()]
    t0 = time.perf_counter()
    try:
        distributed, provider = await product_analyst.analyze(s, product, images)
    except VisionError as e:
        raise HTTPException(status_code=502, detail={"code": "analysis_failed", "message": str(e)})
    obs = product_analyst.observation(provider, order, int((time.perf_counter() - t0) * 1000), distributed)
    logger.info("analysis_spike", extra=obs)  # provider 결정 회의용 관측 로그
    return JSONResponse({"observation": obs, "data": distributed})


@router.get(
    "/projects/{project_id}/analysis/match-candidates",
    responses={**COMMON_RESPONSES, 500: {"model": ErrorResponse}},
    tags=["Analysis"],
    summary="매칭 의류 후보군 조회",
)
async def match_candidates(
    request: Request,
    project_id: str,
    clothingType: str = Query(...),
    gender: list[str] = Query(default=[]),
    styleTags: list[str] = Query(default=[]),
    limit: int | None = Query(default=None),
    user_id: str = Depends(require_user),
):
    """AI 추천 매칭 의류 후보군(예: 상의에 어울리는 바지/치마 목록)을 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
      - `500 Internal Server Error` (`r2_public_base_missing`): CDN 이미지 서버 도메인 설정이 누락된 경우 발생
    """
    if not request.app.state.settings.r2_public_base:
        raise HTTPException(status_code=500, detail={
            "code": "r2_public_base_missing",
            "message": "이미지 서버 설정이 누락됐어요. 잠시 후 다시 시도해 주세요."})
    r2 = _r2(request)
    settings = request.app.state.settings
    product_tags = [t.strip() for part in styleTags for t in part.split(",") if t.strip()]
    # 색은 태그 랭킹 경로(recommend_v1)에서만 쓰인다 — 그 경로가 아닐 때(off·태그 없음·
    # 가중치 0·보완타입 없음)는 상품/분석 조회를 아예 하지 않아 왕복을 늘리지 않는다(리뷰 반영).
    color_needed = (
        matching.complementary_type(clothingType) is not None
        and settings.retrieval_matching == "tags"
        and bool(product_tags)
        and settings.matching_color_weight > 0
    )
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        items = await repo.list_active_matching_items(conn, user_id, project_id)
        # 색은 상품/분석에 이미 저장된 구조화 값만 읽는다. 요청 중 모델 호출 없음.
        # 후보 조회를 먼저 끝내 두어 부가적인 상품색 조회가 실패해도 추천 자체는 살린다.
        product_color = None
        if color_needed:
            try:
                product = await repo.get_product(conn, project_id)
            except Exception:  # noqa: BLE001 - 색 조회 실패는 기존 스타일 랭킹으로 조용히 폴백
                logger.warning("matching product color lookup failed", exc_info=True)
            else:
                product_color = _matching_product_color(product)
                if product_color is None:
                    try:
                        analysis = await repo.get_analysis(conn, project_id)
                    except Exception:  # noqa: BLE001 - 분석 폴백도 추천 API를 막지 않는다
                        logger.warning("matching analysis color fallback failed", exc_info=True)
                    else:
                        product_color = _matching_product_color(product, analysis)
    genders = (
        ["women"]
        if clothingType == "dress"
        else [g.strip() for part in gender for g in part.split(",") if g.strip()]
    )
    custom_items = [item for item in items if item.get("is_custom")]
    curated_items = [item for item in items if not item.get("is_custom")]
    if matching.complementary_type(clothingType) is None:
        return JSONResponse([])
    if request.app.state.settings.retrieval_matching == "tags" and product_tags:
        ranked = retrieval.recommend_v1(
            curated_items,
            clothingType,
            genders,
            product_tags,
            style_affinity.affinity_map(),
            limit,
            product_color=product_color,
            harmony=color_harmony.harmony_map(),
            color_weight=request.app.state.settings.matching_color_weight,
        )
    else:
        ranked = matching.recommend(curated_items, clothingType, genders, limit)
    expected_type = matching.complementary_type(clothingType)
    ordered = [
        (item, item.get("clothing_type") == expected_type) for item in custom_items
    ] + [(item, True) for item in ranked]
    return JSONResponse([
        _matching_item_to_api(r2, item, compatible=compatible)
        for item, compatible in ordered if item.get("thumb_key")
    ])


@router.post(
    "/projects/{project_id}/analysis/custom-match-item",
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse},
               409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["Analysis"],
    summary="프로젝트 전용 매칭 의류 추가",
)
async def add_custom_match_item(
    request: Request,
    project_id: str,
    body: CustomMatchItemRequest,
    user_id: str = Depends(require_user),
):
    asset_ids = [str(asset_id) for asset_id in body.asset_ids]
    r2 = _r2(request)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        if await repo.get_custom_matching_item(conn, user_id, project_id):
            # DELETE가 먼저 프로젝트 잠금을 잡았다면 완료를 기다린 뒤 다시 확인한다.
            # 평상시 중복 POST도 이 지점에서 막아 불필요한 AI 호출은 하지 않는다.
            if not await repo.lock_custom_match_project(conn, user_id, project_id):
                raise _not_found()
            if await repo.get_custom_matching_item(conn, user_id, project_id):
                raise _custom_match_error(
                    409, "custom_match_item_exists", "이미 내 옷이 있어요. 지우고 다시 올려주세요."
                )
        assets = await repo.get_uploaded_assets_for_project(
            conn, user_id, project_id, asset_ids
        )
        product = await repo.get_product(conn, project_id) or {}
    if len(assets) != len(asset_ids):
        raise _not_found()

    try:
        source_bytes = await asyncio.gather(*[
            asyncio.to_thread(r2.get_bytes, asset["r2_key"]) for asset in assets
        ])
    except Exception as exc:
        raise _custom_match_error(
            503,
            "custom_match_storage_unavailable",
            "사진을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
        ) from exc

    qc_results = await asyncio.gather(*[
        asyncio.to_thread(input_qc.evaluate_input_qc, raw) for raw in source_bytes
    ])
    for qc in qc_results:
        if qc.verdict == "reject":
            raise _custom_match_error(400, "input_quality", input_qc.input_qc_message(qc))

    product_type = product.get("clothing_type") or product.get("clothingType")
    expected_type = matching.complementary_type(product_type)
    if expected_type is None:
        raise _custom_match_error(
            400, "wrong_garment_type", "매칭 의류로 쓸 수 있는 건 상의 또는 하의예요."
        )
    # D10(2026-08-05 오너) — 업로드 대기시간을 줄이려고 AI 메타데이터 추론을 뺐다. 종류는 슬롯이
    # 곧 정본이다(하의 슬롯에 올리면 하의). 추측으로 카테고리·기장을 지어내지 않으므로
    # matching.fit_category 는 하의 커스텀에 대해 None 을 돌려주고 핏 조정 스텝이 뜨지 않는다 —
    # 셀러가 올린 실물은 '있는 그대로' 입히는 게 맞다는 판단. 상의는 length 축만 그대로 산다.
    inferred = _custom_match_metadata(expected_type)

    try:
        grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid, source_bytes)
    except Exception as exc:
        raise _custom_match_error(
            503,
            "custom_match_storage_unavailable",
            "사진 합성본을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
        ) from exc
    checksum = hashlib.sha256(grid_bytes).hexdigest()
    async with get_conn(request) as conn:
        existing_grid = await repo.find_custom_grid_asset(
            conn, user_id, project_id, checksum
        )
    if existing_grid:
        grid_asset_id = existing_grid["id"]
        grid_key = existing_grid["r2_key"]
        grid_is_new = False
    else:
        grid_asset_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"wearless:custom-match-grid:{user_id}:{project_id}:{checksum}"
        ))
        grid_key = derived_key(user_id, project_id, grid_asset_id, "jpg")
        grid_is_new = True
        try:
            await asyncio.to_thread(r2.put_bytes, grid_key, grid_bytes, "image/jpeg")
        except Exception as exc:
            raise _custom_match_error(
                503,
                "custom_match_storage_unavailable",
                "사진 합성본을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.",
            ) from exc

    try:
        async with get_conn(request) as conn:
            if not await repo.lock_custom_match_project(conn, user_id, project_id):
                raise _not_found()
            if await repo.get_custom_matching_item(conn, user_id, project_id):
                raise _custom_match_error(
                    409, "custom_match_item_exists", "이미 내 옷이 있어요. 지우고 다시 올려주세요."
                )
            await repo.set_custom_match_source_order(conn, user_id, project_id, asset_ids)
            if grid_is_new:
                await repo.insert_custom_grid_asset(
                    conn,
                    asset_id=grid_asset_id,
                    user_id=user_id,
                    project_id=project_id,
                    bucket=request.app.state.settings.r2_bucket,
                    key=grid_key,
                    size=len(grid_bytes),
                    checksum=checksum,
                    source_asset_ids=asset_ids,
                )
            await repo.insert_custom_matching_item(
                conn,
                user_id=user_id,
                project_id=project_id,
                metadata=inferred,
                image_asset_id=grid_asset_id,
                thumbnail_asset_id=asset_ids[0],
            )
            row = await repo.get_custom_matching_item(conn, user_id, project_id)
            item = _matching_item_to_api(r2, row, compatible=True)
            payload = _analysis_with_added_custom(
                await repo.get_analysis(conn, project_id) or {}, item
            )
            saved = await repo.save_analysis(conn, project_id, payload)
            await conn.commit()
    except errors.UniqueViolation as exc:
        raise _custom_match_error(
            409, "custom_match_item_exists", "이미 내 옷이 있어요. 지우고 다시 올려주세요."
        ) from exc
    return {
        "item": item,
        "analysis": {"projectId": saved["project_id"], **(saved["payload"] or {})},
    }


@router.delete(
    "/projects/{project_id}/analysis/custom-match-item",
    status_code=204,
    responses={**COMMON_RESPONSES, 204: {"description": "삭제 완료 또는 이미 없음"}},
    tags=["Analysis"],
    summary="프로젝트 전용 매칭 의류 삭제",
)
async def remove_custom_match_item(
    request: Request,
    project_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_user),
):
    cleanup_assets: list[dict] = []
    async with get_conn(request) as conn:
        if not await repo.lock_custom_match_project(conn, user_id, project_id):
            raise _not_found()
        item = await repo.get_custom_matching_item(conn, user_id, project_id)
        if item:
            source_asset_ids = list((item.get("image_metadata") or {}).get("sourceAssetIds") or [])
            asset_ids = list(dict.fromkeys([
                item.get("image_asset_id"), item.get("thumbnail_asset_id"), *source_asset_ids,
            ]))
            asset_ids = [asset_id for asset_id in asset_ids if asset_id]
            payload = _analysis_without_custom(
                await repo.get_analysis(conn, project_id) or {}, item["id"]
            )
            await repo.delete_custom_matching_item(conn, user_id, project_id)
            await repo.save_analysis(conn, project_id, payload)
            cleanup_assets = await repo.soft_delete_unreferenced_custom_assets(
                conn, user_id, project_id, asset_ids
            )
        await conn.commit()
    if cleanup_assets:
        background_tasks.add_task(_cleanup_custom_match_r2, _r2(request), cleanup_assets)
    return Response(status_code=204)


# ---------- 숨은 임시저장 슬롯 ----------


@router.get(
    "/draft-slot",
    responses={**COMMON_RESPONSES, 204: {"description": "저장된 슬롯 없음"}},
    tags=["Draft Slot"],
    summary="숨은 임시저장 슬롯 메타 조회",
)
async def get_draft_slot(
    request: Request,
    background_tasks: BackgroundTasks,
    full: bool = Query(False),
    x_draft_token: str | None = Header(default=None, alias="X-Draft-Token"),
    user_id: str = Depends(require_user),
):
    cleanup_assets: list[dict] = []
    async with get_conn(request) as conn:
        row = await repo.lock_draft_slot(conn, user_id)
        if row and _draft_slot_expired(row):
            cleanup_assets = await _remove_draft_slot(conn, user_id, row)
            await conn.commit()
            row = None
    if cleanup_assets:
        background_tasks.add_task(_cleanup_draft_slot_r2, _r2(request), cleanup_assets)
    if row is None:
        return Response(status_code=204)
    result = {
        "meta": _draft_slot_meta(row),
        "holdsToken": bool(x_draft_token) and x_draft_token == row["active_token"],
    }
    if full:
        result["payload"] = row["payload"]
    return result


@router.put(
    "/draft-slot",
    responses={**COMMON_RESPONSES, 409: {"model": ErrorResponse}},
    tags=["Draft Slot"],
    summary="숨은 임시저장 슬롯 생성 또는 갱신",
)
async def put_draft_slot(
    request: Request,
    body: DraftSlotPutRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_user),
):
    cleanup_assets: list[dict] = []
    incoming_asset_ids = _draft_slot_asset_ids(body.payload)
    token = str(body.token) if body.token is not None else None
    consumed_token_conflict = False
    async with get_conn(request) as conn:
        row = await repo.lock_draft_slot(conn, user_id)
        if row and _draft_slot_expired(row):
            cleanup_assets = await _remove_draft_slot(
                conn, user_id, row, keep_asset_ids=incoming_asset_ids
            )
            row = None

        if row is None and token is not None:
            # 삭제·만료로 소비된 작업권은 다시 슬롯을 만들 수 없다. 새 슬롯 생성은 token=null인
            # 최초 저장만 허용한다(오래된 PUT이 삭제 뒤 도착해 슬롯을 부활시키는 레이스 차단).
            # 만료 슬롯을 같은 트랜잭션에서 발견한 경우에는 삭제를 먼저 커밋한 뒤 409를 반환한다.
            await conn.commit()
            consumed_token_conflict = True
        elif row is None:
            token = str(uuid.uuid4())
            saved = await repo.create_draft_slot(
                conn,
                user_id=user_id,
                payload=body.payload,
                active_token=token,
                device_label=body.device_label,
                photos_pending=body.photos_pending,
            )
            status_code = 201
        else:
            if token != row["active_token"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "token_mismatch",
                        "message": "다른 기기에서 이어서 작업을 시작했어요.",
                        "meta": _draft_slot_meta(row),
                    },
                )
            removed_ids = _draft_slot_asset_ids(row.get("payload") or {}) - incoming_asset_ids
            saved = await repo.update_draft_slot(
                conn,
                user_id=user_id,
                payload=body.payload,
                device_label=body.device_label,
                photos_pending=body.photos_pending,
            )
            cleanup_assets.extend(await repo.soft_delete_unreferenced_draft_assets(
                conn, user_id, sorted(removed_ids)
            ))
            status_code = 200
        if not consumed_token_conflict:
            await conn.commit()
    if cleanup_assets:
        background_tasks.add_task(_cleanup_draft_slot_r2, _r2(request), cleanup_assets)
    if consumed_token_conflict:
        return JSONResponse(
            status_code=409,
            content={"error": {
                "code": "token_mismatch",
                "message": "이 기기의 임시저장 작업권이 만료됐어요.",
                "meta": None,
            }},
        )
    return JSONResponse(
        status_code=status_code,
        content={"token": saved["active_token"], "meta": _draft_slot_meta(saved)},
    )


@router.post(
    "/draft-slot:takeover",
    responses={**COMMON_RESPONSES, 204: {"description": "저장된 슬롯 없음"}},
    tags=["Draft Slot"],
    summary="숨은 임시저장 슬롯 작업권 이어받기",
)
async def takeover_draft_slot(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_user),
):
    cleanup_assets: list[dict] = []
    async with get_conn(request) as conn:
        row = await repo.lock_draft_slot(conn, user_id)
        if row and _draft_slot_expired(row):
            cleanup_assets = await _remove_draft_slot(conn, user_id, row)
            await conn.commit()
            row = None
        if row:
            token = str(uuid.uuid4())
            row = await repo.takeover_draft_slot(conn, user_id, token)
            await conn.commit()
    if cleanup_assets:
        background_tasks.add_task(_cleanup_draft_slot_r2, _r2(request), cleanup_assets)
    if row is None:
        return Response(status_code=204)
    return {"token": row["active_token"], "payload": row["payload"], "meta": _draft_slot_meta(row)}


@router.delete(
    "/draft-slot",
    status_code=204,
    responses={
        **COMMON_RESPONSES,
        204: {"description": "삭제 완료 또는 이미 없음"},
        409: {"model": ErrorResponse},
    },
    tags=["Draft Slot"],
    summary="숨은 임시저장 슬롯 삭제",
)
async def delete_draft_slot(
    request: Request,
    background_tasks: BackgroundTasks,
    x_draft_token: str | None = Header(default=None, alias="X-Draft-Token"),
    user_id: str = Depends(require_user),
):
    cleanup_assets: list[dict] = []
    async with get_conn(request) as conn:
        row = await repo.lock_draft_slot(conn, user_id)
        if row:
            if x_draft_token != row["active_token"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "token_mismatch",
                        "message": "다른 기기에서 이어서 작업을 시작했어요.",
                        "meta": _draft_slot_meta(row),
                    },
                )
            # advisory/row lock을 잡은 이 트랜잭션 안에서 active token을 소비한다.
            cleanup_assets = await _remove_draft_slot(conn, user_id, row)
        await conn.commit()
    if cleanup_assets:
        background_tasks.add_task(_cleanup_draft_slot_r2, _r2(request), cleanup_assets)
    return Response(status_code=204)


@router.delete(
    "/draft-slot/assets/{asset_id}",
    status_code=204,
    responses={**COMMON_RESPONSES, 204: {"description": "미참조 임시 자산 폐기 완료"}},
    tags=["Draft Slot"],
    summary="슬롯에 반영되지 않은 임시 사진 폐기",
)
async def discard_draft_slot_asset(
    request: Request,
    asset_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_user),
):
    try:
        normalized_id = str(uuid.UUID(asset_id))
    except (ValueError, TypeError):
        return Response(status_code=204)
    async with get_conn(request) as conn:
        cleanup_assets = await repo.soft_delete_unreferenced_draft_assets(
            conn, user_id, [normalized_id]
        )
        await conn.commit()
    if cleanup_assets:
        background_tasks.add_task(_cleanup_draft_slot_r2, _r2(request), cleanup_assets)
    return Response(status_code=204)


# ---------- 자산 업로드 (§3 presigned + finalize) ----------


@router.post(
    "/assets/upload-url",
    response_model=UploadUrlResponse,
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Assets & Uploads"],
    summary="업로드 presigned URL 발급",
)
async def create_upload_url(
    request: Request, body: UploadUrlRequest, user_id: str = Depends(require_user)
):
    """R2 클라우드 스토리지에 클라이언트가 파일을 직접 PUT 업로드할 수 있는 presigned URL을 발급받습니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `400 Bad Request` (`unsupported_type`): 지원되지 않는 MIME 타입(포맷)인 경우 발생
      - `400 Bad Request` (`file_too_large`): 파일 크기가 0이하 또는 25MB를 초과하는 경우 발생
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    ext = ext_for_mime(body.mime)
    if ext is None:
        raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")
    if body.size <= 0 or body.size > MAX_UPLOAD_BYTES:
        raise _bad_request("file_too_large", "파일 크기가 허용 범위를 벗어났습니다.")

    # draft_slot은 프로젝트 생성 전 백업이므로 project_id 없이 계정 전용 경로를 쓴다.
    if body.purpose != "draft_slot":
        async with get_conn(request) as conn:
            if await repo.get_project(conn, user_id, body.project_id) is None:
                raise _not_found()

    asset_id = str(uuid.uuid4())
    storage_scope = DRAFT_SLOT_STORAGE_KEY if body.purpose == "draft_slot" else body.project_id
    key = upload_key(user_id, storage_scope, asset_id, ext)
    upload_url = _r2(request).presigned_put(key, body.mime)  # 서명만 — 블로킹 아님
    return {
        "assetId": asset_id,
        "uploadUrl": upload_url,
        "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=UPLOAD_URL_TTL),
    }


@router.post(
    "/assets/{asset_id}/complete",
    response_model=Asset,
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Assets & Uploads"],
    summary="에셋 업로드 완료 알림 및 등록",
)
async def complete_upload(
    request: Request,
    asset_id: str,
    body: AssetCompleteRequest,
    user_id: str = Depends(require_user),
):
    """클라이언트가 R2 스토리지로 직접 업로드를 마친 후 호출합니다. 서버가 파일의 R2 적재를 최종 검증하고 데이터베이스에 등록합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `400 Bad Request` (`unsupported_type`): 지원하지 않는 이미지 파일 확장자/포맷인 경우 발생
      - `400 Bad Request` (`upload_incomplete`): R2 스토리지에 실제 파일 업로드가 완료되지 않은(찾을 수 없는) 경우 발생
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    ext = ext_for_mime(body.mime)
    if ext is None:
        raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")

    if body.purpose != "draft_slot":
        async with get_conn(request) as conn:
            if await repo.get_project(conn, user_id, body.project_id) is None:
                raise _not_found()

    # 키는 클라가 아니라 서버가 (user_id, projectId, assetId, ext)로 재유도 — 위변조 차단.
    storage_scope = DRAFT_SLOT_STORAGE_KEY if body.purpose == "draft_slot" else body.project_id
    key = upload_key(user_id, storage_scope, asset_id, ext)
    r2 = _r2(request)
    meta = await asyncio.to_thread(r2.head, key)  # 네트워크 → 스레드 격리 (§5)
    if meta is None:
        raise _bad_request("upload_incomplete", "업로드가 완료되지 않았어요. 다시 시도해 주세요.")

    # 입력측 QC (FR-D4) — off면 완전 skip. shadow=로그만, enforce=불합격 시 400.
    settings = request.app.state.settings
    if settings.input_qc != "off":
        # fail-open: QC용 R2 fetch 실패가 '이미 성공한 업로드'를 잃게 하면 안 됨(회귀 방지).
        # 인프라 에러면 iqc=None으로 두고 정상 등록 — enforce 거부는 '실제 품질 불합격'에만.
        try:
            raw = await asyncio.to_thread(r2.get_bytes, key)  # 네트워크 → 스레드 격리
            iqc = await asyncio.to_thread(input_qc.evaluate_input_qc, raw)
        except Exception:
            logger.warning(
                "input_qc_fetch_failed",
                extra={"asset_id": asset_id, "mode": settings.input_qc}, exc_info=True)
            iqc = None
        if iqc is not None:
            logger.info(
                "input_qc",
                extra={"mode": settings.input_qc, "asset_id": asset_id,
                       "verdict": iqc.verdict, "reasons": iqc.reasons},
            )
            if settings.input_qc == "enforce" and iqc.verdict == "reject":
                # 미등록으로 종료 — R2 객체는 남지만 asset row 없음(참조 없는 고아, 무해).
                raise _bad_request("input_quality", input_qc.input_qc_message(iqc))

    async with get_conn(request) as conn:
        row = await repo.create_asset(
            conn,
            asset_id=asset_id,
            user_id=user_id,
            project_id=None if body.purpose == "draft_slot" else body.project_id,
            source="upload",
            bucket=request.app.state.settings.r2_bucket,
            key=key,
            mime=meta["mime"] or body.mime,
            size=meta["size"],
            original_filename=body.filename,
            metadata={"purpose": body.purpose},
        )
        await conn.commit()
    return {
        "id": row["id"],
        "url": r2.public_url(key),
        "mimeType": row["mime_type"],
        "byteSize": row["byte_size"],
    }


# ---------- 마네킹 job (계약 §4·§6 · ai_pipeline_spec §4) ----------


def _cut_to_api(c: dict) -> dict:
    """mannequin_cuts row → MannequinCut. src=안정 앱 URL `/v1/assets/{id}/file` (만료 없음, §3).
    finalize_mannequin_success가 만드는 result/SSE done의 shape와 동일하게 유지."""
    display_asset_id = c.get("active_asset_id") or c["asset_id"]
    return {
        "id": f"{c['candidate']}-{c['version']}",
        "src": f"/v1/assets/{display_asset_id}/file",
        "candidate": c["candidate"],
        "version": c["version"],
        "baseFit": c["base_fit"],
        "fitAdjust": c["fit_adjust"],
        "lengthAdjust": c["length_adjust"],
        "matchAdjust": c["match_adjust"],
        # QC 점수 스냅샷. 재생성 경로는 jobs.result 봉투를 버리고 이 라우트를 재조회하므로,
        # 여기서 안 실으면 "생성 직후엔 보이다 재생성 후 사라지는" 비대칭이 생긴다.
        "qcScores": c.get("qc_scores"),
    }


@router.post(
    "/projects/{project_id}/mannequins:generate",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "새로운 마네킹 생성 작업이 대기열에 진입했습니다."},
        400: {"model": ErrorResponse, "description": "필수 전조건 미비 (예: 정면 이미지 누락)"},
        402: {"model": ErrorResponse, "description": "크레딧 잔액 부족"},
        409: {"model": ErrorResponse, "description": "다른 입력의 마네킹 생성이 이미 진행 중"},
    },
    tags=["Mannequins (AI)"],
    summary="마네킹 후보 생성 작업 시작",
)
async def generate_mannequins(
    request: Request,
    project_id: str,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """지정된 프로젝트의 상품 이미지를 기반으로 AI 마네킹 합성 컷(후보군 A, B)을 생성하는 비동기 작업을 요청합니다.

    - **Bearer Token**: 필수
    - **Header**: `Idempotency-Key` (필수 권장, 중복 차감 및 중복 작업 방지)
    - **에지 케이스 & 멱등성**:
      1. **완료된 결과가 이미 존재**: `200 OK`와 함께 기존 생성 결과를 그대로 반환하여 추가 크레딧 차감이 발생하지 않습니다.
      2. **이미 동일 작업이 진행 중**: 새로 작업을 띄우지 않고 `202 Accepted`와 함께 기존 실행 중인 `jobId`를 그대로 반환(작업 합류)합니다.
      3. **크레딧 차감 (402)**: 마네킹 생성에 필요한 크레딧(설정값, 기본 2)이 없으면 `402 Payment Required` 예외가 발생합니다.
      4. **입력 조건 (400)**: 기준 색상의 정면(Front) 사진 에셋이 아직 등록되지 않은 경우 `missing_front_photo` 에러가 발생합니다.
    """
    cost = request.app.state.settings.credit_cost_mannequin_generate
    # Idempotency-Key는 project:kind로 스코프 — 다른 프로젝트/종류에서 키 재사용 시 오인 방지
    scoped_key = f"{project_id}:mannequin:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        # 완료 재호출 → 기존 결과 반환 (새 job·차감 없음)
        cuts = await repo.list_mannequin_cuts(conn, user_id, project_id)
        if cuts:
            account = await repo.get_account(conn, user_id)
            return JSONResponse(
                {"data": [_cut_to_api(c) for c in cuts],
                 "credits": (account or {}).get("credits", 0)},
            )
        # create_job이 멱등/활성 합류를 원자 처리: 부분 unique INSERT라 동시 요청도
        # blocking으로 직렬화되어 프로젝트당 active job 1개만 생성되고 나머지는 합류한다(§6).
        # 합류(created=False)는 게이트·예약 없이 기존 job 반환 → 동시 재시도/입력검증으로 막지 않음.
        # 합류 시 기존 job payload 가 정본 — 아래 스냅샷은 신규 job 에만 실린다.
        snapshot = await _fit_profile_snapshot(conn, user_id, project_id, None)
        job_payload = {"mode": "generate", "fitProfileSnapshot": snapshot}
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload=job_payload, idempotency_key=scoped_key,
            credits_reserved=cost,
            metadata={"creditCostVersion": request.app.state.settings.credit_cost_version})
        if not created and not _mannequin_payload_matches(job, job_payload):
            raise _generation_in_progress()
        if created:  # 신규 job만 입력 게이트 + 예약. 실패 시 raise → 커밋 안 함 → job 생성 롤백
            product = await repo.get_product(conn, project_id)
            if not mannequin.has_base_front(product or {}):  # A-6: 정면 사진 필수
                raise _bad_request("missing_front_photo", "기준 색상 정면 사진을 먼저 올려주세요.")
            if await repo.reserve_credits(conn, user_id, cost) is None:
                raise HTTPException(
                    status_code=402,
                    detail={"code": "insufficient_credits", "message": "크레딧이 부족해요."})
        await conn.commit()
    _wake_dispatcher(request)
    return JSONResponse(status_code=202, content={"jobId": job["id"]})


@router.get(
    "/projects/{project_id}/mannequins",
    response_model=list[MannequinCut],
    responses={**COMMON_RESPONSES},
    tags=["Mannequins (AI)"],
    summary="생성된 마네킹 후보 목록 조회",
)
async def get_mannequins(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    """프로젝트 내에 생성 완료된 AI 마네킹 후보 컷 목록을 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        cuts = await repo.list_mannequin_cuts(conn, user_id, project_id)
    return [_cut_to_api(c) for c in cuts]


# ---------- 톤 에디터 (색감·밝기) ----------
#
# 마스크는 생성 직후 비동기로 준비된다. 여기서는 **읽기와 붙이기만** 한다 — SAM 호출도,
# 이미지 생성도, 크레딧 이동도 없다.


def _tone_editor_enabled(request: Request) -> bool:
    return getattr(request.app.state.settings, "mannequin_tone_editor", "off") == "on"


async def _tone_state(conn, r2, *, user_id: str, project_id: str, cut_id: str) -> dict:
    """컷 하나의 톤 에디터 상태. 마스크가 없으면 processing — 오류가 아니다."""
    cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, cut_id)
    if cut is None:
        raise _not_found()
    source_asset_id = str(cut.get("id") or "")
    mask = await editor_garment_mask.find_for_cut(conn, project_id=project_id, cut_id=cut_id)
    render = await mannequin_tone_render.active_for_cut(
        conn, project_id=project_id, cut_id=cut_id)
    meta = (render or {}).get("metadata") or {}
    mask_meta = (mask or {}).get("metadata") or {}
    return {
        "cutId": cut_id,
        "status": "ready" if mask else "processing",
        "maskAssetId": (mask or {}).get("id"),
        "maskAlgorithmVersion": mask_meta.get("algorithmVersion"),
        "sourceAssetId": source_asset_id,
        "adjustment": {"saturation": int(meta.get("saturation") or 0),
                       "exposure": int(meta.get("exposure") or 0)},
        "renderAssetId": (render or {}).get("id"),
    }


async def _enqueue_missing_tone_mask(conn, *, user_id: str, project_id: str,
                                     cut_id: str, state: dict) -> bool:
    """플래그를 켜기 전에 만들어진 컷의 마스크를 첫 조회에서 무과금으로 준비한다.

    생성 직후 큐잉과 같은 멱등키를 써서 새 컷·기존 컷·중복 폴링이 한 잡으로 합류한다.
    보조 마스크 큐 실패가 컷 조회를 500으로 만들면 안 되므로 실패는 rollback 후 삼킨다.
    """
    if state.get("status") != "processing":
        return False
    try:
        _job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id,
            kind="editor_garment_mask", payload={"cutId": cut_id},
            idempotency_key=(
                f"{project_id}:editor_garment_mask:{cut_id}:"
                f"{editor_garment_mask.ALGORITHM_VERSION}"
            ),
            credits_reserved=0, metadata={})
        await conn.commit()
        return created
    except Exception:  # noqa: BLE001 - 톤 마스크 실패가 기존 컷 조회를 막지 않는다
        with contextlib.suppress(Exception):
            await conn.rollback()
        logger.warning("tone mask lazy enqueue failed project=%s cut=%s",
                       project_id, cut_id, exc_info=True)
        return False


@router.get(
    "/projects/{project_id}/mannequins/{cut_id}/tone-editor",
    response_model=ToneEditorState,
    responses={**COMMON_RESPONSES},
    tags=["Mannequins (AI)"],
    summary="톤 에디터 상태 조회",
)
async def get_tone_editor(request: Request, project_id: str, cut_id: str,
                          user_id: str = Depends(require_user)):
    """색감·밝기 조정이 가능한지와, 저장된 조정값을 돌려준다.

    - `processing`: 마스크 전처리가 아직 안 끝났다. 다른 기능은 그대로 쓸 수 있다.
    - `ready`: 슬라이더를 열어도 된다.
    - `disabled`: 기능 플래그가 꺼져 있다.
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        if not _tone_editor_enabled(request):
            return {"cutId": cut_id, "status": "disabled"}
        state = await _tone_state(conn, _r2(request), user_id=user_id,
                                  project_id=project_id, cut_id=cut_id)
        created = await _enqueue_missing_tone_mask(
            conn, user_id=user_id, project_id=project_id, cut_id=cut_id, state=state)
    if created:
        _wake_dispatcher(request)
    return state


async def _tone_bytes(request: Request, project_id: str, cut_id: str, user_id: str,
                      which: str) -> Response:
    """편집에 필요한 픽셀을 **API 가 직접** 실어 보낸다 (302 리다이렉트가 아니라).

    `/assets/{id}/file` 은 R2 공개 도메인으로 302 를 준다. 브라우저는 그 응답의 CORS 헤더를
    R2 쪽에서 받게 되는데, 캔버스로 픽셀을 **읽으려면**(getImageData) 그쪽이 반드시
    Access-Control-Allow-Origin 을 줘야 한다. 인프라를 바꾸지 않고 그 조건을 만족시키는
    가장 작은 방법이 이 라우트다 — FastAPI 의 CORS 설정이 그대로 적용된다.

    에디터를 열 때 원본 1장 + 마스크 1장, 슬라이더를 움직이는 동안은 0장이다.
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, cut_id)
        if cut is None or not cut.get("r2_key"):
            raise _not_found()
        if which == "source":
            key, mime = cut["r2_key"], cut.get("mime_type") or "image/png"
        else:
            mask = await editor_garment_mask.find_for_cut(
                conn, project_id=project_id, cut_id=cut_id)
            if mask is None or not mask.get("r2_key"):
                raise _not_found()
            key, mime = mask["r2_key"], "image/png"
    try:
        data = await asyncio.to_thread(_r2(request).get_bytes, key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "code": "asset_unavailable",
            "message": "이미지를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."}) from exc
    return Response(content=data, media_type=mime, headers={"Cache-Control": IMMUTABLE_CACHE})


@router.get(
    "/projects/{project_id}/mannequins/{cut_id}/tone-editor/source",
    responses={**COMMON_RESPONSES},
    tags=["Mannequins (AI)"],
    summary="톤 에디터 원본 픽셀 (CORS 안전)",
)
async def get_tone_editor_source(request: Request, project_id: str, cut_id: str,
                                 user_id: str = Depends(require_user)):
    return await _tone_bytes(request, project_id, cut_id, user_id, "source")


@router.get(
    "/projects/{project_id}/mannequins/{cut_id}/tone-editor/mask",
    responses={**COMMON_RESPONSES},
    tags=["Mannequins (AI)"],
    summary="톤 에디터 의류 마스크 (CORS 안전)",
)
async def get_tone_editor_mask(request: Request, project_id: str, cut_id: str,
                               user_id: str = Depends(require_user)):
    return await _tone_bytes(request, project_id, cut_id, user_id, "mask")


@router.post(
    "/projects/{project_id}/mannequins/{cut_id}/tone-editor:apply",
    response_model=ToneEditorState,
    responses={**COMMON_RESPONSES, 400: {"model": ErrorResponse}},
    tags=["Mannequins (AI)"],
    summary="톤 조정 적용",
)
async def apply_tone_editor(request: Request, project_id: str, cut_id: str,
                            body: ToneApplyRequest, user_id: str = Depends(require_user)):
    """클라이언트가 원본 해상도로 렌더한 조정본을 이 컷에 붙인다.

    원본 컷은 건드리지 않는다. 조정본은 **별도 파생 자산**이고, 다시 편집할 때는 이 PNG 가
    아니라 원본 + 저장된 파라미터로 재구성한다(반복 편집 시 열화 방지).

    조정값이 0/0 이면 붙이지 않고 기존 조정본을 내린다 — 그게 곧 "초기화"다.
    """
    if not _tone_editor_enabled(request):
        raise _bad_request("tone_editor_disabled", "지금은 색감 조정을 쓸 수 없어요.")
    saturation, exposure = mannequin_tone_render.clamp_params(body.saturation, body.exposure)

    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, cut_id)
        if cut is None:
            raise _not_found()
        mask = await editor_garment_mask.find_for_cut(
            conn, project_id=project_id, cut_id=cut_id)
        if mask is None:
            raise _bad_request("mask_not_ready", "색감 조정 준비가 아직 끝나지 않았어요.")

        if mannequin_tone_render.is_neutral(saturation, exposure):
            await mannequin_tone_render.clear_for_cut(
                conn, project_id=project_id, cut_id=cut_id)
            await conn.commit()
            return await _tone_state(conn, _r2(request), user_id=user_id,
                                     project_id=project_id, cut_id=cut_id)

        # 올라온 자산이 **이 사용자·이 프로젝트**의 것인지 확인한다. 메타데이터의 주장만 믿고
        # 다른 프로젝트의 이미지를 결과로 붙이는 일이 없어야 한다.
        rendered = await repo.get_asset_for_user(conn, user_id, str(body.asset_id))
        if rendered is None or str(rendered.get("project_id") or project_id) != project_id:
            raise _not_found()

        await mannequin_tone_render.clear_for_cut(
            conn, project_id=project_id, cut_id=cut_id)
        await mannequin_tone_render.record(
            conn, user_id=user_id, project_id=project_id, asset_id=str(body.asset_id),
            cut_id=cut_id, source_asset_id=str(cut.get("id") or ""),
            source_hash=(mask.get("metadata") or {}).get("sourceHash"),
            mask_asset_id=mask.get("id"),
            mask_algorithm_version=(mask.get("metadata") or {}).get("algorithmVersion"),
            saturation=saturation, exposure=exposure)
        await conn.commit()
        return await _tone_state(conn, _r2(request), user_id=user_id,
                                 project_id=project_id, cut_id=cut_id)


@router.post(
    "/projects/{project_id}/mannequins:cancel",
    responses={**COMMON_RESPONSES},
    tags=["Mannequins (AI)"],
    summary="진행 중인 마네킹 생성 취소",
)
async def cancel_mannequin_generation(
    request: Request,
    project_id: str,
    user_id: str = Depends(require_user),
):
    """활성 마네킹 생성을 취소하고 예약 크레딧을 환불 없이 확정 차감한다.

    활성 job이 없으면 ``cancelled: false``로 성공하므로 재호출해도 안전하다.
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        credits = await repo.cancel_active_mannequin_job(conn, user_id, project_id)
        cancelled = credits is not None
        if not cancelled:
            account = await repo.get_account(conn, user_id)
            credits = (account or {}).get("credits", 0)
        await conn.commit()
    return {"cancelled": cancelled, "credits": credits}


@router.post(
    "/projects/{project_id}/mannequins:adjust",
    responses={
        **COMMON_RESPONSES,
        410: {"model": ErrorResponse, "description": "폐기된 엔드포인트 — mannequins:regenerate 사용"},
    },
    tags=["Mannequins (AI)"],
    summary="마네킹 조정 (AG-05) — @deprecated, 410 Gone",
    deprecated=True,
)
async def adjust_mannequin(
    request: Request,
    project_id: str,
    user_id: str = Depends(require_user),
):
    """**@deprecated (2026-07)** — 마네킹 조정 흐름이 fitProfile 재생성(`:regenerate`)으로 통합돼
    이 엔드포인트는 인증만 통과하면 바디·헤더와 무관하게 **항상 `410 Gone`** 을 반환합니다
    (바디를 파싱하지 않음 — Body 필수 검증이 있으면 빈/비JSON 요청이 422로 새서 계약이 흐려짐).
    잡을 생성하지 않으며 크레딧도 차감하지 않습니다.
    (단가가 0으로 내려간 상태에서 잡 생성을 허용하면 무과금 AI 생성 경로가 되므로 차단.)
    큐에 남은 legacy `mannequin_adjust` 잡은 툼스톤 워커(`mannequin_adjust_job`)가 **AI 호출 없이**
    실패 종결(예약 release)합니다.
    """
    raise HTTPException(
        status_code=410,
        detail={"code": "deprecated_endpoint",
                "message": "마네킹 조정은 종료된 기능이에요. 핏 수정 후 재생성을 이용해 주세요."})


async def _enqueue_base_fidelity_observation(conn, *, user_id, project_id):
    """거부된 컷의 베이스 충실도 관측 잡을 건다 (무과금·이미지 생성 없음).

    **재생성을 절대 막지 않는다.** 판정은 6~17초가 걸리므로 요청 경로에서 돌리지 않고, 잡 하나로
    떼어 비동기로 보낸다. 큐잉 자체가 실패해도 삼킨다 — 관측 때문에 셀러의 재생성이 실패하면
    본말전도다(2026-08-12 sam_preprocess 에서 같은 실수를 이미 한 번 했다).

    멱등키에 거부된 컷 id 와 판정기 버전을 넣는다. 같은 컷을 같은 판정기로 두 번 보는 것은
    표본이 아니라 중복이고, 판정기가 바뀌면 다시 볼 가치가 있다.
    """
    try:
        # 거부된 컷의 신원을 **여기서** 붙잡는다. 재생성 잡은 방금 큐에 들어갔을 뿐이라 아직
        # 새 컷이 없고, 지금의 "선택된 또는 최신 컷"이 곧 셀러가 거부한 그 컷이다.
        #
        # 커밋 **뒤**에 조회하는 이유: 같은 트랜잭션 안에서 돌렸더니 조회가 실패하는 순간
        # 재생성 요청이 통째로 500 이 됐다(2026-08-12 테스트에서 검출). 관측을 위해 본
        # 기능을 위험에 빠뜨리지 않는다.
        rejected = await repo.get_mannequin_edit_parent(conn, user_id, project_id)
        if not rejected or not rejected.get("id"):
            return  # 거부할 이전 컷이 없다(첫 생성 재시도 등) — 관측 대상 아님
        cut_id = rejected["id"]
        await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="base_fidelity_observe",
            payload={"rejectedCutId": cut_id,
                     "cutMetadata": rejected.get("generation_metadata")},
            idempotency_key=(f"{project_id}:base_fidelity_observe:{cut_id}:"
                             f"{mannequin_base_fidelity_qc.QC_VERSION}"),
            credits_reserved=0, metadata={})
        await conn.commit()
    except Exception:  # noqa: BLE001 - 관측 큐잉 실패가 재생성을 막지 않는다
        with contextlib.suppress(Exception):
            await conn.rollback()
        logger.warning("base fidelity observation enqueue failed for project %s",
                       project_id, exc_info=True)


@router.post(
    "/projects/{project_id}/mannequins:regenerate",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "마네킹 재생성 작업이 대기열에 진입했습니다."},
        400: {"model": ErrorResponse, "description": "필수 전조건 미비 (예: 정면 이미지 누락)"},
        402: {"model": ErrorResponse, "description": "크레딧 잔액 부족"},
        409: {"model": ErrorResponse, "description": "다른 입력의 마네킹 생성이 이미 진행 중"},
    },
    tags=["Mannequins (AI)"],
    summary="마네킹 재생성 작업 시작 (fit-profile 반영)",
)
async def regenerate_mannequins(
    request: Request,
    project_id: str,
    body: dict = Body(default={}),
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """조정된 fit-profile 을 반영해 마네킹 후보를 **새 버전으로 재생성**합니다.

    - **Bearer Token**: 필수
    - **Body**: `{ fitProfile? }` — 조정된 fit-profile(axes·matchingFit, legacy matchCut 호환).
      없으면 저장된 analysis 기준.
    - generate 와 동일한 워커·크레딧 경로지만 **완료 캐시 게이트를 건너뛴다** — 매 호출이 새 버전을
      만든다(finalize 가 candidate 별 `max(version)+1` 로 append). 크레딧은 generate 와 동일.
    - **에지 케이스**: `400 missing_front_photo`(정면 사진 없음), `402 insufficient_credits`(크레딧 부족).
    """
    cost = request.app.state.settings.credit_cost_mannequin_generate
    scoped_key = f"{project_id}:mannequin_regenerate:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        # generate 와 달리 완료 캐시 게이트 없음 — 항상 새 job 을 만들어 새 버전을 append 한다.
        # 스냅샷 = 잡 시점 effective profile + 서버 산출 adjustedAxes (fidelity 설계 D3·§E-2).
        snapshot = await _fit_profile_snapshot(
            conn,
            user_id,
            project_id,
            body.get("fitProfile"),
            validate_matching_fit=True,
        )
        job_payload = {
            "mode": "regenerate",
            "fitProfile": body.get("fitProfile"),
            "fitProfileSnapshot": snapshot,
        }
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload=job_payload,
            idempotency_key=scoped_key, credits_reserved=cost,
            metadata={"creditCostVersion": request.app.state.settings.credit_cost_version})
        if not created and not _mannequin_payload_matches(job, job_payload):
            raise _generation_in_progress()
        if created:  # 신규 job만 입력 게이트 + 예약. 실패 시 raise → 커밋 안 함 → job 생성 롤백
            product = await repo.get_product(conn, project_id)
            if not mannequin.has_base_front(product or {}):  # 정면 사진 필수(generate 동일)
                raise _bad_request("missing_front_photo", "기준 색상 정면 사진을 먼저 올려주세요.")
            if await repo.reserve_credits(conn, user_id, cost) is None:
                raise HTTPException(
                    status_code=402,
                    detail={"code": "insufficient_credits", "message": "크레딧이 부족해요."})
            # fit-profile 반영: 클라가 조정한 fitProfile 을 analysis 에 영속 → 워커의
            # generation_spec(analysis) 이 이를 읽어 재생성 컷에 반영한다(mannequin_job.py:205,
            # agents/mannequin.generation_spec = analysis["fitProfile"]). save_analysis 는 REPLACE 라
            # 저장된 analysis 가 있을 때만 full payload 에 머지한다(빈 {}에 넣어 다른 필드 유실 방지).
            fit_profile = body.get("fitProfile")
            if fit_profile:
                analysis = await repo.get_analysis(conn, project_id)
                if analysis:
                    analysis["fitProfile"] = fit_profile
                    await repo.save_analysis(conn, project_id, analysis)
        await conn.commit()
        if created:
            await _enqueue_base_fidelity_observation(
                conn, user_id=user_id, project_id=project_id)
    _wake_dispatcher(request)
    return JSONResponse(status_code=202, content={"jobId": job["id"]})


# ---------- 콘티/에디터/상세페이지 (PL-4) ----------


@router.get("/projects/{project_id}/storyboard", responses={**COMMON_RESPONSES},
            tags=["Detail Page"], summary="콘티 조회")
async def get_storyboard(request: Request, project_id: str, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        return await repo.get_storyboard(conn, project_id)


@router.put("/projects/{project_id}/storyboard", responses={**COMMON_RESPONSES},
            tags=["Detail Page"], summary="콘티 저장")
async def save_storyboard(request: Request, project_id: str, blocks: list = Body(...),
                          user_id: str = Depends(require_user)):
    _require_bg_examples_enabled(request, blocks)
    try:
        canonical = content_roles.canonicalize_storyboard(blocks, for_storage=True)
    except ValueError as exc:
        if str(exc) == "invalid_example_selection_origin":
            raise _bad_request("invalid_example_selection_origin", "생성예시 선택 출처 값이 올바르지 않아요.")
        raise
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        if any(
            isinstance(block, dict)
            and (
                block.get("exampleId")
                or block.get("spaceGroupId")
                or block.get("space_group_id")
            )
            for block in canonical
        ):
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            clothing_type = (
                product.get("clothingType")
                or product.get("clothing_type")
                or "top"
            )
            gender = mannequin.select_base_gender(analysis, clothing_type)
            error = space_set_assets.validate_storyboard_space_sets(
                canonical, clothing_type=clothing_type, gender=gender
            )
            if error:
                raise _bad_request(*error)
            standalone_set_example_blocks = [
                block
                for block in canonical
                if isinstance(block, dict)
                and str(block.get("exampleId") or "").startswith("ss_")
                and not (
                    block.get("spaceGroupId") or block.get("space_group_id")
                )
            ]
            for block in standalone_set_example_blocks:
                scope = block.get("refScope") or block.get("ref_scope") or "all"
                try:
                    space_set_assets.resolve_published_example_reference(
                        block,
                        clothing_type=clothing_type,
                        gender=gender,
                        scope=scope,
                    )
                except space_set_assets.SpaceSetBindingError as exc:
                    raise _bad_request(exc.code, exc.message) from exc
            standalone_set_example_ids = {
                id(block) for block in standalone_set_example_blocks
            }
            flat_blocks = [
                block
                for block in canonical
                if isinstance(block, dict)
                and block.get("exampleId")
                and id(block) not in standalone_set_example_ids
                and space_set_assets.parse_space_set_group_id(
                    block.get("spaceGroupId") or block.get("space_group_id")
                )
                is None
            ]
            if flat_blocks:
                _base_url, assets = cut_generator.load_example_asset_registry()
                error = content_roles.validate_storyboard_example_references(
                    flat_blocks,
                    assets=assets,
                    clothing_type=clothing_type,
                    gender=gender,
                )
                if error:
                    raise _bad_request(*error)
        out = await repo.save_storyboard(conn, user_id, project_id, canonical)
        await conn.commit()
    return out


@router.get("/projects/{project_id}/editor-blocks", responses={**COMMON_RESPONSES},
            tags=["Detail Page"], summary="에디터 블록 조회")
async def get_editor_blocks(request: Request, project_id: str, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        return await repo.get_editor_blocks(conn, project_id)


@router.put("/projects/{project_id}/editor-blocks", responses={**COMMON_RESPONSES},
            tags=["Detail Page"], summary="에디터 블록 저장")
async def save_editor_blocks(request: Request, project_id: str, blocks: list = Body(...),
                             user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        await repo.save_editor_blocks(conn, user_id, project_id, blocks)
        await conn.commit()
    return {"ok": True}


# ---------- 에디터 의류 탭(Wardrobe, PL-5/6 · AG-06/07) ----------


@router.get(
    "/projects/{project_id}/wardrobe",
    responses={**COMMON_RESPONSES},
    tags=["Detail Page"],
    summary="에디터 Wardrobe(의류 탭) 목록 조회",
)
async def get_wardrobe(request: Request, project_id: str, user_id: str = Depends(require_user)):
    """에디터 AI 탭에 표시할 Wardrobe 이미지 목록. 그룹 키(colorId | 'misc')로 묶어 반환합니다
    (계약 §3.6 `Record<colorId|'misc', WardrobeImage[]>`).

    - **Bearer Token**: 필수
    - **에지 케이스**: `404 Not Found` — 프로젝트가 존재하지 않거나 타 사용자 소유인 경우
    """
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        rows = await repo.list_wardrobe_images(conn, user_id, project_id)
    wardrobe: dict[str, list] = {}
    for r in rows:
        group = r["color_id"] or "misc"
        wardrobe.setdefault(group, []).append(repo._wardrobe_image_api(r))
    return wardrobe


@router.post(
    "/projects/{project_id}/editor:generate-image",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "에디터 이미지 생성 작업이 대기열에 진입했습니다."},
        402: {"model": ErrorResponse, "description": "크레딧 잔액 부족"},
    },
    tags=["Detail Page"],
    summary="에디터 이미지 생성 작업 시작 (AG-06/07)",
)
async def generate_editor_image(
    request: Request,
    project_id: str,
    body: dict = Body(...),
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """에디터 AI 탭의 '새 이미지 추가'(`mode:'new'`, AG-06 재사용) 또는 '현재 이미지 수정'
    (`mode:'vary'`, AG-07)을 생성하는 비동기 작업을 요청합니다. `NewCutRequest` /
    `VaryRequest`(계약 §6)를 그대로 본문으로 받습니다.

    - **Bearer Token**: 필수
    - **Body**: `NewCutRequest { mode:'new', colorId, contentRole, cutType, direction?, shot?, modelId? }` |
      `VaryRequest { mode:'vary', source:{src,cutType}, changes[], refBg? }`
    - **Header**: `Idempotency-Key` (권장, 중복 차감 및 중복 작업 방지) — `editor_image`는 매 호출이
      새 이미지를 생성하므로(완료 재호출 재사용 없음) 활성-중복 dedup 대상에서 제외되고, 멱등은
      이 키로만 보장됩니다.
    - **에지 케이스**: `402 Payment Required` — 크레딧(설정값, 기본 1)이 없으면 발생
    """
    _require_bg_examples_enabled(request, body)
    if (body or {}).get("mode") == "new" and (
        (body or {}).get("spaceGroupId") or (body or {}).get("space_group_id")
    ):
        raise _bad_request(
            "space_set_editor_unsupported",
            "촬영 세트는 콘티보드에서만 사용할 수 있어요.",
        )
    s = request.app.state.settings
    cost = s.credit_cost_editor_image
    scoped_key = f"{project_id}:editor_image:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        if (
            (body or {}).get("mode") == "new"
            and str((body or {}).get("exampleId") or "").startswith("ss_")
        ):
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            clothing_type = (
                product.get("clothingType")
                or product.get("clothing_type")
                or "top"
            )
            try:
                example_spec = cut_generator.normalize_spec(
                    content_roles.canonicalize_storyboard_block(body),
                    clothing_type=clothing_type,
                )
                space_set_assets.resolve_published_example_reference(
                    example_spec,
                    clothing_type=clothing_type,
                    gender=mannequin.select_base_gender(
                        analysis, clothing_type
                    ),
                    scope=example_spec["refScope"],
                )
            except space_set_assets.SpaceSetBindingError as exc:
                raise _bad_request(exc.code, exc.message) from exc
            except ValueError as exc:
                raise _bad_request(
                    "invalid_spec", "컷 설정이 올바르지 않아요. 다시 시도해 주세요."
                ) from exc
        # FaceMarket verify-before-use 게이트(FM-30) — 에디터 새 컷도 상세페이지와 동일하게,
        # 실존 모델(UUID modelId) 선택 시 라이선스 자격을 잡 생성 전에 검증한다(실패=409, 예약 없음).
        # 가상모델('mA' 등 비-UUID)·무라이선스 모델은 no-op → 기존 플로우 무영향.
        if s.facemarket_enabled and (body or {}).get("mode") == "new":
            license_row = await facemarket.resolve_model_license(
                conn, (body or {}).get("modelId") or (body or {}).get("model_id"))
            if license_row is not None:
                await facemarket.verify_license(request.app, license_row)  # 실패=409
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="editor_image",
            payload=body, idempotency_key=scoped_key, credits_reserved=cost,
            metadata={"creditCostVersion": s.credit_cost_version})
        if created:  # 신규 job만 예약. 실패 시 raise → 커밋 안 함 → job 생성 롤백
            if await repo.reserve_credits(conn, user_id, cost) is None:
                raise HTTPException(
                    status_code=402,
                    detail={"code": "insufficient_credits", "message": "크레딧이 부족해요."})
        await conn.commit()
    _wake_dispatcher(request)
    return JSONResponse(status_code=202, content={"jobId": job["id"]})


@router.post(
    "/projects/{project_id}/detail-page:generate",
    responses={**COMMON_RESPONSES, 202: {"description": "상세페이지 생성 작업 진입"},
               400: {"model": ErrorResponse}, 402: {"model": ErrorResponse}},
    tags=["Detail Page"], summary="상세페이지 생성 작업 시작 (PL-4)",
)
async def generate_detail_page(
    request: Request, project_id: str, user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """저장된 콘티로 AI 컷(AG-06) + 카피(AG-02/03) 생성 → M-02 조립 → EditorBlock[]. 크레딧:
    storyboardPerCut × source='ai' 블록 수(성공 컷만 차감). 완료 재호출은 기존 결과 반환(무차감)."""
    s = request.app.state.settings
    scoped_key = f"{project_id}:detail_page:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        project = await repo.get_project(conn, user_id, project_id)
        if project is None:
            raise _not_found()
        # FaceMarket verify-before-use 게이트(FM-30). **캐시 반환보다 먼저** — 해지·만료된
        # 라이선스가 이미 생성된 페이지의 재생성까지 막아야 하므로(장면⑤). facemarket off면
        # 미진입 → 기존 셀러 플로우 무영향. 선택 모델에 라이선스 없으면 no-op(비-FaceMarket 셀러).
        if s.facemarket_enabled:
            analysis = await repo.get_analysis(conn, project_id)
            license_row = await facemarket.resolve_project_license(conn, project, analysis)
            if license_row is not None:
                await facemarket.verify_license(request.app, license_row)  # 실패=409
                await facemarket.set_project_license(conn, project_id, license_row["id"])
                await conn.commit()  # 잠금 확정 — 캐시 반환 경로도 워커 정산 포인터 보존
        existing = await repo.get_editor_blocks(conn, project_id)
        if existing:  # 완료 재호출 → 기존 결과 반환(재생성·재차감 없음)
            account = await repo.get_account(conn, user_id)
            return JSONResponse({"data": existing, "credits": (account or {}).get("credits", 0)})
        storyboard = await repo.get_storyboard(conn, project_id)
        _require_bg_examples_enabled(request, storyboard)
        # 크레딧 견적은 실제 생성 수 기준 — 생성 계약이 완전히 같은 복제 컷은 1장만
        # 생성해 복사하므로(ADR-0011, _duplicate_source_indexes) 예약에서도 접는다.
        # 그대로 두면 복제가 많은 보드가 사전검사(402)에서 과도하게 거절된다(Codex 2차 #3).
        from .workers.detail_page_job import _duplicate_source_indexes
        product_row = await repo.get_product(conn, project_id)
        clothing_type = ((product_row or {}).get("clothing_type")
                         or (product_row or {}).get("clothingType") or "top")
        ai_blocks = [b for b in storyboard if isinstance(b, dict) and b.get("source") == "ai"]
        dup_sources = _duplicate_source_indexes(ai_blocks, clothing_type)
        ai_count = sum(1 for source in dup_sources if source is None)
        cost = ai_count * s.credit_cost_storyboard_per_cut
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="detail_page",
            payload={"mode": "generate"}, idempotency_key=scoped_key, credits_reserved=cost,
            # perCutCost = 예약 시점 컷당 단가 스냅샷 — 워커 정산의 단일 기준(실행 시점 설정
            # 변경·콘티 재저장으로 인한 블록 수 변동과 무관하게 견적 가격을 고정).
            metadata={"creditCostVersion": s.credit_cost_version,
                      "perCutCost": s.credit_cost_storyboard_per_cut, "aiCount": ai_count})
        if created:
            if not storyboard:
                raise _bad_request("empty_storyboard", "콘티가 비어 있어요. 먼저 콘티를 저장해 주세요.")
            if cost > 0 and await repo.reserve_credits(conn, user_id, cost) is None:
                raise HTTPException(
                    status_code=402,
                    detail={"code": "insufficient_credits", "message": "크레딧이 부족해요."})
        await conn.commit()
    _wake_dispatcher(request)
    return JSONResponse(status_code=202, content={"jobId": job["id"]})


@router.get(
    "/assets/{asset_id}/file",
    responses={**COMMON_RESPONSES, 302: {"description": "R2 presigned GET URL로 302 리다이렉트"}},
    tags=["Assets & Uploads"],
    summary="안정 에셋 파일 서빙 (302 Redirect)",
)
async def get_asset_file(request: Request, asset_id: str):
    """프론트엔드 에디터/화면에서 상시 사용 가능한 불변 에셋 이미지 경로입니다. 실제 스토리지 URL로 302 리다이렉트합니다.

    - **인증 없음 (capability URL)**: 브라우저 `<img src>`가 Bearer를 붙일 수 없어 무인증이 필수.
      asset id(UUIDv4)가 능력 토큰이며, R2 객체 자체가 public base로 이미 공개라 새 노출 없음.
      (구 계약의 "Bearer 필수·타인 소유 404"는 <img> 렌더 불가 실버그라 2026-07-11 폐기.)
    - **에지 케이스**:
      - `404 Not Found`: 자산이 존재하지 않거나 id 형식이 잘못된 경우
    """
    try:
        uuid.UUID(asset_id)  # 공개 라우트 — 쓰레기 입력은 DB 전에 404로 컷
    except ValueError:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "자산을 찾을 수 없습니다."})
    async with get_conn(request) as conn:
        asset = await repo.get_asset_public(conn, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "자산을 찾을 수 없습니다."})
    return RedirectResponse(
        _r2(request).public_url(asset["r2_key"]),
        status_code=302,
        headers={"Cache-Control": IMMUTABLE_CACHE},
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobView,
    responses={**COMMON_RESPONSES},
    tags=["Jobs & SSE"],
    summary="작업(Job) 상태 조회",
)
async def get_job(request: Request, job_id: str, user_id: str = Depends(require_user)):
    """비동기로 시작된 백그라운드 작업(AI 생성 등)의 현재 상태(pending, running, done, error, cancelled) 및 진행도(0~100%)를 조회합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 해당 작업이 존재하지 않거나, 다른 사용자가 소유한 경우 발생
    """
    async with get_conn(request) as conn:
        row = await repo.get_job(conn, user_id, job_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "작업을 찾을 수 없습니다."})
    return row


@router.get(
    "/jobs/{job_id}/events",
    responses={**COMMON_RESPONSES},
    tags=["Jobs & SSE"],
    summary="작업 실시간 이벤트 스트림 (SSE)",
)
async def job_events(
    request: Request,
    job_id: str,
    user_id: str = Depends(require_user),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    after: int = Query(0),
    poll: int = Query(0),
):
    """지정된 백그라운드 작업의 상태 변경이나 진행 이벤트 로그를 실시간 Server-Sent Events (SSE) 형식으로 스트리밍합니다.

    - **Bearer Token**: 필수
    - **Header**: `Last-Event-ID` (클라이언트 연결 재시도 시, 마지막으로 받은 이벤트 ID 이후부터 스트림 재개)
    - **에지 케이스**:
      - `404 Not Found`: 해당 작업이 존재하지 않거나, 다른 사용자가 소유한 경우 발생
      - 완료(`done`), 실패(`error`) 혹은 취소(`cancelled`) 이벤트가 전달되거나, 최대 5분(300초)이 경과하면 연결이 안전하게 정리 종료됩니다.
    """
    async with get_conn(request) as conn:  # 소유권 확인
        if await repo.get_job(conn, user_id, job_id) is None:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "작업을 찾을 수 없습니다."})
    # ?poll=1 — SSE 대신 1회 JSON 조회(editor_wait_dev_spec §2-2). EventSource 는 Bearer
    # 헤더를 못 실으므로 프론트는 마네킹과 동일하게 폴링으로 통일한다. after 커서 재사용.
    if poll:
        async with get_conn(request) as conn:
            events = await repo.list_job_events(conn, user_id, job_id, after)
        return JSONResponse({"events": [
            {"id": e["id"], "type": e["event_type"], "payload": e["payload"]} for e in events
        ]})
    start = int(last_event_id) if (last_event_id is not None and last_event_id.isdigit()) else after
    pool = request.app.state.pool

    async def gen():
        after_id = start
        deadline = time.monotonic() + 300  # 5분 상한 (이후 클라가 재연결)
        while time.monotonic() < deadline:
            async with pool.connection() as conn:
                events = await repo.list_job_events(conn, user_id, job_id, after_id)
            for e in events:
                after_id = e["id"]
                payload = json.dumps(e["payload"], ensure_ascii=False)
                yield f"id: {e['id']}\nevent: {e['event_type']}\ndata: {payload}\n\n"
                if e["event_type"] in ("done", "error", "cancelled"):
                    return
            if not events:  # 종결 이벤트를 이미 본 뒤 재연결 → 상태 확인해 즉시 종료(5분 hang 방지)
                async with pool.connection() as conn:
                    job = await repo.get_job(conn, user_id, job_id)
                if job and job["status"] in ("done", "error", "cancelled"):
                    return
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
