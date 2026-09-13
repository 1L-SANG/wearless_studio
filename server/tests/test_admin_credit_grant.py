"""관리자 계좌이체 지급의 인증, 원장 기록, 재시도 계약."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app import facemarket_admin
from app.main import create_app
from conftest import auth_headers, make_settings


TARGET = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
BODY = {
    "plan_code": "topup_finish", "payer_name": "홍길동", "amount_krw": 12345,
    "paid_at": "2026-09-10", "note": "계좌이체 확인",
}


class CreditConn:
    """외부 DB만 대체한다. 가드와 purchase_topup은 실제 코드를 실행한다."""

    def __init__(self):
        self.role = "admin"
        self.accounts = {TARGET: {"balance": 20, "reserved": 5},
                         OTHER: {"balance": 0, "reserved": 0}}
        self.payments, self.sources, self.ledger = [], [], []
        self.commits = 0

    @asynccontextmanager
    async def cursor(self):
        yield CreditCursor(self)

    async def commit(self):
        self.commits += 1


class CreditCursor:
    def __init__(self, conn):
        self.conn, self.row = conn, None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        c = self.conn
        self.row = None
        if "select role from profiles" in q:
            self.row = {"role": c.role}
        elif "from profiles" in q and "auth.users" in q:
            self.row = {"user_id": params[0]} if params[0] in c.accounts else None
        elif "from credit_accounts" in q:
            self.row = c.accounts.get(params[0])
        elif "from credit_ledger cl" in q:
            entry = next((x for x in c.ledger if x["key"] == params[0]), None)
            if entry:
                self.row = {"src": entry["source"], "initial_credits": entry["credits"]}
        elif "from pricing_plans" in q:
            assert "kind = 'topup'" in q and "is_active" in q
            if params[0] == "topup_finish":
                self.row = {"id": "plan-1", "credits": 100, "price": 9900}
        elif q.startswith("insert into payment_history"):
            c.payments.append({"user": params[0], "amount": params[2],
                               "provider": params[3], "provider_ref": params[4]})
            self.row = {"id": f"payment-{len(c.payments)}"}
        elif q.startswith("insert into credit_sources"):
            c.sources.append(params)
            self.row = {"id": f"source-{len(c.sources)}"}
        elif q.startswith("insert into credit_ledger"):
            c.ledger.append({"user": params[0], "source": params[1], "credits": params[2],
                             "key": params[5], "metadata": params[6].obj})
        elif q.startswith("update credit_accounts"):
            c.accounts[params[1]]["balance"] = params[0]
        else:
            raise AssertionError(f"Unexpected SQL: {q}")

    async def fetchone(self):
        return self.row


@pytest.fixture()
def grant(keypair, monkeypatch):
    conn = CreditConn()

    @asynccontextmanager
    async def get_conn(_request):
        yield conn

    monkeypatch.setattr(facemarket_admin, "get_conn", get_conn)
    app = create_app(make_settings(
        facemarket_enabled=True, app_env="production", fm_vc_required=True,
        opendid_holder_url="http://holder.test", opendid_holder_hmac_secret="test-secret",
    ))
    app.state.jwt_key_resolver = lambda token: keypair[1]
    return TestClient(app), conn


def post(grant, make_token, *, target=TARGET, key="transfer-1", body=None):
    headers = auth_headers(make_token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return grant[0].post(f"/v1/facemarket/admin/users/{target}/credits/grants",
                         headers=headers, json=BODY if body is None else body)


def test_non_admin_cannot_grant(grant, make_token):
    grant[1].role = "user"
    assert post(grant, make_token).status_code == 403
    assert not grant[1].ledger and grant[1].commits == 0


def test_authentication_required(grant):
    res = grant[0].post(f"/v1/facemarket/admin/users/{TARGET}/credits/grants", json=BODY)
    assert res.status_code == 401
    assert not grant[1].ledger


def test_admin_grants_in_production_and_records_transfer(grant, make_token):
    res = post(grant, make_token)
    assert res.status_code == 200, res.text
    assert res.json() == {"creditSourceId": "source-1", "paymentId": "payment-1",
                          "credits": 100, "available": 115}
    c = grant[1]
    assert c.payments == [{"user": TARGET, "amount": 9900, "provider": "bank_transfer",
                           "provider_ref": "홍길동/2026-09-10"}]
    assert c.ledger[0]["metadata"] == {"granted_by": "user-1", "payer_name": "홍길동",
                                       "amount_krw": 12345, "paid_at": "2026-09-10",
                                       "note": "계좌이체 확인"}
    assert c.accounts[TARGET]["balance"] == 120
    assert c.commits == 1


def test_retry_returns_same_grant_without_duplicate_credit(grant, make_token):
    first = post(grant, make_token)
    retry = post(grant, make_token)
    assert first.status_code == retry.status_code == 200
    assert retry.json() == {"creditSourceId": first.json()["creditSourceId"],
                            "credits": 100, "available": 115, "idempotent": True}
    assert len(grant[1].ledger) == len(grant[1].payments) == len(grant[1].sources) == 1
    assert grant[1].accounts[TARGET]["balance"] == 120


def test_same_key_for_different_targets_does_not_collide(grant, make_token):
    assert post(grant, make_token).status_code == 200
    assert post(grant, make_token, target=OTHER).status_code == 200
    assert len(grant[1].ledger) == 2
    assert grant[1].ledger[0]["key"] != grant[1].ledger[1]["key"]


@pytest.mark.parametrize("code", ["topup_basic", "missing", "subscription_basic"])
def test_inactive_unknown_or_non_topup_plan_is_400(grant, make_token, code):
    res = post(grant, make_token, body={**BODY, "plan_code": code})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "unknown_plan"
    assert not grant[1].ledger and grant[1].commits == 0


def test_missing_user_is_404(grant, make_token):
    del grant[1].accounts[TARGET]
    res = post(grant, make_token)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "user_not_found"
    assert not grant[1].ledger and grant[1].commits == 0


def test_optional_key_and_note_and_iso_datetime(grant, make_token):
    body = {**BODY, "paid_at": "2026-09-10T12:30:00+09:00"}
    del body["note"]
    assert post(grant, make_token, key=None, body=body).status_code == 200
    assert grant[1].ledger[0]["key"] is None
    assert grant[1].ledger[0]["metadata"]["note"] is None


@pytest.mark.parametrize("patch", [
    {"payer_name": "   "}, {"amount_krw": -1}, {"amount_krw": 1.5},
    {"paid_at": "yesterday"},
])
def test_invalid_transfer_data_is_rejected(grant, make_token, patch):
    assert post(grant, make_token, body={**BODY, **patch}).status_code == 422
    assert not grant[1].ledger
