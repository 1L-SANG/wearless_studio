"""구독 시작 — 돈 불변식 회귀.

거절 경로가 본체다: 금액은 서버가 정하고, customerKey 는 반드시 본인이어야 하며,
첫 결제가 실패하면 빌링키만 저장된 유령 구독이 남지 않는다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
import app.toss_billing as tb
from app.main import create_app
from conftest import make_settings

PLAN = {"id": "plan-seller", "code": "seller", "name": "Seller",
        "credits": 1800, "price": 79900}


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from pricing_plans" in q:
            self._row = dict(PLAN) if params[0] == PLAN["code"] else None
        elif "insert into subscriptions" in q:
            self.s["insert_params"] = params
            self.s["sub"] = {"id": "sub-1", "status": "active"}
            self._row = {"id": "sub-1",
                         "current_period_start": "2026-09-09T00:00:00+00:00",
                         "current_period_end": "2026-10-09T00:00:00+00:00"}
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
        elif "insert into subscription_invoices" in q:
            self.s["invoices"].append(params)
            self._row = {"id": "inv-1"}
        elif "update subscription_invoices" in q:
            self.s["invoice_updates"].append(q)
            self._row = None
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
    state = {"sql": [], "sub": None, "invoices": [], "invoice_updates": [], "plan_updates": [],
             "commits": 0, "rollbacks": 0, "issued": [], "charged": [], "deleted": [],
             "grants": [], "charge_error": None, "balance": 0, "insert_params": None}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_issue(settings, *, auth_key, customer_key):
        state["issued"].append({"authKey": auth_key, "customerKey": customer_key})
        return {"billingKey": "bk-1", "method": "CARD", "label": "현대", "last4": "1234"}

    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"orderId": order_id, "amount": amount,
                                 "billingKey": billing_key})
        if state["charge_error"] is not None:
            raise state["charge_error"]
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-1", "method": "카드"}

    async def fake_delete(settings, *, billing_key):
        state["deleted"].append(billing_key)

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None):
        granted = PLAN["credits"] if credits is None else credits
        state["balance"] += granted
        state["grants"].append({"plan_code": plan_code, "credits": granted})
        return {"creditSourceId": "src-1", "credits": granted, "available": state["balance"]}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", fake_issue)
    monkeypatch.setattr(subs.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(subs.toss_billing, "delete_billing_key", fake_delete)
    monkeypatch.setattr(subs.repo, "grant_subscription", fake_grant)

    app = create_app(make_settings(
        toss_secret_key="sk", toss_billing_kek="kek", subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token, sub_id="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub_id)}"}


def test_start_charges_server_side_price_and_grants(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 200, res.text
    assert state["charged"][0]["amount"] == PLAN["price"]      # 서버 정본 금액
    assert state["grants"][0]["credits"] == PLAN["credits"]
    assert res.json()["available"] == PLAN["credits"]
    assert state["plan_updates"][0][0] == "seller"             # profiles.plan 승급


def test_start_never_returns_the_billing_key(sub, make_token):
    client, _ = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert "bk-1" not in res.text


def test_start_rejects_customer_key_of_another_user(sub, make_token):
    """customerKey 는 우리가 발급한 값(=user_id)이다. 남의 것을 들고 오면 빌링키가
    엉뚱한 사람에게 묶인다 — 토스를 부르기 전에 막는다."""
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-2", "planCode": "seller"})
    assert res.status_code == 403
    assert state["issued"] == []


def test_start_with_unknown_plan_is_404(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "nope"})
    assert res.status_code == 404
    assert state["issued"] == []


def test_first_charge_failure_leaves_no_ghost_subscription(sub, make_token):
    """첫 결제가 확정 거절되면 구독을 만들지 않는다. 빌링키만 남은 구독이 있으면
    다음 달 스케줄러가 돈을 못 받은 사람에게 크레딧을 준다."""
    client, state = sub
    state["charge_error"] = tb.TossBillingError("REJECT_CARD_COMPANY", "한도초과",
                                                retryable=False)
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 402
    assert state["rollbacks"] >= 1
    assert state["grants"] == []
    assert state["deleted"] == ["bk-1"]        # 쓸모없어진 빌링키는 토스에서도 지운다


def test_unknown_charge_result_is_503_and_also_rolls_back(sub, make_token):
    """결과 미상이라도 구독을 안 만들었으므로 남길 근거가 없다."""
    client, state = sub
    state["charge_error"] = tb.TossBillingError("payment_gateway_unreachable", "지연",
                                                retryable=True)
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 503
    assert state["grants"] == []


def test_duplicate_subscription_is_409(sub, make_token):
    client, state = sub
    state["sub"] = {"id": "sub-existing", "status": "active"}
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 409
    assert state["issued"] == []


def test_routes_absent_when_flag_off(keypair, make_token):
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=False))
    app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(app).post("/v1/subscriptions/start", headers=_auth(make_token),
                               json={"authKey": "a", "customerKey": "user-1",
                                     "planCode": "seller"})
    assert res.status_code == 404


def test_missing_kek_is_503_not_plaintext_storage(keypair, make_token):
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek=None,
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(app).post("/v1/subscriptions/start", headers=_auth(make_token),
                               json={"authKey": "a", "customerKey": "user-1",
                                     "planCode": "seller"})
    assert res.status_code == 503
