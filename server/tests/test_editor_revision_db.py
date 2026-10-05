"""editor_revision 조건부 저장 — 실제 Postgres 에서 검사한다(2026-10-02).

흉내 DB 로는 `editor_revision = %s` 술어와 `%s::integer is null` 분기를 못 잡는다. 로컬
Supabase DB 에서 한 트랜잭션 안에서 끝내고 되돌린다. CI 는 test-db 잡이 돌린다.
"""

import asyncio
import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app import repo

DB_URL = os.environ.get("EDITOR_REVISION_TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

# server/.env 의 DATABASE_URL 은 운영 DB 를 가리킨 적이 있다 — 이 파일은 로컬 호스트에만 붙는다.
assert urlparse(DB_URL).hostname in ("127.0.0.1", "localhost"), "로컬 DB 에서만 돈다"


def _db_reachable() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


if not _db_reachable():
    if os.environ.get("CI"):
        pytest.fail(f"테스트 DB({DB_URL})에 붙지 못했어요.", pytrace=False)
    pytest.skip("로컬 Supabase DB 가 없어 실제 DB 검사를 건너뛰어요.", allow_module_level=True)


def _run(body):
    async def main():
        conn = await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)
        try:
            user_id = str(uuid.uuid4())
            await conn.execute("insert into auth.users (id) values (%s)", (user_id,))
            cur = await conn.execute(
                "insert into projects (user_id, title) values (%s, '리비전_테스트') returning id::text as id",
                (user_id,))
            project_id = (await cur.fetchone())["id"]
            await body(conn, user_id, project_id)
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(main())


def test_matching_base_saves_and_bumps_revision():
    async def body(conn, user_id, project_id):
        assert await repo.get_editor_state(conn, project_id) == ([], 0)
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "a"}], base_revision=0) == 1
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "b"}], base_revision=1) == 2
        assert await repo.get_editor_state(conn, project_id) == ([{"id": "b"}], 2)

    _run(body)


def test_stale_base_does_not_overwrite():
    async def body(conn, user_id, project_id):
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "tab-a"}], base_revision=0) == 1
        # 탭 B 는 리비전 0 을 보고 있었다 — 탭 A 의 저장을 덮으면 안 된다.
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "tab-b"}], base_revision=0) is None
        assert await repo.get_editor_state(conn, project_id) == ([{"id": "tab-a"}], 1)
        assert await repo.get_editor_revision(conn, project_id) == 1

    _run(body)


def test_missing_base_saves_unconditionally_for_old_clients():
    async def body(conn, user_id, project_id):
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "a"}], base_revision=3) is None
        assert await repo.save_editor_blocks(conn, user_id, project_id, [{"id": "old"}]) == 1
        assert await repo.get_editor_state(conn, project_id) == ([{"id": "old"}], 1)

    _run(body)


def test_other_owner_cannot_save():
    async def body(conn, user_id, project_id):
        assert await repo.save_editor_blocks(conn, str(uuid.uuid4()), project_id, [{"id": "x"}]) is None
        assert await repo.get_editor_state(conn, project_id) == ([], 0)

    _run(body)
