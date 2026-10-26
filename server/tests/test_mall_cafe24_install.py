"""Cafe24 installation contracts with the real routes/repository and no network."""

import copy
import hashlib
import io
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mall_connect
from app.auth import require_user
from app.log_privacy import protect_log_handler
from app.main import create_app
from app.services.cafe24_client import derive_state_key, make_signed, parse_signed
from conftest import make_settings
from test_mall_connect import FakeConn, FakeCursor, USER


BASE = "/v1/malls/cafe24"
COOKIE = "wl_cafe24_install"
CODE = "fixture-authorization-code"
ACCESS = "fixture-access-token"
REFRESH = "fixture-refresh-token"
SCOPES = ["mall.read_product", "mall.write_product", "mall.read_store", "mall.read_category"]


class InstallCursor(FakeCursor):
    async def execute(self, sql, args=()):
        q = " ".join(sql.lower().split())
        self.result, self.rowcount = [], 0
        now = datetime.now(timezone.utc)
        if "mall_install_attempts" in q:
            self.store["sql"].append(q)
            installs = self.store["installs"]
            if q.startswith("insert"):
                user, mall, mall_id, generation, return_to, cookie_hash, expires_at = args
                attempt_id = str(uuid.uuid4())
                installs[attempt_id] = dict(id=attempt_id, user_id=user, mall=mall, mall_id=mall_id,
                    expected_generation=generation, return_to=return_to, cookie_hash=cookie_hash,
                    expires_at=expires_at, created_at=now, started_at=None, consumed_at=None,
                    saved_at=None, voided_at=None)
                self.result = [{"id": attempt_id}]
            elif q.startswith("delete"):
                assert "created_at <" in q and "interval '1 day'" in q
                for key, row in list(installs.items()):
                    if row["user_id"] == args[0] and row["created_at"] < now - timedelta(days=1):
                        del installs[key]
                        self.rowcount += 1
            elif q.startswith("select"):
                assert "for update" in q and "user_id = %s" in q and "mall = 'cafe24'" in q
                row = installs.get(args[0])
                if row and row["user_id"] == args[1] and row["mall"] == "cafe24":
                    self.result = [copy.deepcopy(row)]
            elif "set saved_at" in q:
                row = installs[args[0]]
                assert row["consumed_at"] and row["saved_at"] is None and row["voided_at"] is None
                row["saved_at"] = now
                self.rowcount = 1
            else:
                assert "cookie_hash = %s" in q and "consumed_at is null" in q
                assert "voided_at is null" in q and "expires_at > now()" in q
                starting = "set started_at" in q
                assert ("started_at is null" if starting else "started_at is not null") in q
                row = installs.get(args[0])
                if (row and row["cookie_hash"] == args[1] and row["consumed_at"] is None
                        and row["voided_at"] is None and row["expires_at"] > now
                        and ((row["started_at"] is None) if starting else (row["started_at"] is not None))):
                    row["started_at" if starting else "consumed_at"] = now
                    self.result, self.rowcount = [copy.deepcopy(row)], 1
            return
        if "from mall_connections" in q and "order by created_at desc, id desc" in q:
            assert "status = 'connected'" not in q
            rows = [r for r in self.store["connections"] if (r["user_id"], r["mall"]) == args]
            self.result = [copy.deepcopy(sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)[0])] if rows else []
            return
        if q.startswith("insert into mall_connections") and "token_expires_at" in q:
            assert q.count("public.wl_billing_encrypt") == 2
            user, mall_id, name, access, kek1, refresh, kek2, expires, refresh_expires, scopes, permissions = args
            assert kek1 == kek2 == "mall-test-kek"
            row = dict(id=str(uuid.uuid4()), user_id=user, mall="cafe24", status="connected", shop_id=mall_id,
                shop_name=name, key_hint=None, access_key_enc=b"encrypted:" + access.encode(),
                secret_key_enc=b"encrypted:" + refresh.encode(), token_expires_at=expires,
                refresh_expires_at=refresh_expires, scopes=scopes, permissions=permissions,
                connected_at=now, created_at=now, last_registered_at=None, revoked_at=None)
            self.store["connections"].append(row)
            self.result = [self.safe(row)]
            return
        if "pg_advisory_xact_lock" in q:
            self.store["locks"].append(args[0])
        await super().execute(sql, args)


class InstallConn(FakeConn):
    def cursor(self):
        return InstallCursor(self.store)

    async def commit(self):
        self.store["committed"] = {key for key, row in self.store["installs"].items() if row["consumed_at"]}


class InstallPool:
    def __init__(self, store):
        self.store = store

    @asynccontextmanager
    async def connection(self):
        conn = InstallConn(self.store)
        yield conn
        await conn.commit()


@pytest.fixture
def env(monkeypatch):
    store = dict(connections=[], defaults={}, attempts={}, sql=[], closed_accounts=set(),
                 installs={}, committed=set(), locks=[], remote=[], token_status=200, store_status=200)
    settings = make_settings(mall_connect_enabled=True, cafe24_enabled=True, mall_keys_kek="mall-test-kek",
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-client-secret",
        cafe24_redirect_uri="https://api.test/v1/malls/cafe24/callback", public_web_origin="https://web.test")
    app = create_app(settings)
    app.state.pool = InstallPool(store)
    app.dependency_overrides[require_user] = lambda: USER

    async def respond(request):
        path = request.url.path
        store["remote"].append(path)
        assert request.url.host == "myshop.cafe24api.com"
        if path.endswith("/oauth/token"):
            attempt = next(r for r in store["installs"].values() if r["consumed_at"])
            assert attempt["id"] in store["committed"], "consumption must commit before exchange"
            assert parse_qs(request.content.decode())["code"] == [CODE]
            if hook := store.get("on_exchange"):
                await hook()
            return httpx.Response(store["token_status"], json=store.get("token_payload", {
                "mall_id": "myshop", "access_token": ACCESS, "refresh_token": REFRESH,
                "scopes": SCOPES, "expires_at": "2099-01-01T00:00:00", "refresh_token_expires_at": "2099-02-01T00:00:00"}))
        if path.endswith("/oauth/revoke"):
            assert parse_qs(request.content.decode()) == {"token": [ACCESS], "token_hint": ["access_token"]}
            return httpx.Response(store.get("revoke_status", 200))
        assert path.endswith("/admin/store") and request.headers["Authorization"] == f"Bearer {ACCESS}"
        return httpx.Response(store["store_status"], json={"store": {"shop_name": "테스트 스토어"}})

    cls = mall_connect.Cafe24Client if hasattr(mall_connect, "Cafe24Client") else None
    # Patch the I/O boundary only; the real client still serializes every request.
    from app.services.cafe24_client import Cafe24Client
    monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *a, **kw:
        (cls or Cafe24Client)(*a, **{**kw, "transport": httpx.MockTransport(respond)}), raising=False)
    return TestClient(app, base_url="https://api.test"), store


def install(client, **body):
    response = client.post(BASE + "/install", json={"mallId": "myshop", **body})
    assert response.status_code == 200
    return response


def start(client):
    response = install(client)
    started = client.get(response.json()["startUrl"], follow_redirects=False)
    assert started.status_code == 302
    assert urlsplit(started.headers["location"]).hostname == "myshop.cafe24api.com"
    return parse_qs(urlsplit(started.headers["location"]).query)["state"][0]


def callback(client, state, **query):
    return client.get(BASE + "/callback", params={"state": state, "code": CODE, **query}, follow_redirects=False)


def result(response, expected):
    assert response.status_code == 302
    target = urlsplit(response.headers["location"])
    assert (target.scheme, target.netloc, target.path) == ("https", "web.test", "/settings/malls")
    assert parse_qs(target.query)["result"] == [expected]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_install_creates_attempt_with_cookie_hash_and_generation_and_sets_httponly_cookie(env):
    client, store = env
    client.app.dependency_overrides.clear()
    assert client.post(BASE + "/install", json={"mallId": "myshop"}).status_code == 401
    client.app.dependency_overrides[require_user] = lambda: USER
    old_id = str(uuid.uuid4())
    store["connections"].append(dict(id=old_id, user_id=USER, mall="cafe24", status="revoked", created_at=datetime.now(timezone.utc)))
    response = install(client, mallId=" HTTPS://MyShop.cafe24.com/admin ", returnTo="/editor/project")
    attempt_id, secret = client.cookies[COOKIE].split(".", 1)
    row = store["installs"][attempt_id]
    assert row["mall_id"] == "myshop" and row["user_id"] == USER
    assert row["expected_generation"] == old_id + ":revoked"
    assert row["cookie_hash"] == hashlib.sha256(secret.encode()).digest()
    assert row["return_to"] == "/editor/project" and 595 < (row["expires_at"] - row["created_at"]).total_seconds() <= 600
    cookie = response.headers["set-cookie"]
    assert all(v in cookie for v in ("Secure", "HttpOnly", "SameSite=lax", "Path=/v1/malls/cafe24", "Max-Age=600"))
    assert "domain=" not in cookie.lower() and response.headers["cache-control"] == "no-store"
    row["created_at"] -= timedelta(days=2)
    other_id = str(uuid.uuid4())
    store["installs"][other_id] = {**row, "id": other_id, "user_id": "22222222-2222-4222-8222-222222222222"}
    install(client)
    assert attempt_id not in store["installs"] and other_id in store["installs"]
    store["attempts"][f"mall-connect:{USER}"] = 10
    assert client.post(BASE + "/install", json={"mallId": "myshop"}).status_code == 429


def test_install_response_body_has_no_secret(env):
    client, store = env
    response = install(client)
    attempt_id, secret = client.cookies[COOKIE].split(".", 1)
    assert response.json() == {"startUrl": f"https://api.test{BASE}/install/{attempt_id}/start"}
    assert secret not in response.text and "state" not in response.text
    assert store["installs"][attempt_id]["expected_generation"] == "none"


@pytest.mark.parametrize("mall_id, expected", [("https://evil.test", "invalid_mall_id"), ("myshop", "cafe24_not_allowed")])
def test_install_rejects_bad_mall_id_and_not_allowed_mall(env, mall_id, expected):
    client, store = env
    client.app.state.settings = replace(client.app.state.settings, cafe24_allowed_mall_ids=frozenset({"other-shop"}))
    response = client.post(BASE + "/install", json={"mallId": mall_id})
    assert response.status_code == (400 if expected == "invalid_mall_id" else 403)
    assert response.json()["error"]["code"] == expected and store["installs"] == {}


@pytest.mark.parametrize("field", ["cafe24_enabled", "cafe24_client_id", "cafe24_client_secret", "mall_keys_kek"])
def test_install_503_when_unconfigured(env, field):
    client, store = env
    client.app.state.settings = replace(client.app.state.settings, **{field: None})
    response = client.post(BASE + "/install", json={"mallId": "myshop"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "cafe24_unconfigured"
    assert not store["installs"] and not store["remote"]


@pytest.mark.parametrize("invalid", ["missing", "wrong_id", "wrong_hash", "expired", "voided", "consumed", "malformed", "expires_after_start"])
def test_start_requires_matching_cookie_and_marks_started_once(env, monkeypatch, invalid):
    client, store = env
    url = install(client).json()["startUrl"]
    attempt = next(iter(store["installs"].values()))
    if invalid == "expires_after_start":
        class AfterExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                return attempt["expires_at"] + timedelta(seconds=1)
        monkeypatch.setattr(mall_connect, "datetime", AfterExpiry)
    elif invalid == "missing": client.cookies.clear()
    elif invalid in {"wrong_id", "wrong_hash", "malformed"}:
        client.cookies.clear()
        client.cookies.set(COOKIE, "invalid" if invalid == "malformed" else
            f"{uuid.uuid4() if invalid == 'wrong_id' else attempt['id']}.{secrets.token_urlsafe(32)}")
    elif invalid == "expired": attempt["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    else: attempt["voided_at" if invalid == "voided" else "consumed_at"] = datetime.now(timezone.utc)
    result(client.get(url, follow_redirects=False), "state_invalid")
    assert (attempt["started_at"] is not None) == (invalid == "expires_after_start")
    assert not store["remote"]


def test_start_does_not_issue_cookie(env):
    client, _ = env
    response = client.get(install(client).json()["startUrl"], follow_redirects=False)
    assert response.status_code == 302 and "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    payload = parse_signed(derive_state_key("fixture-client-secret"), state, now=datetime.now(timezone.utc).timestamp())
    assert set(payload) == {"a", "e"} and payload["a"] == client.cookies[COOKIE].split(".")[0]


def test_forwarded_start_url_cannot_bind_victim_browser_to_attacker_account(env):
    attacker, store = env
    url = install(attacker).json()["startUrl"]
    victim = TestClient(attacker.app, base_url="https://api.test")
    victim.app.dependency_overrides.clear()
    result(victim.get(url, follow_redirects=False), "state_invalid")
    assert COOKIE not in victim.cookies and not store["remote"] and not store["connections"]


def test_start_second_visit_is_state_invalid(env):
    client, store = env
    url = install(client).json()["startUrl"]
    assert urlsplit(client.get(url, follow_redirects=False).headers["location"]).hostname == "myshop.cafe24api.com"
    assert next(iter(store["installs"].values()))["started_at"] is not None
    result(client.get(url, follow_redirects=False), "state_invalid")


@pytest.mark.parametrize("invalid", ["cookie", "tampered", "expired", "unstarted", "voided", "row_expired", "bad_attempt", "missing_state"])
def test_callback_without_cookie_makes_no_external_call(env, invalid):
    client, store = env
    state = start(client)
    row = next(iter(store["installs"].values()))
    if invalid == "cookie": client.cookies.clear()
    elif invalid == "tampered": state += "x"
    elif invalid in {"expired", "bad_attempt"}:
        state = make_signed(derive_state_key("fixture-client-secret"),
            {"a": "invalid" if invalid == "bad_attempt" else row["id"], "e": 100 if invalid == "expired" else 4102444800}, now=0)
    elif invalid == "unstarted": row["started_at"] = None
    elif invalid == "voided": row["voided_at"] = datetime.now(timezone.utc)
    elif invalid == "row_expired": row["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    else: state = ""
    result(callback(client, state), "state_invalid")
    assert not store["remote"] and row["consumed_at"] is None


@pytest.mark.parametrize("same_attempt", [True, False])
def test_callback_with_other_browser_cookie_is_state_invalid(env, same_attempt):
    client, store = env
    state = start(client)
    attempt_id = client.cookies[COOKIE].split(".")[0] if same_attempt else str(uuid.uuid4())
    client.cookies.clear()
    client.cookies.set(COOKIE, f"{attempt_id}.{secrets.token_urlsafe(32)}")
    result(callback(client, state), "state_invalid")
    assert not store["remote"]


@pytest.mark.parametrize("token_status", [200, 500])
def test_callback_commits_consumption_before_token_exchange(env, token_status):
    client, store = env
    state = start(client)
    store["token_status"] = token_status
    cookie = client.cookies[COOKIE]
    client.app.dependency_overrides.clear()  # Callback must work without a login session.
    result(callback(client, state), "connected" if token_status == 200 else "token_exchange_failed")
    assert store["committed"] and store["remote"][0].endswith("/oauth/token")
    remote = list(store["remote"])
    client.cookies.clear()
    client.cookies.set(COOKIE, cookie)
    result(callback(client, state), "state_invalid")
    assert store["remote"] == remote


def test_callback_access_denied_redirects_cancelled(env):
    client, store = env
    result(callback(client, start(client), error="access_denied"), "cancelled")
    assert next(iter(store["installs"].values()))["consumed_at"] is not None and not store["remote"]


def test_callback_mall_mismatch_revokes_and_stores_nothing(env):
    client, store = env
    store["token_payload"] = dict(mall_id="other-shop", access_token=ACCESS, refresh_token=REFRESH, scopes=SCOPES)
    store["revoke_status"] = 500  # Revocation is best effort and must not leak upstream errors.
    result(callback(client, start(client)), "mall_mismatch")
    assert store["remote"] == ["/api/v2/oauth/token", "/api/v2/oauth/revoke"] and not store["connections"]


@pytest.mark.parametrize("failure", ["missing_permission", "not_allowed", "malformed_scopes"])
def test_callback_missing_scope_or_not_allowed_revokes_and_redirects(env, failure):
    client, store = env
    state = start(client)
    if failure == "not_allowed": client.app.state.settings = replace(client.app.state.settings, cafe24_allowed_mall_ids=frozenset({"other-shop"}))
    else: store["token_payload"] = dict(mall_id="myshop", access_token=ACCESS, refresh_token=REFRESH,
                                         scopes="mall.write_product" if failure == "malformed_scopes" else SCOPES[:-1])
    result(callback(client, state), "not_allowed" if failure == "not_allowed" else "missing_permission")
    assert store["remote"][-1].endswith("/oauth/revoke") and not store["connections"]


@pytest.mark.parametrize("store_status", [200, 500])
def test_callback_stores_encrypted_tokens_expiries_scopes_and_marks_saved(env, caplog, store_status):
    client, store = env
    # Production redacts formatted output, including third-party request logs.
    protect_log_handler(caplog.handler)
    store["store_status"] = store_status
    state = start(client)
    now = datetime.now(timezone.utc)
    response = callback(client, state)
    result(response, "connected")
    row = store["connections"][0]
    assert row["access_key_enc"] == b"encrypted:" + ACCESS.encode() and row["secret_key_enc"] == b"encrypted:" + REFRESH.encode()
    assert row["shop_id"] == "myshop" and row["shop_name"] == ("테스트 스토어" if store_status == 200 else "myshop")
    assert 7139 <= (row["token_expires_at"] - now).total_seconds() <= 7145
    assert 1205999 <= (row["refresh_expires_at"] - now).total_seconds() <= 1206005
    assert row["scopes"] == SCOPES and row["permissions"] == ["상품 조회와 등록", "쇼핑몰 정보 조회", "상품 분류 조회"]
    assert next(iter(store["installs"].values()))["saved_at"] is not None
    locks = store["locks"]
    assert locks.index(f"mall-account:{USER}") < locks.index(f"mall:{USER}:cafe24") < locks.index(f"mall-defaults:{USER}:cafe24")
    public = mall_connect._connection_result(row, "cafe24")
    assert public["mallId"] == "myshop" and public["mall"] == "cafe24"
    assert all(value not in response.text + response.headers["location"] + caplog.text + str(public)
               for value in (ACCESS, REFRESH, CODE, state, "fixture-client-secret"))
    assert "Max-Age=0" in response.headers["set-cookie"] and COOKIE not in client.cookies


@pytest.mark.parametrize("when", ["install", "exchange"])
def test_callback_after_account_delete_stores_nothing(env, when):
    client, store = env
    if when == "install":
        store["closed_accounts"].add(USER)
        response = client.post(BASE + "/install", json={"mallId": "myshop"})
        assert response.status_code == 404 and response.json()["error"]["code"] == "account_closed"
        assert not store["installs"]
    else:
        state = start(client)
        store["closed_accounts"].add(USER)
        result(callback(client, state), "account_closed")
        assert store["remote"][-1].endswith("/oauth/revoke")
    assert not store["connections"]


@pytest.mark.parametrize("change", ["generation", "voided", "saved", "unconsumed", "owner"])
def test_callback_generation_changed_by_other_window_stores_nothing(env, change):
    client, store = env
    state = start(client)
    async def changed():
        row = next(iter(store["installs"].values()))
        if change == "generation":
            store["connections"].append(dict(id=str(uuid.uuid4()), user_id=USER, mall="cafe24", status="revoked", created_at=datetime.now(timezone.utc)))
        elif change == "owner": row["user_id"] = "22222222-2222-4222-8222-222222222222"
        else: row[{"voided": "voided_at", "saved": "saved_at", "unconsumed": "consumed_at"}[change]] = None if change == "unconsumed" else datetime.now(timezone.utc)
    store["on_exchange"] = changed
    result(callback(client, state), "connection_changed")
    assert not any(row["status"] == "connected" for row in store["connections"])
    assert not store["defaults"] and store["remote"][-1].endswith("/oauth/revoke")


@pytest.mark.parametrize("return_to, accepted", [("https://evil.test/", False), ("//evil.test", False), ("/editor/project?x=1", True)])
def test_callback_redirect_never_leaves_public_origin_and_sets_no_store(env, return_to, accepted):
    client, _ = env
    started = client.get(install(client, returnTo=return_to).json()["startUrl"], follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    response = callback(client, state)
    result(response, "connected")
    assert parse_qs(urlsplit(response.headers["location"]).query).get("returnTo") == ([return_to] if accepted else None)


def test_access_log_redacts_callback_and_start_query():
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    protect_log_handler(handler)
    logger = logging.getLogger("test.cafe24.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for path in (BASE + "/callback", BASE + f"/install/{uuid.uuid4()}/start"):
            logger.info('GET %s?code=%s&state=%s HTTP/1.1', path, CODE, "fixture-state")
        logger.info('GET /v1/malls?test=visible HTTP/1.1')
    finally:
        logger.removeHandler(handler)
    text = output.getvalue()
    assert CODE not in text and "fixture-state" not in text and text.count("[redacted]") == 2
    assert "/v1/malls?test=visible" in text
