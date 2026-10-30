"""HTTP and real repository contracts, with in-memory SQL and httpx transports."""

import asyncio
import contextlib
import copy
import importlib
import importlib.util
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app import client_events, routes
from app.auth import require_user
from app.main import create_app
from conftest import make_settings


USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
KEYS = {"accessKey": "local-fixture-access-12345678", "secretKey": "local-fixture-secret-abcdefgh"}
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def module():
    assert importlib.util.find_spec("app.mall_connect"), "mall router is missing"
    return importlib.import_module("app.mall_connect")


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.result = []
        self.rowcount = 0

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False

    async def execute(self, sql, args=()):
        q = " ".join(sql.lower().split())
        self.store["sql"].append(q)
        self.result = []
        if "pg_advisory_xact_lock" in q:
            return
        if q.startswith("update mall_install_attempts set voided_at"):
            self.rowcount = 0
            for row in self.store.get("installs", {}).values():
                if ((row["user_id"], row["mall"]) == args
                        and row["saved_at"] is None and row["voided_at"] is None):
                    row["voided_at"] = NOW
                    self.rowcount += 1
            return
        if "from jobs" in q and "as closed" in q:
            self.result = [{"closed": args[0] in self.store["closed_accounts"]}]
            return
        if "from personalization_profiles" in q:
            return
        if "client_event_rate_limits" in q:
            if q.startswith("select"):
                self.result = [{"count": self.store["attempts"].get(args[0], 0)}]
            elif q.startswith("insert"):
                self.store["attempts"][args[0]] = self.store["attempts"].get(args[0], 0) + 1
            return
        if q.startswith("insert into mall_connections"):
            assert q.count("public.wl_billing_encrypt") == 2
            user, mall, shop_id, shop_name, hint, ak, kek1, sk, kek2, permissions = args
            assert kek1 == kek2 == "mall-test-kek"
            row = dict(id=str(len(self.store["connections"]) + 1), user_id=user, mall=mall, status="connected",
                       shop_id=shop_id, shop_name=shop_name, key_hint=hint, permissions=permissions,
                       access_key_enc=b"encrypted:" + ak.encode(), secret_key_enc=b"encrypted:" + sk.encode(),
                       connected_at=NOW, last_registered_at=None, revoked_at=None)
            self.store["connections"].append(row)
            self.result = [self.safe(row)]
        elif q.startswith("update mall_connections"):
            user, *malls = args
            self.rowcount = 0
            for row in self.store["connections"]:
                if row["user_id"] == user and (not malls or row["mall"] == malls[0]) and (not malls or row["status"] == "connected"):
                    row.update(status="revoked", revoked_at=NOW, access_key_enc=None, secret_key_enc=None)
                    self.rowcount += 1
        elif "from mall_connections" in q:
            if "wl_billing_decrypt" in q:
                kek1, kek2, user, mall = args
                assert kek1 == kek2 == "mall-test-kek"
            else:
                user, mall = args
            row = next((r for r in self.store["connections"] if r["user_id"] == user and r["mall"] == mall and r["status"] == "connected"), None)
            if row:
                if "wl_billing_decrypt" in q:
                    self.result = [{"access_key": row["access_key_enc"].decode().removeprefix("encrypted:"), "secret_key": row["secret_key_enc"].decode().removeprefix("encrypted:")}]
                else:
                    assert "access_key_enc" not in q and "secret_key_enc" not in q
                    self.result = [self.safe(row)]
        elif q.startswith("insert into mall_defaults"):
            user, mall, shop_id, *values = args
            previous = self.store["defaults"].get((user, mall))
            row = previous or dict(shop_id=shop_id, addresses={}, revision=1)
            if values:
                columns = q.split("insert into mall_defaults (")[1].split(")")[0].split(", ")[3:]
                row.update({key: value.obj if hasattr(value, "obj") else value for key, value in zip(columns, values)})
                row["revision"] = previous["revision"] + 1 if previous else 1
                self.result = [copy.deepcopy(row)]
            elif previous and previous["shop_id"] != shop_id:
                assert "revision = mall_defaults.revision + 1" in q
                row.update(shop_id=shop_id, addresses={}, revision=row["revision"] + 1)
            self.store["defaults"][(user, mall)] = copy.deepcopy(row)
        elif "from mall_defaults" in q:
            row = self.store["defaults"].get(tuple(args))
            self.result = [copy.deepcopy(row)] if row else []
        else:
            raise AssertionError(f"Unsupported mall SQL: {q}")

    @staticmethod
    def safe(row):
        return {k: copy.deepcopy(v) for k, v in row.items() if not k.endswith("_enc")}

    async def fetchone(self): return self.result[0] if self.result else None


class FakeConn:
    def __init__(self, store): self.store = store
    def cursor(self): return FakeCursor(self.store)
    async def commit(self): pass

    @contextlib.asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy(self.store)
        try:
            yield
        except Exception:
            self.store.clear()
            self.store.update(before)
            raise


class FakePool:
    def __init__(self, store): self.store = store
    @contextlib.asynccontextmanager
    async def connection(self): yield FakeConn(self.store)


@pytest.fixture
def env(monkeypatch):
    mall = module()
    store = {"connections": [], "defaults": {}, "attempts": {}, "sql": [], "remote": 0, "closed_accounts": set()}
    async def respond(request):
        store["remote"] += 1
        authorization = request.headers["Authorization"]
        if "access-key=bad" in authorization:
            return httpx.Response(401)
        if "access-key=noperm" in authorization:
            return httpx.Response(200, json={"errors": [{"message": "permission denied"}]})
        if "shop_shipping_address_list" in request.content.decode():
            return httpx.Response(200, json={"data": {"shop_shipping_address_list": {"item_list": [{"id": "a1", "name": "창고", "address": "서울", "address_detail": "", "postcode": "12345", "is_default": True}]}}})
        if pause := store.get("pause_shop"):
            await pause(authorization)
        shop_id = "s2" if "access-key=other-shop" in authorization else "s1"
        return httpx.Response(200, json={"data": {"shop": {"shop_id": shop_id, "shop_name": "데일리 니트 스토어", "allowed_brand_list": [], "site_country_list": []}}})
    client_class = mall.ZigzagClient
    monkeypatch.setattr(mall, "ZigzagClient", lambda *args, **kwargs:
        client_class(*args, **{**kwargs, "transport": httpx.MockTransport(respond)}))
    app = create_app(make_settings(mall_connect_enabled=True, mall_keys_kek="mall-test-kek"))
    app.state.pool = FakePool(store)
    app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app), store


def connect(client, keys=None):
    return client.post("/v1/malls/zigzag/connect", json=keys or KEYS)


def test_connect_success_stores_encrypted_and_returns_hint(env):
    client, store = env
    response = connect(client, {k: f"  {v}  " for k, v in KEYS.items()})
    assert response.status_code == 200
    body = response.json()
    assert body["shopName"] == "데일리 니트 스토어" and body["keyHint"] == "12345678"
    row = store["connections"][0]
    assert row["access_key_enc"] != KEYS["accessKey"].encode()
    assert row["secret_key_enc"] != KEYS["secretKey"].encode()
    assert any("wl_billing_encrypt" in q for q in store["sql"])
    assert body["permissions"] == ["상품 조회와 등록"]


def test_connect_invalid_key_400_with_seller_copy(env):
    client, store = env
    response = connect(client, {**KEYS, "accessKey": "bad-fixture"})
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "invalid_key", "message": module().ERROR_COPY["invalid_key"]}
    assert store["connections"] == []


def test_connect_response_never_contains_keys(env, caplog):
    client, _ = env
    responses = [connect(client), client.get("/v1/malls"),
                 connect(client, {**KEYS, "secretKey": KEYS["secretKey"] * 30}),
                 connect(client, {**KEYS, "accessKey": {"private": KEYS["accessKey"]}})]
    assert [r.status_code for r in responses] == [200, 200, 422, 422]
    for response in responses:
        assert all(value not in response.text for value in KEYS.values())
        assert "access_key_enc" not in response.text and "secret_key_enc" not in response.text
    assert all(value not in caplog.text for value in KEYS.values())


@pytest.mark.parametrize("credentials, hint", [
    ({"accessKey": "short123", "secretKey": "local-fixture-secret"}, None),
    ({"accessKey": "local-fixture-tail1234", "secretKey": "tail1234"}, "tail1234"),
])
def test_key_hint_uses_access_key_suffix_only_when_longer_than_eight(env, credentials, hint):
    client, _ = env
    response = connect(client, credentials)
    assert response.status_code == 200
    assert response.json()["keyHint"] == hint
    assert client.get("/v1/malls").json()["items"][0]["keyHint"] == hint


def test_rotate_keeps_existing_connection_on_failure(env):
    client, store = env
    connect(client)
    before = copy.deepcopy(store["connections"])
    response = client.post("/v1/malls/zigzag/rotate", json={**KEYS, "accessKey": "bad-new-fixture"})
    assert response.status_code == 400
    assert store["connections"] == before
    assert client.get("/v1/malls").json()["items"][0]["status"] == "connected"


def test_rotate_replaces_on_success(env):
    client, store = env
    connect(client)
    response = client.post("/v1/malls/zigzag/rotate", json={**KEYS, "accessKey": "new-fixture-87654321"})
    assert response.status_code == 200 and response.json()["keyHint"] == "87654321"
    assert [r["status"] for r in store["connections"]] == ["revoked", "connected"]


def test_rotate_requires_an_existing_connection(env):
    client, store = env
    response = client.post("/v1/malls/zigzag/rotate", json=KEYS)
    assert response.status_code == 409 and response.json()["error"]["code"] == "connection_changed"
    assert store["connections"] == [] and store["remote"] == 0


@pytest.mark.parametrize("endpoint", ["connect", "rotate"])
def test_connect_after_account_delete_stores_nothing(env, monkeypatch, endpoint):
    client, store = env
    if endpoint == "rotate":
        assert connect(client).status_code == 200

    async def start_purge(conn, user_id, profile, *, reason):
        assert reason == "account_delete" and profile is None
        conn.store["closed_accounts"].add(user_id)
        return "purge-job"

    monkeypatch.setattr(client_events, "delete_user_events", AsyncMock())
    monkeypatch.setattr(routes.personalization, "_start_purge", start_purge)
    monkeypatch.setattr(routes, "_wake_dispatcher", lambda _: None)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(_authorization):
            entered.set()
            await release.wait()

        store["pause_shop"] = pause
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as browser:
            pending = asyncio.create_task(browser.post(f"/v1/malls/zigzag/{endpoint}", json=KEYS))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                assert (await browser.delete("/v1/me/account")).status_code == 202
                defaults = copy.deepcopy(store["defaults"])
            finally:
                release.set()
            response = await asyncio.wait_for(pending, timeout=2)
            assert response.status_code == 404
            assert response.json()["error"] == {
                "code": "account_closed", "message": "계정 삭제가 완료되어 사용할 수 없습니다."}
            assert store["defaults"] == defaults

    asyncio.run(scenario())
    assert len(store["connections"]) == (1 if endpoint == "rotate" else 0)
    assert all(row["status"] == "revoked" and row["access_key_enc"] is None and row["secret_key_enc"] is None
               for row in store["connections"])


@pytest.mark.parametrize("change, expected_hint, pending_key", [
    ("disconnect", None, "stale-fixture-87654321"),
    ("reconnect", "22223333", "stale-fixture-87654321"),
    ("rotate", "22223333", "stale-fixture-87654321"),
    ("disconnect", None, "other-shop-fixture-87654321"),
])
def test_stale_rotation_cannot_restore_or_replace_a_changed_connection(env, change, expected_hint, pending_key):
    client, store = env
    assert connect(client).status_code == 200
    original_id = store["connections"][0]["id"]

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(authorization):
            if f"access-key={pending_key}" in authorization:
                entered.set()
                await release.wait()

        store["pause_shop"] = pause
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as browser:
            pending = asyncio.create_task(browser.post("/v1/malls/zigzag/rotate", json={**KEYS, "accessKey": pending_key}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                if change in {"disconnect", "reconnect"}:
                    assert (await browser.delete("/v1/malls/zigzag")).status_code == 204
                if change in {"reconnect", "rotate"}:
                    endpoint = "connect" if change == "reconnect" else "rotate"
                    response = await browser.post(f"/v1/malls/zigzag/{endpoint}", json={**KEYS, "accessKey": "winner-fixture-22223333"})
                    assert response.status_code == 200
            finally:
                release.set()
            response = await asyncio.wait_for(pending, timeout=2)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "connection_changed"
            current = (await browser.get("/v1/malls")).json()["items"][0]
            assert current["status"] == ("none" if change == "disconnect" else "connected")
            assert current["keyHint"] == expected_hint

    asyncio.run(scenario())
    assert store["connections"][0]["status"] == "revoked"
    assert store["connections"][0]["access_key_enc"] is None
    assert store["connections"][0]["secret_key_enc"] is None
    active = [row for row in store["connections"] if row["status"] == "connected"]
    if change == "disconnect":
        assert active == []
    else:
        assert len(active) == 1 and active[0]["id"] != original_id
        assert active[0]["access_key_enc"] == b"encrypted:winner-fixture-22223333"


def test_connect_to_other_shop_clears_saved_addresses(env):
    client, store = env
    connect(client)
    saved = {"shipping": {"feeType": "FREE", "jejuFee": 0, "isolatedFee": 0},
             "returns": {"partialFee": 3000, "totalFee": 6000, "exchangeFee": 6000},
             "addresses": {"returnAddressId": "a1"},
             "disclosure": {"country": "한국", "manufacturer": "니트", "asManager": "담당", "phone": "02-123-4567"}}
    assert client.put("/v1/malls/zigzag/registration-defaults", json={"baseRevision": 1, **saved}).status_code == 200
    assert client.delete("/v1/malls/zigzag").status_code == 204
    assert connect(client, {**KEYS, "accessKey": "other-shop-fixture-87654321"}).status_code == 200
    defaults = client.get("/v1/malls/zigzag/registration-defaults").json()
    assert defaults["addresses"] == {} and defaults["revision"] == 3
    common = client.get("/v1/malls/zigzag/registration-defaults").json()
    assert all(common[section] == saved[section] for section in ("returns", "disclosure"))
    assert common["shipping"] == {**saved["shipping"], "baseFee": 0}
    assert store["defaults"][(USER, "zigzag")]["shop_id"] == "s2"


def test_rotate_rejects_other_shop_key(env):
    client, store = env
    connect(client)
    before = copy.deepcopy(store)
    response = client.post("/v1/malls/zigzag/rotate", json={**KEYS, "accessKey": "other-shop-fixture-87654321"})
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "shop_mismatch", "message": "다른 스토어의 인증키예요. 스토어를 바꾸려면 연결을 해제한 뒤 다시 연결해 주세요"}
    assert store["connections"] == before["connections"] and store["defaults"] == before["defaults"]


def test_revoke_keeps_defaults(env):
    client, store = env
    connect(client)
    assert client.put("/v1/malls/zigzag/registration-defaults", json={"baseRevision": 1,
        "returns": {"partialFee": 3000, "totalFee": 6000, "exchangeFee": 6000}}).status_code == 200
    assert client.put("/v1/malls/zigzag/registration-defaults", json={"baseRevision": 2,
        "addresses": {"returnAddressId": "a1"}}).status_code == 200
    before = copy.deepcopy(store["defaults"])
    assert client.delete("/v1/malls/zigzag").status_code == 204
    assert store["defaults"] == before
    connect(client)
    assert client.get("/v1/malls/zigzag/registration-defaults").json()["returns"]["totalFee"] == 6000
    assert client.get("/v1/malls/zigzag/registration-defaults").json()["addresses"]["returnAddressId"] == "a1"
    assert store["defaults"] == before


def test_addresses_requires_connection(env):
    client, _ = env
    response = client.get("/v1/malls/zigzag/addresses")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_connected"
    connect(client)
    assert client.get("/v1/malls/zigzag/addresses").json()[0]["id"] == "a1"


def test_defaults_partial_save_tracks_missing_items_and_revision(env):
    client, _ = env
    connect(client)
    path = "/v1/malls/zigzag/registration-defaults"
    first = client.put(path, json={"baseRevision": 1, "shipping": {"feeType": "CONDITIONAL_FREE", "baseFee": 3000}})
    assert first.status_code == 200 and "shipping.conditionalAmount" in first.json()["missingItems"]
    second = client.put(path, json={"baseRevision": 2, "shipping": {"jejuFee": 0, "isolatedFee": 0, "conditionalAmount": 50000}})
    assert second.status_code == 200 and not any(k.startswith("shipping.") for k in second.json()["missingItems"])
    assert second.json()["shipping"]["baseFee"] == 3000 and second.json()["revision"] == 3
    invalid = client.put(path, json={"baseRevision": 3, "returns": {"totalFee": -1}})
    assert invalid.status_code == 400
    conflict = client.put(path, json={"baseRevision": 1, "listingMode": "ON_SALE"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "defaults_conflict"


@pytest.mark.parametrize("mall,patch", [
    ("cafe24", {"shipping": {}}), ("cafe24", {"returns": {}}), ("cafe24", {"addresses": {}}),
    ("cafe24", {"shippingDays": 3}), ("cafe24", {"productOrigin": "imported"}),
    ("cafe24", {"disclosure": {"manufacturer": "factory"}}), ("cafe24", {"unknown": 1}),
    ("zigzag", {"addresses": {"shippingAddressId": "a1"}}), ("ably", {"listingMode": "HIDDEN"}),
])
def test_registration_defaults_reject_fields_outside_mall_allowlist(env, mall, patch):
    client, store = env
    connect(client)
    if mall != "zigzag":
        row = copy.deepcopy(store["connections"][0])
        row.update(mall=mall, shop_id="shop", id="other")
        store["connections"].append(row)
    response = client.put(f"/v1/malls/{mall}/registration-defaults", json={"baseRevision": 0, **patch})
    assert response.status_code == 400, response.text


def test_cafe24_defaults_are_separate_and_do_not_require_shipping_or_returns(env):
    client, store = env
    connect(client)
    row = copy.deepcopy(store["connections"][0]); row.update(mall="cafe24", shop_id="shop", id="cafe")
    store["connections"].append(row)
    path = "/v1/malls/cafe24/registration-defaults"
    result = client.put(path, json={"baseRevision": 0, "taxType": "FREE", "disclosure": {"country": "KR"}, "listingMode": "ON_SALE"})
    assert result.status_code == 200, result.text
    assert result.json() == {"revision": 1, "taxType": "FREE", "disclosure": {"country": "KR"}, "listingMode": "ON_SALE", "missingItems": []}
    assert client.get("/v1/malls/zigzag/registration-defaults").json()["taxType"] == "TAX"
    assert client.get("/v1/malls/sales-defaults").status_code == 404
    assert client.put("/v1/malls/sales-defaults", json={"baseRevision": 0}).status_code == 404


def test_rate_limited(env):
    client, store = env
    assert connect(client).status_code == 200
    for i in range(9):
        endpoint = "connect" if i % 2 == 0 else "rotate"
        assert client.post(f"/v1/malls/zigzag/{endpoint}", json={**KEYS, "accessKey": "bad-fixture"}).status_code == 400
    response = connect(client)
    assert response.status_code == 429 and response.json()["error"]["code"] == "rate_limited"
    assert store["remote"] == 10


def test_routes_absent_when_flag_off():
    app = create_app(make_settings(mall_connect_enabled=False))
    assert not any(getattr(r, "path", "").startswith("/v1/malls") for r in app.routes)
    assert TestClient(app).get("/v1/malls").status_code == 404


def test_connection_is_owner_scoped(env):
    client, _ = env
    connect(client)
    client.app.dependency_overrides[require_user] = lambda: OTHER
    assert client.get("/v1/malls").json()["items"][0]["status"] == "none"
    assert client.get("/v1/malls/zigzag/addresses").status_code == 404
    assert client.delete("/v1/malls/zigzag").status_code == 204
    client.app.dependency_overrides[require_user] = lambda: USER
    assert client.get("/v1/malls").json()["items"][0]["status"] == "connected"


def test_connect_requires_separate_kek(env):
    client, store = env
    client.app.state.settings = make_settings(mall_connect_enabled=True, toss_billing_kek="not-for-malls")
    assert connect(client).status_code == 503
    assert store["connections"] == [] and store["remote"] == 0


def test_mall_routes_require_authentication():
    client = TestClient(create_app(make_settings(mall_connect_enabled=True)))
    assert client.get("/v1/malls").status_code == 401
