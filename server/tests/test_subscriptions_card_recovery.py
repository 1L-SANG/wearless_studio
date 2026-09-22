"""카드 교체 후 유예 중 청구 복구. 실제 SQL의 상태 변경을 메모리 DB로 확인한다.

PG 호출은 대체하고 Postgres의 함수 이름·자리표시자만 SQLite에 맞춘다.
행 잠금은 SQLite로 검증할 수 없으므로 발급 전 잠금 쿼리 계약을 따로 확인한다.
"""

import asyncio
import contextlib
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.subscriptions as subs

NOW = "2026-09-24T12:00:00+00:00"
DEADLINE = "2026-09-25T12:00:00+00:00"
NEXT_MONTH = "2026-10-22T12:00:00+00:00"


class _Connection:
    def __init__(self):
        self.now = NOW
        self.sql = []
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("clock_timestamp", 0, lambda: self.now)
        self.db.create_function("now", 0, lambda: NOW)
        self.db.create_function("wl_billing_decrypt", 2, lambda key, kek: key)
        self.db.create_function("wl_billing_encrypt", 2, lambda key, kek: key)
        self.db.executescript("""
            create table subscriptions (
                id text primary key, user_id text unique, status text,
                billing_key_enc text, pay_method text, method_label text,
                method_last4 text, billing_key_invalid boolean,
                fail_count integer, grace_until text, next_billing_at text
            );
            insert into subscriptions values (
                'sub-1', 'user-1', 'past_due', 'bk-old', 'CARD', 'old', '1111',
                true, 3, '2026-09-25T12:00:00+00:00', null
            );
        """)

    def cursor(self):
        conn = self

        class Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, sql, params=None):
                conn.sql.append(" ".join(sql.split()))
                translated = (sql.replace("public.", "").replace("::text", "")
                              .replace("%s", "?").replace(" for update", ""))
                self.cursor = conn.db.execute(translated, params or ())

            async def fetchone(self):
                row = self.cursor.fetchone()
                return dict(row) if row is not None else None

        return Cursor()

    async def commit(self):
        self.db.commit()

    async def rollback(self):
        self.db.rollback()

    def row(self):
        return dict(self.db.execute("select * from subscriptions").fetchone())


@pytest.fixture()
def recovery(monkeypatch):
    conn = _Connection()
    state = SimpleNamespace(conn=conn, issued=[], deleted=[], after_issue=None)

    @contextlib.asynccontextmanager
    async def connection(request):
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise

    async def issue(settings, *, auth_key, customer_key):
        state.issued.append(customer_key)
        if state.after_issue:
            state.after_issue()
        return {"billingKey": "bk-new", "method": "CARD", "label": "new", "last4": "2222"}

    async def delete(settings, *, billing_key):
        state.deleted.append(billing_key)

    async def charge(*args, **kwargs):
        pytest.fail("카드 교체는 청구 워커에 예약해야 하며 직접 청구하면 안 된다")

    monkeypatch.setattr(subs, "get_conn", connection)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", issue)
    monkeypatch.setattr(subs.toss_billing, "delete_billing_key", delete)
    monkeypatch.setattr(subs.toss_billing, "charge", charge)
    settings = SimpleNamespace(toss_secret_key="sk", toss_billing_secret_key=None,
                               toss_billing_kek="kek")
    state.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    yield state
    conn.db.close()


def _replace(state):
    return asyncio.run(subs.replace_card(
        state.request, subs.CardBody(authKey="auth", customerKey="user-1"), user_id="user-1"))


def test_last_grace_day_card_swap_schedules_retry_without_resetting_grace_or_attempts(recovery):
    assert _replace(recovery).status_code == 200
    row = recovery.conn.row()
    assert row["next_billing_at"] == NOW
    assert row["status"] == "past_due"
    assert row["fail_count"] == 3
    assert row["grace_until"] == DEADLINE
    assert row["billing_key_enc"] == "bk-new"
    assert recovery.deleted == ["bk-old"]


@pytest.mark.parametrize("status,next_billing", [("active", NEXT_MONTH), ("canceled", None)])
def test_card_swap_does_not_start_a_charge_for_active_or_canceled_subscription(
    recovery, status, next_billing,
):
    recovery.conn.db.execute(
        "update subscriptions set status=?, next_billing_at=?, grace_until=null, fail_count=0",
        (status, next_billing),
    )
    assert _replace(recovery).status_code == 200
    row = recovery.conn.row()
    assert row["next_billing_at"] == next_billing
    assert row["status"] == status
    assert row["grace_until"] is None
    assert row["fail_count"] == 0


def test_ended_subscription_rejects_card_swap_without_issuing_a_key(recovery):
    recovery.conn.db.execute("update subscriptions set status='ended'")
    with pytest.raises(HTTPException) as exc:
        _replace(recovery)
    assert exc.value.status_code == 404
    assert recovery.issued == []


def test_expired_grace_rejects_card_swap_before_issuing_a_key(recovery):
    recovery.conn.now = DEADLINE
    with pytest.raises(HTTPException) as exc:
        _replace(recovery)
    assert exc.value.status_code == 409
    assert recovery.issued == []
    assert recovery.conn.row()["billing_key_enc"] == "bk-old"


def test_grace_expiring_during_key_issue_discards_new_key_and_does_not_rearm(recovery):
    recovery.after_issue = lambda: setattr(recovery.conn, "now", DEADLINE)
    with pytest.raises(HTTPException) as exc:
        _replace(recovery)
    assert exc.value.status_code == 409
    assert recovery.deleted == ["bk-new"]
    row = recovery.conn.row()
    assert row["billing_key_enc"] == "bk-old"
    assert row["next_billing_at"] is None
    assert row["grace_until"] == DEADLINE


def test_rejected_replacement_key_does_not_schedule_a_charge(recovery, monkeypatch):
    async def reject(*args, **kwargs):
        raise subs.toss_billing.TossBillingError("REJECT_CARD_COMPANY", "거절", retryable=False)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", reject)
    with pytest.raises(HTTPException) as exc:
        _replace(recovery)
    assert exc.value.status_code == 402
    row = recovery.conn.row()
    assert row["billing_key_enc"] == "bk-old"
    assert row["next_billing_at"] is None
    assert recovery.deleted == []


def test_card_swap_locks_current_key_before_issuing_replacement(recovery):
    def verify_locked():
        assert any("wl_billing_decrypt" in q and q.endswith("for update")
                   for q in recovery.conn.sql)
    recovery.after_issue = verify_locked
    _replace(recovery)
