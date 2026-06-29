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


@router.get("/me/account", response_model=Account)
async def get_account(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        row = await repo.get_account(conn, user_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "account_not_found", "message": "계정 정보를 찾을 수 없습니다."},
        )
    return row


# ---------- 크레딧 (credit_system_design.md §6) ----------


@router.get("/pricing-plans", response_model=list[PricingPlan])
async def get_pricing_plans(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        return await repo.list_pricing_plans(conn)


@router.get("/credits/sources", response_model=list[CreditSource])
async def get_credit_sources(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        return await repo.list_credit_sources(conn, user_id)


@router.get("/credits/history", response_model=list[CreditHistoryEntry])
async def get_credit_history(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        return await repo.list_credit_history(conn, user_id)


@router.post("/credits/topups:purchase")
async def purchase_topup(
    request: Request,
    body: TopupPurchaseBody,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async with get_conn(request) as conn:
        try:
            result = await repo.purchase_topup(
                conn, user_id=user_id, plan_code=body.plan_code, idempotency_key=idempotency_key
            )
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.post("/credits/refunds")
async def request_refund(
    request: Request, body: RefundRequestBody, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        try:
            result = await repo.request_refund(
                conn, user_id=user_id, credit_source_id=body.credit_source_id, reason=body.reason
            )
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result, status_code=201)


@router.post("/admin/refunds/{request_id}/approve")
async def approve_refund(
    request: Request, request_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        if not await repo.is_admin(conn, user_id):
            raise HTTPException(403, detail={"code": "forbidden", "message": "관리자만 가능해요."})
        try:
            result = await repo.approve_refund(conn, request_id=request_id, resolved_by=user_id)
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.post("/admin/refunds/{request_id}/reject")
async def reject_refund(
    request: Request, request_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        if not await repo.is_admin(conn, user_id):
            raise HTTPException(403, detail={"code": "forbidden", "message": "관리자만 가능해요."})
        try:
            result = await repo.reject_refund(conn, request_id=request_id, resolved_by=user_id)
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(result)


@router.get("/projects", response_model=list[ProjectSummary])
async def get_library(
    request: Request,
    view: str = Query("library"),
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        return await repo.list_library(conn, user_id)


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        row = await repo.create_project(conn, user_id)
        await conn.commit()
    return row


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        row = await repo.get_project(conn, user_id, project_id)
    if row is None:
        raise _not_found()
    return row


@router.patch("/projects/{project_id}", response_model=Project)
async def patch_project(
    request: Request,
    project_id: str,
    patch: ProjectPatch,
    user_id: str = Depends(require_user),
):
    # adjustCount·status 등은 모델에 없어 자동 무시 (계약 §6). exclude_unset = 보낸 필드만.
    fields = patch.model_dump(exclude_unset=True)
    async with get_conn(request) as conn:
        row = await repo.patch_project(conn, user_id, project_id, fields)
        await conn.commit()
    if row is None:
        raise _not_found()
    return row


# ---------- product (계약 §3.1) ----------


@router.get("/projects/{project_id}/product", response_model=Product)
async def get_product(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
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


@router.patch("/projects/{project_id}/product", response_model=Product)
async def save_product(
    request: Request,
    project_id: str,
    patch: ProductPatch,
    user_id: str = Depends(require_user),
):
    fields = patch.model_dump(exclude_unset=True)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        row = await repo.save_product(conn, project_id, user_id, fields)
        await conn.commit()
    return row


# ---------- analysis (계약 §3.2) ----------


@router.patch("/projects/{project_id}/analysis")
async def save_analysis(
    request: Request,
    project_id: str,
    analysis: dict = Body(...),
    user_id: str = Depends(require_user),
):
    # analysis는 프론트 소유 shape → payload jsonb 패스스루 저장.
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        row = await repo.save_analysis(conn, project_id, analysis)
        await conn.commit()
    return {"projectId": row["project_id"], **(row["payload"] or {})}


@router.get("/projects/{project_id}/analysis/match-candidates")
async def match_candidates(
    request: Request,
    project_id: str,
    clothingType: str = Query(...),
    gender: list[str] = Query(default=[]),
    limit: int | None = Query(default=None),
    user_id: str = Depends(require_user),
):
    """매칭 후보(보색 의류) — 공개 R2 썸네일 URL 포함 레거시 MatchClothing[].
    선택값은 클라가 오버레이(서버 저장 없음, 과도기 계약 §4)."""
    if not request.app.state.settings.r2_public_base:
        raise HTTPException(status_code=500, detail={
            "code": "r2_public_base_missing",
            "message": "이미지 서버 설정이 누락됐어요. 잠시 후 다시 시도해 주세요."})
    r2 = _r2(request)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        items = await repo.list_active_matching_items(conn)
    genders = [g for part in gender for g in part.split(",") if g]
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


@router.post("/assets/upload-url", response_model=UploadUrlResponse)
async def create_upload_url(
    request: Request, body: UploadUrlRequest, user_id: str = Depends(require_user)
):
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


@router.post("/assets/{asset_id}/complete", response_model=Asset)
async def complete_upload(
    request: Request,
    asset_id: str,
    body: AssetCompleteRequest,
    user_id: str = Depends(require_user),
):
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


@router.post("/projects/{project_id}/mannequins:generate")
async def generate_mannequins(
    request: Request,
    project_id: str,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """완료 컷 있으면 그 결과(200·무차감), 없으면 예약(at route·즉시 402)+job 생성→202 {jobId}.
    멱등: Idempotency-Key/활성 중복은 create_job이 합류 (계약 §6)."""
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


@router.get("/projects/{project_id}/mannequins", response_model=list[MannequinCut])
async def get_mannequins(
    request: Request, project_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _not_found()
        cuts = await repo.list_mannequin_cuts(conn, user_id, project_id)
    return [_cut_to_api(c) for c in cuts]


@router.get("/assets/{asset_id}/file")
async def get_asset_file(
    request: Request, asset_id: str, user_id: str = Depends(require_user)
):
    """안정 앱 URL → 권한 확인 후 R2 서빙 URL로 302 (계약 §3). src가 만료돼도 이 URL은 불변."""
    async with get_conn(request) as conn:
        asset = await repo.get_asset_for_user(conn, user_id, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "자산을 찾을 수 없습니다."})
    return RedirectResponse(_r2(request).public_url(asset["r2_key"]), status_code=302)


@router.get("/jobs/{job_id}", response_model=JobView)
async def get_job(request: Request, job_id: str, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        row = await repo.get_job(conn, user_id, job_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "작업을 찾을 수 없습니다."})
    return row


@router.get("/jobs/{job_id}/events")
async def job_events(
    request: Request,
    job_id: str,
    user_id: str = Depends(require_user),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    after: int = Query(0),
):
    """SSE — job_events를 replay·스트림. Last-Event-ID/?after 이후부터 (§5)."""
    async with get_conn(request) as conn:  # 소유권 확인
        if await repo.get_job(conn, user_id, job_id) is None:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "작업을 찾을 수 없습니다."})
    start = int(last_event_id) if (last_event_id or "").isdigit() else after
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
