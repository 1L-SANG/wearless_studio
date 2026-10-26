"""Opt-in atomic install consumption on an explicitly configured local PostgreSQL."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import repo
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
