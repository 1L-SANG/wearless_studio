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


#: 조회·전이 응답이 공유하는 컬럼 목록. **billing_key_enc 는 절대 넣지 않는다**(불변식 ⑤) —
#: 여기 한 곳에 모아 두면 새 라우트가 실수로 빌링키를 끌어올 여지가 없다.
_ME_COLUMNS = (
    "id::text as id, plan_code, status, current_period_end, next_billing_at, "
    "scheduled_plan_code, card_brand, card_last4, grace_until, billing_key_invalid"
)


def _expiring(summary: dict) -> dict:
    return {"credits": summary["credits"],
            "expiresAt": str(summary["expiresAt"]) if summary["expiresAt"] else None}


@router.get("/me", summary="내 구독 상태")
async def get_my_subscription(request: Request, user_id: str = Depends(require_user)):
    """구독이 없으면 `{"status": "none"}`. 404 가 아니다 — 화면이 분기 없이 렌더한다.

    빌링키는 어떤 경우에도 응답에 넣지 않는다(카드사·끝 4자리만 표시용).
    """
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {_ME_COLUMNS} from subscriptions where user_id = %s", (user_id,)
            )
            sub = await cur.fetchone()
            if sub is None or sub["status"] == "ended":
                return JSONResponse({"status": "none"})
        expiring = await repo.subscription_bucket_summary(conn, user_id)
    return JSONResponse({
        "status": sub["status"],
        "planCode": sub["plan_code"],
        "currentPeriodEnd": str(sub["current_period_end"]),
        "nextBillingAt": str(sub["next_billing_at"]) if sub["next_billing_at"] else None,
        "scheduledPlanCode": sub["scheduled_plan_code"],
        "graceUntil": str(sub["grace_until"]) if sub["grace_until"] else None,
        "cardNeedsUpdate": bool(sub["billing_key_invalid"]),
        "card": {"brand": sub["card_brand"], "last4": sub["card_last4"]},
        "expiring": _expiring(expiring),
    })


@router.post("/cancel", summary="구독 해지 예약")
async def cancel_subscription(request: Request, user_id: str = Depends(require_user)):
    """즉시 차단이 아니다 — `current_period_end` 까지 그대로 쓰고 그때 크레딧이 소멸한다.

    응답의 `expiring` 은 **이월분을 포함한** 소멸 예정 수량이다. 화면은 이 숫자를
    확인 모달에 반드시 노출한다(계획서 §0.1 — 큰 금액이 한 번에 사라지는 사건이다).
    """
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 조건부 UPDATE 로 상태 전이와 판정을 한 문장에 담는다 — 읽고 나서 쓰면
            # 두 탭이 동시에 해지를 눌렀을 때 늦은 쪽이 이미 해지된 구독을 또 해지한다.
            await cur.execute(
                "update subscriptions set status = 'canceled', next_billing_at = null, "
                "canceled_at = now() where user_id = %s and status in ('active', 'past_due') "
                f"returning {_ME_COLUMNS}",
                (user_id,),
            )
            sub = await cur.fetchone()
            if sub is None:
                raise _err("not_cancelable", "해지할 수 있는 구독이 없어요.", 409)
        expiring = await repo.subscription_bucket_summary(conn, user_id)
        await conn.commit()
    return JSONResponse({
        "status": "canceled",
        "accessUntil": str(sub["current_period_end"]),
        "expiring": _expiring(expiring),
    })


@router.post("/resume", summary="해지 철회")
async def resume_subscription(request: Request, user_id: str = Depends(require_user)):
    """주기 종료 전이면 되돌릴 수 있다. 다음 청구를 `current_period_end` 로 되살린다."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "update subscriptions set status = 'active', canceled_at = null, "
                "next_billing_at = current_period_end "
                "where user_id = %s and status = 'canceled' and current_period_end > now() "
                f"returning {_ME_COLUMNS}",
                (user_id,),
            )
            sub = await cur.fetchone()
            if sub is None:
                raise _err("not_resumable", "되돌릴 수 있는 구독이 없어요.", 409)
        await conn.commit()
    return JSONResponse({"status": "active", "nextBillingAt": str(sub["next_billing_at"])})
