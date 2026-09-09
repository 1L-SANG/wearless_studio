"""구독 크레딧 이월 정책 — repo 계층 회귀.

정책(계획서 §0.1): 구독이 살아있는 동안 남은 구독 크레딧은 소멸하지 않는다.
소멸은 해지·유예만료라는 '사건'에서만 일어난다. 이 구분이 무너지면 사용자는
매달 산 크레딧을 조용히 잃는다.

async 테스트는 레포 관례대로 asyncio.run 으로 돌린다(pytest-asyncio 미사용).
"""

import asyncio

import pytest

import app.repo as repo


class _Cur:
    """SQL 문자열로 분기하는 최소 커서. 실행된 SQL 을 전부 기록해 단언에 쓴다."""

    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from pricing_plans" in q:
            self._rows = [{"id": "plan-1", "credits": 600}] if self.s["plan_ok"] else []
        elif "from credit_accounts" in q:
            self._rows = [{"balance": self.s["balance"], "reserved": 0}]
        elif "from credit_sources" in q:
            self._rows = list(self.s["buckets"])
        elif "insert into credit_sources" in q:
            self.s["inserted"].append(params)
            self._rows = [{"id": "src-new"}]
        elif "update credit_sources set status = 'expired'" in q:
            self.s["expired"].append(params)
            self._rows = []
        elif "insert into credit_ledger" in q:
            # action_key 는 SQL 문자열에 박혀 있다(파라미터가 아니다) — 문장에서 뽑는다.
            action = next(k for k in ("grant_subscription", "expire_subscription") if k in q)
            self.s["ledger"].append({"action_key": action, "delta": params[2]})
            self._rows = []
        elif "update credit_accounts" in q:
            self.s["balance"] = params[0]
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


@pytest.fixture()
def state():
    return {"sql": [], "plan_ok": True, "balance": 500, "inserted": [], "expired": [],
            "ledger": [], "buckets": [{"id": "src-old", "remaining_credits": 500}]}


def test_renewal_keeps_existing_subscription_buckets(state):
    """갱신은 이월이다 — 기존 버킷을 만료시키지 않고 새 버킷만 더한다."""
    result = asyncio.run(
        repo.grant_subscription(_Conn(state), user_id="u1", plan_code="seller"))
    assert state["expired"] == []                                    # 아무것도 소멸하지 않았다
    assert [entry["action_key"] for entry in state["ledger"]] == ["grant_subscription"]
    assert result["available"] == 500 + 600                          # 이월분 + 신규


def test_expire_buckets_zeroes_all_subscription_credits(state):
    """해지·유예만료에서만 소멸한다. 이월분까지 전부."""
    result = asyncio.run(
        repo.expire_subscription_buckets(_Conn(state), user_id="u1", reason="canceled"))
    assert len(state["expired"]) == 1
    assert result["expired"] == 500
    assert result["available"] == 0
    assert [entry["action_key"] for entry in state["ledger"]] == ["expire_subscription"]
    assert state["ledger"][0]["delta"] == -500


def test_grant_accepts_prorated_credits_override(state):
    """업그레이드 비례 지급 — 요금제 정가가 아니라 계산된 양을 지급한다."""
    result = asyncio.run(
        repo.grant_subscription(_Conn(state), user_id="u1", plan_code="pro", credits=137))
    assert result["credits"] == 137
    assert result["available"] == 500 + 137


def test_summary_reports_what_would_expire(state):
    """해지 화면이 보여줄 숫자 — active 구독 버킷 합계와 가장 늦은 만료일."""
    state["buckets"] = [{"credits": 24000, "expires_at": "2026-10-09T00:00:00+00:00"}]
    out = asyncio.run(repo.subscription_bucket_summary(_Conn(state), "u1"))
    assert out["credits"] == 24000
    assert out["expiresAt"] == "2026-10-09T00:00:00+00:00"
