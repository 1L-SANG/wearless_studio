"""계좌이체 신청 확인·거절 — 관리자 라우트.

지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §3.2, §10.
기존 관리자 라우터와 같은 prefix(/v1/facemarket/admin) 아래 산다 — 프론트 관리자 클라이언트가
같은 규칙(Bearer + X-Admin-Device)으로 부른다. 가드는 어떤 잠금·변경보다 먼저 부른다.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import admin_guard, bank_transfer_notify, bank_transfer_service as service, repo
from .auth import require_user
from .db import get_conn

router = APIRouter(prefix="/v1/facemarket/admin/bank-transfers", tags=["FaceMarket admin console"])

STATUSES = ("requested", "paid", "rejected", "canceled", "expired", "all")
_MAX_LIMIT = 200


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _credit_error(e: repo.CreditError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


class ConfirmBody(BaseModel):
    paid_at: str = Field(alias="paidAt")
    admin_note: str | None = Field(default=None, alias="adminNote", max_length=500)

    model_config = {"populate_by_name": True}

    @field_validator("paid_at")
    @classmethod
    def validate_paid_at(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("입금일은 ISO 날짜 또는 일시로 입력해 주세요.")
        return value


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("거절 사유를 적어 주세요.")
        return value.strip()


@router.get("", summary="계좌이체 신청 목록")
async def list_requests(
    request: Request,
    status: str = Query("requested"),
    limit: int = Query(100),
    user_id: str = Depends(require_user),
):
    """기본은 확인 대기(`requested`). `status=all` 이면 전부.

    - **Bearer Token**: 필수(관리자)
    """
    if status not in STATUSES:
        raise _err("invalid_status", "볼 수 없는 상태예요.")
    limit = max(1, min(int(limit), _MAX_LIMIT))
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        items = await service.list_requests_admin(conn, status=status, limit=limit)
    return JSONResponse({"items": items})


@router.post("/{request_id}/confirm", summary="입금 확인 → 지급")
async def confirm(
    request: Request, request_id: uuid.UUID, body: ConfirmBody,
    user_id: str = Depends(require_user),
):
    """충전은 충전 버킷, 구독은 1개월 수동 이용권(크레딧 + 등급). 이미 paid 면 저장된 결과를 돌려준다.

    - **Bearer Token**: 필수(관리자)
    - **에지 케이스**: `404 request_not_found` · `409 request_closed` · `409 toss_subscription_active` ·
      `409 plan_change_not_supported` · `400 note_required_for_expired`
    """
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        try:
            result = await service.confirm_request(
                conn, request_id=str(request_id), actor_user_id=user_id,
                paid_at=body.paid_at, admin_note=body.admin_note,
            )
        except repo.CreditError as e:
            raise _credit_error(e)
        email = None
        if not result.get("idempotent"):
            await admin_guard.write_audit(
                conn, actor_user_id=user_id, action="bank_transfer.confirm",
                target_type="bank_transfer_request", target_id=str(request_id),
                after={"kind": result["kind"], "planCode": result["planCode"],
                       "credits": result["credits"], "paidAt": body.paid_at,
                       "paymentId": result["paymentId"], "creditSourceId": result["creditSourceId"],
                       "manualPlanGrantId": result["manualPlanGrantId"]},
                note=body.admin_note,
            )
            # 알림용 이메일은 같은 커넥션에서 미리 읽는다(커밋 뒤 커넥션을 다시 잡지 않는다).
            async with conn.cursor() as cur:
                await cur.execute(
                    "select u.email from bank_transfer_requests r "
                    "join auth.users u on u.id = r.user_id where r.id = %s",
                    (str(request_id),),
                )
                row = await cur.fetchone()
                email = row["email"] if row else None
        await conn.commit()
    if not result.get("idempotent"):
        await bank_transfer_notify.notify_user_confirmed(
            settings, to=email,
            req={"kind": result["kind"], "plan_code": result["planCode"], "credits": result["credits"]},
            ends_at=result.get("endsAt"),
        )
    return JSONResponse(result)


@router.post("/{request_id}/reject", summary="신청 거절")
async def reject(
    request: Request, request_id: uuid.UUID, body: RejectBody,
    user_id: str = Depends(require_user),
):
    """`requested`·`expired` 만 거절할 수 있다.

    - **Bearer Token**: 필수(관리자)
    - **에지 케이스**: `409 request_closed`
    """
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        try:
            row = await service.reject_request(conn, request_id=str(request_id), reason=body.reason)
        except repo.CreditError as e:
            raise _credit_error(e)
        await admin_guard.write_audit(
            conn, actor_user_id=user_id, action="bank_transfer.reject",
            target_type="bank_transfer_request", target_id=str(request_id),
            after={"reason": body.reason},
        )
        await conn.commit()
    return JSONResponse(service.request_to_json(row, admin=True))
