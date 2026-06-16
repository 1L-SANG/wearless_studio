"""HTTP 라우트 — Phase 1~2 (backend_integration_plan §4).

읽기(/me/account, /projects?view=library) + projects CRUD + 자산 업로드(§3).
모든 라우트는 require_user로 JWT sub를 받고, repo가 그 user_id로 소유권을 스코프한다.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import repo
from .auth import require_user
from .db import get_conn
from .models import (
    Account,
    Asset,
    AssetCompleteRequest,
    Product,
    ProductPatch,
    Project,
    ProjectPatch,
    ProjectSummary,
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
        row = await repo.get_or_create_product(conn, project_id)
        await conn.commit()
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
        row = await repo.save_product(conn, project_id, fields)
        await conn.commit()
    return row


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
