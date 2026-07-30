"""D축 기준 컷 선택 SQL 계약 — 로컬 DB 가 있을 때만 실행.

`list_series_reference_cuts` 는 순수 SQL 이라 FakeConn 으로는 의미가 없다(문법·`distinct on`
동작·JSON 필터가 전부 DB 안에서 일어난다). 로컬 54322 가 없으면 스킵한다.

잠그는 계약 둘:
- candidate 별 **최신 1장**, limit 개 (전 버전을 끌어오면 재생성 이력에 비례해 비용이 는다)
- `outcome='regenerate'` 버전은 **기준에서 제외** (실패본을 앵커로 삼으면 오류가 전파된다)
"""
import asyncio
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import repo

LOCAL_DB = os.getenv(
    "TEST_LOCAL_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _db_available() -> bool:
    try:
        with psycopg.connect(LOCAL_DB, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("select to_regclass('public.mannequin_cuts')")
            return cur.fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="로컬 54322 없음 — SQL 계약 테스트 스킵")


async def _seed(conn, project_id, user_id, rows):
    """(candidate, version, outcome) 목록으로 컷을 만든다. asset 은 최소 필드만."""
    from psycopg.types.json import Json
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into projects (id, user_id) values (%s, %s)", (project_id, user_id))
        await cur.execute(
            "insert into products (project_id) values (%s)", (project_id,))
        for candidate, version, outcome in rows:
            asset_id = str(uuid.uuid4())
            await cur.execute(
                "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
                "r2_key, mime_type) values (%s, %s, %s, 'ai', 'private', 'b', %s, 'image/png')",
                (asset_id, user_id, project_id, f"k/{asset_id}"))
            await cur.execute(
                "insert into mannequin_cuts (project_id, candidate, version, asset_id, base_fit, "
                "qc_scores) values (%s, %s, %s, %s, 'regular', %s)",
                (project_id, candidate, version, asset_id,
                 Json({"outcome": outcome}) if outcome else None))


def _run(rows, *, limit=3):
    project_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())

    async def main():
        conn = await psycopg.AsyncConnection.connect(LOCAL_DB, row_factory=dict_row)
        async with conn:
            await conn.execute(
                "insert into auth.users (id, email) values (%s, %s) on conflict do nothing",
                (user_id, f"{user_id}@test.local"))
            await _seed(conn, project_id, user_id, rows)
            out = await repo.list_series_reference_cuts(conn, project_id, limit=limit)
            await conn.rollback()   # 테스트 데이터는 남기지 않는다
            return [(r["candidate"], r["version"]) for r in out]

    return asyncio.run(main())


def test_picks_latest_version_per_candidate():
    assert _run([("A", 1, None), ("A", 3, None), ("A", 2, None)]) == [("A", 3)]


def test_excludes_regenerate_verdict_versions():
    """실패본이 다음 생성의 앵커가 되면 오류가 전파된다 — 최신이어도 제외하고 폴백."""
    assert _run([("A", 1, "auto_pass"), ("A", 2, "regenerate")]) == [("A", 1)]


def test_keeps_unjudged_legacy_rows():
    """판정 없는 구 행(qc_scores null)은 포함한다 — 배제하면 기준이 통째로 빈다."""
    assert _run([("A", 1, None)]) == [("A", 1)]


def test_respects_limit():
    rows = [("A", 1, None), ("B", 1, None)]
    assert len(_run(rows, limit=1)) == 1
