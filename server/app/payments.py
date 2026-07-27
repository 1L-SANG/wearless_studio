"""토스페이먼츠 크레딧 추가구매(WS3/T6) — 주문 생성 + 결제 승인.

흐름: checkout(서버가 금액 스냅샷 + orderId) → 프론트가 토스 결제창 호출 → successUrl 리다이렉트
→ confirm(금액 대조 → 토스 승인 API → 크레딧 적립).

**돈을 다루므로 아래 불변식이 이 모듈의 존재 이유다:**
  ① 금액의 정본은 주문 생성 시 서버가 pricing_plans 에서 스냅샷한 값. 클라이언트가 보낸 amount 는
     '대조용'일 뿐이며 불일치하면 **토스를 호출하기도 전에** 거절한다(위변조로 싸게 사기 차단).
  ② 이중 적립은 3중으로 막는다 — 토스 Idempotency-Key(orderId) · 주문 상태 전이(paid 면 재적립 없이
     기존 결과 반환) · purchase_topup 의 원장 멱등키.
  ③ 주문 소유자가 아니면 404(주문 존재 자체를 노출하지 않는다).
  ④ 시크릿 키는 서버 전용 — 로그·응답·에러 메시지에 절대 싣지 않는다.
  ⑤ 키 미설정이면 503 으로 거절한다. '목 성공'으로 조용히 크레딧을 주면 결제 없이 크레딧이 는다.
"""

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import repo
from .auth import require_user
from .db import get_conn

log = logging.getLogger("wearless.payments")

router = APIRouter(prefix="/v1/payments", tags=["Payments"])

_ORDER_ID_BYTES = 24          # token_urlsafe → 32자 안팎(토스 계약 6~64자 내)
_TOSS_OK_STATUS = "DONE"


class CheckoutBody(BaseModel):
    plan_code: str = Field(alias="planCode", min_length=1, max_length=64)

    model_config = {"populate_by_name": True}


class ConfirmBody(BaseModel):
    payment_key: str = Field(alias="paymentKey", min_length=1, max_length=200)
    order_id: str = Field(alias="orderId", min_length=6, max_length=64)
    amount: int = Field(ge=0)

    model_config = {"populate_by_name": True}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class _RetryableConfirm(Exception):
    """승인 결과를 알 수 없는 실패(전송 오류·게이트웨이 5xx). 주문을 실패로 굳히지 않는다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _require_secret(request: Request) -> str:
    secret = request.app.state.settings.toss_secret_key
    if not secret:
        # 키가 없으면 결제 자체가 불가능하다. 여기서 목 성공을 반환하면 결제 없이 크레딧이 늘어난다.
        raise _err("payment_not_configured", "결제가 아직 설정되지 않았어요.", 503)
    return secret


def _new_order_id() -> str:
    """토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. token_urlsafe 는 이 문자셋 안에 있다."""
    return f"wl-{secrets.token_urlsafe(_ORDER_ID_BYTES)}"[:64]


@router.post("/toss/checkout", summary="크레딧 추가구매 주문 생성")
async def create_checkout(
    request: Request, body: CheckoutBody, user_id: str = Depends(require_user),
):
    """결제창에 넘길 주문을 만든다. **금액은 여기서 서버가 정하고 저장한다** — 승인 단계는 이 값과만
    대조하므로 클라이언트가 금액을 조작해도 싸게 살 수 없다.

    - **Bearer Token**: 필수
    - **에지 케이스**: `404 unknown_plan`(topup 상품 아님/비활성) · `503 payment_not_configured`
    """
    _require_secret(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select code, name, credits, price from pricing_plans "
                "where code = %s and kind = 'topup' and is_active",
                (body.plan_code,),
            )
            plan = await cur.fetchone()
            if plan is None:
                raise _err("unknown_plan", "추가구매 상품을 찾을 수 없어요.", 404)
            order_id = _new_order_id()
            await cur.execute(
                "insert into toss_payment_orders (order_id, user_id, plan_code, amount, credits) "
                "values (%s, %s, %s, %s, %s)",
                (order_id, user_id, plan["code"], plan["price"], plan["credits"]),
            )
        await conn.commit()
    return JSONResponse({
        "orderId": order_id,
        "amount": plan["price"],
        "credits": plan["credits"],
        "orderName": plan["name"],
        # 토스 계약: 유추 가능한 값 금지 → 사용자 uuid(랜덤) 사용. 2~50자 조건 충족.
        "customerKey": user_id,
    })


async def _confirm_with_toss(request: Request, *, secret: str, body: ConfirmBody, amount: int) -> dict:
    """토스 결제 승인 API 호출. 실패는 (code, message)로 정규화해 올린다(시크릿·원문 헤더 미노출)."""
    s = request.app.state.settings
    auth = httpx.BasicAuth(secret, "")   # Basic base64("{secretKey}:")
    try:
        async with httpx.AsyncClient(timeout=s.toss_confirm_timeout) as client:
            res = await client.post(
                f"{s.toss_api_base}/v1/payments/confirm",
                json={"paymentKey": body.payment_key, "orderId": body.order_id, "amount": amount},
                # 토스 멱등키 — 같은 주문의 중복 승인 요청을 토스 쪽에서도 한 번으로 접는다.
                headers={"Idempotency-Key": body.order_id},
                auth=auth,
            )
    except httpx.HTTPError as e:
        # 전송 실패(타임아웃·연결 끊김)는 **결과를 모르는 상태**다. 카드가 승인됐는데 응답만
        # 유실됐을 수 있으므로 주문을 실패로 굳히면 안 된다(돈만 받고 크레딧 미지급).
        # retryable=True → 호출자가 주문을 pending 으로 남긴다. 같은 Idempotency-Key 로
        # 재시도하면 토스가 원 승인 결과를 그대로 돌려준다.
        log.warning("toss confirm unreachable order=%s: %r", body.order_id, e)
        raise _RetryableConfirm("payment_gateway_unreachable",
                                "결제 확인이 지연되고 있어요. 잠시 후 다시 시도해 주세요.")
    if res.status_code != 200:
        try:
            payload = res.json()
        except Exception:
            payload = {}
        code = str(payload.get("code") or "payment_confirm_failed")
        message = str(payload.get("message") or "결제 승인에 실패했어요.")
        log.warning("toss confirm rejected order=%s status=%s code=%s",
                    body.order_id, res.status_code, code)
        if res.status_code >= 500:
            # 게이트웨이 장애 — 승인 여부가 확정되지 않았다. 위와 같은 이유로 재시도 가능 상태 유지.
            raise _RetryableConfirm(code, "결제 확인이 지연되고 있어요. 잠시 후 다시 시도해 주세요.")
        raise _err(code, message, 402)   # 4xx = 토스의 확정적 거절(카드사 거절 등)
    return res.json()


@router.post("/toss/confirm", summary="결제 승인 + 크레딧 적립")
async def confirm_payment(
    request: Request, body: ConfirmBody, user_id: str = Depends(require_user),
):
    """successUrl 리다이렉트 뒤 호출된다. 주문 금액과 대조 → 토스 승인 → 크레딧 적립.

    - **Bearer Token**: 필수
    - **에지 케이스**: `404`(주문 없음/타인) · `400 amount_mismatch` · `402`(토스 승인 실패) ·
      `409 order_not_payable`(실패·취소된 주문) · `503 payment_not_configured`
    - 같은 주문으로 다시 호출해도 크레딧은 한 번만 적립된다(멱등).
    """
    secret = _require_secret(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # for update — 동시 confirm(더블클릭·재시도)을 직렬화한다.
            await cur.execute(
                "select order_id, user_id::text as user_id, plan_code, amount, credits, status, "
                "payment_key from toss_payment_orders where order_id = %s for update",
                (body.order_id,),
            )
            order = await cur.fetchone()
            # 타인의 주문은 '없음'과 구분하지 않는다(주문 존재 노출 금지).
            if order is None or order["user_id"] != user_id:
                raise _err("order_not_found", "주문을 찾을 수 없어요.", 404)
            if order["status"] == "paid":
                # 이미 승인·적립된 주문 — 재적립 없이 현재 잔액만 돌려준다.
                acct = await repo.get_account(conn, user_id)
                return JSONResponse({
                    "orderId": order["order_id"], "credits": order["credits"],
                    "available": (acct or {}).get("credits", 0), "idempotent": True,
                })
            if order["status"] != "pending":
                raise _err("order_not_payable", "이미 종료된 주문이에요.", 409)
            # ① 금액 위변조 차단 — 토스를 호출하기 전에 막는다.
            if body.amount != order["amount"]:
                log.warning("toss confirm amount mismatch order=%s", order["order_id"])
                raise _err("amount_mismatch", "결제 금액이 주문과 달라요.")

            try:
                payment = await _confirm_with_toss(
                    request, secret=secret, body=body, amount=order["amount"])
            except _RetryableConfirm as e:
                # 승인 여부 미확정 — 주문은 pending 그대로 두고(재시도 가능) 커밋하지 않는다.
                raise _err(e.code, e.message, 503)
            except HTTPException as e:
                detail = e.detail if isinstance(e.detail, dict) else {}
                await cur.execute(
                    "update toss_payment_orders set status = 'failed', fail_code = %s, "
                    "fail_message = %s where order_id = %s and status = 'pending'",
                    (str(detail.get("code"))[:100], str(detail.get("message"))[:500],
                     order["order_id"]),
                )
                await conn.commit()
                raise

            # ② 토스 응답 재확인 — 상태·금액이 우리 주문과 어긋나면 적립하지 않는다.
            if payment.get("status") != _TOSS_OK_STATUS or payment.get("totalAmount") != order["amount"]:
                await cur.execute(
                    "update toss_payment_orders set status = 'failed', fail_code = 'unexpected_result',"
                    " fail_message = %s where order_id = %s",
                    (f"status={payment.get('status')} total={payment.get('totalAmount')}",
                     order["order_id"]),
                )
                await conn.commit()
                raise _err("payment_not_approved", "결제가 승인되지 않았어요.", 402)

            await cur.execute(
                "update toss_payment_orders set status = 'paid', payment_key = %s, "
                "approved_at = now() where order_id = %s",
                (body.payment_key, order["order_id"]),
            )

        # ③ 적립 — 원장 멱등키를 orderId 로 묶어 재시도해도 한 번만 적립된다.
        try:
            result = await repo.purchase_topup(
                conn, user_id=user_id, plan_code=order["plan_code"],
                idempotency_key=order["order_id"], provider="toss",
                provider_ref=body.payment_key,
                metadata={"orderId": order["order_id"], "method": payment.get("method")},
                # 결제 시점 확정값으로 적립 — 그 사이 카탈로그가 바뀌어도 결제한 만큼만 준다.
                snapshot={"credits": order["credits"], "price": order["amount"]},
            )
        except repo.CreditError as e:
            raise _err(e.code, e.message, e.status)
        # 주문 paid 표시와 적립을 한 트랜잭션으로 커밋 — 결제만 되고 크레딧이 안 붙는 상태를 만들지 않는다.
        await conn.commit()

    return JSONResponse({
        "orderId": order["order_id"],
        "credits": result.get("credits"),
        "available": result.get("available"),
        "idempotent": bool(result.get("idempotent")),
    })
