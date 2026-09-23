"""계좌이체(무통장입금) 신청 — 사용자 라우트.

지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md. PG(토스) 심사 전 결제 경로다.
사용자는 여기서 신청만 한다. 지급은 관리자가 입금을 확인한 뒤 bank_transfer_admin.py 에서 한다.
계좌 정보는 서버 설정(env)에서만 나온다 — 프론트에 계좌번호를 하드코딩하지 않는다.
"""

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import bank_transfer_notify, bank_transfer_service as service, repo
from .auth import require_user
from .db import get_conn

log = logging.getLogger("wearless.bank_transfer")

router = APIRouter(prefix="/v1/bank-transfer", tags=["Bank transfer"])

_PHONE_RE = re.compile(r"^[0-9-]{9,14}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RequestBody(BaseModel):
    plan_code: str = Field(alias="planCode", min_length=1, max_length=64)
    payer_name: str = Field(alias="payerName", max_length=60)
    phone: str | None = Field(default=None, max_length=20)
    tax_invoice: bool = Field(default=False, alias="taxInvoice")
    business_no: str | None = Field(default=None, alias="businessNo", max_length=20)
    business_name: str | None = Field(default=None, alias="businessName", max_length=60)
    representative_name: str | None = Field(default=None, alias="representativeName", max_length=30)
    invoice_email: str | None = Field(default=None, alias="invoiceEmail", max_length=254)
    note: str | None = Field(default=None, max_length=200)

    model_config = {"populate_by_name": True}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _credit_error(e: repo.CreditError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def bank_info(settings) -> dict | None:
    """계좌 3종이 다 있고 스위치가 켜져 있을 때만 값을 준다. 아니면 None."""
    if not getattr(settings, "bank_transfer_enabled", False):
        return None
    bank = getattr(settings, "bank_transfer_bank", None)
    account = getattr(settings, "bank_transfer_account", None)
    holder = getattr(settings, "bank_transfer_holder", None)
    if not (bank and account and holder):
        return None
    return {"bank": bank, "account": account, "holder": holder,
            "expiresInDays": service.REQUEST_TTL_DAYS}


def _clean(body: RequestBody) -> dict:
    payer_name = " ".join(body.payer_name.split())
    if not 2 <= len(payer_name) <= 30:
        raise _err("payer_name_invalid", "입금자명은 2~30자로 적어 주세요.")
    phone = (body.phone or "").strip() or None
    if phone and not _PHONE_RE.match(phone):
        raise _err("phone_invalid", "연락처는 숫자와 하이픈만 적어 주세요.")
    out = {
        "payer_name": payer_name, "phone": phone, "tax_invoice": bool(body.tax_invoice),
        "business_no": None, "business_name": None, "representative_name": None,
        "invoice_email": None, "note": (body.note or "").strip() or None,
    }
    if body.tax_invoice:
        business_no = re.sub(r"\D", "", body.business_no or "")
        business_name = " ".join((body.business_name or "").split())
        representative = " ".join((body.representative_name or "").split())
        email = (body.invoice_email or "").strip()
        if len(business_no) != 10:
            raise _err("business_no_invalid", "사업자등록번호는 숫자 10자리예요.")
        if not business_name or not representative or not email:
            raise _err("tax_invoice_fields_required",
                       "세금계산서를 받으려면 상호, 대표자 성명, 받을 이메일이 필요해요.")
        if not _EMAIL_RE.match(email):
            raise _err("invoice_email_invalid", "세금계산서 받을 이메일 형식이 맞지 않아요.")
        out.update({"business_no": business_no, "business_name": business_name,
                    "representative_name": representative, "invoice_email": email})
    return out


@router.get("/info", summary="계좌이체 안내(계좌 정보)")
async def get_info(request: Request, user_id: str = Depends(require_user)):
    """켜져 있고 계좌가 설정돼 있으면 계좌 정보를, 아니면 `{"enabled": false}` 를 준다.

    - **Bearer Token**: 필수
    """
    info = bank_info(request.app.state.settings)
    if info is None:
        return JSONResponse({"enabled": False})
    return JSONResponse({"enabled": True, **info})


@router.post("/requests", summary="계좌이체 신청")
async def create_request(request: Request, body: RequestBody, user_id: str = Depends(require_user)):
    """상품·금액·크레딧을 신청 시점에 스냅샷한다. 종류(구독·충전)당 열린 신청은 1건.

    - **Bearer Token**: 필수
    - **에지 케이스**: `503 bank_transfer_unavailable` · `404 unknown_plan` ·
      `409 request_already_open` · `409 toss_subscription_active` · `409 plan_change_not_supported` ·
      `400 tax_invoice_fields_required` 등
    """
    settings = request.app.state.settings
    info = bank_info(settings)
    if info is None:
        raise _err("bank_transfer_unavailable", "계좌이체 신청을 잠시 받지 않아요.", 503)
    fields = _clean(body)
    async with get_conn(request) as conn:
        try:
            row = await service.create_request(conn, user_id=user_id, plan_code=body.plan_code, **fields)
        except repo.CreditError as e:
            raise _credit_error(e)
        email = await service.user_email(conn, user_id)
        await conn.commit()
    # 커밋 뒤 알림 — 실패해도 신청 결과를 뒤집지 않는다.
    await bank_transfer_notify.notify_admin_new_request(settings, row, user_email=email)
    return JSONResponse({"request": service.request_to_json(row), "bank": info})


@router.get("/requests/open", summary="내 열린 계좌이체 신청")
async def list_open(request: Request, user_id: str = Depends(require_user)):
    """- **Bearer Token**: 필수"""
    async with get_conn(request) as conn:
        result = await service.list_open_requests(conn, user_id=user_id)
    return JSONResponse(result)


@router.post("/requests/{request_id}/cancel", summary="계좌이체 신청 취소")
async def cancel(request: Request, request_id: uuid.UUID, user_id: str = Depends(require_user)):
    """본인의 `requested` 신청만 취소된다.

    - **Bearer Token**: 필수
    - **에지 케이스**: `409 not_cancelable`
    """
    async with get_conn(request) as conn:
        try:
            row = await service.cancel_request(conn, user_id=user_id, request_id=str(request_id))
        except repo.CreditError as e:
            raise _credit_error(e)
        await conn.commit()
    return JSONResponse(service.request_to_json(row))


@router.get("/entitlement", summary="내 계좌이체 이용권")
async def entitlement(request: Request, user_id: str = Depends(require_user)):
    """활성 수동 이용권. 없으면 `{"active": false}`.

    - **Bearer Token**: 필수
    """
    async with get_conn(request) as conn:
        result = await service.get_entitlement(conn, user_id=user_id)
    return JSONResponse(result)
