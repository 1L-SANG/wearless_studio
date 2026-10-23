"""Signup grants and bonus buckets exercise real repository and consent routes."""

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from app import config, legal_versions, repo, routes
from conftest import assert_query_binds, auth_headers, make_settings
from test_bank_transfer import Cursor, Store, USER


class CreditStore(Store):
    def __init__(self):
        super().__init__()
        self.accounts[USER] = {"balance": 0, "reserved": 0}
        self.consents = {}
        self.fail_signup_ledger = False
        self.fail_savepoint_release = False
        self.locked = False
        self.refunds = []

    @asynccontextmanager
    async def cursor(self):
        yield CreditCursor(self)

    @asynccontextmanager
    async def transaction(self):
        saved = copy.deepcopy((self.accounts, self.sources, self.ledger))
        try:
            yield
            if self.fail_savepoint_release:
                raise RuntimeError("simulated savepoint release failure")
        except Exception:
            self.accounts, self.sources, self.ledger = saved
            raise


class CreditCursor(Cursor):
    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        q = " ".join(sql.split())
        p = params or ()
        s = self.s
        self.rows = []
        if q.startswith("insert into seller_consents"):
            old = s.consents.get(p[0])
            row = {"terms_version": p[1], "privacy_version": p[2], "age_attested": p[3],
                   "accepted_at": s.now, "inserted": old is None}
            s.consents[p[0]] = row
            self._set([dict(row)])
        elif q.startswith("select balance, reserved from credit_accounts"):
            assert "for update" in q
            s.locked = True
            await super().execute(sql, params)
        elif "from credit_ledger" in q and "join credit_sources" not in q:
            assert s.locked, "The account lock must precede the signup idempotency lookup"
            self._set([next((entry for entry in s.ledger if entry.get("key") == p[0]), None)])
        elif q.startswith("insert into credit_sources") and "'free'" in q:
            sid = s.next_id("src")
            s.sources[sid] = {"id": sid, "user_id": p[0], "type": "free", "status": "active",
                              "initial": p[1], "remaining": p[2], "period_end": None,
                              "plan_id": None, "payment_id": None}
            self._set([{"id": sid}])
        elif q.startswith("insert into credit_ledger") and "'grant_signup'" in q:
            if s.fail_signup_ledger:
                raise RuntimeError("simulated ledger failure")
            s.ledger.append({"user": p[0], "source": p[1], "action": "grant_signup",
                             "delta": p[2], "balance_after": p[3], "available_after": p[4],
                             "key": p[5], "metadata": p[6].obj})
        elif q.startswith("insert into credit_ledger") and "'grant_topup'" in q:
            await super().execute(sql, params)
            s.ledger[-1].update(action="grant_topup", credits=p[2], metadata=p[6].obj)
        elif q.startswith("insert into credit_ledger") and "'expire_topup'" in q:
            s.ledger.append({"user": p[0], "source": p[1], "action": "expire_topup",
                             "delta": p[2], "balance_after": p[3],
                             "available_after": p[4], "metadata": p[5].obj})
        elif (q.startswith("select id::text as id, remaining_credits from credit_sources")
              and "source_type = 'topup'" in q and "period_end <= now()" in q):
            due = [b for b in s.sources.values() if b["user_id"] == p[0]
                   and b["type"] == "topup" and b["status"] == "active"
                   and b["remaining"] > 0 and b.get("period_end") is not None
                   and b["period_end"] <= s.now]
            self._set([{"id": b["id"], "remaining_credits": b["remaining"]} for b in due])
        elif q.startswith("select id::text as id, remaining_credits from credit_sources") and "order by" in q:
            priority = {"subscription": 0, "free": 1, "topup": 2}
            if "case source_type" in q:
                key = lambda b: (priority[b["type"]], b.get("created_at", 0))
            else:
                key = lambda b: (b["type"], b.get("created_at", 0))
            buckets = sorted((b for b in s.sources.values() if b["user_id"] == p[0]
                              and b["status"] == "active" and b["remaining"] > 0), key=key)
            self._set([{"id": b["id"], "remaining_credits": b["remaining"]} for b in buckets])
        elif q.startswith("update credit_sources set remaining_credits = remaining_credits -"):
            s.sources[p[1]]["remaining"] -= p[0]
        elif q.startswith("insert into credit_ledger") and "project_id" in q:
            s.ledger.append({"source": p[3], "delta": p[5]})
        elif q.startswith("update credit_accounts set balance") and "reserved =" in q:
            s.accounts[p[2]].update(balance=p[0], reserved=p[1])
        elif q.startswith("select id::text as id, source_type"):
            b = s.sources.get(p[0])
            if b and b["user_id"] == p[1]:
                self._set([{**b, "source_type": b["type"], "initial_credits": b["initial"],
                            "remaining_credits": b["remaining"], "within_window": True}])
        elif q.startswith("update credit_sources set status = 'pending_refund'"):
            s.sources[p[0]]["status"] = "pending_refund"
        elif q.startswith("update credit_sources set status = 'expired'"):
            s.sources[p[0]].update(status="expired", remaining=0)
        elif q.startswith("insert into refund_requests"):
            rid = s.next_id("refund")
            s.refunds.append({"id": rid, "source": p[1]})
            self._set([{"id": rid}])
        else:
            await super().execute(sql, params)


def consent_body():
    return {"termsVersion": legal_versions.SELLER_TERMS_VERSION,
            "privacyVersion": legal_versions.SELLER_PRIVACY_VERSION, "ageAttested": True}


@pytest.fixture
def credits_store(monkeypatch):
    store = CreditStore()

    @asynccontextmanager
    async def connection(_request):
        yield store

    monkeypatch.setattr(routes, "get_conn", connection)
    return store


def test_first_seller_consent_grants_signup_150(client, make_token, credits_store):
    client.app.state.settings = make_settings(signup_credits=150)
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.status_code == 200, response.text
    assert response.json()["signupCredits"] == 150
    assert credits_store.accounts[USER]["balance"] == 150
    assert len(credits_store.sources) == len(credits_store.ledger) == 1
    bucket = next(iter(credits_store.sources.values()))
    assert (bucket["type"], bucket["initial"], bucket["remaining"]) == ("free", 150, 150)
    assert (bucket["period_end"], bucket["plan_id"], bucket["payment_id"]) == (None, None, None)
    assert credits_store.ledger[0] == {
        "user": USER, "source": bucket["id"], "action": "grant_signup", "delta": 150,
        "balance_after": 150, "available_after": 150, "key": f"signup:{USER}",
        "metadata": {"reason": "signup"},
    }


def test_revised_consent_does_not_grant_again(client, make_token, credits_store):
    client.app.state.settings = make_settings(signup_credits=150)
    credits_store.consents[USER] = {"terms_version": "old", "privacy_version": "old"}
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.json()["signupCredits"] == 0
    assert credits_store.consents[USER]["terms_version"] == legal_versions.SELLER_TERMS_VERSION
    assert credits_store.sources == {} and credits_store.ledger == []


def test_signup_grant_idempotency_retains_one_bucket(credits_store):
    async def scenario():
        first = await repo.grant_signup_credits(credits_store, user_id=USER, credits=150)
        second = await repo.grant_signup_credits(credits_store, user_id=USER, credits=150)
        assert (first, second) == (150, 0)
    asyncio.run(scenario())
    assert credits_store.accounts[USER]["balance"] == 150
    assert len(credits_store.sources) == len(credits_store.ledger) == 1


def test_default_settings_first_consent_records_without_grant(client, make_token, credits_store):
    assert client.app.state.settings.signup_credits == 0
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.status_code == 200, response.text
    assert response.json()["signupCredits"] == 0
    assert USER in credits_store.consents
    assert credits_store.accounts[USER]["balance"] == 0
    assert credits_store.sources == {} and credits_store.ledger == []


def test_signup_zero_switch_records_consent_without_grant(client, make_token, credits_store):
    object.__setattr__(client.app.state.settings, "signup_credits", 0)
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.json()["signupCredits"] == 0
    assert USER in credits_store.consents
    assert credits_store.accounts[USER]["balance"] == 0
    assert credits_store.sources == {} and credits_store.ledger == []


def test_signup_uses_configured_amount(client, make_token, credits_store):
    object.__setattr__(client.app.state.settings, "signup_credits", 27)
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.json()["signupCredits"] == 27
    assert credits_store.accounts[USER]["balance"] == 27


def test_signup_failure_rolls_back_only_grant_and_commits_consent(client, make_token, credits_store, caplog):
    client.app.state.settings = make_settings(signup_credits=150)
    credits_store.fail_signup_ledger = True
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.status_code == 200, response.text
    assert response.json()["signupCredits"] == 0
    assert USER in credits_store.consents and credits_store.commits == 1
    assert credits_store.accounts[USER]["balance"] == 0
    assert credits_store.sources == {} and credits_store.ledger == []
    assert "simulated ledger failure" in caplog.text


def test_signup_env_setting(monkeypatch):
    monkeypatch.delenv("SIGNUP_CREDITS", raising=False)
    assert config.load_settings().signup_credits == 0
    monkeypatch.setenv("SIGNUP_CREDITS", "0")
    assert config.load_settings().signup_credits == 0
    monkeypatch.setenv("SIGNUP_CREDITS", "37")
    assert config.load_settings().signup_credits == 37


def test_signup_savepoint_failure_reports_zero(client, make_token, credits_store):
    client.app.state.settings = make_settings(signup_credits=150)
    credits_store.fail_savepoint_release = True
    response = client.post("/v1/me/consents", headers=auth_headers(make_token), json=consent_body())
    assert response.status_code == 200, response.text
    assert response.json()["signupCredits"] == 0
    assert USER in credits_store.consents and credits_store.commits == 1
    assert credits_store.accounts[USER]["balance"] == 0


def test_consumption_subscription_then_free_then_topup(credits_store):
    credits_store.accounts[USER]["balance"] = 30
    for ident, kind in [("topup", "topup"), ("free", "free"), ("subscription", "subscription")]:
        credits_store.sources[ident] = {"id": ident, "type": kind, "user_id": USER,
                                       "status": "active", "remaining": 10, "initial": 10}
    available = asyncio.run(repo._consume_buckets(
        credits_store, user_id=USER, project_id=None, job_id="j1", reserved=0, charge=23,
        action_key="generate", metadata={},
    ))
    assert available == 7
    assert [e["source"] for e in credits_store.ledger] == ["subscription", "free", "topup"]
    assert [e["delta"] for e in credits_store.ledger] == [-10, -10, -3]


def test_free_bucket_is_not_refundable(credits_store):
    asyncio.run(repo.grant_signup_credits(credits_store, user_id=USER, credits=150))
    sid = next(iter(credits_store.sources))
    with pytest.raises(repo.CreditError, match="무료 크레딧은 환불 대상이 아니에요."):
        asyncio.run(repo.request_refund(credits_store, user_id=USER, credit_source_id=sid))
    assert credits_store.accounts[USER]["balance"] == 150
    assert credits_store.refunds == []


def test_topup_bonus_is_one_bucket_with_snapshot_metadata_and_idempotency(credits_store):
    snapshot = {"credits": 500, "price": 25000, "bonusCredits": 50,
                "bonusPercent": 10, "planAtOrder": "pro"}
    async def scenario():
        first = await repo.purchase_topup(
            credits_store, user_id=USER, plan_code="topup_finish", snapshot=snapshot,
            idempotency_key="order-1", metadata={"orderId": "order-1"},
        )
        again = await repo.purchase_topup(
            credits_store, user_id=USER, plan_code="topup_finish",
            snapshot={**snapshot, "bonusCredits": 0}, idempotency_key="order-1",
        )
        assert first["credits"] == again["credits"] == 550
    asyncio.run(scenario())
    bucket = next(iter(credits_store.sources.values()))
    assert (bucket["initial"], bucket["remaining"]) == (550, 550)
    assert bucket["period_end"] == credits_store.now + timedelta(days=365)
    assert len(credits_store.sources) == len(credits_store.ledger) == len(credits_store.payments) == 1
    assert credits_store.payments[0]["amount"] == 25000
    assert credits_store.ledger[0]["metadata"] == {
        "orderId": "order-1", "baseCredits": 500, "bonusCredits": 50,
        "bonusPercent": 10, "planAtOrder": "pro",
    }


def test_unused_bonus_topup_refund_removes_total(credits_store):
    result = asyncio.run(repo.purchase_topup(
        credits_store, user_id=USER, plan_code="topup_finish",
        snapshot={"credits": 500, "price": 25000, "bonusCredits": 50},
    ))
    asyncio.run(repo.request_refund(credits_store, user_id=USER, credit_source_id=result["creditSourceId"]))
    assert credits_store.accounts[USER]["balance"] == 0
    assert credits_store.ledger[-1]["delta"] == -550


def test_subscription_expiration_preserves_free_and_topup(credits_store):
    asyncio.run(repo.grant_signup_credits(credits_store, user_id=USER, credits=150))
    credits_store.sources["sub"] = {"id": "sub", "type": "subscription", "user_id": USER,
                                     "status": "active", "remaining": 10, "initial": 10}
    credits_store.accounts[USER]["balance"] += 10
    result = asyncio.run(repo.expire_subscription_buckets(credits_store, user_id=USER, reason="canceled"))
    assert result["expired"] == 10
    assert credits_store.accounts[USER]["balance"] == 150
    assert next(iter(credits_store.sources.values()))["status"] == "active"


def test_due_topups_expire_but_legacy_and_future_purchases_remain(credits_store):
    credits_store.accounts[USER]["balance"] = 100
    credits_store.sources = {
        "due": {"id": "due", "type": "topup", "user_id": USER, "status": "active",
                "remaining": 30, "initial": 30, "period_end": credits_store.now - timedelta(seconds=1)},
        "future": {"id": "future", "type": "topup", "user_id": USER, "status": "active",
                   "remaining": 40, "initial": 40, "period_end": credits_store.now + timedelta(days=1)},
        "legacy": {"id": "legacy", "type": "topup", "user_id": USER, "status": "active",
                   "remaining": 30, "initial": 30, "period_end": None},
    }
    result = asyncio.run(repo.expire_due_topup_buckets(credits_store, user_id=USER))
    assert result == {"expired": 30, "available": 70, "deferred": False}
    assert credits_store.accounts[USER]["balance"] == 70
    assert credits_store.sources["due"]["status"] == "expired"
    assert credits_store.sources["future"]["status"] == "active"
    assert credits_store.sources["legacy"]["status"] == "active"
    assert credits_store.ledger[-1]["action"] == "expire_topup"


def test_due_topup_expiration_waits_for_running_credit_job(credits_store):
    credits_store.accounts[USER] = {"balance": 30, "reserved": 10}
    credits_store.sources = {
        "due": {"id": "due", "type": "topup", "user_id": USER, "status": "active",
                "remaining": 30, "initial": 30, "period_end": credits_store.now - timedelta(seconds=1)},
    }
    result = asyncio.run(repo.expire_due_topup_buckets(credits_store, user_id=USER))
    assert result == {"expired": 0, "available": 20, "deferred": True}
    assert credits_store.sources["due"]["status"] == "active"
