"""FM-32 온체인 정산(선택과제2) 라우트/서비스 테스트.

web3/RPC 를 FakeChain 으로, DB 를 FakeConn 으로 대체해 순수 로직만 검증:
분배 미러(70/20/10·canonical=컨트랙트 반환값), 멱등(payment_id 재기록 없음),
소유 스코프, 체인 미설정 graceful(no-op / 404).
"""

import contextlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import facemarket
from app.main import create_app
from conftest import make_settings

FIXED_DT = datetime(2026, 7, 11, 12, 0, 0)
_SET_KEYS = (
    "id", "payment_id", "license_id", "job_id", "model_ref", "total_amount",
    "model_amount", "platform_amount", "ops_amount", "chain_status", "tx_hash",
    "chain_id", "recorded_block", "created_at",
)


class FakeChain:
    """컨트랙트 산식 미러(70/20/10, dust→ops). record 호출 카운트로 멱등 검증."""

    def __init__(self):
        self.chain_id = 1337
        self._store = {}
        self.record_calls = []

    def record_settlement(self, *, payment_key, model_uuid, total):
        self.record_calls.append(payment_key)
        ma = total * 7000 // 10000
        pa = total * 2000 // 10000
        oa = total - ma - pa
        self._store[payment_key] = {
            "model_ref": "0x" + "ab" * 32, "total": total, "model_amount": ma,
            "platform_amount": pa, "ops_amount": oa, "block": 100, "exists": True,
        }
        return {
            "tx_hash": "0x" + "cd" * 32, "block": 100, "chain_id": self.chain_id,
            "model_ref": "0x" + "ab" * 32, "model_amount": ma, "platform_amount": pa,
            "ops_amount": oa, "total": total,
        }

    def get_settlement(self, payment_key):
        return self._store.get(
            payment_key,
            {"model_ref": "0x" + "00" * 32, "total": 0, "model_amount": 0,
             "platform_amount": 0, "ops_amount": 0, "block": 0, "exists": False},
        )


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._one = None
        self._many = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        p = params or ()
        if "from fm_settlements where payment_id" in s:
            self._one = next(
                (r for r in self.store["settlements"] if r["payment_id"] == p[0]), None)
        elif s.startswith("insert into fm_settlements"):
            payment_id = p[0]
            if any(r["payment_id"] == payment_id for r in self.store["settlements"]):
                self._one = None  # on conflict do nothing
            else:
                row = dict(zip(
                    ("payment_id", "job_id", "license_id", "credit_ledger_id", "model_ref",
                     "total_amount", "model_amount", "platform_amount", "ops_amount",
                     "tx_hash", "chain_id", "recorded_block"), p))
                row.update(id=f"set-{len(self.store['settlements']) + 1}",
                           chain_status="confirmed", created_at=FIXED_DT)
                self.store["settlements"].append(row)
                self._one = {k: row.get(k) for k in _SET_KEYS}
        elif "from fm_settlements st" in s:  # list
            rows = [r for r in self.store["settlements"]]
            self._many = [{k: r.get(k) for k in _SET_KEYS} for r in rows]
        elif "from fm_licenses l join fm_models m" in s and "l.unit_price" in s:
            lic = next(
                (x for x in self.store["licenses"]
                 if x["id"] == p[0] and x["user_id"] == p[1]), None)
            self._one = (
                {"id": lic["id"], "model_id": lic["model_id"],
                 "unit_price": lic["unit_price"], "status": lic["status"]}
                if lic else None)
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._many or []


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        return None


@contextlib.asynccontextmanager
async def _conn_ctx(store):
    yield FakeConn(store)


@pytest.fixture()
def fmset(keypair, monkeypatch):
    """정산 활성 클라이언트 + chain 주입 setter + store."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.pool = _FakePool({"settlements": [], "licenses": []})
    store = app.state.pool.store

    monkeypatch.setattr(facemarket, "get_conn", lambda _r: _conn_ctx(store))

    def add_license(license_id, user_id, unit_price=10000, status="active"):
        store["licenses"].append({
            "id": license_id, "model_id": f"model-of-{license_id}",
            "user_id": user_id, "unit_price": unit_price, "status": status})

    return app, TestClient(app), store, add_license


class _FakePool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        return _conn_ctx(self.store)


def _uid(make_token):
    # conftest make_token → sub 클레임이 user_id. 헤더+본인 id 동일 소스.
    import jwt as _jwt
    tok = make_token()
    return tok, _jwt.decode(tok, options={"verify_signature": False})["sub"]


# ---- 체인 미설정(graceful) ----

def test_confirm_404_when_chain_unset(fmset, make_token):
    app, client, _s, _add = fmset
    app.state.fm_chain = None
    tok, _ = _uid(make_token)
    r = client.get("/v1/facemarket/settlements/pk/confirm", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "chain_unavailable"


def test_simulate_404_when_chain_unset(fmset, make_token):
    app, client, store, add = fmset
    app.state.fm_chain = None
    tok, uid = _uid(make_token)
    add("lic-1", uid)
    r = client.post("/v1/facemarket/settlements/simulate", json={"licenseId": "lic-1"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "chain_unavailable"


def test_record_service_noop_without_chain(fmset):
    import asyncio
    app, _client, _s, _add = fmset
    app.state.fm_chain = None
    out = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="x", license_id="lic-1", model_id="m", total=10000))
    assert out is None


# ---- 체인 설정(정상 경로) ----

def test_simulate_records_split_70_20_10(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    add("lic-1", uid, unit_price=10000)
    r = client.post("/v1/facemarket/settlements/simulate", json={"licenseId": "lic-1"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["totalAmount"] == 10000
    assert (b["modelAmount"], b["platformAmount"], b["opsAmount"]) == (7000, 2000, 1000)
    assert b["modelAmount"] + b["platformAmount"] + b["opsAmount"] == b["totalAmount"]
    assert b["chainStatus"] == "confirmed" and b["txHash"]
    assert len(store["settlements"]) == 1


def test_confirm_reads_onchain(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    add("lic-1", uid)
    sim = client.post("/v1/facemarket/settlements/simulate", json={"licenseId": "lic-1"},
                      headers={"Authorization": f"Bearer {tok}"})
    pk = sim.json()["paymentId"]
    r = client.get(f"/v1/facemarket/settlements/{pk}/confirm",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["exists"] is True and r.json()["modelAmount"] == 7000


def test_simulate_nonowner_license_404(fmset, make_token):
    app, client, store, add = fmset
    app.state.fm_chain = FakeChain()
    tok, _uid_self = _uid(make_token)
    add("lic-other", "someone-else")  # 남의 라이선스
    r = client.post("/v1/facemarket/settlements/simulate", json={"licenseId": "lic-other"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


def test_simulate_revoked_license_400(fmset, make_token):
    app, client, store, add = fmset
    app.state.fm_chain = FakeChain()
    tok, uid = _uid(make_token)
    add("lic-rev", uid, status="revoked")
    r = client.post("/v1/facemarket/settlements/simulate", json={"licenseId": "lic-rev"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "license_inactive"


def test_record_idempotent_no_double_chain(fmset):
    import asyncio
    app, _client, store, _add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    first = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="job:1", license_id="lic-1", model_id="m", total=10000))
    second = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="job:1", license_id="lic-1", model_id="m", total=10000))
    assert first is not None and second is not None
    # 서비스는 DB row(snake_case) 반환 — 라우트만 CamelModel 로 감싼다.
    assert first["payment_id"] == second["payment_id"] == "job:1"
    assert len(chain.record_calls) == 1  # 2번째는 DB 선확인으로 체인 미호출
    assert len(store["settlements"]) == 1
