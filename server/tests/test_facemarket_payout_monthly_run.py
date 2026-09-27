"""월말 정산 실행 + 실제 이체 기록 없이는 지급 완료가 안 되는 규칙 (2026-09-26).

· [월말 정산 실행] 은 마감된 달의 확정 정산을 모델별 지급 확인서(prepared)로 모을 뿐, 돈을
  옮기지 않는다(moneyMoved=False). 다시 눌러도 확인서가 늘지 않는다(멱등).
· 지급 완료(paid)는 참조번호·금액·이체일이 있어야 하고, 금액이 확인서와 달라도 안 된다.
· 모델 화면에는 참조번호 끝 4자리만 간다.
"""
from datetime import date, datetime
from pathlib import Path

import pytest

from app import facemarket_payout as payout
from payout_helpers import MODEL_ID, NOW, Conn, account_fixture, client_for, patch_db

CONFIRM_ID = "55555555-5555-5555-5555-555555555555"
RUN = "/v1/facemarket/admin/payout-statements/2026-08/run"
PAID = f"/v1/facemarket/admin/payout-confirmations/{CONFIRM_ID}/paid"
MIGRATION = Path(__file__).resolve().parents[2] / "supabase/migrations/20260926230000_fm_payout_transfer_record.sql"


def headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def freeze(monkeypatch, today=date(2026, 9, 26)):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(today.year, today.month, today.day, 12, tzinfo=tz or payout.KST)

    monkeypatch.setattr(payout, "datetime", Frozen)


def confirmation(**overrides):
    row = dict(id=CONFIRM_ID, model_id=MODEL_ID, period_month=date(2026, 8, 1), amount=7000, count=1,
               status="transfer_started", responsible_admin="user-1", bank_code="kb",
               account_last4="1234", holder_name="테스트", account_version=CONFIRM_ID,
               created_at=NOW, started_at=NOW, paid_at=None, cancelled_at=None,
               provider="manual", provider_ref=None, transferred_on=None)
    row.update(overrides)
    return row


def transfer(**overrides):
    body = {"transferReference": "신한 20260912-000123", "amount": 7000, "transferredOn": "2026-09-12"}
    body.update(overrides)
    return body


# ── 지급 완료 = 실제 이체 기록과 함께만 ─────────────────────────────────────────

def test_paid_without_transfer_record_is_refused(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([confirmation()])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(PAID, headers=headers(make_token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "payout_transfer_record_required"
    assert not any(sql.startswith("update") for sql, _ in conn.executed)
    assert "commit" not in conn.events


@pytest.mark.parametrize("body, code", [
    (transfer(amount=7001), "payout_amount_mismatch"),
    (transfer(transferReference="  12 "), "invalid_transfer_reference"),
    (transfer(transferReference="a\nbcdef"), "invalid_transfer_reference"),
    (transfer(transferredOn="2026-09-27"), "invalid_transfer_date"),   # 미래
    (transfer(transferredOn="2026-09-10"), "invalid_transfer_date"),   # 확인서보다 먼저
])
def test_paid_with_wrong_record_is_refused(body, code, keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([confirmation()])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(PAID, json=body, headers=headers(make_token))
    assert response.status_code in (400, 409)
    assert response.json()["error"]["code"] == code
    assert not any(sql.startswith("update") for sql, _ in conn.executed)


def test_paid_records_reference_and_date_in_the_same_update(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    paid = confirmation(status="paid", paid_at=NOW, provider_ref="신한 20260912-000123",
                        transferred_on=date(2026, 9, 12))
    conn = Conn([confirmation(), paid])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(PAID, json=transfer(), headers=headers(make_token))
    assert response.status_code == 200, response.text
    update = next((sql, params) for sql, params in conn.executed if sql.startswith("update"))
    assert "status = 'paid'" in update[0] and "provider_ref = %s" in update[0] and "transferred_on = %s" in update[0]
    assert update[1] == ("신한 20260912-000123", date(2026, 9, 12), CONFIRM_ID)
    body = response.json()
    assert body["status"] == "paid" and body["simulated"] is False
    assert body["transferredOn"] == "2026-09-12"
    assert body["providerRef"] == "신한 20260912-000123"  # 관리자 응답에는 원문
    assert body["transferReferenceMasked"] == "••••0123"
    assert conn.events == ["audit", "commit"]


def test_paid_retry_does_not_need_record_again(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([confirmation(status="paid", paid_at=NOW, provider_ref="x-0123", transferred_on=date(2026, 9, 12))])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(PAID, headers=headers(make_token))
    assert response.status_code == 200
    assert not any(sql.startswith("update") for sql, _ in conn.executed)


def test_model_view_sees_only_masked_reference():
    view = payout._confirmation_view(confirmation(status="paid", paid_at=NOW, provider_ref="신한 20260912-000123",
                                                  transferred_on=date(2026, 9, 12)))
    assert view["providerRef"] is None
    assert view["transferReferenceMasked"] == "••••0123"
    assert view["transferredOn"] == date(2026, 9, 12)
    assert view["simulated"] is False


def test_stub_confirmation_stays_simulated_in_every_view():
    view = payout._confirmation_view(confirmation(status="paid", provider="stub", provider_ref="stub-abcd"))
    assert view["simulated"] is True


# ── 월말 정산 실행 ──────────────────────────────────────────────────────────────

MODEL_ROW = {"model_id": MODEL_ID, "model_name": "모델"}
TOTALS = {"amount": 16000, "count": 2, "unpaid_amount": 16000, "unpaid_count": 2}


def prepared_rows(account):
    """새 확인서가 만들어지는 한 모델의 쿼리 순서(가짜 커서가 execute 마다 한 행씩 꺼낸다)."""
    entries = [{"id": "s1", "model_amount": 7000}, {"id": "s2", "model_amount": 9000}]
    return [
        [MODEL_ROW],            # 대상 모델
        {"id": MODEL_ID},       # 모델 잠금
        None,                   # 명세 상태(없음)
        {"count": 0},           # 체인 미확정 건수
        TOTALS,                 # 월 합계
        None,                   # 명세 새로 고침(upsert)
        None,                   # 진행 중 확인서(없음)
        account,                # 계좌 스냅샷
        entries,                # 미배정 정산 항목
        confirmation(status="prepared", started_at=None, amount=16000, count=2),  # INSERT … returning
        None,                   # 항목 INSERT
    ]


def test_run_prepares_confirmation_and_moves_no_money(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    key, _raw, account = account_fixture()
    account["account_version"] = CONFIRM_ID
    conn = Conn(prepared_rows(account))
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair, fm_payout_account_key=key).post(RUN, headers=headers(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["moneyMoved"] is False
    assert body["summary"]["prepared"] == 1
    assert body["results"][0]["outcome"] == "prepared"
    assert body["results"][0]["amount"] == 16000 and body["results"][0]["count"] == 2
    executed = [sql for sql, _ in conn.executed]
    assert any(sql.startswith("insert into fm_payout_statements") for sql in executed)
    assert any(sql.startswith("insert into fm_payout_confirmations") for sql in executed)
    # 돈을 옮기는 전이는 없다 — 확인서는 prepared 로 태어날 뿐이다.
    assert not any("transfer_started" in sql and sql.startswith("update") for sql in executed)
    assert not any("status = 'paid'" in sql for sql in executed)
    assert conn.events == ["audit", "audit", "commit"]  # 모델별 prepare + 달 실행


def test_rerun_with_active_confirmation_shows_it_instead_of_creating_another(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    active = confirmation(status="prepared", started_at=None, amount=16000, count=2)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, {"status": "scheduled"}, {"count": 0},
                 {**TOTALS, "unpaid_amount": 0, "unpaid_count": 0}, None, active])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(RUN, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "in_progress"
    assert body["results"][0]["confirmationId"] == CONFIRM_ID
    assert not any(sql.startswith("insert into fm_payout_confirmations") for sql, _ in conn.executed)


def test_rerun_after_payment_has_nothing_due(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, {"status": "scheduled"}, {"count": 0},
                 {**TOTALS, "unpaid_amount": 0, "unpaid_count": 0}, None, None])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(RUN, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "nothing_due"
    assert not any(sql.startswith("insert into fm_payout_confirmations") for sql, _ in conn.executed)


def test_unconfirmed_chain_settlements_are_not_aggregated(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, {"count": 2}])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(RUN, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "unconfirmed"
    assert body["results"][0]["unconfirmedCount"] == 2
    assert not any(sql.startswith("insert into fm_") for sql, _ in conn.executed)


@pytest.mark.parametrize("status", ["held", "paid"])
def test_held_or_legacy_paid_month_is_left_alone(status, keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, {"status": status}])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(RUN, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == status
    assert not any(sql.startswith("insert into fm_") for sql, _ in conn.executed)


def test_model_without_account_is_skipped_not_failed(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, {"count": 0}, TOTALS, None, None, None])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(RUN, headers=headers(make_token))
    assert response.status_code == 200
    assert response.json()["results"][0]["outcome"] == "no_account"
    assert not any(sql.startswith("insert into fm_payout_confirmations") for sql, _ in conn.executed)


@pytest.mark.parametrize("month", ["2026-09", "2026-10"])
def test_open_month_cannot_run(month, keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"/v1/facemarket/admin/payout-statements/{month}/run",
                                        headers=headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_month_open"
    assert conn.executed == []


def test_non_admin_cannot_run(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn, admin=False)
    assert client_for(keypair).post(RUN, headers=headers(make_token)).status_code == 403
    assert conn.executed == []


def test_run_route_is_gated_and_audited_before_commit():
    source = Path(payout.__file__).read_text()
    body = source.split("async def run_monthly_payout(")[1].split("@router.")[0]
    assert body.index("await admin_guard.require_admin(conn, user_id, request)") < body.index("RUN_MODELS_SQL")
    assert body.index("admin_guard.write_audit(") < body.index("await conn.commit()")
    assert '"payout_statement.run"' in body


# ── 마이그레이션 ────────────────────────────────────────────────────────────────

def test_transfer_record_migration_guards_manual_paid_without_touching_old_trigger():
    sql = " ".join(MIGRATION.read_text().lower().split())
    assert "add column if not exists transferred_on date" in sql
    assert "check (transferred_on is null or status = 'paid')" in sql
    assert ("check (status <> 'paid' or provider <> 'manual' or (provider_ref is not null "
            "and btrim(provider_ref) <> '' and transferred_on is not null)) not valid") in sql
    # 기존 불변 트리거 함수(20260911170000)는 고치지 않는다 — 별도 트리거로 막는다.
    assert "fm_payout_confirmation_immutable()" not in sql
    assert "create trigger fm_payout_transfer_record_immutable before update" in sql
