"""Token lifecycle contracts using real routes, repository SQL and a fake Cafe24."""

import asyncio
import copy
import importlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mall_connect, repo
from app.auth import require_user
from app.main import create_app
from app.services.cafe24_client import (
    AppRemoved, Cafe24Client, ConnectionChanged, MissingPermission, ReconnectRequired,
)
from conftest import assert_query_binds, make_settings
from test_mall_cafe24_install import InstallConn, InstallCursor, SCOPES
from test_mall_connect import USER


ACCESS, REFRESH = "fixture-access-private", "fixture-refresh-private"
NEW_ACCESS, NEW_REFRESH = "fixture-access-rotated", "fixture-refresh-rotated"
CONNECTION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def service():
    assert importlib.util.find_spec("app.services.cafe24_tokens"), "token lifecycle service is missing"
    return importlib.import_module("app.services.cafe24_tokens")


class TokenCursor(InstallCursor):
    async def execute(self, sql, args=()):
        assert_query_binds(sql, args)
        q = " ".join(sql.lower().split())
        self.result, self.rowcount = [], 0
        if q.startswith("update mall_install_attempts set voided_at"):
            assert "saved_at is null" in q and "voided_at is null" in q
            self.store["sql"].append(q)
            for row in self.store["installs"].values():
                if ((row["user_id"], row["mall"]) == args and row["saved_at"] is None
                        and row["voided_at"] is None):
                    row["voided_at"] = datetime.now(timezone.utc)
                    self.rowcount += 1
            return
        if "mall_connections" in q and ("where id = %s" in q or "and id = %s" in q):
            self.store["sql"].append(q)
            if q.startswith("select"):
                if "wl_billing_decrypt" in q:
                    kek1, kek2, user, connection_id = args
                    assert kek1 == kek2 == "mall-test-kek"
                elif "user_id = %s" in q:
                    user, connection_id = args
                else:
                    connection_id, = args
                    user = None
                row = next((r for r in self.store["connections"] if r["id"] == connection_id
                            and r["mall"] == "cafe24" and (user is None or r["user_id"] == user)), None)
                if row and ("status = 'connected'" not in q or row["status"] == "connected"):
                    if "wl_billing_decrypt" in q:
                        def decrypt(value):
                            return value.decode().removeprefix("encrypted:") if value else None
                        self.result = [{**self.safe(row), "access_token": decrypt(row["access_key_enc"]),
                                        "refresh_token": decrypt(row["secret_key_enc"])}]
                    else:
                        self.result = [self.safe(row)]
                return
            if "wl_billing_encrypt" in q:
                if self.store.pop("fail_update", False):
                    raise RuntimeError("fixture database write failed")
                access, kek1, refresh, kek2, expires, refresh_expires, user, connection_id = args
                assert kek1 == kek2 == "mall-test-kek"
                row = next((r for r in self.store["connections"] if r["id"] == connection_id
                            and r["user_id"] == user and r["mall"] == "cafe24" and r["status"] == "connected"), None)
                if row:
                    row.update(access_key_enc=b"encrypted:" + access.encode(),
                               secret_key_enc=b"encrypted:" + refresh.encode(),
                               token_expires_at=expires, refresh_expires_at=refresh_expires)
                    self.rowcount = 1
                return
            assert "token_expires_at = %s" in q and "status = 'connected'" in q
            assert "access_key_enc = null" in q and "secret_key_enc = null" in q
            connection_id, version = args
            row = next((r for r in self.store["connections"] if r["id"] == connection_id
                        and r["mall"] == "cafe24" and r["status"] == "connected"
                        and r["token_expires_at"] == version), None)
            if row:
                row.update(refresh_expires_at=datetime.now(timezone.utc), access_key_enc=None, secret_key_enc=None)
                self.rowcount = 1
            return
        await super().execute(sql, args)


class TokenConn(InstallConn):
    def cursor(self):
        return TokenCursor(self.store)

    async def commit(self):
        if self.store.pop("fail_commit", False):
            raise RuntimeError("fixture database commit failed")
        await super().commit()
        self.rollback_connections = copy.deepcopy(self.store["connections"])
        self.store["committed_connections"] = copy.deepcopy(self.store["connections"])
        self.store["committed_installs"] = copy.deepcopy(self.store["installs"])


class TokenPool:
    def __init__(self, store):
        self.store = store

    @asynccontextmanager
    async def connection(self):
        before = copy.deepcopy(self.store["connections"])
        conn = TokenConn(self.store)
        try:
            yield conn
            await conn.commit()
        except BaseException:
            self.store["connections"] = getattr(conn, "rollback_connections", before)
            raise


@pytest.fixture
def env(monkeypatch):
    now = datetime.now(timezone.utc)
    row = dict(id=CONNECTION, user_id=USER, mall="cafe24", status="connected", shop_id="myshop",
        shop_name="테스트 스토어", key_hint=None, permissions=["상품 조회와 등록"], scopes=SCOPES,
        access_key_enc=b"encrypted:" + ACCESS.encode(), secret_key_enc=b"encrypted:" + REFRESH.encode(),
        token_expires_at=now + timedelta(hours=1), refresh_expires_at=now + timedelta(days=10),
        connected_at=now, created_at=now, last_registered_at=None, revoked_at=None)
    store = dict(connections=[row], defaults={}, attempts={}, sql=[], locks=[], installs={},
                 committed=set(), closed_accounts=set(), remote=[], rotations=0)
    settings = make_settings(mall_connect_enabled=True, cafe24_enabled=True, mall_keys_kek="mall-test-kek",
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-client-private")
    app = create_app(settings)
    app.state.pool = TokenPool(store)
    app.dependency_overrides[require_user] = lambda: USER

    async def respond(request):
        store["remote"].append(request.url.path)
        assert request.url.host == "myshop.cafe24api.com"
        body = parse_qs(request.content.decode())
        if request.url.path.endswith("/oauth/token"):
            assert body["grant_type"] == ["refresh_token"]
            if store.get("rotated") or store.get("invalid_grant"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            assert body["refresh_token"] == [REFRESH]
            store["rotated"] = True
            store["rotations"] += 1
            if store.get("lost_response"):
                store["lost_response"] = False
                raise httpx.ReadError("fixture response lost")
            return httpx.Response(200, json={"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH})
        assert request.url.path.endswith("/oauth/revoke")
        assert body == {"token": [NEW_REFRESH if store.get("rotated") else REFRESH], "token_hint": ["refresh_token"]}
        assert store["committed_connections"][0]["status"] == "revoked"
        if hook := store.get("during_revoke"):
            hook()
        return httpx.Response(503)

    factory = lambda *a, **kw: Cafe24Client(*a, **{**kw, "transport": httpx.MockTransport(respond)})
    monkeypatch.setattr(mall_connect, "Cafe24Client", factory)
    if importlib.util.find_spec("app.services.cafe24_tokens"):
        monkeypatch.setattr(service(), "Cafe24Client", factory)
    return TestClient(app), store


def ensure(client, **kw):
    return asyncio.run(service().ensure_cafe24_access_token(client.app, user_id=USER,
                                                          connection_id=CONNECTION, **kw))


def test_ensure_token_fast_path_without_lock_when_not_expiring(env):
    client, store = env
    assert ensure(client) == (ACCESS, store["connections"][0]["token_expires_at"])
    assert not store["locks"] and not store["remote"]


@pytest.mark.parametrize("seconds", [300, 299, -1])
def test_ensure_token_refreshes_within_five_minutes_and_rotates_once(env, seconds):
    client, store = env
    store["connections"][0]["token_expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    before = datetime.now(timezone.utc)
    token, version = ensure(client)
    after = datetime.now(timezone.utc)
    assert token == NEW_ACCESS
    assert before + timedelta(seconds=7140) <= version <= after + timedelta(seconds=7140)
    row = store["committed_connections"][0]
    assert row["secret_key_enc"] == b"encrypted:" + NEW_REFRESH.encode()
    assert before + timedelta(hours=335) <= row["refresh_expires_at"] <= after + timedelta(hours=335)
    assert ensure(client) == (NEW_ACCESS, version) and store["rotations"] == 1
    assert store["locks"] and set(store["locks"]) == {f"mall:{USER}:cafe24"}


def test_ensure_token_rejected_version_forces_refresh_only_when_still_current(env):
    client, store = env
    version = store["connections"][0]["token_expires_at"]
    token, newer = ensure(client, rejected_token_expires_at=version)
    assert token == NEW_ACCESS and newer != version
    assert ensure(client, rejected_token_expires_at=version) == (NEW_ACCESS, newer)
    assert store["rotations"] == 1


@pytest.mark.parametrize("failure", ["invalid_grant", "fail_update", "fail_commit", "lost_response", "expired_refresh"])
def test_ensure_token_invalid_grant_marks_reconnect_required_and_clears_ciphertext(env, failure):
    client, store = env
    store["connections"][0]["token_expires_at"] = datetime.now(timezone.utc)
    if failure == "expired_refresh":
        store["connections"][0]["refresh_expires_at"] = datetime.now(timezone.utc)
    else:
        store[failure] = True
    if failure in {"fail_update", "fail_commit", "lost_response"}:
        with pytest.raises((RuntimeError, httpx.TransportError)):
            ensure(client)
        assert store["connections"][0]["secret_key_enc"] == b"encrypted:" + REFRESH.encode()
    with pytest.raises(ReconnectRequired):
        ensure(client)
    if failure != "expired_refresh":
        row = store["committed_connections"][0]
        assert row["access_key_enc"] is None and row["secret_key_enc"] is None
    item = next(r for r in client.get("/v1/malls").json()["items"] if r["mall"] == "cafe24")
    assert item["status"] == "reconnect_required"


@pytest.mark.parametrize("change", ["id", "owner", "mall", "revoked", "positional"])
def test_ensure_token_rejects_other_connection_id_and_keyword_only_contract(env, change):
    client, store = env
    row = store["connections"][0]
    if change == "positional":
        with pytest.raises(TypeError):
            asyncio.run(service().ensure_cafe24_access_token(client.app, USER, CONNECTION))
    else:
        row[{"id": "id", "owner": "user_id", "mall": "mall", "revoked": "status"}[change]] = {
            "id": OTHER, "owner": OTHER, "mall": "zigzag", "revoked": "revoked"}[change]
        with pytest.raises(ConnectionChanged):
            ensure(client)
    assert not store["remote"]


def test_mark_reconnect_required_is_noop_when_version_changed(env):
    client, store = env
    version = store["connections"][0]["token_expires_at"]
    async def mark(rejected):
        async with client.app.state.pool.connection() as conn:
            return await service().mark_cafe24_reconnect_required(conn, connection_id=CONNECTION,
                                                                  rejected_token_expires_at=rejected)
    assert not asyncio.run(mark(version - timedelta(seconds=1)))
    assert store["connections"][0]["access_key_enc"] is not None
    assert asyncio.run(mark(version))
    assert store["connections"][0]["secret_key_enc"] is None
    assert asyncio.run(mark(version))  # same version remains persistently invalid


def unauthorized():
    request = httpx.Request("GET", "https://myshop.cafe24api.com/api/v2/admin/store")
    return httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request))


@pytest.mark.parametrize("third_401", [False, True, "rotated_again"])
def test_stale_second_401_does_not_clear_newer_tokens(env, third_401):
    client, store = env
    calls = []
    async def fn(token):
        calls.append(token)
        if len(calls) == 2:
            row = store["connections"][0]
            row.update(token_expires_at=row["token_expires_at"] + timedelta(seconds=10),
                       access_key_enc=b"encrypted:fixture-third-access", secret_key_enc=b"encrypted:fixture-third-refresh")
        if len(calls) == 3 and third_401 == "rotated_again":
            row = store["connections"][0]
            row.update(token_expires_at=row["token_expires_at"] + timedelta(seconds=10),
                       access_key_enc=b"encrypted:fixture-fourth-access", secret_key_enc=b"encrypted:fixture-fourth-refresh")
        if len(calls) <= 2 or third_401:
            raise unauthorized()
        return "ok"
    async def run():
        return await service().call_with_token(client.app, user_id=USER, connection_id=CONNECTION, fn=fn)
    if third_401:
        with pytest.raises(ReconnectRequired): asyncio.run(run())
        assert store["connections"][0]["secret_key_enc"] == (
            b"encrypted:fixture-fourth-refresh" if third_401 == "rotated_again" else None)
    else:
        assert asyncio.run(run()) == "ok"
        assert store["connections"][0]["secret_key_enc"] == b"encrypted:fixture-third-refresh"
    assert calls == [ACCESS, NEW_ACCESS, "fixture-third-access"] and store["rotations"] == 1


@pytest.mark.parametrize("failure", ["401", "removed", "permission"])
def test_call_with_token_retry_budget_and_app_removed(env, failure):
    client, store = env
    calls = []
    async def fn(token):
        calls.append(token)
        if failure == "401": raise unauthorized()
        raise AppRemoved() if failure == "removed" else MissingPermission()
    async def run():
        await service().call_with_token(client.app, user_id=USER, connection_id=CONNECTION, fn=fn)
    expected = {"401": ReconnectRequired, "removed": AppRemoved, "permission": MissingPermission}[failure]
    with pytest.raises(expected): asyncio.run(run())
    assert len(calls) == (2 if failure == "401" else 1)
    assert (store["connections"][0]["secret_key_enc"] is None) == (failure != "permission")


@pytest.mark.parametrize("status", ["connected", "expired", "none", "unavailable"])
def test_list_malls_reports_reconnect_required_and_mall_id(env, status):
    client, store = env
    if status == "expired": store["connections"][0]["refresh_expires_at"] = datetime.now(timezone.utc)
    if status == "none": store["connections"].clear()
    if status == "unavailable": client.app.state.settings = replace(client.app.state.settings, cafe24_enabled=False)
    response = client.get("/v1/malls")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    item = next(r for r in response.json()["items"] if r["mall"] == "cafe24")
    assert item["status"] == {"expired": "reconnect_required"}.get(status, status)
    assert item["mallId"] == (None if status == "none" else "myshop")


def test_list_malls_keeps_items_envelope_and_reports_registration_capability(env):
    client, _ = env
    client.app.state.settings = replace(client.app.state.settings, zigzag_connect_enabled=True)
    body = client.get("/v1/malls").json()
    assert set(body) == {"items"}
    assert body["items"][0] == dict(mall="zigzag", status="none", shopName=None, keyHint=None,
        permissions=[], connectedAt=None, lastRegisteredAt=None, defaultsRevision=0,
        defaultsMissingItems=["shipping.feeType", "shipping.baseFee", "shipping.jejuFee", "shipping.isolatedFee",
            "returns.exchangeFee", "returns.partialFee", "returns.totalFee", "disclosure.asManager", "disclosure.country",
            "disclosure.manufacturer", "disclosure.phone", "disclosure.warrantyText", "addresses.returnAddressId"],
        connectionVersion=None, registration={"status": "preparing"})
    assert {r["mall"] for r in body["items"]} == {"zigzag", "cafe24", "ably", "smartstore"}


def test_disconnect_revokes_locally_first_then_remote_best_effort(env):
    client, store = env
    def reconnect():
        row = copy.deepcopy(store["connections"][0])
        row.update(id=OTHER, status="connected", access_key_enc=b"encrypted:new", secret_key_enc=b"encrypted:new")
        store["connections"].append(row)
    store["during_revoke"] = reconnect
    response = client.delete("/v1/malls/cafe24")
    assert response.status_code == 204 and response.content == b""
    assert store["connections"][0]["secret_key_enc"] is None
    assert store["connections"][1]["status"] == "connected"
    assert store["remote"] == ["/api/v2/oauth/revoke"]
    client.app.dependency_overrides.clear()
    assert client.delete("/v1/malls/cafe24").status_code == 401


@pytest.mark.parametrize("active", [False, True])
def test_disconnect_voids_pending_started_and_consumed_unsaved_attempts(env, active):
    client, store = env
    now = datetime.now(timezone.utc)
    if not active: store["connections"].clear()
    for name in ("pending", "started", "consumed", "saved", "voided", "other"):
        store["installs"][name] = dict(user_id=OTHER if name == "other" else USER, mall="cafe24",
            started_at=now if name != "pending" else None, consumed_at=now if name in {"consumed", "saved"} else None,
            saved_at=now if name == "saved" else None, voided_at=now if name == "voided" else None)
    assert client.delete("/v1/malls/cafe24").status_code == 204
    committed = store["committed_installs"]
    assert all(committed[name]["voided_at"] for name in ("pending", "started", "consumed", "voided"))
    assert committed["saved"]["voided_at"] is None and committed["other"]["voided_at"] is None
    assert client.delete("/v1/malls/cafe24").status_code == 204


def test_responses_and_logs_never_contain_tokens(env, caplog):
    client, store = env
    ensure(client, rejected_token_expires_at=store["connections"][0]["token_expires_at"])
    responses = [client.get("/v1/malls"), client.delete("/v1/malls/cafe24")]
    exposed = caplog.text + "".join(response.text for response in responses)
    assert all(secret not in exposed for secret in (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, "fixture-client-private"))
    assert "access_key_enc" not in exposed and "secret_key_enc" not in exposed
