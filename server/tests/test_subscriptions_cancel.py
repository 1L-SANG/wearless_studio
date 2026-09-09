"""해지·철회 — '언제까지 쓰고 무엇이 사라지는가' 계약.

해지는 즉시 차단이 아니다(결제한 주기 끝까지). 그리고 소멸 예정 수량·날짜를
응답에 반드시 담는다 — 이월분까지 한 번에 사라지므로 화면이 숫자를 보여줘야
환불 분쟁이 안 난다(계획서 §0.1).
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

ACTIVE = {"id": "sub-1", "plan_code": "seller", "status": "active",
          "current_period_end": "2026-10-09T00:00:00+00:00",
          "next_billing_at": "2026-10-09T00:00:00+00:00", "scheduled_plan_code": None,
          "card_brand": "현대", "card_last4": "1234", "grace_until": None,
          "billing_key_invalid": False}


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "update subscriptions set status = 'canceled'" in q:
            if self.s["sub"] and self.s["sub"]["status"] in ("active", "past_due"):
                self.s["sub"] = {**self.s["sub"], "status": "canceled", "next_billing_at": None}
                self._row = self.s["sub"]
            else:
                self._row = None
        elif "update subscriptions set status = 'active'" in q:
            if self.s["sub"] and self.s["sub"]["status"] == "canceled":
                self.s["sub"] = {**self.s["sub"], "status": "active",
                                 "next_billing_at": self.s["sub"]["current_period_end"]}
                self._row = self.s["sub"]
            else:
                self._row = None
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
    state = {"sql": [], "sub": dict(ACTIVE), "commits": 0}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_summary(conn, user_id):
        return {"credits": 24000, "expiresAt": "2026-10-09T00:00:00+00:00"}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.repo, "subscription_bucket_summary", fake_summary)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_cancel_keeps_access_until_period_end(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/cancel", headers=_auth(make_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "canceled"
    assert body["accessUntil"] == ACTIVE["current_period_end"]
    assert state["sub"]["next_billing_at"] is None       # 다음 청구는 걸리지 않는다


def test_cancel_reports_what_will_be_lost(sub, make_token):
    """이월분 포함 소멸 예정 수량·날짜를 숫자로 돌려준다."""
    client, _ = sub
    body = client.post("/v1/subscriptions/cancel", headers=_auth(make_token)).json()
    assert body["expiring"]["credits"] == 24000
    assert body["expiring"]["expiresAt"] == ACTIVE["current_period_end"]


def test_cancel_twice_is_409(sub, make_token):
    client, state = sub
    state["sub"] = {**ACTIVE, "status": "canceled", "next_billing_at": None}
    res = client.post("/v1/subscriptions/cancel", headers=_auth(make_token))
    assert res.status_code == 409


def test_resume_restores_billing_schedule(sub, make_token):
    client, state = sub
    state["sub"] = {**ACTIVE, "status": "canceled", "next_billing_at": None}
    res = client.post("/v1/subscriptions/resume", headers=_auth(make_token))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "active"
    assert state["sub"]["next_billing_at"] == ACTIVE["current_period_end"]


def test_resume_on_active_subscription_is_409(sub, make_token):
    client, _ = sub
    assert client.post("/v1/subscriptions/resume",
                       headers=_auth(make_token)).status_code == 409


def test_me_without_subscription_is_none_not_404(sub, make_token):
    """구독이 없는 것은 오류가 아니다. 화면이 분기 없이 렌더할 수 있어야 한다."""
    client, state = sub
    state["sub"] = None
    res = client.get("/v1/subscriptions/me", headers=_auth(make_token))
    assert res.status_code == 200
    assert res.json() == {"status": "none"}


def test_me_reports_status_and_card_without_billing_key(sub, make_token):
    client, _ = sub
    res = client.get("/v1/subscriptions/me", headers=_auth(make_token))
    body = res.json()
    assert body["status"] == "active"
    assert body["card"] == {"brand": "현대", "last4": "1234"}
    assert body["expiring"]["credits"] == 24000
    # 빌링키는 조회 응답에 절대 없다(불변식 ⑤)
    assert "billingKey" not in res.text
    assert "billing_key" not in res.text


def test_me_never_selects_the_encrypted_billing_key_column(sub, make_token):
    """SELECT 절에 billing_key_enc 가 없어야 한다 — 실수로 직렬화될 여지를 없앤다."""
    client, state = sub
    client.get("/v1/subscriptions/me", headers=_auth(make_token))
    selects = [q for q in state["sql"] if q.startswith("select")]
    assert selects and all("billing_key_enc" not in q for q in selects)
