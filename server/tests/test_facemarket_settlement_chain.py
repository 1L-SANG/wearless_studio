"""정산 체인 대조(2026-09-26) — 소유 범위, 관리자 가드, eth_call 일치·불일치·실패.

진짜 체인은 부르지 않는다. app.state.fm_chain 을 get_settlement 만 가진 가짜로 바꾼다.
핵심 계약: 체인을 못 읽으면 **실패라고 말한다** — "일치"를 흉내 내는 응답이 없어야 한다.
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import facemarket_settlement_chain as chain_mod
from payout_helpers import Conn, client_for, patch_db

SETTLEMENT_ID = "99999999-9999-4999-8999-999999999999"
PAYMENT_ID = "product:11111111-1111-4111-8111-111111111111:2026-09-20"
MODEL_REF = "0x" + "ab" * 32
NOW = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
CONTRACT = Path(__file__).resolve().parents[2] / "contracts/FaceMarketSettlement.sol"


def db_row(**overrides):
    row = {
        "id": SETTLEMENT_ID, "payment_id": PAYMENT_ID, "tx_hash": "0x" + "cd" * 32,
        "chain_id": "201210", "recorded_block": 4242, "model_ref": MODEL_REF,
        "total_amount": 14900, "model_amount": 10430, "platform_amount": 2980,
        "ops_amount": 1490, "chain_status": "confirmed", "created_at": NOW,
    }
    row.update(overrides)
    return row


def on_chain(**overrides):
    stored = {"model_ref": MODEL_REF, "total": 14900, "model_amount": 10430,
              "platform_amount": 2980, "ops_amount": 1490, "block": 4242, "exists": True}
    stored.update(overrides)
    return stored


class FakeChain:
    chain_id = 201210
    address = "0x" + "12" * 20

    def __init__(self, stored=None, error=None, delay=0.0):
        self.stored, self.error, self.delay, self.calls = stored, error, delay, []

    def get_settlement(self, payment_key):
        self.calls.append(payment_key)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.stored


def client(keypair, monkeypatch, rows, *, chain, admin=True, **settings):
    conn = Conn(rows)
    patch_db(monkeypatch, chain_mod, conn, admin=admin)
    api = client_for(keypair, **settings)
    api.app.state.fm_chain = chain
    return api, conn


def auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


SELLER = f"/v1/facemarket/settlements/{PAYMENT_ID}/chain-check"
MODEL = f"/v1/facemarket/model/settlements/{SETTLEMENT_ID}/chain-check"
ADMIN = f"/v1/facemarket/admin/settlements/{SETTLEMENT_ID}/chain-check"
ADMIN_LIST = "/v1/facemarket/admin/settlements"


# ── 순수 비교 ────────────────────────────────────────────────────────────────

def test_compare_all_fields_equal_is_match():
    result = chain_mod.compare_settlement(db_row(), on_chain(), chain_id=201210)
    assert result["verdict"] == "match" and result["match"] is True
    assert {field["key"] for field in result["fields"]} == {
        "total", "model", "platform", "ops", "block", "modelRef", "chainId"}
    assert all(field["match"] for field in result["fields"])


def test_compare_one_amount_off_is_mismatch_and_names_the_field():
    result = chain_mod.compare_settlement(db_row(model_amount=10431), on_chain(), chain_id=201210)
    assert result["verdict"] == "mismatch" and result["match"] is False
    bad = [field for field in result["fields"] if not field["match"]]
    assert [field["key"] for field in bad] == ["model"]
    assert bad[0]["db"] == 10431 and bad[0]["chain"] == 10430


def test_compare_missing_on_chain_is_not_a_match_even_when_zeroes_line_up():
    empty = {"model_ref": "0x" + "00" * 32, "total": 0, "model_amount": 0, "platform_amount": 0,
             "ops_amount": 0, "block": 0, "exists": False}
    result = chain_mod.compare_settlement(
        db_row(total_amount=0, model_amount=0, platform_amount=0, ops_amount=0, recorded_block=0),
        empty, chain_id=201210)
    assert result["verdict"] == "not_found" and result["match"] is False
    assert not any(field["match"] for field in result["fields"])
    assert all(field["chain"] is None for field in result["fields"] if field["key"] != "chainId")


def test_compare_other_chain_id_is_mismatch():
    result = chain_mod.compare_settlement(db_row(chain_id="1"), on_chain(), chain_id=201210)
    assert result["verdict"] == "mismatch"


def test_contract_rule_lines_are_quoted_verbatim_from_the_contract():
    source = CONTRACT.read_text()
    for line in chain_mod.CONTRACT_RULE_LINES:
        assert line in source, line


# ── 셀러: 영수증 [체인에서 확인] ───────────────────────────────────────────────

def test_seller_owner_gets_live_match(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain())
    api, conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    response = api.get(SELLER, headers=auth(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "match" and body["match"] is True
    assert body["method"] == "eth_call getSettlement"
    assert body["chainId"] == 201210 and body["contractAddress"] == FakeChain.address
    assert body["checkedAt"]
    assert fake.calls == [PAYMENT_ID]
    sql, params = conn.executed[0]
    # 영수증 라우트와 같은 기준 — 잡 소유자(셀러)만.
    assert "j.user_id = %s" in sql and params == (PAYMENT_ID, "user-1")
    assert response.headers["cache-control"] == "no-store"


def test_seller_cannot_read_someone_elses_settlement(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain())
    api, _conn = client(keypair, monkeypatch, [None], chain=fake)
    response = api.get(SELLER, headers=auth(make_token, sub="intruder"))
    assert response.status_code == 404
    assert fake.calls == []  # 남의 결제 id 로는 체인 조회조차 하지 않는다


def test_seller_mismatch_is_reported_as_mismatch(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain(ops_amount=1491))
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    body = api.get(SELLER, headers=auth(make_token)).json()
    assert body["verdict"] == "mismatch" and body["match"] is False


def test_seller_not_recorded_on_chain_is_not_found(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain(exists=False))
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    body = api.get(SELLER, headers=auth(make_token)).json()
    assert body["verdict"] == "not_found" and body["match"] is False


@pytest.mark.parametrize("error", [ConnectionError("rpc down https://secret@rpc"), TimeoutError()])
def test_rpc_failure_is_a_calm_error_not_a_fake_success(error, keypair, make_token, monkeypatch):
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=FakeChain(error=error))
    response = api.get(SELLER, headers=auth(make_token))
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "chain_rpc_failed"
    assert "match" not in body and "verdict" not in body
    assert "secret" not in response.text  # RPC 주소·예외 원문을 흘리지 않는다


def test_rpc_timeout_is_cut_and_reported(keypair, make_token, monkeypatch):
    monkeypatch.setattr(chain_mod, "CHAIN_READ_TIMEOUT", 0.05)
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=FakeChain(on_chain(), delay=0.5))
    response = api.get(SELLER, headers=auth(make_token))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "chain_rpc_failed"


def test_chain_not_configured_says_so(keypair, make_token, monkeypatch):
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=None)
    response = api.get(SELLER, headers=auth(make_token))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "chain_unavailable"


def test_anonymous_cannot_check(keypair, monkeypatch):
    fake = FakeChain(on_chain())
    api, conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    assert api.get(SELLER).status_code == 401
    assert api.get(MODEL).status_code == 401
    assert conn.executed == [] and fake.calls == []


# ── 모델: 정산 내역 [체인 확인] ────────────────────────────────────────────────

def test_model_owner_scope_and_live_match(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain())
    api, conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    response = api.get(MODEL, headers=auth(make_token, sub="model-user"))
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "match"
    sql, params = conn.executed[0]
    assert "m.user_id = %s" in sql and params == (SETTLEMENT_ID, "model-user")


def test_model_cannot_read_other_models_settlement(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain())
    api, _conn = client(keypair, monkeypatch, [None], chain=fake)
    assert api.get(MODEL, headers=auth(make_token)).status_code == 404
    assert fake.calls == []


def test_model_bad_id_is_not_found_without_query(keypair, make_token, monkeypatch):
    api, conn = client(keypair, monkeypatch, [], chain=FakeChain(on_chain()))
    response = api.get("/v1/facemarket/model/settlements/not-a-uuid/chain-check", headers=auth(make_token))
    assert response.status_code == 404
    assert conn.executed == []


# ── 관리자: 체인 검증 화면 ─────────────────────────────────────────────────────

def test_non_admin_cannot_list_or_check(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain())
    api, conn = client(keypair, monkeypatch, [[db_row()], db_row()], chain=fake, admin=False)
    assert api.get(ADMIN_LIST, headers=auth(make_token)).status_code == 403
    assert api.get(ADMIN, headers=auth(make_token)).status_code == 403
    assert not any("fm_settlements" in sql for sql, _ in conn.executed)
    assert fake.calls == []


def test_admin_list_is_latest_first_with_contract_rule(keypair, make_token, monkeypatch):
    rows = [db_row(model_id="m1", model_name="모델", product_name="린넨 셔츠"),
            db_row(id="88888888-8888-4888-8888-888888888888", payment_id="sim:l:abc")]
    totals = {"count": 11, "confirmed_count": 11, "total_amount": 163900, "model_amount": 114730}
    api, conn = client(keypair, monkeypatch, [rows, totals], chain=FakeChain(on_chain()),
                       fm_settlement_address="0x" + "12" * 20, fm_chain_id=201210)
    response = api.get(ADMIN_LIST, headers=auth(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "order by st.created_at desc" in conn.executed[0][0]
    assert [item["kind"] for item in body["items"]] == ["generation", "simulation"]
    assert body["items"][0]["productName"] == "린넨 셔츠"
    assert body["totals"] == {"count": 11, "confirmedCount": 11, "totalAmount": 163900, "modelAmount": 114730}
    contract = body["contract"]
    assert contract["address"] == "0x" + "12" * 20 and contract["chainId"] == 201210
    assert contract["ruleLines"] == list(chain_mod.CONTRACT_RULE_LINES)
    assert "잔돈은 운영에" in contract["ruleSummary"]


def test_admin_check_reads_chain_live(keypair, make_token, monkeypatch):
    fake = FakeChain(on_chain(block=4243))
    api, _conn = client(keypair, monkeypatch, [db_row()], chain=fake)
    body = api.get(ADMIN, headers=auth(make_token)).json()
    assert fake.calls == [PAYMENT_ID]
    assert body["verdict"] == "mismatch"
    block = next(field for field in body["fields"] if field["key"] == "block")
    assert block == {"key": "block", "label": "기록 블록", "db": 4242, "chain": 4243, "match": False}
