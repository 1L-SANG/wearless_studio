"""토스 자동결제(빌링) 구독 — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md.

**돈을 다루므로 아래 불변식이 이 모듈의 존재 이유다:**
  ① 금액의 정본은 pricing_plans. 클라이언트가 보낸 금액은 어떤 경로에서도 근거가 아니다.
  ② customerKey 는 우리가 발급한 값(=user_id)이다. 토큰 주체와 다르면 토스를 부르기 전에 막는다.
  ③ 첫 결제가 거절되면 구독 행을 남기지 않는다 — 결제 안 된 구독이 다음 달에 크레딧을 준다.
  ④ 빌링키는 pgcrypto 로만 저장한다. KEK 가 없으면 503(평문 폴백 금지).
  ⑤ 빌링키는 어떤 응답·로그에도 싣지 않는다. customerKey(=user_id)는 사실상 공개값이라
     빌링키만 새면 무단 결제가 가능하다.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import repo, toss_billing
from .auth import require_user
from .db import get_conn

log = logging.getLogger("wearless.subscriptions")

router = APIRouter(prefix="/v1/subscriptions", tags=["Subscriptions"])

_ORDER_ID_BYTES = 18       # "wl-sub-" + token_urlsafe(18) ≈ 31자 (토스 6~64자 안)


class StartBody(BaseModel):
    auth_key: str = Field(alias="authKey", min_length=1, max_length=300)
    customer_key: str = Field(alias="customerKey", min_length=2, max_length=300)
    plan_code: str = Field(alias="planCode", min_length=1, max_length=64)

    model_config = {"populate_by_name": True}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _settings(request: Request):
    return request.app.state.settings


def _require_billing_config(request: Request) -> str:
    """키가 없으면 거절한다. 목 성공이나 평문 저장으로 떨어지면 안 된다(불변식 ④)."""
    s = _settings(request)
    if not toss_billing.billing_secret(s) or not s.toss_billing_kek:
        raise _err("payment_not_configured", "결제가 아직 설정되지 않았어요.", 503)
    return s.toss_billing_kek


def _new_order_id(prefix: str = "sub") -> str:
    """토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. token_urlsafe 가 그 안에 있다."""
    return f"wl-{prefix}-{secrets.token_urlsafe(_ORDER_ID_BYTES)}"[:64]


def _billing_http_status(err: "toss_billing.TossBillingError") -> int:
    """결과 미상은 503(다시 시도해 달라), 확정 거절은 402(결제가 안 됐다)."""
    return 503 if err.retryable else 402


async def _load_plan(cur, plan_code: str) -> dict:
    await cur.execute(
        "select id::text as id, code, name, credits, price from pricing_plans "
        "where code = %s and kind = 'subscription' and is_active",
        (plan_code,),
    )
    plan = await cur.fetchone()
    if plan is None:
        raise _err("unknown_plan", "요금제를 찾을 수 없어요.", 404)
    return plan


@router.post("/start", summary="구독 시작 — 빌링키 발급 + 첫 결제")
async def start_subscription(
    request: Request, body: StartBody, user_id: str = Depends(require_user),
):
    """`requestBillingAuth` 성공 리다이렉트에서 받은 authKey 로 구독을 연다.

    - **Bearer Token**: 필수
    - **에지 케이스**: `403 customer_key_mismatch` · `404 unknown_plan` ·
      `409 subscription_exists` · `402`(카드 거절) · `503`(게이트웨이 미상·키 미설정)
    """
    kek = _require_billing_config(request)
    settings = _settings(request)
    # 불변식 ② — 토스를 부르기 전에 막는다.
    if body.customer_key != user_id:
        raise _err("customer_key_mismatch", "결제 요청 정보가 계정과 달라요.", 403)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, status from subscriptions where user_id = %s", (user_id,)
            )
            existing = await cur.fetchone()
            if existing is not None and existing["status"] in ("active", "past_due", "canceled"):
                raise _err("subscription_exists", "이미 구독 중이에요.", 409)
            plan = await _load_plan(cur, body.plan_code)

        # 빌링키 발급 — 이 응답을 잃으면 영구 분실이므로(조회 API 없음) 곧바로 저장한다.
        try:
            issued = await toss_billing.issue_billing_key(
                settings, auth_key=body.auth_key, customer_key=user_id)
        except toss_billing.TossBillingError as e:
            raise _err(e.code, e.message, _billing_http_status(e))

        order_id = _new_order_id("sub")
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into subscriptions (user_id, plan_code, billing_key_enc, "
                    "card_brand, card_last4, current_period_start, current_period_end, "
                    "next_billing_at) values (%s, %s, pgp_sym_encrypt(%s, %s), %s, %s, "
                    "now(), now() + interval '1 month', now() + interval '1 month') "
                    "returning id::text as id, current_period_start, current_period_end",
                    (user_id, plan["code"], issued["billingKey"], kek,
                     issued.get("cardBrand"), issued.get("cardLast4")),
                )
                sub_row = await cur.fetchone()
                await cur.execute(
                    "insert into subscription_invoices (subscription_id, user_id, order_id, "
                    "kind, plan_code, amount, credits, period_start, period_end) "
                    "values (%s, %s, %s, 'initial', %s, %s, %s, %s, %s)",
                    (sub_row["id"], user_id, order_id, plan["code"], plan["price"],
                     plan["credits"], sub_row["current_period_start"],
                     sub_row["current_period_end"]),
                )

            charged = await toss_billing.charge(
                settings, billing_key=issued["billingKey"], customer_key=user_id,
                order_id=order_id, order_name=f"{plan['name']} 구독", amount=plan["price"])

            async with conn.cursor() as cur:
                await cur.execute(
                    "update subscription_invoices set status = 'paid', payment_key = %s, "
                    "approved_at = now() where order_id = %s",
                    (charged.get("paymentKey"), order_id),
                )
            granted = await repo.grant_subscription(
                conn, user_id=user_id, plan_code=plan["code"],
                metadata={"orderId": order_id, "kind": "initial"},
                period_end_sql="now() + interval '1 month'")
            async with conn.cursor() as cur:
                await cur.execute(
                    "update profiles set plan = %s where user_id = %s", (plan["code"], user_id)
                )
        except toss_billing.TossBillingError as e:
            # 불변식 ③ — 유령 구독을 남기지 않는다. 구독 자체를 만들지 않았으니 빌링키를
            # 붙들고 있을 근거도 없다 → 롤백하고 토스에서도 키를 지운다.
            await conn.rollback()
            await toss_billing.delete_billing_key(settings, billing_key=issued["billingKey"])
            raise _err(e.code, e.message, _billing_http_status(e))
        except repo.CreditError as e:
            await conn.rollback()
            raise _err(e.code, e.message, e.status)
        # 청구서·구독·크레딧·등급을 한 트랜잭션으로 커밋 — 결제만 되고 크레딧이 없는 상태 금지.
        await conn.commit()

    return JSONResponse({
        "subscriptionId": sub_row["id"],
        "planCode": plan["code"],
        "credits": granted["credits"],
        "available": granted["available"],
        "currentPeriodEnd": str(sub_row["current_period_end"]),
        "card": {"brand": issued.get("cardBrand"), "last4": issued.get("cardLast4")},
    })
