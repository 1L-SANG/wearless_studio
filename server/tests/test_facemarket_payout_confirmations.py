"""Manual transfer authorization, retries and immutable payment history."""
from datetime import date, datetime

import pytest

from app import facemarket_payout as payout
from payout_helpers import MODEL_ID, NOW, Conn, client_for, patch_db, account_fixture

CONFIRM_ID = "55555555-5555-5555-5555-555555555555"
BASE = "/v1/facemarket/admin/payout-statements"
CONFIRM = f"{BASE}/{MODEL_ID}/2026-08/confirm"
ACTIONS = f"/v1/facemarket/admin/payout-confirmations/{CONFIRM_ID}"


def confirmation(**overrides):
    return dict(id=CONFIRM_ID, model_id=MODEL_ID, period_month=date(2026, 8, 1),
                amount=7000, count=1, status="prepared", responsible_admin="user-1",
                bank_code="kb", account_last4="1234", holder_name="테스트",
                account_version=CONFIRM_ID, created_at=NOW, started_at=None,
                paid_at=None, cancelled_at=None, **overrides)


def headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_generic_paid_action_cannot_authorize_or_reverse_a_payment(keypair, make_token, monkeypatch):
    conn = Conn([{"id": MODEL_ID, "display_name": "모델"}, {"status": "paid"},
                 {"amount": 7000, "count": 1}, {**confirmation(), "status": "paid", "scheduled_for": date(2026, 9, 10)}, None])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{BASE}/{MODEL_ID}/2026-08/status", json={"status": "paid"}, headers=headers(make_token))
    assert response.status_code == 409
    assert not any(sql.startswith("insert into fm_payout_statements") for sql, _ in conn.executed)


def test_legacy_paid_cannot_be_reopened(keypair, make_token, monkeypatch):
    conn = Conn([{"id": MODEL_ID, "display_name": "모델"}, {"status": "paid"},
                 {"amount": 7000, "count": 1}, {**confirmation(), "status": "scheduled", "scheduled_for": date(2026, 9, 10)}, None])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{BASE}/{MODEL_ID}/2026-08/status", json={"status": "scheduled"}, headers=headers(make_token))
    assert response.status_code == 409
    assert "commit" not in conn.events


def test_stale_hold_after_new_payment_is_rejected(keypair, make_token, monkeypatch):
    conn = Conn([{"id": MODEL_ID, "display_name": "모델"}, {"status": "scheduled"},
                 {"id": CONFIRM_ID, "status": "paid"}])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{BASE}/{MODEL_ID}/2026-08/status", json={"status": "held"}, headers=headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_statement_changed"


def test_confirmation_retry_recovers_same_record_without_reallocation(keypair, make_token, monkeypatch):
    conn = Conn([{"id": MODEL_ID}, confirmation()])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(CONFIRM, json={"confirmationId": CONFIRM_ID}, headers=headers(make_token))
    assert response.status_code == 200
    assert response.json()["id"] == CONFIRM_ID
    assert response.json()["amount"] == 7000
    assert not any("insert into fm_payout_confirmation" in sql for sql, _ in conn.executed)


@pytest.mark.parametrize("month", ["9998-12", "2026-09"])
def test_current_or_future_month_cannot_confirm(month, keypair, make_token, monkeypatch):
    from test_facemarket_payout_statements import freeze_month
    freeze_month(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{BASE}/{MODEL_ID}/{month}/confirm", json={"confirmationId": CONFIRM_ID}, headers=headers(make_token))
    assert response.status_code == 409
    assert conn.executed == []


@pytest.mark.parametrize("action", ["start", "paid", "cancel"])
def test_other_admin_cannot_advance_confirmation(action, keypair, make_token, monkeypatch):
    item = confirmation()
    item["responsible_admin"] = "other-admin"
    conn = Conn([item])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ACTIONS}/{action}", headers=headers(make_token))
    assert response.status_code == 403
    assert "commit" not in conn.events


def test_paid_retry_preserves_original_amount_and_timestamp(keypair, make_token, monkeypatch):
    item = confirmation()
    item.update(status="paid", paid_at=NOW)
    conn = Conn([item])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ACTIONS}/paid", headers=headers(make_token))
    assert response.status_code == 200
    assert response.json()["amount"] == 7000
    assert datetime.fromisoformat(response.json()["paidAt"]) == NOW
    assert not any(sql.startswith("update ") for sql, _ in conn.executed)


@pytest.mark.parametrize("status", ["transfer_started", "paid"])
def test_started_or_paid_transfer_cannot_cancel(status, keypair, make_token, monkeypatch):
    item = confirmation(); item["status"] = status
    conn = Conn([item])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ACTIONS}/cancel", headers=headers(make_token))
    assert response.status_code == 409
    assert not any("released_at" in sql for sql, _ in conn.executed)


def test_start_requires_unchanged_account_version(keypair, make_token, monkeypatch):
    conn = Conn([confirmation(), {"account_version": "changed"}])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ACTIONS}/start", headers=headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_account_changed"


def test_finish_after_account_change_uses_original_confirmation(keypair, make_token, monkeypatch):
    item = confirmation(); item["status"] = "transfer_started"
    paid = {**item, "status": "paid", "paid_at": NOW}
    conn = Conn([item, paid])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ACTIONS}/paid", headers=headers(make_token))
    assert response.status_code == 200
    assert response.json()["amount"] == 7000
    assert not any("fm_payout_accounts" in sql or "fm_settlements" in sql for sql, _ in conn.executed)
    assert conn.events == ["audit", "commit"]


def test_reveal_uses_saved_full_account_tuple(keypair, make_token, monkeypatch):
    key, raw, account = account_fixture()
    item = {**confirmation(), **account}
    conn = Conn([item])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair, fm_payout_account_key=key).get(f"{ACTIONS}/account", headers=headers(make_token))
    assert response.status_code == 200
    assert response.json()["accountNumber"] == raw
    assert response.json()["bankName"] == "신한은행"
    assert response.json()["holderName"] == "테스트 예금주"
    assert response.headers["cache-control"] == "no-store"
    assert not any("from fm_payout_accounts" in sql for sql, _ in conn.executed)
    assert raw not in repr(conn.executed)
