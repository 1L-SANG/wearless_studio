"""FM-32 온체인 정산(선택과제2) 라우트/서비스 테스트.

web3/RPC 를 FakeChain 으로, DB 를 FakeConn 으로 대체해 순수 로직만 검증:
분배 미러(70/20/10·canonical=컨트랙트 반환값), 멱등(payment_id 재기록 없음),
소유 스코프, 체인 미설정 graceful(no-op / 404).
"""

import asyncio
import contextlib
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

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

    def wait_for_settlement(self, payment_key, timeout=None):
        return self.get_settlement(payment_key)


class PendingDuplicateChain(FakeChain):
    """다른 recorder의 동일 payment TX가 아직 pending인 상태를 재현한다."""

    def __init__(self):
        super().__init__()
        self.attempts = 0
        self._pending = None

    def record_settlement(self, *, payment_key, model_uuid, total):
        self.attempts += 1
        self._pending = (payment_key, model_uuid, total)
        raise RuntimeError("duplicate transaction still pending")

    def wait_for_settlement(self, payment_key, timeout=None):
        time.sleep(0.05)
        pending_key, model_uuid, total = self._pending
        super().record_settlement(
            payment_key=pending_key, model_uuid=model_uuid, total=total
        )
        return self.get_settlement(payment_key)


class CrossProcessChain(FakeChain):
    """서로 다른 API task가 같은 signer/ledger를 공유하는 상황을 흉내 낸다."""

    def __init__(self, state):
        super().__init__()
        self.state = state

    def record_settlement(self, *, payment_key, model_uuid, total):
        with self.state["lock"]:
            attempt = len(self.state["sent_nonces"])
            nonce = 7 + self.state["confirmed"]
            self.state["sent_nonces"].append(nonce)
        if attempt == 0:
            self.state["second_started"].wait(timeout=0.2)
        else:
            self.state["second_started"].set()
        time.sleep(0.01)
        with self.state["lock"]:
            self.state["confirmed"] += 1
        return super().record_settlement(
            payment_key=payment_key, model_uuid=model_uuid, total=total
        )


class SlowFirstChain(FakeChain):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def record_settlement(self, *, payment_key, model_uuid, total):
        if not self.record_calls:
            self.started.set()
            self.release.wait(timeout=1)
        return super().record_settlement(
            payment_key=payment_key, model_uuid=model_uuid, total=total
        )


class RecoveringChain(FakeChain):
    confirm_timeout = 0.2

    def __init__(self):
        super().__init__()
        self.reconciled = threading.Event()

    def wait_for_settlement(self, payment_key, timeout=None):
        if payment_key == "job:crashed":
            time.sleep(min(timeout or 0.05, 0.05))
            self._store[payment_key] = {
                "model_ref": "0x" + "ef" * 32, "total": 10000,
                "model_amount": 7000, "platform_amount": 2000,
                "ops_amount": 1000, "block": 99, "exists": True,
            }
            self.reconciled.set()
        return self.get_settlement(payment_key)

    def record_settlement(self, *, payment_key, model_uuid, total):
        assert self.reconciled.is_set()
        return super().record_settlement(
            payment_key=payment_key, model_uuid=model_uuid, total=total
        )


class UnresolvedPendingChain(FakeChain):
    confirm_timeout = 0

    def __init__(self):
        super().__init__()
        self.submit_attempts = []

    def record_settlement(self, *, payment_key, model_uuid, total):
        self.submit_attempts.append(payment_key)
        raise RuntimeError("transaction still pending")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.store = conn.store
        self._one = None
        self._many = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        p = params or ()
        if "pg_try_advisory_lock" in s:
            locked = not self.store["signer_locked"]
            if locked:
                self.store["signer_locked"] = True
                self.conn.signer_lock_held = True
            self._one = {"locked": locked}
        elif "pg_advisory_unlock" in s:
            self.conn.release_signer_lock()
            self._one = {"unlocked": True}
        elif "insert into fm_settlement_signer_intents" in s:
            payment_id = p[0]
            if not any(x["payment_id"] == payment_id for x in self.store["intents"]):
                self.store["intents"].append({
                    "payment_id": payment_id, "license_id": p[1], "job_id": p[2],
                    "credit_ledger_id": p[3], "model_id": p[4], "total_amount": p[5],
                    "status": "queued", "attempted_at": None,
                })
            self._one = None
        elif "from fm_settlement_signer_intents" in s and "status = 'broadcasting'" in s:
            self._many = [
                dict(x) for x in self.store["intents"] if x["status"] == "broadcasting"
            ]
        elif s.startswith("update fm_settlement_signer_intents set status = 'broadcasting'"):
            intent = next(x for x in self.store["intents"] if x["payment_id"] == p[0])
            intent.update(status="broadcasting", attempted_at=datetime.now(timezone.utc))
            self._one = None
        elif s.startswith("update fm_settlement_signer_intents set status = %s"):
            intent = next(x for x in self.store["intents"] if x["payment_id"] == p[1])
            intent["status"] = p[0]
            self._one = None
        elif "pg_advisory_xact_lock" in s:
            await self.store["signer_lock"].acquire()
            self.conn.signer_lock_held = True
            self.conn.signer_lock_kind = "transaction"
            self._one = {"pg_advisory_xact_lock": None}
        elif "select role from profiles where user_id" in s:
            self._one = {"role": "admin" if p[0] in self.store["admins"] else "user"}
        elif "insert into public.fm_settlement_simulation_limits" in s:
            keys = (("admin", p[0]), ("ip", p[1]))
            limit = p[2]
            if any(self.store["rate_hits"].get(key, 0) >= limit for key in keys):
                self._one = {"accepted": 0}
            else:
                for key in keys:
                    self.store["rate_hits"][key] = self.store["rate_hits"].get(key, 0) + 1
                self._one = {"accepted": 2}
        elif "from fm_settlements where payment_id" in s:
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
                if self.store.get("insert_conflict"):
                    self.store["insert_conflict"] = False
                    self._one = None  # concurrent request inserted the same payment first
                else:
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
        self.signer_lock_held = False
        self.signer_lock_kind = None

    def cursor(self):
        return FakeCursor(self)

    async def commit(self):
        if self.signer_lock_kind == "transaction":
            self.release_signer_lock()

    async def rollback(self):
        if self.signer_lock_kind == "transaction":
            self.release_signer_lock()

    def release_signer_lock(self):
        if self.signer_lock_held:
            if self.signer_lock_kind == "transaction":
                self.store["signer_lock"].release()
            else:
                self.store["signer_locked"] = False
            self.signer_lock_held = False
            self.signer_lock_kind = None


@contextlib.asynccontextmanager
async def _conn_ctx(store):
    conn = FakeConn(store)
    try:
        yield conn
    finally:
        conn.release_signer_lock()


@pytest.fixture()
def fmset(keypair, monkeypatch):
    """정산 활성 클라이언트 + chain 주입 setter + store."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.pool = _FakePool({
        "settlements": [], "licenses": [], "admins": set(), "insert_conflict": False,
        "rate_hits": {}, "signer_lock": asyncio.Lock(), "signer_locked": False,
        "intents": [],
    })
    store = app.state.pool.store

    monkeypatch.setattr(facemarket, "get_conn", lambda _r: _conn_ctx(store))

    def add_license(license_id, user_id, unit_price=10000, status="active"):
        store["licenses"].append({
            "id": license_id, "model_id": f"model-of-{license_id}",
            "user_id": user_id, "unit_price": unit_price, "status": status})

    return app, TestClient(app), store, add_license


class _FakePool:
    def __init__(self, store, max_size=10):
        self.store = store
        self._slots = asyncio.Semaphore(max_size)

    @contextlib.asynccontextmanager
    async def connection(self):
        await self._slots.acquire()
        self.store["connections_in_use"] = self.store.get("connections_in_use", 0) + 1
        conn = FakeConn(self.store)
        try:
            yield conn
        finally:
            conn.release_signer_lock()
            self.store["connections_in_use"] -= 1
            self._slots.release()


def _uid(make_token):
    # conftest make_token → sub 클레임이 user_id. 헤더+본인 id 동일 소스.
    import jwt as _jwt
    tok = make_token()
    return tok, _jwt.decode(tok, options={"verify_signature": False})["sub"]


def _simulate(client, store, token, user_id, license_id, *, key="test-simulation"):
    store["admins"].add(user_id)
    return client.post(
        "/v1/facemarket/settlements/simulate",
        json={"licenseId": license_id},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
    )


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
    r = _simulate(client, store, tok, uid, "lic-1")
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
    r = _simulate(client, store, tok, uid, "lic-1")
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
    sim = _simulate(client, store, tok, uid, "lic-1")
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
    r = _simulate(client, store, tok, _uid_self, "lic-other")
    assert r.status_code == 404


def test_simulate_revoked_license_400(fmset, make_token):
    app, client, store, add = fmset
    app.state.fm_chain = FakeChain()
    tok, uid = _uid(make_token)
    add("lic-rev", uid, status="revoked")
    r = _simulate(client, store, tok, uid, "lic-rev")
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


def test_record_returns_concurrent_idempotent_winner(fmset):
    import asyncio
    app, _client, store, _add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    store["insert_conflict"] = True

    row = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="job:race", license_id="lic-1", model_id="m", total=10000))

    assert row is not None and row["payment_id"] == "job:race"
    assert len(chain.record_calls) == 1 and len(store["settlements"]) == 1


def test_record_waits_for_concurrent_pending_idempotent_winner(fmset):
    app, _client, store, _add = fmset
    chain = PendingDuplicateChain()
    app.state.fm_chain = chain

    async def record():
        return await facemarket.record_license_settlement(
            app, payment_key="job:pending-race", license_id="lic-1",
            model_id="m", total=10000,
        )

    async def race():
        return await asyncio.gather(record(), record())

    first, second = asyncio.run(race())

    assert first["payment_id"] == second["payment_id"] == "job:pending-race"
    assert chain.attempts == 1 and len(store["settlements"]) == 1


def test_record_uses_shared_signer_lock_across_app_instances():
    store = {
        "settlements": [], "licenses": [], "admins": set(), "insert_conflict": False,
        "rate_hits": {}, "signer_lock": asyncio.Lock(), "signer_locked": False,
        "intents": [],
    }
    pool = _FakePool(store)
    chain_state = {
        "confirmed": 0, "sent_nonces": [], "lock": threading.Lock(),
        "second_started": threading.Event(),
    }
    app_a = SimpleNamespace(state=SimpleNamespace(
        pool=pool, fm_chain=CrossProcessChain(chain_state)
    ))
    app_b = SimpleNamespace(state=SimpleNamespace(
        pool=pool, fm_chain=CrossProcessChain(chain_state)
    ))

    async def race():
        return await asyncio.gather(
            facemarket.record_license_settlement(
                app_a, payment_key="job:a", license_id="lic-a", model_id="m", total=10000
            ),
            facemarket.record_license_settlement(
                app_b, payment_key="job:b", license_id="lic-b", model_id="m", total=10000
            ),
        )

    first, second = asyncio.run(race())

    assert first is not None and second is not None
    assert chain_state["sent_nonces"] == [7, 8]


def test_signer_waiters_do_not_exhaust_three_connection_pool():
    store = {
        "settlements": [], "licenses": [], "admins": set(), "insert_conflict": False,
        "rate_hits": {}, "signer_lock": asyncio.Lock(), "signer_locked": False,
        "intents": [],
    }
    pool = _FakePool(store, max_size=3)
    chain = SlowFirstChain()
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, fm_chain=chain))

    async def scenario():
        owner = asyncio.create_task(facemarket.record_license_settlement(
            app, payment_key="job:owner", license_id="lic-owner",
            model_id="m", total=10000,
        ))
        await asyncio.to_thread(chain.started.wait, 0.2)
        waiters = [asyncio.create_task(facemarket.record_license_settlement(
            app, payment_key=f"job:waiter-{index}", license_id=f"lic-{index}",
            model_id="m", total=10000,
        )) for index in range(3)]
        await asyncio.sleep(0.03)
        async with asyncio.timeout(0.1):
            async with pool.connection():
                pass
        chain.release.set()
        return await asyncio.gather(owner, *waiters)

    rows = asyncio.run(scenario())

    assert all(row is not None for row in rows)


def test_new_submit_reconciles_crashed_broadcast_before_using_signer():
    store = {
        "settlements": [], "licenses": [], "admins": set(), "insert_conflict": False,
        "rate_hits": {}, "signer_lock": asyncio.Lock(), "signer_locked": False,
        "intents": [{
            "payment_id": "job:crashed", "license_id": "lic-crashed", "job_id": None,
            "credit_ledger_id": None, "model_id": "m", "total_amount": 10000,
            "status": "broadcasting", "attempted_at": datetime.now(timezone.utc),
        }],
    }
    chain = RecoveringChain()
    app = SimpleNamespace(state=SimpleNamespace(pool=_FakePool(store), fm_chain=chain))

    row = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="job:new", license_id="lic-new", model_id="m", total=10000,
    ))

    assert row is not None and row["payment_id"] == "job:new"
    assert any(x["payment_id"] == "job:crashed" for x in store["settlements"])
    assert next(x for x in store["intents"] if x["payment_id"] == "job:crashed")["status"] == "confirmed"


def test_unresolved_old_payment_blocks_new_nonce_reuse():
    store = {
        "settlements": [], "licenses": [], "admins": set(), "insert_conflict": False,
        "rate_hits": {}, "signer_lock": asyncio.Lock(), "signer_locked": False,
        "intents": [{
            "payment_id": "job:crashed", "license_id": "lic-crashed", "job_id": None,
            "credit_ledger_id": None, "model_id": "m", "total_amount": 10000,
            "status": "broadcasting", "attempted_at": datetime.now(timezone.utc),
        }],
    }
    chain = UnresolvedPendingChain()
    app = SimpleNamespace(state=SimpleNamespace(pool=_FakePool(store), fm_chain=chain))

    row = asyncio.run(facemarket.record_license_settlement(
        app, payment_key="job:new", license_id="lic-new", model_id="m", total=10000,
    ))

    assert row is None
    assert chain.submit_attempts == ["job:crashed"]
    assert next(x for x in store["intents"] if x["payment_id"] == "job:crashed")["status"] == "broadcasting"
    assert next(x for x in store["intents"] if x["payment_id"] == "job:new")["status"] == "queued"


def test_simulate_non_admin_never_records_transaction(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    add("lic-1", uid)

    r = client.post(
        "/v1/facemarket/settlements/simulate",
        json={"licenseId": "lic-1"},
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": "blocked"},
    )

    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert chain.record_calls == [] and store["settlements"] == []


def test_simulate_requires_idempotency_key(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    store["admins"].add(uid)
    add("lic-1", uid)

    r = client.post(
        "/v1/facemarket/settlements/simulate",
        json={"licenseId": "lic-1"},
        headers={"Authorization": f"Bearer {tok}"},
    )

    assert r.status_code == 422
    assert chain.record_calls == [] and store["settlements"] == []


def test_simulate_same_idempotency_key_returns_same_receipt(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    add("lic-1", uid)

    first = _simulate(client, store, tok, uid, "lic-1", key="same-request")
    second = _simulate(client, store, tok, uid, "lic-1", key="same-request")

    assert first.status_code == second.status_code == 201
    assert first.json()["paymentId"] == second.json()["paymentId"]
    assert len(chain.record_calls) == 1 and len(store["settlements"]) == 1


def test_simulate_rate_limit_returns_429_without_chain_call(fmset, make_token):
    app, client, store, add = fmset
    chain = FakeChain()
    app.state.fm_chain = chain
    tok, uid = _uid(make_token)
    add("lic-1", uid)

    for index in range(5):
        response = _simulate(client, store, tok, uid, "lic-1", key=f"allowed-{index}")
        assert response.status_code == 201

    blocked = _simulate(client, store, tok, uid, "lic-1", key="blocked")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert len(chain.record_calls) == 5 and len(store["settlements"]) == 5
