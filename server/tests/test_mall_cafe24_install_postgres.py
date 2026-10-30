"""Opt-in atomic install consumption on an explicitly configured local PostgreSQL."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import mall_connect, repo
from app.services.cafe24_client import Cafe24Client, derive_state_key, make_signed
from test_mall_cafe24_install import BASE, CODE, SCOPES
from test_mall_connect import USER
from test_mall_connect_postgres import mall_pg


@pytest.fixture
def install_pg(mall_pg):
    _, admin, pool = mall_pg
    migration = Path(__file__).resolve().parents[2] / "supabase/migrations/20261027100000_mall_cafe24.sql"
    for statement in migration.read_text().split(";"):
        if "create table if not exists public.mall_install_attempts" in statement:
            admin.execute(statement.replace("public.mall_install_attempts", f"{pool.schema}.mall_install_attempts")
                          .replace("references auth.users(id) on delete cascade", ""))
    return admin, pool


def test_two_callbacks_for_same_attempt_consume_once(install_pg):
    admin, pool = install_pg
    cookie_hash = hashlib.sha256(b"fixture-browser-secret").digest()

    async def scenario():
        async with pool.connection() as setup:
            attempt_id = await repo.create_install_attempt(setup, user_id=USER, mall="cafe24", mall_id="myshop",
                expected_generation="none", return_to=None, cookie_hash=cookie_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
            assert await repo.start_install_attempt(setup, attempt_id=attempt_id, cookie_hash=cookie_hash)
        async with pool.connection() as first, pool.connection() as second:
            winner = await repo.consume_install_attempt(first, attempt_id=attempt_id, cookie_hash=cookie_hash)
            assert winner and winner["user_id"] == USER
            pending = asyncio.create_task(repo.consume_install_attempt(second, attempt_id=attempt_id, cookie_hash=cookie_hash))
            try:
                async def wait_for_row_lock():
                    while not admin.execute("select %s = any(pg_blocking_pids(%s)) as waiting",
                            (first.info.backend_pid, second.info.backend_pid)).fetchone()["waiting"]:
                        if pending.done(): pytest.fail("second consumption did not wait for the first commit")
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_for_row_lock(), timeout=5)
                await first.commit()
                assert await asyncio.wait_for(pending, timeout=5) is None
                await second.commit()
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        row = admin.execute("select consumed_at, saved_at from mall_install_attempts where id = %s", (attempt_id,)).fetchone()
        assert row["consumed_at"] is not None and row["saved_at"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("first_action", ["install", "close_account"])
def test_install_attempt_creation_serializes_with_account_closure(install_pg, mall_pg, monkeypatch, first_action):
    admin, pool = install_pg
    app = mall_pg[0]
    app.state.settings = replace(app.state.settings, cafe24_enabled=True,
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-private")

    async def close(conn):
        await repo.revoke_all_mall_connections(conn, USER)
        await repo.void_install_attempts(conn, user_id=USER)
        await conn.execute("""insert into jobs (user_id, kind, status, payload)
            values (%s, 'personalization_purge', 'pending', '{"reason":"account_delete"}')""", (USER,))

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        create_attempt = repo.create_install_attempt

        async def hold_insert(conn, **kwargs):
            reached.set()
            await release.wait()
            return await create_attempt(conn, **kwargs)

        monkeypatch.setattr(repo, "create_install_attempt", hold_insert)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.test") as client:
            async with pool.connection() as closure:
                if first_action == "close_account":
                    await close(closure)
                install = asyncio.create_task(client.post(BASE + "/install", json={"mallId": "myshop"}))
                pending = None
                try:
                    if first_action == "install":
                        await asyncio.wait_for(reached.wait(), timeout=5)
                        pending = asyncio.create_task(close(closure))
                    async def wait_for_lock():
                        while not admin.execute("""select exists (select 1 from pg_stat_activity
                            where application_name = %s and cardinality(pg_blocking_pids(pid)) > 0) as waiting""",
                            (pool.schema,)).fetchone()["waiting"]:
                            if (pending if first_action == "install" else install).done() or (
                                    first_action == "close_account" and reached.is_set()):
                                pytest.fail("install and account closure did not share the account fence")
                            await asyncio.sleep(0.01)
                    await asyncio.wait_for(wait_for_lock(), timeout=5)
                    if first_action == "install":
                        release.set()
                        assert (await asyncio.wait_for(install, timeout=5)).status_code == 200
                        await asyncio.wait_for(pending, timeout=5)
                        await closure.commit()
                        rows = admin.execute("select voided_at from mall_install_attempts").fetchall()
                        assert len(rows) == 1 and rows[0]["voided_at"] is not None
                    else:
                        await closure.commit()
                        response = await asyncio.wait_for(install, timeout=5)
                        assert response.status_code == 404 and response.json()["error"]["code"] == "account_closed"
                        assert admin.execute("select count(*) as n from mall_install_attempts").fetchone()["n"] == 0
                finally:
                    release.set()
                    tasks = [task for task in (install, pending) if task is not None]
                    for task in tasks:
                        if not task.done(): task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_disconnect_waits_for_install_insert_and_invalidates_returned_attempt(install_pg, mall_pg, monkeypatch):
    admin, pool = install_pg
    app = mall_pg[0]
    app.state.settings = replace(app.state.settings, cafe24_enabled=True,
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-private")
    remote = []

    def respond(request):
        remote.append(request.url.path)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"mall_id": "myshop", "access_token": "fixture-access",
                "refresh_token": "fixture-refresh", "scopes": SCOPES})
        return httpx.Response(200, json={"store": {"shop_name": "테스트 스토어"}})

    monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *args, **kwargs:
        Cafe24Client(*args, **{**kwargs, "transport": httpx.MockTransport(respond)}))

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        create_attempt = repo.create_install_attempt

        async def hold_insert(conn, **kwargs):
            reached.set()
            await release.wait()
            return await create_attempt(conn, **kwargs)

        monkeypatch.setattr(repo, "create_install_attempt", hold_insert)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.test") as client:
            installing = asyncio.create_task(client.post(BASE + "/install", json={"mallId": "myshop"}))
            deleting = None
            try:
                await asyncio.wait_for(reached.wait(), timeout=5)
                deleting = asyncio.create_task(client.delete(BASE))
                async def wait_for_delete_lock():
                    while not admin.execute("""select exists (select 1 from pg_stat_activity
                        where application_name = %s and cardinality(pg_blocking_pids(pid)) > 0) as waiting""",
                        (pool.schema,)).fetchone()["waiting"]:
                        if deleting.done():
                            pytest.fail("disconnect completed while an earlier install was between generation read and insert")
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_for_delete_lock(), timeout=5)
                release.set()
                installed = await asyncio.wait_for(installing, timeout=5)
                assert installed.status_code == 200, installed.text
                assert (await asyncio.wait_for(deleting, timeout=5)).status_code == 204
                attempt = admin.execute("select id::text, voided_at, started_at from mall_install_attempts").fetchone()
                assert attempt["voided_at"] is not None and attempt["started_at"] is None
                started = await client.get(installed.json()["startUrl"], follow_redirects=False)
                assert parse_qs(urlsplit(started.headers["location"]).query)["result"] == ["state_invalid"]
                state = make_signed(derive_state_key(app.state.settings.cafe24_client_secret),
                    {"a": attempt["id"], "e": (datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()},
                    now=datetime.now(timezone.utc).timestamp())
                callback = await client.get(BASE + "/callback", params={"state": state, "code": CODE}, follow_redirects=False)
                assert parse_qs(urlsplit(callback.headers["location"]).query)["result"] == ["state_invalid"]
                assert not remote and admin.execute("select count(*) as n from mall_connections").fetchone()["n"] == 0
            finally:
                release.set()
                tasks = [task for task in (installing, deleting) if task is not None]
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
