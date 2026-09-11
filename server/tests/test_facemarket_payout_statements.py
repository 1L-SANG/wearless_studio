from datetime import date, datetime, timezone
import sqlite3

import pytest

from app import facemarket_payout as payout
from payout_helpers import MODEL_ID, NOW, Conn, client_for, patch_db

BASE = "/v1/facemarket/payout-statements"
ADMIN = "/v1/facemarket/admin/payout-statements"


def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def row(month=date(2026, 8, 1), **overrides):
    return {
        "period_month": month, "amount": 7000, "count": 1,
        "status": "scheduled", "scheduled_for": date(2026, 9, 10), "paid_at": None,
        **overrides,
    }


def freeze_month(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 11, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(payout, "datetime", Clock)


def test_statements_route_exists(keypair, make_token):
    assert client_for(keypair).get(BASE, headers=auth(make_token)).status_code == 503


def test_months_merge_paid_and_choose_oldest_closed_scheduled(keypair, make_token, monkeypatch):
    freeze_month(monkeypatch)
    conn = Conn([{"id": MODEL_ID}, [row(), row(date(2026, 7, 1), status="paid", paid_at=NOW), row(date(2026, 6, 1))]])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).get(BASE, headers=auth(make_token))
    assert response.status_code == 200
    result = response.json()
    assert [item["periodMonth"] for item in result["items"]] == ["2026-09", "2026-08", "2026-07", "2026-06"]
    assert result["items"][0] == {
        "periodMonth": "2026-09", "amount": 0, "count": 0, "status": "scheduled",
        "scheduledFor": "2026-10-10", "paidAt": None, "open": True,
        "unpaidAmount": 0, "unpaidCount": 0,
    }
    assert result["items"][2]["status"] == "paid"
    assert result["items"][2]["paidAt"] is not None
    assert result["nextPayout"]["periodMonth"] == "2026-06"
    assert result["items"][1]["open"] is False


def test_current_month_fallback_and_24_month_cap(keypair, make_token, monkeypatch):
    freeze_month(monkeypatch)
    months = [date(year, month, 1) for year in (2024, 2025) for month in range(1, 13)]
    conn = Conn([{"id": MODEL_ID}, [row(month, status="paid") for month in months]])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).get(BASE, headers=auth(make_token))
    assert len(response.json()["items"]) == 24
    assert response.json()["nextPayout"] == {"periodMonth": "2026-09", "amount": 0, "scheduledFor": "2026-10-10"}


def test_seoul_month_boundaries_and_year_rollover():
    start, end = payout.month_bounds(date(2026, 12, 1))
    assert start.astimezone(timezone.utc) == datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc) == datetime(2026, 12, 31, 15, tzinfo=timezone.utc)
    assert payout.scheduled_for(date(2026, 12, 1)) == date(2027, 1, 10)


def test_live_aggregation_uses_seoul_months_and_saved_amounts():
    # 운영 SELECT를 SQLite로 실행해 조인과 월 경계를 함께 확인해요.
    sql = payout.MODEL_STATEMENTS_SQL
    sql = sql.replace(payout._MODEL_HISTORY_SQL, "null")
    sql = sql.replace("date_trunc('month', st.created_at at time zone 'Asia/Seoul')::date", "seoul_month(st.created_at)")
    sql = sql.replace("%s", "?")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from zoneinfo import ZoneInfo
    conn.create_function("seoul_month", 1, lambda value: datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-01"))
    conn.executescript("""
        create table fm_licenses(id text, model_id text);
        create table fm_settlements(license_id text, created_at text, model_amount integer, id text);
        create table fm_payout_confirmation_entries(settlement_id text, released_at text);
        create table fm_payout_statements(model_id text, period_month text, amount integer, count integer,
                                         status text, scheduled_for text, paid_at text);
        insert into fm_licenses values ('own', 'm1'), ('other', 'm2');
        insert into fm_settlements(license_id, created_at, model_amount) values
            ('own', '2026-08-31T14:59:59+00:00', 7000),
            ('own', '2026-08-31T15:00:00+00:00', 8000),
            ('own', '2026-09-30T14:59:59+00:00', 9000),
            ('own', '2026-09-30T15:00:00+00:00', 10000),
            ('other', '2026-09-01T00:00:00+00:00', 999);
        insert into fm_payout_statements values ('m1', '2026-08-01', 6000, 1, 'paid', '2026-09-10', '2026-09-10T00:00:00Z');
    """)
    try:
        items = [dict(item) for item in conn.execute(sql, ("m1", "m1"))]
        assert [item["amount"] for item in items] == [10000, 17000, 6000]
        assert items[1]["count"] == 2
        assert items[2]["status"] == "paid"
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["held", "scheduled"])
def test_admin_upserts_live_totals_and_audits(status, keypair, make_token, monkeypatch):
    freeze_month(monkeypatch)
    updated = row(status=status, paid_at=NOW if status == "paid" else None)
    conn = Conn([{"id": MODEL_ID, "display_name": "모델"}, {"status": "held"}, None, {"amount": 7000, "count": 1}, updated,
                 {**updated, "model_id": MODEL_ID, "model_name": "모델"}])
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).post(f"{ADMIN}/{MODEL_ID}/2026-08/status", json={"status": status, "note": "확인"}, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert (response.json()["paidAt"] is not None) == (status == "paid")
    assert response.json()["modelId"] == MODEL_ID
    assert "for update" in conn.executed[0][0]
    params = next(params for sql, params in conn.executed if sql.startswith("insert into fm_payout_statements"))
    assert params[2:4] == (7000, 1)
    audit = next(params for sql, params in conn.executed if sql.startswith("insert into admin_audit_log"))
    assert audit[:4] == ("user-1", "payout_statement.status", "model", MODEL_ID)
    assert audit[4].obj == {"periodMonth": "2026-08", "status": "held"}
    assert audit[5].obj["status"] == status
    assert conn.events == ["audit", "commit"]


def test_admin_list_is_masked_and_empty_month_is_empty(keypair, make_token, monkeypatch):
    item = row(model_id=MODEL_ID, model_name="모델", bank_code="kb", account_last4="0000", holder_name="예금주")
    conn = Conn([[item], []])
    patch_db(monkeypatch, payout, conn)
    client = client_for(keypair)
    result = client.get(ADMIN, params={"month": "2026-08"}, headers=auth(make_token))
    assert result.status_code == 200
    assert result.json()["items"][0]["accountMasked"] == "***-****-0000"
    assert result.json()["items"][0]["bankName"] == "국민은행"
    assert "accountNumber" not in result.text and "account_number_enc" not in conn.executed[0][0]
    assert client.get(ADMIN, params={"month": "2026-08"}, headers=auth(make_token)).json() == {"viewerId": "user-1", "items": []}


@pytest.mark.parametrize("month", ["2026-13", "2026-8", "invalid", "2026-08-01"])
def test_bad_month_does_not_reach_queries(month, keypair, make_token, monkeypatch):
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    response = client_for(keypair).get(ADMIN, params={"month": month}, headers=auth(make_token))
    assert response.status_code == 400
    assert conn.executed == []


def test_status_requires_admin_existing_model_and_valid_status(keypair, make_token, monkeypatch):
    conn = Conn()
    patch_db(monkeypatch, payout, conn, admin=False)
    client = client_for(keypair)
    path = f"{ADMIN}/{MODEL_ID}/2026-08/status"
    assert client.post(path, json={"status": "paid"}).status_code == 401
    assert client.post(path, json={"status": "paid"}, headers=auth(make_token)).status_code == 403
    patch_db(monkeypatch, payout, conn)
    assert client.post(path, json={"status": "scheduled"}, headers=auth(make_token)).status_code == 404
    assert client.post(path, json={"status": "unknown"}, headers=auth(make_token)).status_code == 400


@pytest.mark.parametrize(("method", "path", "kwargs"), [
    ("get", ADMIN, {"params": {"month": "2026-08"}}),
    (
        "post", f"{ADMIN}/{MODEL_ID}/2026-08/status",
        {"json": {"status": "paid"}},
    ),
])
def test_admin_statement_routes_require_registered_device_in_enforce_mode(
    method, path, kwargs, keypair, make_token, monkeypatch,
):
    conn = Conn()
    patch_db(monkeypatch, payout, conn)
    client = client_for(keypair, admin_device_gate="enforce")

    response = getattr(client, method)(path, headers=auth(make_token), **kwargs)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "device_missing"
    assert conn.executed == []
