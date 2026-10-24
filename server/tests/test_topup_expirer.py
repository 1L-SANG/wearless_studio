import asyncio
from contextlib import asynccontextmanager

import pytest

from app import repo
from app.workers.topup_expirer import TopupExpirer


class Cursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    async def execute(self, sql, params=None):
        if "pg_try_advisory_lock" in sql:
            self.row = {"locked": self.state["locked"]}
        elif "pg_advisory_unlock" in sql:
            self.state["unlocked"] += 1
            self.row = None
        else:
            raise AssertionError(sql)

    async def fetchone(self):
        return self.row


class Conn:
    def __init__(self, locked=True):
        self.state = {"locked": locked, "unlocked": 0, "commits": 0, "rollbacks": 0}

    @asynccontextmanager
    async def cursor(self):
        yield Cursor(self.state)

    async def commit(self):
        self.state["commits"] += 1

    async def rollback(self):
        self.state["rollbacks"] += 1


def test_topup_expirer_commits_each_due_user_and_sums_expired(monkeypatch):
    conn = Conn()
    monkeypatch.setattr(repo, "list_due_topup_users",
                        lambda _conn, _limit: _async([{"user_id": "u1"}, {"user_id": "u2"}]))

    async def expire(_conn, *, user_id):
        return {"expired": 30 if user_id == "u1" else 0,
                "available": 0, "deferred": user_id == "u2"}

    monkeypatch.setattr(repo, "expire_due_topup_buckets", expire)
    result = asyncio.run(TopupExpirer(None).tick(conn))
    assert result == {"users": 2, "expired": 30, "deferred": 1}
    assert conn.state["commits"] == 2
    assert conn.state["unlocked"] == 1


def test_topup_expirer_skips_when_another_task_holds_the_lock(monkeypatch):
    conn = Conn(locked=False)
    result = asyncio.run(TopupExpirer(None).tick(conn))
    assert result == {"skipped": "locked"}
    assert conn.state["commits"] == conn.state["unlocked"] == 0


def test_topup_expirer_rolls_back_before_releasing_session_lock(monkeypatch):
    conn = Conn()
    monkeypatch.setattr(repo, "list_due_topup_users",
                        lambda _conn, _limit: _async([{"user_id": "u1"}]))

    async def fail(_conn, *, user_id):
        raise RuntimeError(user_id)

    monkeypatch.setattr(repo, "expire_due_topup_buckets", fail)
    with pytest.raises(RuntimeError, match="u1"):
        asyncio.run(TopupExpirer(None).tick(conn))
    assert conn.state["rollbacks"] == 1
    assert conn.state["unlocked"] == 1


async def _async(value):
    return value
