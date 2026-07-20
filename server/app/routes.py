"""HTTP 라우트 — Phase 1~2 (backend_integration_plan §4).

읽기(/me/account, /projects?view=library) + projects CRUD + 자산 업로드(§3).
모든 라우트는 require_user로 JWT sub를 받고, repo가 그 user_id로 소유권을 스코프한다.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from . import facemarket, repo
from .agents import content_roles, fit_axes, mannequin, product_analyst, style_affinity
from .agents.gemini_image import InlineImage
from .agents.vision_llm import VisionError
from .services import input_qc, matching, retrieval
from .auth import require_user
from .db import get_conn
from .models import (
    Account,
    Asset,
    AssetCompleteRequest,
    CreditHistoryEntry,
    CreditSource,
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
)
from .r2 import R2Client, ext_for_mime, upload_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15MB — 상품 사진 상한 (업로드 실패 사유 표면화 §)
UPLOAD_URL_TTL = 300  # presigned PUT 만료(초)

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
        item_metadata = await repo.get_matching_item_metadata(conn, main_match_id)
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
            main_match_id and await repo.get_matching_item_asset(conn, main_match_id)
        )
        if not has_match:
            profile = {k: v for k, v in profile.items() if k not in ("matchCut", "matchingFit")}
    return {"version": 1, "profile": profile, "adjustedAxes": adjusted}


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


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
    """지정된 요금제 코드로 크레딧을 수동 충전합니다. (PG 연동 전 임시 테스트용)

    - **Bearer Token**: 필수
    - **Header**: `Idempotency-Key` (선택, 동일 요청 중복 방지)
    - **에지 케이스**:
      - `400 Bad Request`: 존재하지 않는 요금제 코드이거나 중복 충전 시도 시 발생
    """
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
    # analysis는 프론트 소유 shape → payload jsonb 패스스루 저장.
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
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
        payload = await repo.get_analysis(conn, project_id)
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
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        items = await repo.list_active_matching_items(conn)
    genders = [g.strip() for part in gender for g in part.split(",") if g.strip()]
    product_tags = [t.strip() for part in styleTags for t in part.split(",") if t.strip()]
    if request.app.state.settings.retrieval_matching == "tags" and product_tags:
        ranked = retrieval.recommend_v1(
            items, clothingType, genders, product_tags, style_affinity.affinity_map(), limit)
    else:
        ranked = matching.recommend(items, clothingType, genders, limit)
    return JSONResponse([
        {
            "id": i["id"], "name": i["name"], "gender": i["gender"],
            "thumb": r2.public_url(i["thumb_key"]),
            "imageUrl": r2.public_url(i["image_key"]) if i.get("image_key") else None,
            "thumbnailUrl": r2.public_url(i["thumb_key"]),
            "clothingType": i.get("clothing_type"),
            "category": i.get("category"),
            "fit": i.get("fit"),
            "length": i.get("length"),
            "fitCategory": matching.fit_category(i),
            "selected": False,
        }
        for i in ranked if i.get("thumb_key")
    ])


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
      - `400 Bad Request` (`file_too_large`): 파일 크기가 0이하 또는 15MB를 초과하는 경우 발생
      - `404 Not Found`: 프로젝트가 존재하지 않거나, 타 사용자의 소유인 경우 발생
    """
    ext = ext_for_mime(body.mime)
    if ext is None:
        raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")
    if body.size <= 0 or body.size > MAX_UPLOAD_BYTES:
        raise _bad_request("file_too_large", "파일 크기가 허용 범위를 벗어났습니다.")

    # 프로젝트 소유권 확인 — 타인 프로젝트 경로로 업로드 URL 발급 차단
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, body.project_id) is None:
            raise _not_found()

    asset_id = str(uuid.uuid4())
    key = upload_key(user_id, body.project_id, asset_id, ext)
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

    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, body.project_id) is None:
            raise _not_found()

    # 키는 클라가 아니라 서버가 (user_id, projectId, assetId, ext)로 재유도 — 위변조 차단.
    key = upload_key(user_id, body.project_id, asset_id, ext)
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
            iqc = input_qc.evaluate_input_qc(raw)
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
            project_id=body.project_id,
            source="upload",
            bucket=request.app.state.settings.r2_bucket,
            key=key,
            mime=meta["mime"] or body.mime,
            size=meta["size"],
            original_filename=body.filename,
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
    return {
        "id": f"{c['candidate']}-{c['version']}",
        "src": f"/v1/assets/{c['asset_id']}/file",
        "candidate": c["candidate"],
        "version": c["version"],
        "baseFit": c["base_fit"],
        "fitAdjust": c["fit_adjust"],
        "lengthAdjust": c["length_adjust"],
        "matchAdjust": c["match_adjust"],
    }


@router.post(
    "/projects/{project_id}/mannequins:generate",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "새로운 마네킹 생성 작업이 대기열에 진입했습니다."},
        400: {"model": ErrorResponse, "description": "필수 전조건 미비 (예: 정면 이미지 누락)"},
        402: {"model": ErrorResponse, "description": "크레딧 잔액 부족"},
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
        snapshot = await _fit_profile_snapshot(conn, project_id, None)
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "generate", "fitProfileSnapshot": snapshot}, idempotency_key=scoped_key,
            credits_reserved=cost,
            metadata={"creditCostVersion": request.app.state.settings.credit_cost_version})
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


@router.post(
    "/projects/{project_id}/mannequins:regenerate",
    responses={
        **COMMON_RESPONSES,
        202: {"description": "마네킹 재생성 작업이 대기열에 진입했습니다."},
        400: {"model": ErrorResponse, "description": "필수 전조건 미비 (예: 정면 이미지 누락)"},
        402: {"model": ErrorResponse, "description": "크레딧 잔액 부족"},
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
            project_id,
            body.get("fitProfile"),
            validate_matching_fit=True,
        )
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "regenerate", "fitProfile": body.get("fitProfile"),
                     "fitProfileSnapshot": snapshot},
            idempotency_key=scoped_key, credits_reserved=cost,
            metadata={"creditCostVersion": request.app.state.settings.credit_cost_version})
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
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        out = await repo.save_storyboard(
            conn, user_id, project_id,
            content_roles.canonicalize_storyboard(blocks, for_storage=True))
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
    s = request.app.state.settings
    cost = s.credit_cost_editor_image
    scoped_key = f"{project_id}:editor_image:{idempotency_key}" if idempotency_key else None
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
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
        ai_count = sum(1 for b in storyboard if isinstance(b, dict) and b.get("source") == "ai")
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
    return RedirectResponse(_r2(request).public_url(asset["r2_key"]), status_code=302)


@router.get(
    "/jobs/{job_id}",
    response_model=JobView,
    responses={**COMMON_RESPONSES},
    tags=["Jobs & SSE"],
    summary="작업(Job) 상태 조회",
)
async def get_job(request: Request, job_id: str, user_id: str = Depends(require_user)):
    """비동기로 시작된 백그라운드 작업(AI 생성 등)의 현재 상태(pending, running, done, error) 및 진행도(0~100%)를 조회합니다.

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
):
    """지정된 백그라운드 작업의 상태 변경이나 진행 이벤트 로그를 실시간 Server-Sent Events (SSE) 형식으로 스트리밍합니다.

    - **Bearer Token**: 필수
    - **Header**: `Last-Event-ID` (클라이언트 연결 재시도 시, 마지막으로 받은 이벤트 ID 이후부터 스트림 재개)
    - **에지 케이스**:
      - `404 Not Found`: 해당 작업이 존재하지 않거나, 다른 사용자가 소유한 경우 발생
      - 완료(`done`) 혹은 실패(`error`) 이벤트가 전달되거나, 최대 5분(300초)이 경과하면 연결이 안전하게 정리 종료됩니다.
    """
    async with get_conn(request) as conn:  # 소유권 확인
        if await repo.get_job(conn, user_id, job_id) is None:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "작업을 찾을 수 없습니다."})
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
                if e["event_type"] in ("done", "error"):
                    return
            if not events:  # 종결 이벤트를 이미 본 뒤 재연결 → 상태 확인해 즉시 종료(5분 hang 방지)
                async with pool.connection() as conn:
                    job = await repo.get_job(conn, user_id, job_id)
                if job and job["status"] in ("done", "error"):
                    return
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
