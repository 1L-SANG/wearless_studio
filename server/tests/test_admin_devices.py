"""관리자 기기 게이트 — repo·순수 함수·라우트.

설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md
"""
import asyncio
import contextlib

from app import repo


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else None

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows=()):
        self.executed, self.rows, self.commits = [], list(rows), 0

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


# ---------- repo ----------

def test_find_device_by_hash_selects_text_ids_and_status():
    conn = FakeConn([{"id": "d1", "user_id": "u1", "status": "approved", "label": "Mac", "last_seen_at": None}])
    row = asyncio.run(repo.find_admin_device_by_hash(conn, "abc"))
    assert row["id"] == "d1" and row["status"] == "approved"
    sql, params = conn.executed[0]
    assert sql.startswith("select id::text as id, user_id::text as user_id")
    assert "where token_hash = %s" in sql
    assert params == ("abc",)


def test_find_device_by_hash_returns_none_when_missing():
    assert asyncio.run(repo.find_admin_device_by_hash(FakeConn([]), "nope")) is None


def test_touch_device_updates_last_seen_only():
    conn = FakeConn()
    asyncio.run(repo.touch_admin_device(conn, "d1"))
    sql, params = conn.executed[0]
    assert sql == "update admin_devices set last_seen_at = now() where id = %s"
    assert params == ("d1",)
