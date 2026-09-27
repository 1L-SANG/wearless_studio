"""중간 정산(2026-09-27) — 마감 전인 이번 달을 지금까지 먼저 지급 확인서로.

· POST /admin/payout-statements/{이번 달}/run?interim=true 는 기준 시각(cutoff) 전에 생긴
  **체인 확정** 정산 중 아직 어느 확인서에도 없는 것만 모은다. 돈은 움직이지 않는다.
· 진행 중 확인서가 있으면 그걸 돌려주고(멱등), 새로 담을 게 없으면 nothing_due.
· 다음 달 이후는 거절, 마감된 달은 월말 정산과 똑같이 동작.
· 확인서에 kind='interim' + cutoff_at 이 남고, 지급 완료는 여전히 실제 이체 기록과 함께만.
"""
from datetime import date, datetime
from pathlib import Path

import pytest

from app import facemarket_payout as payout
from payout_helpers import MODEL_ID, NOW, Conn, account_fixture, client_for, patch_db

CONFIRM_ID = "66666666-6666-6666-6666-666666666666"
INTERIM = "/v1/facemarket/admin/payout-statements/2026-09/run?interim=true"
CUTOFF = datetime(2026, 9, 27, 12, tzinfo=payout.KST)
MIGRATION = Path(__file__).resolve().parents[2] / "supabase/migrations/20260927090000_fm_payout_interim.sql"
MODEL_ROW = {"model_id": MODEL_ID, "model_name": "모델"}


def headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def freeze(monkeypatch, moment=CUTOFF.replace(microsecond=345678)):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz or payout.KST)

    monkeypatch.setattr(payout, "datetime", Frozen)


def confirmation(**overrides):
    row = dict(id=CONFIRM_ID, model_id=MODEL_ID, period_month=date(2026, 9, 1), amount=16000, count=2,
               status="prepared", responsible_admin="user-1", bank_code="kb", account_last4="1234",
               holder_name="테스트", account_version=CONFIRM_ID, created_at=NOW, started_at=None,
               paid_at=None, cancelled_at=None, provider="manual", provider_ref=None,
               transferred_on=None, kind="interim", cutoff_at=CUTOFF)
    row.update(overrides)
    return row


def interim_rows(account, due=None):
    """새 중간 정산 확인서가 만들어지는 한 모델의 쿼리 순서(가짜 커서는 execute 마다 한 행)."""
    entries = [{"id": "s1", "model_amount": 7000}, {"id": "s2", "model_amount": 9000}]
    return [
        [MODEL_ROW],            # 대상 모델(기준 시각 전 정산이 있는)
        {"id": MODEL_ID},       # 모델 잠금
        None,                   # 명세 상태(없음)
        None,                   # 진행 중 확인서(없음)
        due or {"due_count": 2, "due_amount": 16000, "unconfirmed_count": 1},
        account,                # 계좌 스냅샷
        entries,                # 미배정 확정 정산 항목
        confirmation(),         # INSERT … returning
        None,                   # 항목 INSERT
    ]


def test_interim_run_prepares_confirmed_settlements_up_to_cutoff(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    key, _raw, account = account_fixture()
    account["account_version"] = CONFIRM_ID
    conn = Conn(interim_rows(account))
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair, fm_payout_account_key=key).post(INTERIM, headers=headers(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "interim" and body["moneyMoved"] is False
    assert body["cutoffAt"] == "2026-09-27T12:00:00+09:00"  # 초 단위로 자른 기준 시각
    assert body["coveredThrough"] == "2026-09-27"
    result = body["results"][0]
    assert result["outcome"] == "prepared" and result["amount"] == 16000 and result["count"] == 2
    assert result["unconfirmedCount"] == 1  # 미확정 1건은 담지 않고 남긴다

    executed = conn.executed
    models_sql, models_params = executed[0]
    assert models_params[1] == CUTOFF
    due_sql, due_params = next((sql, params) for sql, params in executed if "due_count" in sql)
    assert due_params[2] == CUTOFF
    assert "e.released_at is null" in due_sql
    pick_sql, pick_params = next((sql, params) for sql, params in executed
                                 if sql.startswith("select st.id::text, st.model_amount"))
    # 확정 정산만, 기준 시각 전만, 이미 배정된 정산은 빼고.
    assert "st.chain_status = 'confirmed'" in pick_sql
    assert "e.released_at is null" in pick_sql
    assert pick_params[2] == CUTOFF
    insert_sql, insert_params = next((sql, params) for sql, params in executed
                                     if sql.startswith("insert into fm_payout_confirmations"))
    assert "kind, cutoff_at" in insert_sql
    assert insert_params[-3:] == ("manual", "interim", CUTOFF)
    # 돈을 옮기는 전이는 없다. 명세(월 금액)도 건드리지 않는다 — 달이 끝나야 정해진다.
    assert not any(sql.startswith("update") for sql, _ in executed)
    assert not any(sql.startswith("insert into fm_payout_statements") for sql, _ in executed)
    assert conn.events == ["audit", "audit", "commit"]


def test_interim_rerun_with_active_confirmation_returns_it(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, confirmation()])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(INTERIM, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "in_progress"
    assert body["results"][0]["confirmationId"] == CONFIRM_ID
    assert not any(sql.startswith("insert into fm_payout_confirmations") for sql, _ in conn.executed)


def test_interim_with_nothing_new_is_nothing_due(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, None,
                 {"due_count": 0, "due_amount": 0, "unconfirmed_count": 0}])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(INTERIM, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "nothing_due"
    assert not any(sql.startswith("insert into fm_") for sql, _ in conn.executed)


def test_interim_with_only_unconfirmed_settlements_prepares_nothing(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, None,
                 {"due_count": 0, "due_amount": 0, "unconfirmed_count": 3}])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(INTERIM, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "nothing_due"
    assert body["results"][0]["unconfirmedCount"] == 3
    assert not any(sql.startswith("insert into fm_") for sql, _ in conn.executed)


@pytest.mark.parametrize("status", ["held", "paid"])
def test_interim_leaves_held_or_legacy_paid_alone(status, keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, {"status": status}])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(INTERIM, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == status
    assert not any(sql.startswith("insert into fm_") for sql, _ in conn.executed)


def test_interim_without_account_is_skipped(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, None,
                 {"due_count": 2, "due_amount": 16000, "unconfirmed_count": 0}, None])
    patch_db(monkeypatch, payout, conn)
    body = client_for(keypair).post(INTERIM, headers=headers(make_token)).json()
    assert body["results"][0]["outcome"] == "no_account" and body["results"][0]["amount"] == 16000
    assert not any(sql.startswith("insert into fm_payout_confirmations") for sql, _ in conn.executed)


def test_interim_for_future_month_is_rejected(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post("/v1/facemarket/admin/payout-statements/2026-10/run?interim=true",
                                        headers=headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_month_future"
    assert conn.executed == []


def test_current_month_without_interim_flag_still_needs_close(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post("/v1/facemarket/admin/payout-statements/2026-09/run",
                                        headers=headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_month_open"


def test_interim_flag_on_closed_month_is_the_normal_monthly_run(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    # 월말 정산 순서: 모델 → 잠금 → 명세 → 체인 미확정 건수(있으면 건너뜀).
    conn = Conn([[MODEL_ROW], {"id": MODEL_ID}, None, {"count": 1}])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post("/v1/facemarket/admin/payout-statements/2026-08/run?interim=true",
                                        headers=headers(make_token))
    body = response.json()
    assert body["kind"] == "monthly" and body["cutoffAt"] is None
    assert body["results"][0]["outcome"] == "unconfirmed"


def test_non_admin_cannot_run_interim(keypair, make_token, monkeypatch):
    freeze(monkeypatch)
    conn = Conn()
    patch_db(monkeypatch, payout, conn, admin=False)
    assert client_for(keypair).post(INTERIM, headers=headers(make_token)).status_code == 403
    assert conn.executed == []


def test_interim_confirmation_is_paid_only_with_a_transfer_record(keypair, make_token, monkeypatch):
    freeze(monkeypatch, datetime(2026, 9, 30, 15, tzinfo=payout.KST))
    started = confirmation(status="transfer_started", started_at=NOW)
    conn = Conn([started])
    patch_db(monkeypatch, payout, conn)
    url = f"/v1/facemarket/admin/payout-confirmations/{CONFIRM_ID}/paid"
    response = client_for(keypair).post(url, headers=headers(make_token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "payout_transfer_record_required"
    assert not any(sql.startswith("update") for sql, _ in conn.executed)

    paid = confirmation(status="paid", started_at=NOW, paid_at=NOW, provider_ref="신한 0930-0042",
                        transferred_on=date(2026, 9, 30))
    conn = Conn([started, paid])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(url, headers=headers(make_token), json={
        "transferReference": "신한 0930-0042", "amount": 16000, "transferredOn": "2026-09-30"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "paid" and body["kind"] == "interim" and body["simulated"] is False
    assert body["coveredThrough"] == "2026-09-27" and body["transferredOn"] == "2026-09-30"


def test_statement_view_reports_paid_so_far_and_keeps_open_month_scheduled():
    paid = payout._confirmation_view(confirmation(status="paid", started_at=NOW, paid_at=NOW,
                                                  provider_ref="x-0042", transferred_on=date(2026, 9, 30)))
    stub = payout._confirmation_view(confirmation(id="c-stub", status="paid", started_at=NOW, paid_at=NOW,
                                                  provider="stub", provider_ref="stub-1", amount=500, count=1))
    item = payout._statement_view({"period_month": date(2026, 9, 1), "amount": 16500, "count": 3,
                                   "unpaid_amount": 0, "unpaid_count": 0}, date(2026, 9, 1))
    item = payout._with_confirmations(item, [paid, stub])
    # 이번 달은 아직 끝나지 않았다 — 지금까지 몫을 다 줬어도 달 전체가 "지급 완료"는 아니다.
    assert item["status"] == "scheduled"
    assert item["paidAmount"] == 16000 and item["paidCount"] == 2  # 스텁 500원은 지급액이 아니다
    assert item["unpaidAmount"] == 0


def test_confirmation_view_from_history_json_parses_cutoff():
    view = payout._confirmation_view(confirmation(cutoff_at="2026-09-27T12:00:00+09:00",
                                                  period_month="2026-09-01"))
    assert view["kind"] == "interim" and view["coveredThrough"] == date(2026, 9, 27)
    # 자정 정각 기준이면 그 전날까지다.
    midnight = payout._confirmation_view(confirmation(cutoff_at="2026-09-28T00:00:00+09:00"))
    assert midnight["coveredThrough"] == date(2026, 9, 27)
    monthly = payout._confirmation_view(confirmation(kind=None, cutoff_at=None))
    assert monthly["kind"] == "monthly" and monthly["coveredThrough"] is None


def test_interim_route_is_gated_and_audited_before_commit():
    source = Path(payout.__file__).read_text()
    body = source.split("async def run_monthly_payout(")[1].split("@router.")[0]
    assert body.index("await admin_guard.require_admin(conn, user_id, request)") < body.index("RUN_MODELS_SQL")
    assert body.index("admin_guard.write_audit(") < body.index("await conn.commit()")
    assert '"payout_statement.interim_run"' in body


def test_interim_migration_is_additive_and_freezes_kind_and_cutoff():
    sql = " ".join(MIGRATION.read_text().lower().split())
    assert "add column if not exists kind text not null default 'monthly'" in sql
    assert "add column if not exists cutoff_at timestamptz" in sql
    assert "check (kind in ('monthly', 'interim'))" in sql
    assert "check ((kind = 'interim') = (cutoff_at is not null))" in sql
    assert "create trigger fm_payout_kind_immutable before update" in sql
    # 기존 불변 트리거 함수·배정 규칙은 고치지 않는다.
    assert "fm_payout_confirmation_immutable()" not in sql
    assert "drop index" not in sql and "drop constraint" not in sql
