"""청구 워커 — 갱신·유예·재시도 상태기계.

여기서 지키는 것: ① 성공하면 이월 지급 + 주기 이동 ② 확정 거절은 past_due 로
내리되 크레딧은 그대로 둔다(유예 중 사용 가능) ③ 3회까지만 재시도한다
④ 결과 미상은 실패로 세지 않는다(fail_count 를 올리지 않는다).
"""

import asyncio

import pytest

import app.toss_billing as tb
import app.workers.subscription_biller as biller
from app.workers.subscription_biller import GRACE_DAYS, MAX_ATTEMPTS, SubscriptionBiller
from conftest import make_settings

PLAN = {"id": "p2", "code": "seller", "name": "Seller", "credits": 18000, "price": 79900}
STARTER = {"id": "p1", "code": "starter", "name": "Starter", "credits": 6000, "price": 29900}


class _Cur:
    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append({"sql": q, "params": params})
        if "pg_try_advisory_lock" in q:
            self._rows = [{"locked": self.s["lock"]}]
        elif "pg_advisory_unlock" in q:
            self._rows = []
        elif "for update skip locked" in q:
            self._rows = list(self.s["due"])
        elif "from pricing_plans" in q:
            self._rows = [dict(PLAN)] if params[0] == "seller" else (
                [dict(STARTER)] if params[0] == "starter" else [])
        elif "insert into subscription_invoices" in q:
            if self.s["invoice_conflict"]:
                self._rows = []          # on conflict do nothing → 반환 행 없음
            else:
                self.s["invoices"].append(params)
                self._rows = [{"id": "inv-1"}]
        elif "select order_id" in q:
            self._rows = [{"order_id": self.s["existing_order_id"]}]
        elif "update subscription_invoices" in q:
            self.s["invoice_updates"].append(q)
            self._rows = []
        elif "update subscriptions set" in q:
            self.s["sub_updates"].append({"sql": q, "params": params})
            self._rows = []
        elif "update profiles set plan" in q:
            self.s["plan_updates"].append(params)
            self._rows = []
        else:
            self._rows = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1

    async def rollback(self):
        self.s["rollbacks"] += 1


def _app(state, monkeypatch, *, charge_error=None):
    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"orderId": order_id, "amount": amount})
        if charge_error is not None:
            raise charge_error
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-r"}

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None, period_end_params=()):
        state["grants"].append({"user_id": user_id, "plan_code": plan_code})
        return {"creditSourceId": "s", "credits": 18000, "available": 18000}

    monkeypatch.setattr(biller.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(biller.repo, "grant_subscription", fake_grant)

    class _App:
        pass

    app = _App()
    app.state = _App()
    app.state.settings = make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                       subscription_billing_enabled=True)
    return app


@pytest.fixture()
def state():
    return {"sql": [], "due": [{"id": "sub-1", "user_id": "u1", "plan_code": "seller",
                                "status": "active", "fail_count": 0, "billing_key": "bk-1",
                                "scheduled_plan_code": None}],
            "invoices": [], "invoice_updates": [], "sub_updates": [], "plan_updates": [],
            "charged": [], "grants": [], "commits": 0, "rollbacks": 0, "lock": True,
            "invoice_conflict": False, "existing_order_id": "wl-sub-existing"}


def _tick(state, monkeypatch, **kw):
    return asyncio.run(SubscriptionBiller(_app(state, monkeypatch, **kw)).tick(_Conn(state)))


def test_successful_renewal_grants_and_advances_period(state, monkeypatch):
    out = _tick(state, monkeypatch)
    assert out["charged"] == 1
    assert state["grants"][0]["plan_code"] == "seller"
    advance = [u for u in state["sub_updates"]
               if "current_period_start = current_period_end" in u["sql"]]
    assert len(advance) == 1
    assert "fail_count = 0" in advance[0]["sql"]
    assert state["plan_updates"][0][0] == "seller"


def test_renewal_charges_plan_price_from_catalog_not_client(state, monkeypatch):
    _tick(state, monkeypatch)
    assert state["charged"][0]["amount"] == PLAN["price"]


def test_scheduled_downgrade_applies_on_renewal(state, monkeypatch):
    state["due"][0]["scheduled_plan_code"] = "starter"
    _tick(state, monkeypatch)
    assert state["charged"][0]["amount"] == STARTER["price"]
    assert any("scheduled_plan_code = null" in u["sql"] for u in state["sub_updates"])
    assert state["grants"][0]["plan_code"] == "starter"


def test_card_rejection_moves_to_past_due_without_touching_credits(state, monkeypatch):
    out = _tick(state, monkeypatch,
                charge_error=tb.TossBillingError("REJECT_CARD_COMPANY", "잔액부족",
                                                 retryable=False))
    assert out["failed"] == 1
    past_due = [u for u in state["sub_updates"] if "status = 'past_due'" in u["sql"]]
    assert len(past_due) == 1
    assert "grace_until" in past_due[0]["sql"]
    assert state["grants"] == []
    # 유예 중 크레딧은 그대로 쓴다 — 소멸 경로를 부르지 않는다.
    assert not any("expire" in entry["sql"] for entry in state["sql"])


def test_failed_renewal_retries_next_day(state, monkeypatch):
    _tick(state, monkeypatch,
          charge_error=tb.TossBillingError("REJECT_CARD_COMPANY", "잔액부족", retryable=False))
    past_due = [u for u in state["sub_updates"] if "status = 'past_due'" in u["sql"]][0]
    assert "next_billing_at = now() + interval '1 day'" in past_due["sql"]


def test_retry_stops_after_max_attempts(state, monkeypatch):
    state["due"][0].update(status="past_due", fail_count=MAX_ATTEMPTS - 1)
    _tick(state, monkeypatch,
          charge_error=tb.TossBillingError("REJECT_CARD_COMPANY", "잔액부족", retryable=False))
    # 마지막 시도가 실패하면 다음 청구를 걸지 않는다(만료 워커가 정리한다).
    assert any("next_billing_at = null" in u["sql"] for u in state["sub_updates"])


def test_unknown_result_does_not_count_as_failure(state, monkeypatch):
    """5xx·전송실패는 승인 여부 미상이다. fail_count 를 올리면 카드가 멀쩡한 사람이
    통신 장애 3번으로 해지된다."""
    out = _tick(state, monkeypatch,
                charge_error=tb.TossBillingError("payment_gateway_unreachable", "지연",
                                                 retryable=True))
    assert out["deferred"] == 1
    assert not any("status = 'past_due'" in u["sql"] for u in state["sub_updates"])
    assert state["rollbacks"] >= 1


def test_retry_reuses_the_same_order_id_as_idempotency_key(state, monkeypatch):
    """다른 멱등키로 재시도하면 토스가 별개 결제로 본다 — 이중 청구 위험."""
    state["invoice_conflict"] = True
    _tick(state, monkeypatch)
    assert state["charged"][0]["orderId"] == "wl-sub-existing"


def test_tick_is_noop_without_advisory_lock(state, monkeypatch):
    """여러 태스크가 동시에 돌아도 청구는 한 번만."""
    state["lock"] = False
    assert _tick(state, monkeypatch) == {"skipped": "locked"}
    assert state["charged"] == []


def test_tick_releases_the_lock_even_when_charging_raises(state, monkeypatch):
    state["due"] = [{"id": "sub-1", "user_id": "u1", "plan_code": "nonexistent-plan",
                     "status": "active", "fail_count": 0, "billing_key": "bk-1",
                     "scheduled_plan_code": None}]
    _tick(state, monkeypatch)
    assert any("pg_advisory_unlock" in entry["sql"] for entry in state["sql"])


def test_grace_is_three_days_and_three_attempts():
    assert GRACE_DAYS == 3
    assert MAX_ATTEMPTS == 3
