"""카드 교체 · 웹훅 — 신뢰 경계 테스트.

웹훅은 서명이 없다(토스 일반 웹훅 계열). 그래서 ① 경로 시크릿이 틀리면 404,
② 맞아도 본문으로 상태를 확정하지 않고 '카드 재등록 필요' 표시만 남긴다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

WEBHOOK_SECRET = "whs-abc"


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "select pgp_sym_decrypt" in q:
            self._row = {"billing_key": "bk-old"} if self.s["sub"] else None
        elif "update subscriptions set billing_key_enc" in q:
            self.s["card"] = {"brand": params[2], "last4": params[3]}
            self._row = {"id": "sub-1"}
        elif "update subscriptions set billing_key_invalid = true" in q:
            self.s["invalidated"].append(params)
            self._row = {"id": "sub-1"}
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
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
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "commits": 0, "issued": [], "deleted": [], "invalidated": [],
             "card": None, "sub": {"id": "sub-1", "status": "active"}}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_issue(settings, *, auth_key, customer_key):
        state["issued"].append(customer_key)
        return {"billingKey": "bk-new", "cardBrand": "신한", "cardLast4": "9876"}

    async def fake_delete(settings, *, billing_key):
        state["deleted"].append(billing_key)

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", fake_issue)
    monkeypatch.setattr(subs.toss_billing, "delete_billing_key", fake_delete)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   toss_webhook_path_secret=WEBHOOK_SECRET,
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_card_swap_replaces_key_and_deletes_old_one(sub, make_token):
    client, state = sub
    res = client.put("/v1/subscriptions/card", headers=_auth(make_token),
                     json={"authKey": "ak-2", "customerKey": "user-1"})
    assert res.status_code == 200, res.text
    assert res.json()["card"] == {"brand": "신한", "last4": "9876"}
    assert state["deleted"] == ["bk-old"]


def test_card_swap_clears_invalid_flag(sub, make_token):
    client, state = sub
    client.put("/v1/subscriptions/card", headers=_auth(make_token),
               json={"authKey": "ak-2", "customerKey": "user-1"})
    assert any("billing_key_invalid = false" in q for q in state["sql"])


def test_card_swap_rejects_other_users_customer_key(sub, make_token):
    client, state = sub
    res = client.put("/v1/subscriptions/card", headers=_auth(make_token),
                     json={"authKey": "ak-2", "customerKey": "user-9"})
    assert res.status_code == 403
    assert state["issued"] == []


def test_card_swap_without_subscription_is_404(sub, make_token):
    client, state = sub
    state["sub"] = None
    res = client.put("/v1/subscriptions/card", headers=_auth(make_token),
                     json={"authKey": "ak-2", "customerKey": "user-1"})
    assert res.status_code == 404
    assert state["issued"] == []


def test_webhook_with_wrong_secret_is_404(sub):
    client, state = sub
    res = client.post("/v1/webhooks/toss/wrong",
                      json={"eventType": "BILLING_DELETED", "data": {"billingKey": "bk-old"}})
    assert res.status_code == 404
    assert state["invalidated"] == []


def test_webhook_marks_card_needs_update_but_does_not_cancel(sub):
    """서명이 없으므로 웹훅은 힌트다. 구독을 끊지 않는다 — 끊으면 위조 요청 하나로
    남의 구독을 종료시킬 수 있다."""
    client, state = sub
    res = client.post(f"/v1/webhooks/toss/{WEBHOOK_SECRET}",
                      json={"eventType": "BILLING_DELETED", "data": {"billingKey": "bk-old"}})
    assert res.status_code == 200
    assert len(state["invalidated"]) == 1
    assert not any("status = 'ended'" in q for q in state["sql"])
    assert not any("expire" in q for q in state["sql"])


def test_unknown_event_type_is_accepted_and_ignored(sub):
    client, state = sub
    res = client.post(f"/v1/webhooks/toss/{WEBHOOK_SECRET}",
                      json={"eventType": "PAYMENT_STATUS_CHANGED", "data": {}})
    assert res.status_code == 200        # 재전송 폭주를 부르지 않게 항상 200
    assert state["invalidated"] == []


def test_webhook_without_configured_secret_is_404(keypair):
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   toss_webhook_path_secret=None,
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(app).post("/v1/webhooks/toss/anything",
                               json={"eventType": "BILLING_DELETED", "data": {}})
    assert res.status_code == 404
