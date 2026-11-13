import asyncio

import pytest

from app import repo


PROJECT_ID = "11111111-1111-1111-1111-111111111111"


class RecordingConnection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        self.calls.append((sql, params))

    async def fetchone(self):
        return next(self.rows)


def test_project_reads_never_select_retired_compose_mode():
    row = {"id": PROJECT_ID, "copywriting": True}
    conn = RecordingConnection([row])
    assert asyncio.run(repo.get_project(conn, "owner", PROJECT_ID)) == row
    assert len(conn.calls) == 1
    assert "compose_mode" not in conn.calls[0][0]
    assert conn.calls[0][1] == (PROJECT_ID, "owner")


@pytest.mark.parametrize("patch", [{"compose_mode": "extended"}, {"composeMode": "extended"}])
def test_retired_only_repository_patch_is_read_only(patch):
    row = {"id": PROJECT_ID, "copywriting": True}
    conn = RecordingConnection([row])
    assert asyncio.run(repo.patch_project(conn, "owner", PROJECT_ID, patch)) == row
    assert len(conn.calls) == 1
    assert conn.calls[0][0].startswith("select ")
    assert "compose_mode" not in conn.calls[0][0]


def test_repository_patch_only_updates_remaining_fields():
    conn = RecordingConnection([{"id": PROJECT_ID, "copywriting": False}])
    asyncio.run(repo.patch_project(conn, "owner", PROJECT_ID, {"compose_mode": "extended", "copywriting": False}))
    sql, params = conn.calls[0]
    assert sql.startswith("update projects set copywriting = %s where")
    assert "compose_mode" not in sql
    assert params == [False, PROJECT_ID, "owner"]


@pytest.mark.parametrize("existing", [False, True])
def test_project_creation_leaves_retired_column_to_database_default(existing):
    row = {"id": PROJECT_ID, "copywriting": True}
    conn = RecordingConnection([row] if existing else [None, row])
    assert asyncio.run(repo.create_project(conn, "owner")) == row
    assert all("compose_mode" not in sql for sql, _ in conn.calls)
    inserts = [sql for sql, _ in conn.calls if sql.startswith("insert into projects")]
    assert len(inserts) == (0 if existing else 1)
    if inserts:
        assert inserts[0].startswith("insert into projects (user_id) values (%s) returning ")
