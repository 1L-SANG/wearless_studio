"""Opt-in real SQL tests. Only the dedicated disposable local database is accepted."""
import asyncio
import contextlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from psycopg.rows import dict_row

from app import facemarket_payout as p

DSN = os.environ.get("PR280_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires dedicated disposable PR280_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2] / "supabase/migrations"
MODEL = "33333333-3333-3333-3333-333333333333"
LICENSE = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def db(monkeypatch):
    parsed = urlparse(DSN)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 55482 and parsed.path == "/pr280_payout", "refusing non-disposable database"
    schema = "payout_test_" + uuid.uuid4().hex
    options = f"-c search_path={schema},public -c timezone=Asia/Seoul"
    key = Fernet.generate_key().decode()
    account = "000000000123"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"create schema {schema}")
        conn.execute(f"set search_path = {schema}, public")
        conn.execute("""
            create table fm_models(id uuid primary key, user_id text, display_name text, status text default 'verified', created_at timestamptz default now());
            create table fm_licenses(id uuid primary key, model_id uuid references fm_models(id));
            create table fm_settlements(id uuid primary key default gen_random_uuid(), license_id uuid references fm_licenses(id), model_amount bigint not null, created_at timestamptz not null default now());
            create table admin_audit_log(actor_user_id text, action text, target_type text, target_id text, before jsonb, after jsonb, note text);
            create function set_updated_at() returns trigger language plpgsql as $$begin new.updated_at := now(); return new; end;$$;
        """)
        for name in ("20260911140000_fm_payout_accounts.sql", "20260911140100_fm_payout_statements.sql", "20260911170000_fm_manual_payout_confirmations.sql"):
            conn.execute((ROOT / name).read_text().replace("public.", schema + "."))
        conn.execute("insert into fm_models(id,user_id,display_name) values (%s,'model-user','모델')", (MODEL,))
        conn.execute("insert into fm_licenses values (%s,%s)", (LICENSE, MODEL))
        conn.execute("insert into fm_payout_accounts(model_id,bank_code,holder_name,account_number_enc,account_last4) values (%s,'kb','원래 예금주',%s,'0123')", (MODEL, Fernet(key.encode()).encrypt(account.encode()).decode()))

    @contextlib.asynccontextmanager
    async def connection(_request):
        async with await psycopg.AsyncConnection.connect(DSN, options=options, row_factory=dict_row) as conn:
            yield conn

    async def is_admin(_conn, _user):
        return True

    monkeypatch.setattr(p, "get_conn", connection)
    monkeypatch.setattr(p.admin_guard.repo, "is_admin", is_admin)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(fm_payout_account_key=key, admin_device_gate="off"))))

    def sql(query, params=()):
        with psycopg.connect(DSN, options=options, row_factory=dict_row) as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else None

    def add(amount=7000, created="2026-08-20T00:00:00+09:00"):
        return sql("insert into fm_settlements(license_id,model_amount,created_at) values (%s,%s,%s) returning id::text", (LICENSE, amount, created))[0]["id"]

    async def confirm(owner="admin-1", ident=None):
        return await p.confirm_payout_statement(MODEL, "2026-08", p.PayoutConfirmationRequest(confirmation_id=ident or uuid.uuid4()), request, Response(), owner)

    async def action(ident, action, owner="admin-1"):
        return await p.advance_payout_confirmation(ident, action, request, Response(), owner)

    async def listing():
        return await p.list_admin_payout_statements("2026-08", request, Response(), "admin-1")

    yield SimpleNamespace(sql=sql, add=add, confirm=confirm, action=action, listing=listing, request=request, account=account)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"drop schema {schema} cascade")


def test_migration_snapshot_entries_and_extra_payment(db):
    first = db.add(); second = db.add(9000)
    async def scenario():
        confirmation = await db.confirm()
        assert confirmation["amount"] == 16000 and confirmation["count"] == 2
        entries = db.sql("select settlement_id::text, amount from fm_payout_confirmation_entries")
        assert {entry["settlement_id"] for entry in entries} == {first, second}
        assert sum(entry["amount"] for entry in entries) == 16000
        assert (await db.listing())["items"][0]["unpaidAmount"] == 0
        await db.action(confirmation["id"], "start")
        paid = await db.action(confirmation["id"], "paid")
        db.add(11000)
        row = (await db.listing())["items"][0]
        assert row["unpaidAmount"] == 11000 and row["unpaidCount"] == 1
        assert row["confirmations"][0]["amount"] == 16000
        assert await db.action(confirmation["id"], "paid") == paid
        extra = await db.confirm()
        assert extra["amount"] == 11000 and extra["count"] == 1
        assert db.sql("select sum(amount) amount from fm_payout_confirmation_entries where released_at is null")[0]["amount"] == 27000
    asyncio.run(scenario())


def test_two_admins_and_lost_response_share_one_authorization(db):
    db.add()
    async def scenario():
        ident = uuid.uuid4()
        first, second = await asyncio.gather(db.confirm(ident=ident), db.confirm(owner="admin-2"))
        assert first["id"] == second["id"]
        owner = first["responsibleAdmin"]
        assert len(db.sql("select id from fm_payout_confirmations")) == 1
        assert len(db.sql("select settlement_id from fm_payout_confirmation_entries")) == 1
        recovered = await db.confirm(owner=owner)
        assert recovered["id"] == first["id"]
        with pytest.raises(HTTPException) as error:
            await db.action(first["id"], "start", owner="admin-2" if owner == "admin-1" else "admin-1")
        assert error.value.status_code == 403
    asyncio.run(scenario())


def test_account_change_before_start_requires_cancel_but_after_start_preserves_snapshot(db):
    db.add()
    async def scenario():
        original = await db.confirm()
        db.sql("update fm_payout_accounts set holder_name = '변경 예금주' where model_id = %s", (MODEL,))
        with pytest.raises(HTTPException) as error:
            await db.action(original["id"], "start")
        assert error.value.detail["code"] == "payout_account_changed"
        await db.action(original["id"], "cancel")
        assert db.sql("select released_at from fm_payout_confirmation_entries")[0]["released_at"] is not None
        replacement = await db.confirm()
        assert replacement["holderName"] == "변경 예금주"
        await db.action(replacement["id"], "start")
        db.sql("update fm_payout_accounts set holder_name = '다시 변경' where model_id = %s", (MODEL,))
        revealed = await p.reveal_confirmation_account(replacement["id"], db.request, Response(), "admin-1")
        assert revealed["holder_name"] == "변경 예금주" and revealed["account_number"] == db.account
        await db.action(replacement["id"], "paid")
        with pytest.raises(HTTPException):
            await db.action(replacement["id"], "cancel")
        assert all(db.account not in str(row) for row in db.sql("select * from admin_audit_log"))
    asyncio.run(scenario())


def test_stale_status_is_rejected_but_explicit_hold_of_extra_balance_is_allowed(db):
    db.add()
    async def scenario():
        confirmation = await db.confirm()
        await db.action(confirmation["id"], "start")
        await db.action(confirmation["id"], "paid")
        db.add(9000)
        with pytest.raises(HTTPException) as error:
            await p.set_payout_statement_status(MODEL, "2026-08", p.PayoutStatusRequest(status="held"), db.request, Response(), "admin-2")
        assert error.value.detail["code"] == "payout_statement_changed"
        updated = await p.set_payout_statement_status(MODEL, "2026-08", p.PayoutStatusRequest(status="held", expected_confirmation_id=confirmation["id"]), db.request, Response(), "admin-2")
        assert updated["unpaidAmount"] == 9000
        assert updated["confirmations"][0]["amount"] == 7000
        row = (await db.listing())["items"][0]
        assert row["status"] == "held" and row["unpaidAmount"] == 9000
        assert row["confirmations"][0]["status"] == "paid"
        with pytest.raises(HTTPException):
            await db.confirm(owner="admin-2")
    asyncio.run(scenario())


def test_database_rejects_history_rewrite_and_reallocation(db):
    db.add()
    confirmation = asyncio.run(db.confirm())
    with pytest.raises(psycopg.Error):
        db.sql("update fm_payout_confirmations set amount = 1 where id = %s", (confirmation["id"],))
    with pytest.raises(psycopg.Error):
        db.sql("delete from fm_payout_confirmations where id = %s", (confirmation["id"],))
    with pytest.raises(psycopg.Error):
        db.sql("update fm_payout_confirmation_entries set released_at = now()")


def test_held_amount_stays_live_and_kst_boundary_is_exact(db):
    db.add(7000, "2026-07-31T15:00:00Z")
    db.add(999, "2026-07-31T14:59:59Z")
    db.add(888, "2026-08-31T15:00:00Z")
    async def scenario():
        await p.set_payout_statement_status(MODEL, "2026-08", p.PayoutStatusRequest(status="held"), db.request, Response(), "admin-1")
        db.add(9000, "2026-08-31T14:59:59Z")
        row = (await db.listing())["items"][0]
        assert row["amount"] == row["unpaidAmount"] == 16000
        assert row["count"] == 2 and row["status"] == "held"
    asyncio.run(scenario())


def test_model_statement_reads_same_paid_and_unpaid_snapshot(db):
    db.add()
    async def scenario():
        confirmation = await db.confirm()
        await db.action(confirmation["id"], "start")
        await db.action(confirmation["id"], "paid")
        db.add(9000)
        data = await p.get_payout_statements(db.request, Response(), "model-user")
        row = next(item for item in data["items"] if item["periodMonth"] == "2026-08")
        assert row["unpaidAmount"] == 9000 and row["confirmations"][0]["amount"] == 7000
        assert row["confirmations"][0]["responsibleAdmin"] is None
        assert row["confirmations"][0]["canManage"] is False
        assert data["nextPayout"]["amount"] == 9000
    asyncio.run(scenario())


def test_audit_failure_rolls_back_confirmation_and_allocations(db):
    db.add()
    db.sql("""create function reject_audit() returns trigger language plpgsql as $$begin raise exception 'synthetic audit failure'; end;$$;
        create trigger reject_audit before insert on admin_audit_log for each row execute function reject_audit();""")
    with pytest.raises(psycopg.Error):
        asyncio.run(db.confirm())
    assert db.sql("select id from fm_payout_confirmations") == []
    assert db.sql("select settlement_id from fm_payout_confirmation_entries") == []


def test_lost_commit_response_retries_same_confirmed_payment(db, monkeypatch):
    db.add()
    connection = p.get_conn
    class LostResponse:
        def __init__(self, conn): self.conn = conn
        def cursor(self): return self.conn.cursor()
        async def commit(self):
            await self.conn.commit()
            raise RuntimeError("synthetic response loss after commit")
    @contextlib.asynccontextmanager
    async def lose_response(request):
        async with connection(request) as conn:
            yield LostResponse(conn)
    async def scenario():
        ident = uuid.uuid4()
        monkeypatch.setattr(p, "get_conn", lose_response)
        with pytest.raises(RuntimeError):
            await db.confirm(ident=ident)
        monkeypatch.setattr(p, "get_conn", connection)
        original = await db.confirm(ident=ident)
        assert original["id"] == str(ident)
        assert len(db.sql("select id from fm_payout_confirmations")) == 1
        await db.action(str(ident), "start")
        monkeypatch.setattr(p, "get_conn", lose_response)
        with pytest.raises(RuntimeError):
            await db.action(str(ident), "paid")
        timestamp = db.sql("select paid_at from fm_payout_confirmations")[0]["paid_at"]
        monkeypatch.setattr(p, "get_conn", connection)
        retried = await db.action(str(ident), "paid")
        assert retried["paidAt"] == timestamp and retried["amount"] == 7000
    asyncio.run(scenario())


def test_legacy_paid_amount_is_immutable_and_blocks_unknown_allocations(db):
    db.add(16000)
    db.sql("insert into fm_payout_statements(model_id,period_month,amount,count,status,scheduled_for,paid_at) values (%s,'2026-08-01',7000,1,'paid','2026-09-10','2026-09-11T00:00:00Z')", (MODEL,))
    async def scenario():
        row = (await db.listing())["items"][0]
        assert row["legacyPaid"] is True and row["amount"] == 7000 and row["unpaidAmount"] == 9000
        with pytest.raises(HTTPException): await db.confirm()
        with pytest.raises(HTTPException):
            await p.set_payout_statement_status(MODEL, "2026-08", p.PayoutStatusRequest(status="scheduled"), db.request, Response(), "admin-1")
        assert db.sql("select amount from fm_payout_statements")[0]["amount"] == 7000
    asyncio.run(scenario())
