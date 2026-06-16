"""HTTP 라우트 — Phase 1 (backend_integration_plan §4).

읽기(/me/account, /projects?view=library) + projects CRUD.
모든 라우트는 require_user로 JWT sub를 받고, repo가 그 user_id로 소유권을 스코프한다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import repo
from .auth import require_user
from .db import get_conn
from .models import Account, Project, ProjectPatch, ProjectSummary

router = APIRouter(prefix="/v1")


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "프로젝트를 찾을 수 없습니다."},
    )


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
