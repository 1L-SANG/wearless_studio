"""Opt-in real PostgreSQL races, with Cafe24 requests intercepted by MockTransport."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from app import mall_connect, repo
from app.services.cafe24_client import Cafe24Client, make_signed, derive_state_key
from test_mall_cafe24_install_postgres import install_pg
from test_mall_connect_postgres import mall_pg
from test_mall_connect import USER


def configure(app):
    app.state.settings = replace(app.state.settings, cafe24_enabled=True,
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-secret",
        cafe24_redirect_uri="https://api.test/v1/malls/cafe24/callback")


async def attempt(pool):
    cookie_hash = hashlib.sha256(b"fixture-browser").digest()
    async with pool.connection() as conn:
        attempt_id = await repo.create_install_attempt(conn, user_id=USER, mall="cafe24", mall_id="myshop",
            expected_generation="none", return_to=None, cookie_hash=cookie_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        await repo.start_install_attempt(conn, attempt_id=attempt_id, cookie_hash=cookie_hash)
    return attempt_id, cookie_hash


def test_concurrent_expiry_refresh_performs_one_rotation(install_pg, mall_pg, monkeypatch):
    from app.services import cafe24_tokens

    admin, pool = install_pg
    app = mall_pg[0]
    configure(app)
    rotations = []

    async def scenario():
        attempt_id, cookie_hash = await attempt(pool)
        async with pool.connection() as conn:
            await repo.consume_install_attempt(conn, attempt_id=attempt_id, cookie_hash=cookie_hash)
        async with pool.connection() as conn:
            row = await repo.save_cafe24_connection(conn, attempt_id=attempt_id, user_id=USER,
                mall_id="myshop", shop_name="fixture", access_token="old-access", refresh_token="old-refresh",
                token_expires_at=datetime.now(timezone.utc), refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=10),
                scopes=[], permissions=[], kek="mall-test-kek")
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            assert parse_qs(request.content.decode()) == {"grant_type": ["refresh_token"], "refresh_token": ["old-refresh"]}
            rotations.append(request.url.path)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh"})

        monkeypatch.setattr(cafe24_tokens, "Cafe24Client", lambda *a, **kw:
            Cafe24Client(*a, **{**kw, "transport": httpx.MockTransport(respond)}))
        first = asyncio.create_task(cafe24_tokens.ensure_cafe24_access_token(app, user_id=USER, connection_id=row["id"]))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            second = asyncio.create_task(cafe24_tokens.ensure_cafe24_access_token(app, user_id=USER, connection_id=row["id"]))

            async def waiting():
                while not admin.execute("""select exists (
                    select 1 from pg_locks l join pg_stat_activity a on a.pid = l.pid
                    where l.locktype = 'advisory' and not l.granted and a.application_name = %s
                ) as waiting""", (pool.schema,)).fetchone()["waiting"]:
                    if second.done(): raise AssertionError("second refresh did not wait for mall lock")
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(waiting(), timeout=5)
            release.set()
            one, two = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
            assert one == two and one[0] == "new-access"
            assert rotations == ["/api/v2/oauth/token"]
            async with pool.connection() as conn:
                tokens = await repo.get_cafe24_tokens(conn, user_id=USER, connection_id=row["id"], kek="mall-test-kek")
                assert (tokens["access_token"], tokens["refresh_token"]) == ("new-access", "new-refresh")
        finally:
            release.set()
            for task in (first, second):
                if task and not task.done(): task.cancel()
            await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)

    asyncio.run(scenario())


def test_disconnect_without_active_connection_blocks_consumed_callback_in_flight(install_pg, mall_pg, monkeypatch):
    admin, pool = install_pg
    app = mall_pg[0]
    configure(app)

    async def scenario():
        attempt_id, _ = await attempt(pool)
        entered, release = asyncio.Event(), asyncio.Event()
        revoked = []

        async def respond(request):
            if request.url.path.endswith("/oauth/token"):
                # A separate connection must see consumption before token exchange completes.
                assert admin.execute("select consumed_at from mall_install_attempts where id = %s",
                                     (attempt_id,)).fetchone()["consumed_at"] is not None
                entered.set()
                await release.wait()
                return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                    "mall_id": "myshop", "scopes": app.state.settings.cafe24_scopes.split()})
            if request.url.path.endswith("/oauth/revoke"):
                revoked.append(parse_qs(request.content.decode()))
                return httpx.Response(200)
            return httpx.Response(200, json={"store": {"shop_name": "fixture"}})

        monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *a, **kw:
            Cafe24Client(*a, **{**kw, "transport": httpx.MockTransport(respond)}))
        state = make_signed(derive_state_key(app.state.settings.cafe24_client_secret),
            {"a": attempt_id, "e": (datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()},
            now=datetime.now(timezone.utc).timestamp())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.test") as browser:
            browser.cookies.set("wl_cafe24_install", attempt_id + ".fixture-browser")
            callback = asyncio.create_task(browser.get("/v1/malls/cafe24/callback", params={"state": state, "code": "fixture"}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert (await browser.delete("/v1/malls/cafe24")).status_code == 204
                assert admin.execute("select voided_at from mall_install_attempts where id = %s", (attempt_id,)).fetchone()["voided_at"] is not None
                release.set()
                response = await asyncio.wait_for(callback, timeout=5)
                assert parse_qs(urlsplit(response.headers["location"]).query)["result"] == ["connection_changed"]
                assert admin.execute("select count(*) as count from mall_connections").fetchone()["count"] == 0
                assert revoked == [{"token": ["access"], "token_hint": ["access_token"]}]
            finally:
                release.set()
                if not callback.done(): callback.cancel()
                await asyncio.gather(callback, return_exceptions=True)

    asyncio.run(scenario())
