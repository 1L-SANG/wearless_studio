"""환불 승인은 토스 원결제 취소가 성공한 뒤에만 끝난다."""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.routes as routes
from app.main import create_app
from conftest import make_settings


class _Conn:
    def __init__(self, state):
        self.state = state

    async def commit(self):
        self.state["commits"] += 1


@pytest.fixture()
def refund(monkeypatch, keypair):
    _, public_key = keypair
    state = {"commits": 0, "begin_calls": [], "cancel_calls": [],
             "approve_calls": [], "cancel_failure": None}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def allow_admin(conn, user_id, request):
        return None

    async def audit(*args, **kwargs):
        return None

    async def begin_cancel(conn, *, request_id):
        state["begin_calls"].append(request_id)
        return {"requestId": request_id, "status": "pending", "reason": "구매 취소",
                "provider": "toss", "paymentKey": "pk_original", "amount": 26000}

    async def cancel(request, *, payment_key, reason, idempotency_key):
        state["cancel_calls"].append({"paymentKey": payment_key, "reason": reason,
                                      "idempotencyKey": idempotency_key})
        if state["cancel_failure"]:
            raise state["cancel_failure"]
        return {"status": "CANCELED"}

    async def approve(conn, *, request_id, resolved_by):
        state["approve_calls"].append(request_id)
        return {"refundRequestId": request_id, "status": "approved", "creditsRefunded": 500}

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.admin_guard, "require_admin", allow_admin)
    monkeypatch.setattr(routes.admin_guard, "write_audit", audit)
    monkeypatch.setattr(routes.repo, "begin_refund_cancel", begin_cancel)
    monkeypatch.setattr(routes.payments, "cancel_topup_payment", cancel)
    monkeypatch.setattr(routes.repo, "approve_refund", approve)

    app = create_app(make_settings(toss_secret_key="test_sk"))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _headers(make_token):
    return {"Authorization": f"Bearer {make_token(sub='admin-user')}"}


def test_refund_approval_cancels_original_payment_before_ledger(refund, make_token):
    client, state = refund
    res = client.post("/v1/admin/refunds/refund-1/approve", headers=_headers(make_token))
    assert res.status_code == 200, res.text
    assert state["cancel_calls"] == [{"paymentKey": "pk_original", "reason": "구매 취소",
                                      "idempotencyKey": "refund-1"}]
    assert state["approve_calls"] == ["refund-1"]
    assert state["begin_calls"] == ["refund-1"]
    assert state["commits"] == 2


def test_refund_gateway_failure_keeps_request_pending_and_retryable(refund, make_token):
    client, state = refund
    state["cancel_failure"] = routes.payments.TossCancelError(
        "refund_gateway_unreachable", "환불 처리가 지연되고 있어요.", 503)
    failed = client.post("/v1/admin/refunds/refund-1/approve", headers=_headers(make_token))
    assert failed.status_code == 503
    assert state["approve_calls"] == [] and state["commits"] == 1

    state["cancel_failure"] = None
    retried = client.post("/v1/admin/refunds/refund-1/approve", headers=_headers(make_token))
    assert retried.status_code == 200
    assert [call["idempotencyKey"] for call in state["cancel_calls"]] == ["refund-1", "refund-1"]
    assert state["approve_calls"] == ["refund-1"] and state["commits"] == 3
