"""만료 워커 — 크레딧이 실제로 사라지는 유일한 자리.

지금까지 credit_sources.period_end 는 쓰기만 하고 읽는 코드가 없었다(2026-09-09 감사).
그래서 해지해도 크레딧이 영영 살아 있었다. 이 워커가 그 구멍을 막는다.
"""

import asyncio

import pytest

import app.workers.subscription_biller as biller
from app.workers.subscription_biller import SubscriptionExpirer
from conftest import make_settings


class _Cur:
    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "pg_try_advisory_lock" in q:
            self._rows = [{"locked": self.s["lock"]}]
        elif "for update skip locked" in q:
            self._rows = list(self.s["expired_due"])
        elif "update subscriptions set status = 'ended'" in q:
            self.s["ended"].append(params)
            self._rows = []
        elif "update profiles set plan" in q:
            self.s["plans"].append(params)
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


def _app(state, monkeypatch):
    async def fake_expire(conn, *, user_id, reason):
        state["expired"].append({"user_id": user_id, "reason": reason})
        return {"expired": 24000, "available": 0}

    monkeypatch.setattr(biller.repo, "expire_subscription_buckets", fake_expire)

    class _App:
        pass

    app = _App()
    app.state = _App()
    app.state.settings = make_settings(subscription_billing_enabled=True)
    return app


@pytest.fixture()
def state():
    return {"sql": [], "lock": True, "commits": 0, "ended": [], "plans": [], "expired": [],
            "expired_due": [{"id": "sub-1", "user_id": "u1", "status": "canceled"}]}


def _tick(state, monkeypatch):
    return asyncio.run(SubscriptionExpirer(_app(state, monkeypatch)).tick(_Conn(state)))


def test_canceled_subscription_expires_all_credits_at_period_end(state, monkeypatch):
    out = _tick(state, monkeypatch)
    assert out["ended"] == 1
    assert state["expired"][0] == {"user_id": "u1", "reason": "canceled"}
    assert state["plans"][0][0] == "free"


def test_past_due_past_grace_is_ended_with_its_own_reason(state, monkeypatch):
    state["expired_due"] = [{"id": "sub-2", "user_id": "u2", "status": "past_due"}]
    _tick(state, monkeypatch)
    assert state["expired"][0]["reason"] == "past_due_expired"


def test_active_subscription_is_never_expired(state, monkeypatch):
    """이월 정책의 핵심 — 살아있는 구독은 절대 소멸시키지 않는다."""
    state["expired_due"] = []
    out = _tick(state, monkeypatch)
    assert out["ended"] == 0
    assert state["expired"] == []
    scan = [q for q in state["sql"] if "for update skip locked" in q][0]
    assert "'active'" not in scan


def test_expirer_respects_advisory_lock(state, monkeypatch):
    state["lock"] = False
    assert _tick(state, monkeypatch) == {"skipped": "locked"}
    assert state["expired"] == []


def test_expirer_commits_each_subscription_separately(state, monkeypatch):
    """한 건이 깨져도 앞서 정리한 건은 남는다."""
    state["expired_due"] = [{"id": "s1", "user_id": "u1", "status": "canceled"},
                            {"id": "s2", "user_id": "u2", "status": "past_due"}]
    out = _tick(state, monkeypatch)
    assert out["ended"] == 2
    assert state["commits"] == 2
