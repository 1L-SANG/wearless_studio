"""계좌이체(무통장입금) 신청·확인·만료 — 지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §8, §10-13.

외부 DB 만 메모리 저장소로 대체한다. 가드·purchase_topup·grant_subscription·expire_subscription_buckets 는
실제 코드가 돈다. 돈 불변식(스냅샷 지급·멱등·상호배제·연장)이 본체다.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import bank_transfer, bank_transfer_admin, bank_transfer_notify, bank_transfer_service as service
from app import subscriptions as subs
from app.main import create_app
from app.workers.bank_transfer_expirer import BankTransferExpirer
from conftest import auth_headers, make_settings

USER = "user-1"          # make_token 의 기본 sub
OTHER = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
MONTH = timedelta(days=31)

PLANS = {
    "seller": {"id": "plan-seller", "code": "seller", "kind": "subscription", "name": "Seller",
               "price": 69900, "credits": 1600, "is_active": True},
    "starter": {"id": "plan-starter", "code": "starter", "kind": "subscription", "name": "Starter",
                "price": 29900, "credits": 600, "is_active": True},
    "topup_finish": {"id": "plan-finish", "code": "topup_finish", "kind": "topup", "name": "마무리 충전",
                     "price": 9900, "credits": 180, "is_active": True},
    "legacy": {"id": "plan-legacy", "code": "legacy", "kind": "topup", "name": "옛 상품",
               "price": 1000, "credits": 10, "is_active": False},
}

REQUEST_COLUMNS = [
    "id", "user_id", "plan_code", "kind", "amount", "credits", "payer_name", "phone", "tax_invoice",
    "business_no", "business_name", "representative_name", "invoice_email", "note", "status",
    "expires_at", "paid_at", "confirmed_at", "admin_note", "payment_id", "credit_source_id",
    "manual_plan_grant_id", "created_at",
]


class Store:
    """SQL 문자열을 표별 동작으로 옮기는 메모리 DB."""

    def __init__(self):
        self.role = "admin"
        self.now = NOW
        self.accounts = {USER: {"balance": 100, "reserved": 0}, OTHER: {"balance": 0, "reserved": 0}}
        self.plans = {k: dict(v) for k, v in PLANS.items()}
        self.requests = {}
        self.grants = {}
        self.subscriptions = {}
        self.payments = []
        self.sources = {}
        self.ledger = []
        self.profiles = {USER: {"plan": "free"}, OTHER: {"plan": "free"}}
        self.emails = {USER: "seller@example.com", OTHER: "other@example.com"}
        self.audits = []
        self.commits = 0
        self.rollbacks = 0
        self.seq = {}
        self.fail_expire_for = set()     # 이 사용자의 버킷 만료는 제약 위반처럼 실패시킨다
        self.after_candidates = None     # 만료 후보 조회 직후 실행할 훅(동시 확인 흉내)

    def due_for_expiry(self, g):
        """만료 워커의 WHERE 절: 활성 + 종료일 지남 + 같은 플랜 연장 신청이 열려 있지 않음."""
        if g["status"] != "active" or g["ends_at"] > self.now:
            return False
        return not any(
            r["user_id"] == g["user_id"] and r.get("kind") == "subscription"
            and r.get("plan_code") == g["plan_code"] and r["status"] == "requested"
            and r["created_at"] < g["ends_at"] and r["expires_at"] > self.now
            for r in self.requests.values()
        )

    def next_id(self, prefix):
        self.seq[prefix] = self.seq.get(prefix, 0) + 1
        return f"{prefix}-{self.seq[prefix]}"

    @asynccontextmanager
    async def cursor(self):
        yield Cursor(self)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class Cursor:
    def __init__(self, s):
        self.s, self.rows, self.rowcount = s, [], -1

    def _set(self, rows):
        self.rows = [r for r in rows if r is not None]

    async def execute(self, sql, params=None):
        s = self.s
        q = " ".join(sql.split())
        self.rows, self.rowcount = [], -1
        p = params or ()
        # ---- 가드·계정 ----
        if "select role from profiles" in q:
            self._set([{"role": s.role}])
        elif q.startswith("select balance, reserved from credit_accounts"):
            self._set([s.accounts.get(p[0])])
        # ---- 요금제 ----
        elif "from pricing_plans" in q and "kind, price, credits" in q:
            plan = s.plans.get(p[0])
            self._set([plan if plan and plan["is_active"] else None])
        elif "from pricing_plans" in q and "credits from pricing_plans" in q:
            plan = s.plans.get(p[0])
            ok = plan and plan["kind"] == "subscription" and (plan["is_active"] or "is_active" not in q)
            self._set([{"id": plan["id"], "credits": plan["credits"]} if ok else None])
        elif "from pricing_plans" in q and "select id::text as id from pricing_plans" in q:
            plan = s.plans.get(p[0])
            kind = "subscription" if "'subscription'" in q else "topup"
            self._set([{"id": plan["id"]} if plan and plan["kind"] == kind else None])
        # ---- 토스 구독 ----
        elif "select status from subscriptions where user_id" in q:
            sub = s.subscriptions.get(p[0])
            self._set([{"status": sub["status"]} if sub else None])
        elif "select id::text as id, status from subscriptions where user_id" in q:
            sub = s.subscriptions.get(p[0])
            self._set([{"id": "sub-1", "status": sub["status"]} if sub else None])
        # ---- 수동 이용권 ----
        elif "from manual_plan_grants where user_id = %s and status = 'active'" in q:
            self._set([dict(g) for g in s.grants.values() if g["user_id"] == p[0] and g["status"] == "active"][:1])
        elif "select ends_at from manual_plan_grants where id" in q:
            g = s.grants.get(p[0])
            self._set([{"ends_at": g["ends_at"]} if g else None])
        elif q.startswith("select now() + interval '1 month'"):
            self._set([{"ends_at": s.now + MONTH}])
        elif q.startswith("select %s::timestamptz + interval '1 month'"):
            self._set([{"ends_at": p[0] + MONTH}])
        elif q.startswith("insert into manual_plan_grants"):
            gid = s.next_id("grant")
            s.grants[gid] = {"id": gid, "user_id": p[0], "plan_code": p[1], "request_id": p[2],
                             "starts_at": s.now, "ends_at": p[3], "status": "active"}
            self._set([{"id": gid}])
        elif q.startswith("update manual_plan_grants set ends_at"):
            s.grants[p[2]].update({"ends_at": p[0], "request_id": p[1]})
        elif q.startswith("update manual_plan_grants set status = 'ended'"):
            g = s.grants.get(p[0])
            if g and g["status"] == "active":
                g.update({"status": "ended", "ended_reason": "expired"})
        elif q.startswith("select g.id::text as id from manual_plan_grants g"):
            # 만료 후보(잠금 없음). 같은 플랜 연장 신청이 열려 있으면 빠진다.
            self._set([{"id": g["id"]} for g in s.grants.values() if s.due_for_expiry(g)])
            if s.after_candidates:
                s.after_candidates()      # 후보 조회와 행 잠금 사이에 끼어드는 확인(연장)을 흉내 낸다
        elif q.startswith("select g.id::text as id, g.user_id::text as user_id, g.plan_code from manual_plan_grants g where g.id = %s"):
            g = s.grants.get(p[0])
            self._set([{"id": g["id"], "user_id": g["user_id"], "plan_code": g["plan_code"]}]
                      if g and s.due_for_expiry(g) else [])
        # ---- 신청 ----
        elif q.startswith("update bank_transfer_requests set status = 'expired' where user_id"):
            for r in s.requests.values():
                if r["user_id"] == p[0] and r["kind"] == p[1] and r["status"] == "requested" and r["expires_at"] <= s.now:
                    r["status"] = "expired"
        elif q.startswith("update bank_transfer_requests set status = 'expired' where status = 'requested'"):
            n = 0
            for r in s.requests.values():
                if r["status"] == "requested" and r["expires_at"] <= s.now:
                    r["status"] = "expired"
                    n += 1
            self.rowcount = n
        elif "select id::text as id from bank_transfer_requests where user_id = %s and kind = %s and status = 'requested'" in q:
            self._set([{"id": r["id"]} for r in s.requests.values()
                       if r["user_id"] == p[0] and r["kind"] == p[1] and r["status"] == "requested"][:1])
        elif q.startswith("insert into bank_transfer_requests"):
            rid = s.next_id("req")
            row = dict(zip(REQUEST_COLUMNS[1:14], p))
            row.update({"id": rid, "status": "requested", "expires_at": s.now + timedelta(days=3),
                        "paid_at": None, "confirmed_at": None, "admin_note": None, "payment_id": None,
                        "credit_source_id": None, "manual_plan_grant_id": None, "created_at": s.now})
            s.requests[rid] = row
            self._set([dict(row)])
        elif "from bank_transfer_requests where user_id = %s and status = 'requested' and expires_at > now()" in q:
            self._set([dict(r) for r in s.requests.values()
                       if r["user_id"] == p[0] and r["status"] == "requested" and r["expires_at"] > s.now])
        elif "status in ('paid', 'rejected', 'expired')" in q and "from bank_transfer_requests" in q:
            rows = [dict(r) for r in s.requests.values()
                    if r["user_id"] == p[0] and r["status"] in ("paid", "rejected", "expired")]
            self._set(rows[-1:])
        elif q.startswith("update bank_transfer_requests set status = 'canceled'"):
            r = s.requests.get(p[0])
            if r and r["user_id"] == p[1] and r["status"] == "requested":
                r["status"] = "canceled"
                self._set([dict(r)])
        elif "from bank_transfer_requests where id = %s for update" in q:
            r = s.requests.get(p[0])
            self._set([dict(r) if r else None])
        elif q.startswith("update bank_transfer_requests set status = 'paid'"):
            r = s.requests[p[6]]
            r.update({"status": "paid", "paid_at": p[0], "confirmed_by": p[1], "confirmed_at": s.now,
                      "admin_note": p[2], "payment_id": p[3], "credit_source_id": p[4],
                      "manual_plan_grant_id": p[5]})
        elif q.startswith("update bank_transfer_requests set status = 'rejected'"):
            r = s.requests.get(p[1])
            if r and r["status"] in ("requested", "expired"):
                r.update({"status": "rejected", "admin_note": p[0]})
                self._set([dict(r)])
        elif "from bank_transfer_requests r left join profiles" in q:
            rows = sorted(s.requests.values(), key=lambda r: r["created_at"], reverse=True)
            if p["status"] != "all":
                rows = [r for r in rows if r["status"] == p["status"]]
            self._set([{**r, "email": s.emails.get(r["user_id"]), "display_name": None,
                        "plan_name": s.plans[r["plan_code"]]["name"]} for r in rows[:p["limit"]]])
        elif "select u.email from bank_transfer_requests r join auth.users" in q:
            r = s.requests.get(p[0])
            self._set([{"email": s.emails.get(r["user_id"])} if r else None])
        elif q.startswith("select email from auth.users where id"):
            self._set([{"email": s.emails.get(p[0])}])
        # ---- 원장(purchase_topup / grant_subscription / expire) ----
        elif "from credit_ledger cl" in q:
            hit = next((l for l in s.ledger if l.get("key") == p[0]), None)
            self._set([{"src": hit["source"], "initial_credits": hit["credits"]} if hit else None])
        elif q.startswith("insert into payment_history"):
            pid = s.next_id("pay")
            if "'subscription', 'bank_transfer'" in q:
                s.payments.append({"id": pid, "user": p[0], "plan_id": p[1], "amount": p[2],
                                   "kind": "subscription", "provider": "bank_transfer", "ref": p[3]})
            else:
                s.payments.append({"id": pid, "user": p[0], "plan_id": p[1], "amount": p[2],
                                   "kind": "topup", "provider": p[3], "ref": p[4]})
            self._set([{"id": pid}])
        elif q.startswith("insert into credit_sources"):
            sid = s.next_id("src")
            if "period_end" in q:
                s.sources[sid] = {"id": sid, "user_id": p[0], "plan_id": p[1], "initial": p[2],
                                  "remaining": p[3], "status": "active", "type": "subscription",
                                  "period_end": p[4], "payment_id": None}
            else:
                s.sources[sid] = {"id": sid, "user_id": p[0], "plan_id": p[1], "initial": p[2],
                                  "remaining": p[3], "status": "active", "type": "topup",
                                  "period_end": None, "payment_id": p[4]}
            self._set([{"id": sid}])
        elif q.startswith("update credit_sources set payment_id"):
            s.sources[p[1]]["payment_id"] = p[0]
        elif q.startswith("update credit_sources set period_end"):
            for src in s.sources.values():
                if src["user_id"] == p[1] and src["type"] == "subscription" and src["status"] == "active" and src["id"] != p[2]:
                    src["period_end"] = p[0]
        elif "select id::text as id, remaining_credits from credit_sources" in q:
            if p[0] in s.fail_expire_for:
                raise RuntimeError("check constraint reserved <= balance")
            self._set([{"id": src["id"], "remaining_credits": src["remaining"]} for src in s.sources.values()
                       if src["user_id"] == p[0] and src["type"] == "subscription" and src["status"] == "active"])
        elif q.startswith("update credit_sources set status = 'expired'"):
            s.sources[p[0]].update({"status": "expired", "remaining": 0})
        elif "coalesce(sum(remaining_credits), 0) as credits" in q:
            mine = [src for src in s.sources.values()
                    if src["user_id"] == p[0] and src["type"] == "subscription" and src["status"] == "active"]
            self._set([{"credits": sum(x["remaining"] for x in mine),
                        "expires_at": max((x["period_end"] for x in mine), default=None)}])
        elif q.startswith("insert into credit_ledger"):
            entry = {"user": p[0], "source": p[1], "delta": p[2], "key": None}
            if "idempotency_key" in q:
                entry["key"] = p[5]
            s.ledger.append(entry)
        elif q.startswith("update credit_accounts set balance"):
            s.accounts[p[1]]["balance"] = p[0]
        elif q.startswith("update profiles set plan = 'free'"):
            s.profiles.setdefault(p[0], {})["plan"] = "free"
        elif q.startswith("update profiles set plan"):
            s.profiles.setdefault(p[1], {})["plan"] = p[0]
        elif q.startswith("insert into admin_audit_log"):
            s.audits.append({"actor": p[0], "action": p[1], "target": p[3], "after": p[5].obj})
        elif "pg_try_advisory_lock" in q:
            self._set([{"locked": True}])
        elif "pg_advisory_unlock" in q:
            pass
        else:
            raise AssertionError(f"Unexpected SQL: {q}")

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


@pytest.fixture()
def store(keypair, monkeypatch):
    s = Store()

    @asynccontextmanager
    async def get_conn(_request):
        yield s

    for module in (bank_transfer, bank_transfer_admin, subs):
        monkeypatch.setattr(module, "get_conn", get_conn)
    s.notifications = []

    async def fake_admin_notify(settings, req, *, user_email):
        s.notifications.append(("admin", req["id"], user_email))

    async def fake_user_notify(settings, *, to, req, ends_at):
        s.notifications.append(("user", to, req["plan_code"], ends_at))

    monkeypatch.setattr(bank_transfer_notify, "notify_admin_new_request", fake_admin_notify)
    monkeypatch.setattr(bank_transfer_notify, "notify_user_confirmed", fake_user_notify)
    return s


def make_client(keypair, **overrides):
    base = dict(
        facemarket_enabled=True, app_env="production", fm_vc_required=True,
        opendid_holder_url="http://holder.test", opendid_holder_hmac_secret="test-secret",
        subscription_billing_enabled=True, toss_billing_secret_key="sk", toss_billing_kek="kek",
        bank_transfer_bank="국민은행", bank_transfer_account="123-456-789", bank_transfer_holder="정일상",
    )
    base.update(overrides)
    settings = make_settings(**base)
    app = create_app(settings)
    app.state.jwt_key_resolver = lambda token: keypair[1]
    return TestClient(app)


@pytest.fixture()
def client(store, keypair):
    return make_client(keypair)


def body(**over):
    base = {"planCode": "seller", "payerName": "홍길동"}
    base.update(over)
    return base


def create(client, make_token, **over):
    return client.post("/v1/bank-transfer/requests", headers=auth_headers(make_token), json=body(**over))


def confirm(client, make_token, rid, **over):
    payload = {"paidAt": "2026-09-22"}
    payload.update(over)
    return client.post(f"/v1/facemarket/admin/bank-transfers/{rid}/confirm",
                       headers=auth_headers(make_token), json=payload)


UUID1 = "11111111-1111-4111-8111-111111111111"


def _uuidify(store):
    """라우트가 UUID 경로 인자를 요구하므로 저장소의 신청 ID 를 UUID 문자열로 바꾼다."""
    for i, (rid, row) in enumerate(list(store.requests.items())):
        new = f"{i + 1:08d}-1111-4111-8111-111111111111"
        del store.requests[rid]
        row["id"] = new
        store.requests[new] = row
        for g in store.grants.values():
            if g.get("request_id") == rid:
                g["request_id"] = new
    return list(store.requests)


# ---------------------------------------------------------------- 안내

def test_info_requires_auth_and_returns_account(client, make_token):
    assert client.get("/v1/bank-transfer/info").status_code == 401
    res = client.get("/v1/bank-transfer/info", headers=auth_headers(make_token))
    assert res.status_code == 200
    assert res.json() == {"enabled": True, "bank": "국민은행", "account": "123-456-789",
                          "holder": "정일상", "expiresInDays": 3}


def test_info_disabled_without_account(store, keypair, make_token):
    c = make_client(keypair, bank_transfer_account=None)
    assert c.get("/v1/bank-transfer/info", headers=auth_headers(make_token)).json() == {"enabled": False}
    res = create(c, make_token)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "bank_transfer_unavailable"
    assert not store.requests


# ---------------------------------------------------------------- 신청

def test_create_snapshots_plan_and_notifies_admin(client, store, make_token):
    res = create(client, make_token, phone="010-1234-5678", note=" 급해요 ")
    assert res.status_code == 200, res.text
    data = res.json()
    req = data["request"]
    assert req["planCode"] == "seller" and req["kind"] == "subscription"
    assert req["amount"] == 69900 and req["credits"] == 1600
    assert req["payerName"] == "홍길동" and req["phone"] == "010-1234-5678" and req["note"] == "급해요"
    assert req["status"] == "requested" and req["expiresAt"].startswith("2026-09-25")
    assert "adminNote" not in req and "userId" not in req
    assert data["bank"]["account"] == "123-456-789"
    assert store.commits == 1
    assert store.notifications == [("admin", req["id"], "seller@example.com")]


def test_create_rejects_second_open_request_of_same_kind_but_allows_other_kind(client, store, make_token):
    assert create(client, make_token).status_code == 200
    dup = create(client, make_token)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "request_already_open"
    assert create(client, make_token, planCode="topup_finish").status_code == 200
    assert len(store.requests) == 2


def test_create_reopens_after_previous_request_expired(client, store, make_token):
    assert create(client, make_token).status_code == 200
    store.now = NOW + timedelta(days=4)
    res = create(client, make_token)
    assert res.status_code == 200
    statuses = sorted(r["status"] for r in store.requests.values())
    assert statuses == ["expired", "requested"]


@pytest.mark.parametrize("over, code", [
    ({"planCode": "legacy"}, "unknown_plan"),
    ({"planCode": "nope"}, "unknown_plan"),
    ({"payerName": " 홍 "}, "payer_name_invalid"),
    ({"phone": "abc"}, "phone_invalid"),
    ({"taxInvoice": True}, "business_no_invalid"),
    ({"taxInvoice": True, "businessNo": "123-45-67890"}, "tax_invoice_fields_required"),
    ({"taxInvoice": True, "businessNo": "1234567890", "businessName": "데일리모먼트",
      "representativeName": "정일상", "invoiceEmail": "nope"}, "invoice_email_invalid"),
])
def test_create_validation(client, store, make_token, over, code):
    res = create(client, make_token, **over)
    assert res.status_code in (400, 404), res.text
    assert res.json()["error"]["code"] == code
    assert not store.requests and store.commits == 0


def test_create_tax_invoice_fields_are_normalized(client, store, make_token):
    res = create(client, make_token, taxInvoice=True, businessNo="123-45-67890",
                 businessName="  데일리모먼트 ", representativeName="정일상",
                 invoiceEmail="tax@example.com")
    assert res.status_code == 200, res.text
    req = res.json()["request"]
    assert req["taxInvoice"] is True and req["businessNo"] == "1234567890"
    assert req["businessName"] == "데일리모먼트" and req["representativeName"] == "정일상"
    assert req["invoiceEmail"] == "tax@example.com"


@pytest.mark.parametrize("status", ["active", "past_due", "canceled"])
def test_create_subscription_blocked_by_toss_subscription(client, store, make_token, status):
    store.subscriptions[USER] = {"status": status}
    res = create(client, make_token)
    assert res.status_code == 409 and res.json()["error"]["code"] == "toss_subscription_active"
    # 충전은 토스 구독과 무관하게 된다
    assert create(client, make_token, planCode="topup_finish").status_code == 200


def test_create_subscription_allows_same_plan_but_not_other_plan_while_grant_active(client, store, make_token):
    store.grants["g1"] = {"id": "g1", "user_id": USER, "plan_code": "seller", "request_id": None,
                          "starts_at": NOW, "ends_at": NOW + MONTH, "status": "active"}
    other = create(client, make_token, planCode="starter")
    assert other.status_code == 409 and other.json()["error"]["code"] == "plan_change_not_supported"
    assert create(client, make_token, planCode="seller").status_code == 200


def test_open_list_and_cancel(client, store, make_token):
    rid = create(client, make_token).json()["request"]["id"]
    res = client.get("/v1/bank-transfer/requests/open", headers=auth_headers(make_token))
    assert [r["id"] for r in res.json()["open"]] == [rid] and res.json()["recent"] is None
    rids = _uuidify(store)
    res = client.post(f"/v1/bank-transfer/requests/{rids[0]}/cancel", headers=auth_headers(make_token))
    assert res.status_code == 200 and res.json()["status"] == "canceled"
    again = client.post(f"/v1/bank-transfer/requests/{rids[0]}/cancel", headers=auth_headers(make_token))
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_cancelable"


def test_cancel_rejects_other_users_request(client, store, make_token):
    create(client, make_token)
    rids = _uuidify(store)
    store.requests[rids[0]]["user_id"] = OTHER
    res = client.post(f"/v1/bank-transfer/requests/{rids[0]}/cancel", headers=auth_headers(make_token))
    assert res.status_code == 409


def test_entitlement_none_then_active(client, store, make_token):
    assert client.get("/v1/bank-transfer/entitlement", headers=auth_headers(make_token)).json() == {"active": False}
    store.grants["g1"] = {"id": "g1", "user_id": USER, "plan_code": "seller", "request_id": None,
                          "starts_at": NOW, "ends_at": NOW + MONTH, "status": "active"}
    store.sources["s1"] = {"id": "s1", "user_id": USER, "plan_id": "plan-seller", "initial": 1600,
                           "remaining": 900, "status": "active", "type": "subscription",
                           "period_end": NOW + MONTH, "payment_id": None}
    res = client.get("/v1/bank-transfer/entitlement", headers=auth_headers(make_token)).json()
    assert res["active"] is True and res["planCode"] == "seller" and res["credits"] == 900
    assert res["autoRenew"] is False and res["endsAt"].startswith("2026-10-23")


# ---------------------------------------------------------------- 관리자 확인

def test_non_admin_cannot_list_or_confirm(client, store, make_token):
    create(client, make_token)
    rids = _uuidify(store)
    store.role = "user"
    assert client.get("/v1/facemarket/admin/bank-transfers", headers=auth_headers(make_token)).status_code == 403
    assert confirm(client, make_token, rids[0]).status_code == 403
    assert store.requests[rids[0]]["status"] == "requested" and not store.ledger


def test_admin_list_defaults_to_requested_and_exposes_admin_fields(client, store, make_token):
    create(client, make_token, taxInvoice=True, businessNo="1234567890", businessName="데일리모먼트",
           representativeName="정일상", invoiceEmail="tax@example.com")
    res = client.get("/v1/facemarket/admin/bank-transfers", headers=auth_headers(make_token))
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "seller@example.com" and items[0]["planName"] == "Seller"
    assert items[0]["businessNo"] == "1234567890" and "adminNote" in items[0]
    assert client.get("/v1/facemarket/admin/bank-transfers?status=paid",
                      headers=auth_headers(make_token)).json()["items"] == []
    assert client.get("/v1/facemarket/admin/bank-transfers?status=weird",
                      headers=auth_headers(make_token)).status_code == 400


def test_confirm_topup_grants_snapshot_and_is_idempotent(client, store, make_token):
    create(client, make_token, planCode="topup_finish")
    rids = _uuidify(store)
    # 신청 뒤 카탈로그가 바뀌어도(가격·크레딧·비활성) 스냅샷대로 지급한다
    store.plans["topup_finish"].update({"price": 1, "credits": 1, "is_active": False})
    res = confirm(client, make_token, rids[0], adminNote="입금 확인")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["kind"] == "topup" and data["credits"] == 180 and data["available"] == 280
    assert data["paymentId"] == "pay-1" and data["creditSourceId"] == "src-1" and data["endsAt"] is None
    assert store.payments == [{"id": "pay-1", "user": USER, "plan_id": "plan-finish", "amount": 9900,
                               "kind": "topup", "provider": "bank_transfer", "ref": rids[0]}]
    assert store.accounts[USER]["balance"] == 280
    req = store.requests[rids[0]]
    assert req["status"] == "paid" and req["paid_at"] == "2026-09-22" and req["admin_note"] == "입금 확인"
    assert req["payment_id"] == "pay-1" and req["credit_source_id"] == "src-1"
    assert store.audits[-1]["action"] == "bank_transfer.confirm" and store.audits[-1]["target"] == rids[0]
    assert ("user", "seller@example.com", "topup_finish", None) in store.notifications
    # 응답 유실 뒤 재확인 — 아무것도 다시 하지 않는다
    again = confirm(client, make_token, rids[0])
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert again.json()["creditSourceId"] == "src-1" and again.json()["available"] == 280
    assert len(store.ledger) == len(store.payments) == 1 and len(store.audits) == 1
    assert store.notifications.count(("user", "seller@example.com", "topup_finish", None)) == 1


def test_confirm_subscription_creates_grant_bucket_payment_and_plan(client, store, make_token):
    create(client, make_token, planCode="seller")
    rids = _uuidify(store)
    store.plans["seller"]["is_active"] = False       # 확인 시점 비활성이어도 스냅샷대로
    res = confirm(client, make_token, rids[0])
    assert res.status_code == 200, res.text
    data = res.json()
    ends = NOW + MONTH
    assert data["kind"] == "subscription" and data["credits"] == 1600 and data["available"] == 1700
    assert data["manualPlanGrantId"] == "grant-1" and data["endsAt"] == ends.isoformat()
    grant = store.grants["grant-1"]
    assert grant["plan_code"] == "seller" and grant["ends_at"] == ends and grant["request_id"] == rids[0]
    src = store.sources["src-1"]
    assert src["type"] == "subscription" and src["initial"] == 1600 and src["period_end"] == ends
    assert src["payment_id"] == "pay-1"
    assert store.payments == [{"id": "pay-1", "user": USER, "plan_id": "plan-seller", "amount": 69900,
                               "kind": "subscription", "provider": "bank_transfer", "ref": rids[0]}]
    assert store.profiles[USER]["plan"] == "seller"
    assert store.requests[rids[0]]["manual_plan_grant_id"] == "grant-1"
    assert ("user", "seller@example.com", "seller", ends.isoformat()) in store.notifications


def test_confirm_same_plan_extends_grant_and_aligns_old_buckets(client, store, make_token):
    create(client, make_token, planCode="seller")
    rids = _uuidify(store)
    assert confirm(client, make_token, rids[0]).status_code == 200
    first_end = store.grants["grant-1"]["ends_at"]
    store.now = NOW + timedelta(days=10)
    create(client, make_token, planCode="seller")
    rids = _uuidify(store)
    res = confirm(client, make_token, rids[1])
    assert res.status_code == 200, res.text
    assert store.grants["grant-1"]["ends_at"] == first_end + MONTH        # 기존 종료일부터 한 달
    assert len(store.grants) == 1 and store.grants["grant-1"]["request_id"] == rids[1]
    assert res.json()["endsAt"] == (first_end + MONTH).isoformat()
    buckets = [s for s in store.sources.values() if s["type"] == "subscription"]
    assert len(buckets) == 2 and all(b["period_end"] == first_end + MONTH for b in buckets)
    assert store.accounts[USER]["balance"] == 100 + 1600 + 1600
    assert len(store.payments) == 2


@pytest.mark.parametrize("status", ["active", "past_due", "canceled"])
def test_confirm_subscription_blocked_by_toss_subscription(client, store, make_token, status):
    create(client, make_token, planCode="seller")
    rids = _uuidify(store)
    store.subscriptions[USER] = {"status": status}
    res = confirm(client, make_token, rids[0])
    assert res.status_code == 409 and res.json()["error"]["code"] == "toss_subscription_active"
    assert store.requests[rids[0]]["status"] == "requested" and not store.grants and not store.ledger


def test_confirm_other_plan_while_grant_active_is_rejected(client, store, make_token):
    create(client, make_token, planCode="starter")
    rids = _uuidify(store)
    store.grants["g1"] = {"id": "g1", "user_id": USER, "plan_code": "seller", "request_id": None,
                          "starts_at": NOW, "ends_at": NOW + MONTH, "status": "active"}
    res = confirm(client, make_token, rids[0])
    assert res.status_code == 409 and res.json()["error"]["code"] == "plan_change_not_supported"
    assert not store.ledger and store.profiles[USER]["plan"] == "free"


def test_confirm_expired_request_requires_note(client, store, make_token):
    create(client, make_token, planCode="topup_finish")
    rids = _uuidify(store)
    store.requests[rids[0]]["status"] = "expired"
    res = confirm(client, make_token, rids[0])
    assert res.status_code == 400 and res.json()["error"]["code"] == "note_required_for_expired"
    ok = confirm(client, make_token, rids[0], adminNote="9/24 입금 확인, 늦게 봄")
    assert ok.status_code == 200 and store.requests[rids[0]]["status"] == "paid"


@pytest.mark.parametrize("status", ["rejected", "canceled"])
def test_confirm_closed_request_is_409(client, store, make_token, status):
    create(client, make_token, planCode="topup_finish")
    rids = _uuidify(store)
    store.requests[rids[0]]["status"] = status
    res = confirm(client, make_token, rids[0])
    assert res.status_code == 409 and res.json()["error"]["code"] == "request_closed"


def test_confirm_unknown_request_is_404_and_bad_paid_at_422(client, store, make_token):
    assert confirm(client, make_token, UUID1).status_code == 404
    create(client, make_token, planCode="topup_finish")
    rids = _uuidify(store)
    assert confirm(client, make_token, rids[0], paidAt="yesterday").status_code == 422
    assert client.post(f"/v1/facemarket/admin/bank-transfers/not-a-uuid/confirm",
                       headers=auth_headers(make_token), json={"paidAt": "2026-09-22"}).status_code == 422


def test_reject_then_confirm_is_blocked(client, store, make_token):
    create(client, make_token, planCode="topup_finish")
    rids = _uuidify(store)
    res = client.post(f"/v1/facemarket/admin/bank-transfers/{rids[0]}/reject",
                      headers=auth_headers(make_token), json={"reason": "입금 없음"})
    assert res.status_code == 200 and res.json()["status"] == "rejected" and res.json()["adminNote"] == "입금 없음"
    assert store.audits[-1]["action"] == "bank_transfer.reject"
    assert confirm(client, make_token, rids[0]).status_code == 409
    again = client.post(f"/v1/facemarket/admin/bank-transfers/{rids[0]}/reject",
                        headers=auth_headers(make_token), json={"reason": "다시"})
    assert again.status_code == 409
    blank = client.post(f"/v1/facemarket/admin/bank-transfers/{rids[0]}/reject",
                        headers=auth_headers(make_token), json={"reason": "   "})
    assert blank.status_code == 422


# ---------------------------------------------------------------- 토스 시작 가드

def test_toss_start_is_blocked_while_manual_grant_active(client, store, make_token):
    store.grants["g1"] = {"id": "g1", "user_id": USER, "plan_code": "seller", "request_id": None,
                          "starts_at": NOW, "ends_at": NOW + MONTH, "status": "active"}
    res = client.post("/v1/subscriptions/start", headers=auth_headers(make_token),
                      json={"authKey": "a", "customerKey": USER, "planCode": "seller"})
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "manual_plan_active"


# ---------------------------------------------------------------- 만료

@pytest.mark.anyio
async def test_expire_manual_grants_resets_plan_and_buckets_but_keeps_toss_plan(store):
    for uid, toss in ((USER, None), (OTHER, "active")):
        store.grants[f"g-{uid}"] = {"id": f"g-{uid}", "user_id": uid, "plan_code": "seller",
                                    "request_id": None, "starts_at": NOW - MONTH,
                                    "ends_at": NOW - timedelta(hours=1), "status": "active"}
        store.sources[f"s-{uid}"] = {"id": f"s-{uid}", "user_id": uid, "plan_id": "plan-seller",
                                     "initial": 1600, "remaining": 700, "status": "active",
                                     "type": "subscription", "period_end": NOW, "payment_id": None}
        store.profiles[uid]["plan"] = "seller"
        store.accounts[uid]["balance"] = 700
    store.subscriptions[OTHER] = {"status": "active"}
    stats = await service.expire_manual_grants(store)
    assert stats == {"ended": 2, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "ended"
    assert store.sources[f"s-{USER}"]["status"] == "expired" and store.accounts[USER]["balance"] == 0
    assert store.profiles[USER]["plan"] == "free"
    assert store.profiles[OTHER]["plan"] == "seller"       # 토스 구독이 등급을 쥐고 있다
    assert store.commits == 2


@pytest.mark.anyio
async def test_expire_manual_grants_skips_failing_user_and_continues(store):
    for uid in (USER, OTHER):
        store.grants[f"g-{uid}"] = {"id": f"g-{uid}", "user_id": uid, "plan_code": "seller",
                                    "request_id": None, "starts_at": NOW - MONTH,
                                    "ends_at": NOW - timedelta(hours=1), "status": "active"}
        store.profiles[uid]["plan"] = "seller"
    store.fail_expire_for.add(USER)
    stats = await service.expire_manual_grants(store)
    assert stats == {"ended": 1, "skipped": 1}
    # 롤백 2 = 후보 조회 트랜잭션 닫기 1 + 실패한 사용자 1. 커밋은 성공한 사용자 1.
    assert store.rollbacks == 2 and store.commits == 1
    assert store.grants[f"g-{USER}"]["status"] == "active" and store.profiles[USER]["plan"] == "seller"
    assert store.grants[f"g-{OTHER}"]["status"] == "ended" and store.profiles[OTHER]["plan"] == "free"


def _expired_seller_grant(store, uid=USER):
    store.grants[f"g-{uid}"] = {"id": f"g-{uid}", "user_id": uid, "plan_code": "seller",
                                "request_id": None, "starts_at": NOW - MONTH,
                                "ends_at": NOW - timedelta(hours=1), "status": "active"}
    store.sources[f"s-{uid}"] = {"id": f"s-{uid}", "user_id": uid, "plan_id": "plan-seller",
                                 "initial": 1600, "remaining": 700, "status": "active",
                                 "type": "subscription", "period_end": NOW, "payment_id": None}
    store.profiles[uid]["plan"] = "seller"
    store.accounts[uid]["balance"] = 700


@pytest.mark.anyio
async def test_expire_waits_while_same_plan_renewal_request_is_open(store):
    """화면 약속: 종료 전에 같은 요금제로 다시 신청하면 이어진다. 관리자 확인이 종료일을 넘겨도(주말)
    열린 연장 신청이 있는 동안(신청 후 3일)은 이월 크레딧과 등급을 지우지 않는다."""
    _expired_seller_grant(store)
    # 종료(NOW-1h) 전에 낸 연장 신청, 아직 기한 안.
    store.requests["r1"] = {"id": "r1", "user_id": USER, "kind": "subscription", "plan_code": "seller",
                            "status": "requested", "expires_at": NOW + timedelta(days=2),
                            "created_at": NOW - timedelta(hours=3)}
    assert await service.expire_manual_grants(store) == {"ended": 0, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "active"
    assert store.sources[f"s-{USER}"]["status"] == "active" and store.accounts[USER]["balance"] == 700
    assert store.profiles[USER]["plan"] == "seller"
    # 다른 플랜 신청이나 충전 신청은 미루는 사유가 아니다.
    store.requests["r1"]["plan_code"] = "starter"
    assert await service.expire_manual_grants(store) == {"ended": 1, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "ended" and store.profiles[USER]["plan"] == "free"


@pytest.mark.anyio
async def test_expire_ignores_renewal_requests_made_after_the_end_date(store):
    """종료 뒤에 낸 신청은 사유가 아니다 — 아니면 입금 없이 3일마다 신청만 내서 이용권을 붙들 수 있다."""
    _expired_seller_grant(store)
    store.requests["r1"] = {"id": "r1", "user_id": USER, "kind": "subscription", "plan_code": "seller",
                            "status": "requested", "expires_at": NOW + timedelta(days=2),
                            "created_at": NOW - timedelta(minutes=30)}      # 종료(NOW-1h) 뒤
    assert await service.expire_manual_grants(store) == {"ended": 1, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "ended" and store.profiles[USER]["plan"] == "free"


@pytest.mark.anyio
async def test_expire_waits_for_open_renewal_then_expires_when_request_lapses(store):
    _expired_seller_grant(store)
    store.requests["r1"] = {"id": "r1", "user_id": USER, "kind": "subscription", "plan_code": "seller",
                            "status": "requested", "expires_at": NOW - timedelta(minutes=1), "created_at": NOW}
    # 신청 기한이 지났으면(입금 안 함) 더는 기다리지 않는다.
    assert await service.expire_manual_grants(store) == {"ended": 1, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "ended"


@pytest.mark.anyio
async def test_expire_rechecks_each_grant_under_its_own_lock(store):
    """후보 조회 뒤, 행을 잠그기 전에 관리자가 같은 플랜 연장을 확인한 경우(ends_at 이 미래로 밀림).
    옛 코드는 후보 목록만 믿고 새 버킷과 등급을 지웠다. 이제는 행마다 다시 확인해 건너뛴다."""
    _expired_seller_grant(store)

    def extend():
        store.grants[f"g-{USER}"]["ends_at"] = NOW + MONTH
        store.sources["s-new"] = {"id": "s-new", "user_id": USER, "plan_id": "plan-seller",
                                  "initial": 1600, "remaining": 1600, "status": "active",
                                  "type": "subscription", "period_end": NOW + MONTH, "payment_id": None}
    store.after_candidates = extend
    assert await service.expire_manual_grants(store) == {"ended": 0, "skipped": 0}
    assert store.grants[f"g-{USER}"]["status"] == "active"
    assert store.sources["s-new"]["status"] == "active" and store.sources[f"s-{USER}"]["status"] == "active"
    assert store.profiles[USER]["plan"] == "seller"
    assert store.commits == 0


@pytest.mark.anyio
async def test_expirer_tick_expires_stale_requests_and_grants(store):
    store.requests["r1"] = {"id": "r1", "user_id": USER, "kind": "topup", "status": "requested",
                            "expires_at": NOW - timedelta(minutes=1), "created_at": NOW - timedelta(days=4)}
    store.requests["r2"] = {"id": "r2", "user_id": USER, "kind": "subscription", "status": "requested",
                            "expires_at": NOW + timedelta(days=1), "created_at": NOW}
    worker = BankTransferExpirer(app=None)
    stats = await worker.tick(store)
    assert stats == {"expiredRequests": 1, "ended": 0, "skipped": 0}
    assert store.requests["r1"]["status"] == "expired" and store.requests["r2"]["status"] == "requested"
