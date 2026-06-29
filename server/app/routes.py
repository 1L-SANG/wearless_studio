"""HTTP 라우트 — Phase 1~2 (backend_integration_plan §4).

읽기(/me/account, /projects?view=library) + projects CRUD + 자산 업로드(§3).
모든 라우트는 require_user로 JWT sub를 받고, repo가 그 user_id로 소유권을 스코프한다.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from . import repo
from .agents import mannequin
from .services import matching
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
    ranked = matching.recommend(items, clothingType, genders, limit)
    return JSONResponse([
        {
            "id": i["id"], "name": i["name"], "gender": i["gender"],
            "thumb": r2.public_url(i["thumb_key"]),
            "imageUrl": r2.public_url(i["image_key"]) if i.get("image_key") else None,
            "thumbnailUrl": r2.public_url(i["thumb_key"]),
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
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "generate"}, idempotency_key=scoped_key,
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


@router.get(
    "/assets/{asset_id}/file",
    responses={**COMMON_RESPONSES, 302: {"description": "R2 presigned GET URL로 302 리다이렉트"}},
    tags=["Assets & Uploads"],
    summary="안정 에셋 파일 서빙 (302 Redirect)",
)
async def get_asset_file(
    request: Request, asset_id: str, user_id: str = Depends(require_user)
):
    """프론트엔드 에디터/화면에서 상시 사용 가능한 불변 에셋 이미지 경로입니다. 접근 권한(user_id) 확인 후 실제 스토리지의 단기 만료 서명 URL로 302 리다이렉트합니다.

    - **Bearer Token**: 필수
    - **에지 케이스**:
      - `404 Not Found`: 자산이 존재하지 않거나, 다른 사용자가 소유한 경우 발생
    """
    async with get_conn(request) as conn:
        asset = await repo.get_asset_for_user(conn, user_id, asset_id)
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
