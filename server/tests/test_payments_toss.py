"""토스 크레딧 추가구매(WS3) — 돈 불변식 회귀 테스트.

결제 코드에서 지켜야 할 것은 '성공 경로'보다 **거절 경로**다:
금액 위변조로 싸게 사지 못하고, 재시도해도 두 번 적립되지 않고, 남의 주문을 승인할 수 없고,
키가 없으면 조용히 목 성공을 주지 않는다. DB·네트워크 없이 검증한다."""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.payments as payments
from app.main import create_app
from conftest import make_settings

SECRET = "test_sk_stub"
PLAN = {"code": "topup_basic", "name": "크레딧 200", "credits": 200, "price": 19900}


class _Cur:
    """SQL 문자열로 분기하는 최소 커서 — 주문 저장소는 테스트 dict 하나로 흉내낸다."""

    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        if "from pricing_plans" in q:
            self._row = dict(PLAN) if params[0] == PLAN["code"] and self.s["plan_ok"] else None
        elif "insert into toss_payment_orders" in q:
            order_id, user_id, plan_code, amount, credits = params
            self.s["orders"][order_id] = {
                "order_id": order_id, "user_id": user_id, "plan_code": plan_code,
                "amount": amount, "credits": credits, "status": "pending", "payment_key": None,
            }
            self._row = None
        elif "from toss_payment_orders" in q:
            self._row = self.s["orders"].get(params[0])
        elif "update toss_payment_orders set status = 'paid'" in q:
            self.s["orders"][params[1]].update(status="paid", payment_key=params[0])
            self._row = None
        elif "update toss_payment_orders set status = 'failed'" in q:
            order = self.s["orders"].get(params[-1])
            if order and order["status"] == "pending":
                order.update(status="failed", fail_code=params[0])
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


@pytest.fixture()
def pay(monkeypatch, keypair):
    """결제 라우터 + 스텁 DB/토스/적립. state 로 호출 흔적을 관찰한다."""
    private_key, public_key = keypair
    state = {"orders": {}, "plan_ok": True, "commits": 0,
             "toss_calls": [], "toss_response": (200, {}), "grants": [], "balance": 1000}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_confirm(request, *, secret, body, amount):
        state["toss_calls"].append({"secret": secret, "orderId": body.order_id, "amount": amount,
                                    "paymentKey": body.payment_key})
        status, payload = state["toss_response"]
        if status != 200:
            raise payments._err(payload.get("code", "x"), payload.get("message", "x"), 402)
        return {"status": "DONE", "totalAmount": amount, "method": "카드", **payload}

    async def fake_purchase_topup(conn, *, user_id, plan_code, idempotency_key=None,
                                  metadata=None, provider="test", provider_ref=None):
        for g in state["grants"]:      # 원장 멱등 — 같은 키면 재적립 없이 기존 결과
            if g["idempotency_key"] == idempotency_key:
                return {**g["result"], "idempotent": True}
        state["balance"] += PLAN["credits"]
        result = {"credits": PLAN["credits"], "available": state["balance"]}
        state["grants"].append({"idempotency_key": idempotency_key, "provider": provider,
                                "provider_ref": provider_ref, "result": result})
        return result

    async def fake_get_account(conn, user_id):
        return {"credits": state["balance"]}

    monkeypatch.setattr(payments, "get_conn", fake_conn)
    monkeypatch.setattr(payments, "_confirm_with_toss", fake_confirm)
    monkeypatch.setattr(payments.repo, "purchase_topup", fake_purchase_topup)
    monkeypatch.setattr(payments.repo, "get_account", fake_get_account)

    app = create_app(make_settings(toss_secret_key=SECRET))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _checkout(client, make_token, sub="user-1"):
    res = client.post("/v1/payments/toss/checkout", headers=_auth(make_token, sub),
                      json={"planCode": PLAN["code"]})
    assert res.status_code == 200, res.text
    return res.json()


# ── checkout ────────────────────────────────────────────────────────────────
def test_checkout_snapshots_server_side_price(pay, make_token):
    client, state = pay
    body = _checkout(client, make_token)
    assert body["amount"] == PLAN["price"] and body["credits"] == PLAN["credits"]
    assert body["orderName"] == PLAN["name"] and body["customerKey"] == "user-1"
    order = state["orders"][body["orderId"]]
    # 금액의 정본은 서버가 저장한 이 값 — 승인 때 이것과만 대조한다
    assert (order["amount"], order["status"]) == (PLAN["price"], "pending")
    assert 6 <= len(body["orderId"]) <= 64          # 토스 orderId 계약


def test_checkout_unknown_plan_is_404(pay, make_token):
    client, state = pay
    state["plan_ok"] = False
    res = client.post("/v1/payments/toss/checkout", headers=_auth(make_token),
                      json={"planCode": "nope"})
    assert res.status_code == 404 and res.json()["error"]["code"] == "unknown_plan"


def test_checkout_without_secret_key_is_503_not_mock_success(monkeypatch, keypair):
    # 키가 없는데 목 성공을 주면 결제 없이 크레딧이 늘어난다 → 반드시 거절
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key=None))
    app.state.jwt_key_resolver = lambda token: public_key
    client = TestClient(app)
    import jwt as _jwt
    import time as _time
    from conftest import AUDIENCE
    token = _jwt.encode({"sub": "user-1", "aud": AUDIENCE, "exp": int(_time.time()) + 600},
                        keypair[0], algorithm="ES256")
    res = client.post("/v1/payments/toss/checkout", headers={"Authorization": f"Bearer {token}"},
                      json={"planCode": PLAN["code"]})
    assert res.status_code == 503 and res.json()["error"]["code"] == "payment_not_configured"


# ── confirm: 거절 경로 ───────────────────────────────────────────────────────
def test_confirm_rejects_amount_tampering_before_calling_toss(pay, make_token):
    client, state = pay
    order = _checkout(client, make_token)
    res = client.post("/v1/payments/toss/confirm", headers=_auth(make_token),
                      json={"paymentKey": "pk_1", "orderId": order["orderId"], "amount": 100})
    assert res.status_code == 400 and res.json()["error"]["code"] == "amount_mismatch"
    assert state["toss_calls"] == []          # 토스 호출 전에 차단
    assert state["grants"] == []              # 크레딧 미적립
    assert state["orders"][order["orderId"]]["status"] == "pending"


def test_confirm_other_users_order_is_404(pay, make_token):
    client, state = pay
    order = _checkout(client, make_token, sub="user-1")
    res = client.post("/v1/payments/toss/confirm", headers=_auth(make_token, sub="user-2"),
                      json={"paymentKey": "pk_1", "orderId": order["orderId"],
                            "amount": order["amount"]})
    assert res.status_code == 404 and res.json()["error"]["code"] == "order_not_found"
    assert state["grants"] == []


def test_confirm_toss_failure_marks_failed_and_grants_nothing(pay, make_token):
    client, state = pay
    order = _checkout(client, make_token)
    state["toss_response"] = (402, {"code": "REJECT_CARD_COMPANY", "message": "카드사 거절"})
    res = client.post("/v1/payments/toss/confirm", headers=_auth(make_token),
                      json={"paymentKey": "pk_1", "orderId": order["orderId"],
                            "amount": order["amount"]})
    assert res.status_code == 402
    assert state["grants"] == []
    assert state["orders"][order["orderId"]]["status"] == "failed"
    assert SECRET not in res.text              # 시크릿 유출 금지


def test_confirm_rejects_when_toss_amount_differs(pay, make_token):
    # 토스가 승인한 금액이 주문과 다르면 적립하지 않는다(응답 재확인 게이트)
    client, state = pay
    order = _checkout(client, make_token)
    state["toss_response"] = (200, {"totalAmount": 10})
    res = client.post("/v1/payments/toss/confirm", headers=_auth(make_token),
                      json={"paymentKey": "pk_1", "orderId": order["orderId"],
                            "amount": order["amount"]})
    assert res.status_code == 402 and res.json()["error"]["code"] == "payment_not_approved"
    assert state["grants"] == []


# ── confirm: 성공·멱등 ──────────────────────────────────────────────────────
def test_confirm_grants_credits_with_toss_provenance(pay, make_token):
    client, state = pay
    order = _checkout(client, make_token)
    res = client.post("/v1/payments/toss/confirm", headers=_auth(make_token),
                      json={"paymentKey": "pk_1", "orderId": order["orderId"],
                            "amount": order["amount"]})
    assert res.status_code == 200
    body = res.json()
    assert body["credits"] == PLAN["credits"] and body["orderId"] == order["orderId"]
    assert state["orders"][order["orderId"]]["status"] == "paid"
    grant = state["grants"][0]
    # 결제 출처가 원장에 남아야 환불·대사(reconciliation)가 가능하다
    assert grant["provider"] == "toss" and grant["provider_ref"] == "pk_1"
    assert grant["idempotency_key"] == order["orderId"]
    assert state["toss_calls"][0]["amount"] == PLAN["price"]


def test_confirm_twice_grants_credits_only_once(pay, make_token):
    client, state = pay
    order = _checkout(client, make_token)
    payload = {"paymentKey": "pk_1", "orderId": order["orderId"], "amount": order["amount"]}
    first = client.post("/v1/payments/toss/confirm", headers=_auth(make_token), json=payload)
    second = client.post("/v1/payments/toss/confirm", headers=_auth(make_token), json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert len(state["grants"]) == 1                 # 적립 1회
    assert second.json()["idempotent"] is True
    assert len(state["toss_calls"]) == 1             # 이미 paid → 토스 재호출도 안 함


def test_purchase_topup_defaults_to_test_provider(pay):
    # 기존 호출자(수동 충전 라우트)는 provider 인자를 안 넘긴다 → 기본값이 바뀌면 회귀
    import inspect

    import app.repo as repo_module
    sig = inspect.signature(repo_module.purchase_topup)
    assert sig.parameters["provider"].default == "test"
    assert sig.parameters["provider_ref"].default is None
