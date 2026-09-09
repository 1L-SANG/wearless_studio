"""구독 주기 청구·만료 — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md §0.1.

토스는 스케줄링을 제공하지 않는다. 주기가 된 구독을 우리가 찾아 승인 API 를 부른다.

**상태기계**
  active   ─ 성공 ─▶ active (주기 +1달, 크레딧 이월 지급, fail_count=0)
  active   ─ 확정거절 ─▶ past_due (grace_until = now + 3일, next_billing_at = +1일)
  past_due ─ 성공 ─▶ active (밀린 주기부터 다시 시작)
  past_due ─ 확정거절 ─▶ fail_count+1. 3회째면 next_billing_at = null
                          → 만료 워커가 grace_until 도달 시 정리한다
  (결과 미상)─▶ 아무 상태도 바꾸지 않는다. 다음 tick 이 같은 멱등키로 재시도한다.

기존 워커(FaceVcRevocationReconciler·DraftAssetReclaimer)와 같은 형태 — 클래스 +
asyncio 태스크. 다중 ECS 태스크 중복 실행은 pg_try_advisory_lock 으로 막는다.
승인 호출은 전부 httpx 비동기라 이벤트 루프를 막지 않는다(2026-08-26 루프 동결은
동기 이미지 연산이 원인이었다).
"""

import asyncio
import contextlib
import logging
import secrets

from .. import repo, toss_billing

log = logging.getLogger("wearless.subscription_biller")

#: 유예 3일 · 하루 1회 재시도(D+1·D+2·D+3) — 계획서 §0.1
GRACE_DAYS = 3
MAX_ATTEMPTS = 3
#: 청구 tick 간격. 주기가 '하루 단위'라 분 단위 정밀도는 필요 없다.
_IDLE_SECONDS = 300
_STOP_TIMEOUT_SECONDS = 90          # 진행 중인 승인(최대 60초)이 끝날 시간을 준다
#: 전역 단일 러너 락. (classid, objid) — 다른 advisory 락과 겹치지 않는 값.
_LOCK_KEY = (0x5542, 1)
_EXPIRE_LOCK_KEY = (0x5542, 2)
_BATCH = 50


def _new_order_id() -> str:
    """토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자."""
    return f"wl-sub-{secrets.token_urlsafe(18)}"[:64]


class _PeriodicWorker:
    """start/stop + 주기 루프 공통부. tick(conn) 만 구현하면 된다."""

    name = "periodic"
    stop_timeout = 30

    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=self.name)

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self.stop_timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                async with self.app.state.pool.connection() as conn:
                    await self.tick(conn)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s tick failed", self.name)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)

    async def tick(self, conn) -> dict:      # pragma: no cover - 하위 클래스가 구현
        raise NotImplementedError


class SubscriptionBiller(_PeriodicWorker):
    name = "subscription-biller"
    stop_timeout = _STOP_TIMEOUT_SECONDS

    async def tick(self, conn) -> dict:
        settings = self.app.state.settings
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _LOCK_KEY)
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}

        stats = {"charged": 0, "failed": 0, "deferred": 0}
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id, user_id::text as user_id, plan_code, status, "
                    "fail_count, scheduled_plan_code, "
                    "pgp_sym_decrypt(billing_key_enc, %s)::text as billing_key "
                    "from subscriptions "
                    "where status in ('active', 'past_due') and next_billing_at <= now() "
                    "order by next_billing_at limit %s for update skip locked",
                    (settings.toss_billing_kek, _BATCH),
                )
                due = await cur.fetchall()

            for sub in due:
                stats[await self._charge_one(conn, settings, sub)] += 1
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _LOCK_KEY)
        return stats

    async def _charge_one(self, conn, settings, sub: dict) -> str:
        # 예약된 다운그레이드는 이 갱신부터 적용된다.
        plan_code = sub["scheduled_plan_code"] or sub["plan_code"]
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, code, name, credits, price from pricing_plans "
                "where code = %s and kind = 'subscription' and is_active",
                (plan_code,),
            )
            plan = await cur.fetchone()
        if plan is None:
            # 카탈로그에서 사라진 요금제 — 청구할 금액을 모른다. 상태를 건드리지 않고
            # 다음 tick 에 다시 본다(사람이 카탈로그를 고치면 저절로 풀린다).
            log.error("subscription %s references unknown plan %s", sub["id"], plan_code)
            return "deferred"

        order_id = await self._claim_invoice(conn, sub, plan)
        if order_id is None:
            return "deferred"

        try:
            charged = await toss_billing.charge(
                settings, billing_key=sub["billing_key"], customer_key=sub["user_id"],
                order_id=order_id, order_name=f"{plan['name']} 구독", amount=plan["price"])
        except toss_billing.TossBillingError as e:
            if e.retryable:
                # 승인 여부 미상 — 아무 상태도 굳히지 않는다. 다음 tick 이 같은 멱등키로 잇는다.
                await conn.rollback()
                log.warning("subscription %s charge deferred code=%s", sub["id"], e.code)
                return "deferred"
            await self._mark_failed(conn, sub, order_id, e)
            await conn.commit()
            return "failed"

        async with conn.cursor() as cur:
            await cur.execute(
                "update subscription_invoices set status = 'paid', payment_key = %s, "
                "approved_at = now() where order_id = %s",
                (charged.get("paymentKey"), order_id),
            )
            # 한 UPDATE 안의 우변은 모두 옛 값을 본다 — next_billing_at 도 새 주기 끝과 같아진다.
            await cur.execute(
                "update subscriptions set status = 'active', plan_code = %s, "
                "scheduled_plan_code = null, "
                "current_period_start = current_period_end, "
                "current_period_end = current_period_end + interval '1 month', "
                "next_billing_at = current_period_end + interval '1 month', "
                "fail_count = 0, grace_until = null, "
                "last_failure_code = null, last_failure_message = null "
                "where id = %s",
                (plan["code"], sub["id"]),
            )
            await cur.execute(
                "update profiles set plan = %s where user_id = %s",
                (plan["code"], sub["user_id"]),
            )
        try:
            await repo.grant_subscription(
                conn, user_id=sub["user_id"], plan_code=plan["code"],
                metadata={"orderId": order_id, "kind": "renewal"},
                period_end_sql="now() + interval '1 month'")
        except repo.CreditError:
            # 돈은 받았는데 적립이 막혔다 — 굳히지 않고 되돌린다. 같은 멱등키로 재시도하면
            # 토스가 원 승인 결과를 그대로 주므로 이중 청구는 없다.
            log.exception("subscription %s charged but credit grant failed", sub["id"])
            await conn.rollback()
            return "deferred"
        await conn.commit()
        return "charged"

    async def _claim_invoice(self, conn, sub: dict, plan: dict) -> str | None:
        """이 주기의 청구서를 잡는다. 이미 있으면(재시도) **그 order_id 를 그대로 쓴다** —
        다른 멱등키로 재시도하면 토스가 별개 결제로 보고 이중 청구가 난다."""
        order_id = _new_order_id()
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into subscription_invoices (subscription_id, user_id, order_id, kind, "
                "plan_code, amount, credits, period_start, period_end, attempt) "
                "select %s, %s, %s, 'renewal', %s, %s, %s, current_period_end, "
                "current_period_end + interval '1 month', %s "
                "from subscriptions where id = %s "
                "on conflict do nothing returning id::text as id",
                (sub["id"], sub["user_id"], order_id, plan["code"], plan["price"],
                 plan["credits"], sub["fail_count"] + 1, sub["id"]),
            )
            if await cur.fetchone() is not None:
                return order_id
            await cur.execute(
                "select order_id from subscription_invoices i "
                "join subscriptions s on s.id = i.subscription_id "
                "where i.subscription_id = %s and i.kind = 'renewal' "
                "and i.period_start = s.current_period_end",
                (sub["id"],),
            )
            row = await cur.fetchone()
        return row["order_id"] if row else None

    async def _mark_failed(self, conn, sub: dict, order_id: str, err) -> None:
        """확정 거절 — past_due 로 내리되 **크레딧은 건드리지 않는다**(유예 중 사용 가능).

        3회째 실패면 next_billing_at 을 비워 재시도를 멈춘다. 실제 정리(크레딧 소멸·
        plan=free)는 grace_until 도달 시 SubscriptionExpirer 가 한다.

        interval 은 파라미터로 못 만든다 → 상수를 f-string 으로 넣는다(사용자 입력 없음).
        """
        attempt = sub["fail_count"] + 1
        next_billing = ("null" if attempt >= MAX_ATTEMPTS
                        else "now() + interval '1 day'")
        async with conn.cursor() as cur:
            await cur.execute(
                "update subscription_invoices set status = 'failed', fail_code = %s, "
                "fail_message = %s where order_id = %s",
                (err.code[:100], err.message[:500], order_id),
            )
            await cur.execute(
                "update subscriptions set status = 'past_due', fail_count = %s, "
                f"grace_until = coalesce(grace_until, now() + interval '{GRACE_DAYS} days'), "
                f"next_billing_at = {next_billing}, "
                "last_failure_code = %s, last_failure_message = %s "
                "where id = %s",
                (attempt, err.code[:100], err.message[:500], sub["id"]),
            )


class SubscriptionExpirer(_PeriodicWorker):
    """구독 종료 정리 — 크레딧이 실제로 사라지는 유일한 자리(계획서 §0.1).

    대상은 두 갈래뿐이다:
      · canceled 이고 current_period_end 도달 → 결제한 주기를 다 썼다
      · past_due 이고 grace_until 도달 → 3일 유예 안에 결제가 안 됐다
    **active 는 절대 대상이 아니다** — 이월 정책의 핵심이다.
    """

    name = "subscription-expirer"

    async def tick(self, conn) -> dict:
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _EXPIRE_LOCK_KEY)
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}
        ended = 0
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id, user_id::text as user_id, status "
                    "from subscriptions "
                    "where (status = 'canceled' and current_period_end <= now()) "
                    "   or (status = 'past_due' and grace_until is not null "
                    "       and grace_until <= now()) "
                    "limit %s for update skip locked",
                    (_BATCH,),
                )
                rows = await cur.fetchall()

            for row in rows:
                reason = "canceled" if row["status"] == "canceled" else "past_due_expired"
                await repo.expire_subscription_buckets(
                    conn, user_id=row["user_id"], reason=reason)
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update subscriptions set status = 'ended', next_billing_at = null "
                        "where id = %s",
                        (row["id"],),
                    )
                    await cur.execute(
                        "update profiles set plan = %s where user_id = %s",
                        ("free", row["user_id"]),
                    )
                await conn.commit()
                ended += 1
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _EXPIRE_LOCK_KEY)
        return {"ended": ended}
