"""등급 변경 — 비례배분 산식과 적용 시점.

업그레이드는 즉시(차액 결제 + 비례 크레딧), 다운그레이드는 다음 주기(계획서 §0.1·§0.2).
산식을 순수 함수로 분리해 경계값(버림·1일 남음·같은 요금제)을 DB 없이 고정한다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

PLANS = {
    "starter": {"id": "p1", "code": "starter", "name": "Starter", "credits": 6000, "price": 29900},
    "seller": {"id": "p2", "code": "seller", "name": "Seller", "credits": 18000, "price": 79900},
    "pro": {"id": "p3", "code": "pro", "name": "Pro", "credits": 38000, "price": 159000},
}


def test_proration_is_floored_on_both_money_and_credits():
    out = subs._proration(old_price=29900, new_price=79900,
                          old_credits=6000, new_credits=18000,
                          remaining_days=15, period_days=30)
    assert out["amount"] == 25000        # floor(50000 * 15/30)
    assert out["credits"] == 6000        # floor(12000 * 15/30)


def test_proration_on_last_day_is_tiny_but_not_negative():
    out = subs._proration(old_price=29900, new_price=79900,
                          old_credits=6000, new_credits=18000,
                          remaining_days=1, period_days=30)
    assert out["amount"] == 1666         # floor(50000/30)
    assert out["credits"] == 400
    assert out["amount"] > 0


def test_proration_for_cheaper_plan_is_not_an_upgrade():
    out = subs._proration(old_price=79900, new_price=29900,
                          old_credits=18000, new_credits=6000,
                          remaining_days=15, period_days=30)
    assert out["amount"] <= 0            # 라우트가 이걸 보고 다운그레이드로 라우팅한다
    assert out["credits"] == 0           # 크레딧을 회수하지는 않는다


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "remaining_days" in q:
            self._row = {"remaining_days": 15, "period_days": 30}
        elif "from pricing_plans" in q:
            self._row = PLANS.get(params[0])
        elif "wl_billing_decrypt" in q:
            self._row = {"billing_key": "bk-1"}
        elif "update subscriptions set plan_code" in q:
            self.s["sub"] = {**self.s["sub"], "plan_code": params[0]}
            self._row = self.s["sub"]
        elif "update subscriptions set scheduled_plan_code" in q:
            self.s["sub"] = {**self.s["sub"], "scheduled_plan_code": params[0]}
            self._row = self.s["sub"]
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
        elif "insert into subscription_invoices" in q:
            self.s["invoices"].append(params)
            self._row = {"id": "inv-2"}
        elif "update profiles set plan" in q:
            self.s["plan_updates"].append(params)
            self._row = None
        else:
            self._row = None

    async def fetchone(self):
        return self._row

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


@pytest.fixture()
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "commits": 0, "rollbacks": 0, "invoices": [], "charged": [],
             "grants": [], "balance": 1000, "plan_updates": [],
             "sub": {"id": "sub-1", "plan_code": "starter", "status": "active",
                     "current_period_end": "2026-10-09T00:00:00+00:00",
                     "scheduled_plan_code": None}}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"amount": amount, "orderId": order_id})
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-2"}

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None, period_end_params=()):
        state["grants"].append({"credits": credits, "period_end_sql": period_end_sql,
                                "period_end_params": period_end_params})
        state["balance"] += credits
        return {"creditSourceId": "src-2", "credits": credits, "available": state["balance"]}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(subs.repo, "grant_subscription", fake_grant)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_upgrade_charges_difference_and_grants_now(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "seller"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == "immediate"
    assert state["charged"][0]["amount"] == 25000
    assert state["grants"][0]["credits"] == 6000
    assert state["sub"]["plan_code"] == "seller"
    assert state["plan_updates"][0][0] == "seller"


def test_upgrade_bucket_expires_with_current_period_not_a_new_month(sub, make_token):
    """비례 지급분은 현재 주기 끝에 맞춘다 — 한 달을 새로 주면 주기가 어긋난다."""
    client, state = sub
    client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                json={"planCode": "seller"})
    assert "current_period_end" in state["grants"][0]["period_end_sql"]


def test_downgrade_is_scheduled_not_charged(sub, make_token):
    client, state = sub
    state["sub"] = {**state["sub"], "plan_code": "pro"}
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "starter"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == "next_period"
    assert state["charged"] == []
    assert state["grants"] == []
    assert state["sub"]["scheduled_plan_code"] == "starter"
    # 다운그레이드는 이번 주기의 등급을 건드리지 않는다(이미 비싼 값으로 결제됐다)
    assert state["plan_updates"] == []


def test_same_plan_is_400(sub, make_token):
    client, _ = sub
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "starter"})
    assert res.status_code == 400


def test_unknown_plan_is_404(sub, make_token):
    client, _ = sub
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "nope"})
    assert res.status_code == 404


def test_change_plan_while_past_due_is_409(sub, make_token):
    """미납 상태에서 등급을 올리면 못 받은 돈 위에 크레딧을 더 준다."""
    client, state = sub
    state["sub"] = {**state["sub"], "status": "past_due"}
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "seller"})
    assert res.status_code == 409
    assert state["charged"] == []


def test_change_plan_without_subscription_is_404(sub, make_token):
    client, state = sub
    state["sub"] = None
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "seller"})
    assert res.status_code == 404
