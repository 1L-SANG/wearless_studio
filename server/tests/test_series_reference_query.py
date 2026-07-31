"""D축 기준 컷 선택 SQL 계약.

`list_series_reference_cuts` 는 순수 SQL 이라 FakeConn 으로는 의미가 없다(문법·`distinct on`
동작·JSON 필터가 전부 DB 안에서 일어난다). 그래서 실동작 검증은 로컬 54322 를 쓰고, DB 가
없으면 스킵한다 — CI 에는 postgres 서비스가 없어 거기서는 전부 스킵된다.

잠그는 계약 둘:
- candidate 별 **최신 1장**, limit 개 (전 버전을 끌어오면 재생성 이력에 비례해 비용이 는다)
- `outcome='regenerate'` 버전은 **기준에서 제외** (실패본을 앵커로 삼으면 오류가 전파된다)

두 번째 계약은 DB 없이도 도는 카나리로 한 겹 더 덮는다(맨 위 테스트).
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


def test_sql_still_filters_regenerate_without_db():
    """CI 카나리 — DB 없이도 도는 유일한 방어선.

    아래 실동작 테스트는 로컬 54322 가 있어야 돌고, CI(deploy-server.yml)에는 postgres
    서비스가 없어 **전부 스킵된다**. 그래서 CI 에서는 앵커 필터를 지워도 아무도 안 잡는다.
    쿼리 전문을 복사하지 않고 이 계약이 의존하는 토큰만 확인한다 — 필터를 지우려면 이
    테스트도 같이 지워야 하고, 그건 의식적인 결정이 된다.

    로컬에 DB 가 있으면 아래 실동작 테스트가 진짜 검증이다. 이건 대체재가 아니라 알람이다.
    """
    import inspect

    from app import repo as repo_mod

    src = inspect.getsource(repo_mod.list_series_reference_cuts)
    assert "'regenerate'" in src and "qc_scores ->> 'outcome'" in src, (
        "D축 앵커에서 regenerate 제외 필터가 사라졌다 — 실패본이 다음 생성의 기준이 된다")


pytestmark_db = pytest.mark.skipif(
    not _db_available(), reason="로컬 54322 없음 — SQL 실동작 테스트 스킵")


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


@pytestmark_db
def test_picks_latest_version_per_candidate():
    assert _run([("A", 1, None), ("A", 3, None), ("A", 2, None)]) == [("A", 3)]


@pytestmark_db
def test_excludes_regenerate_verdict_versions():
    """실패본이 다음 생성의 앵커가 되면 오류가 전파된다 — 최신이어도 제외하고 폴백."""
    assert _run([("A", 1, "auto_pass"), ("A", 2, "regenerate")]) == [("A", 1)]


@pytestmark_db
def test_keeps_unjudged_legacy_rows():
    """판정 없는 구 행(qc_scores null)은 포함한다 — 배제하면 기준이 통째로 빈다."""
    assert _run([("A", 1, None)]) == [("A", 1)]


@pytestmark_db
def test_respects_limit():
    rows = [("A", 1, None), ("B", 1, None)]
    assert len(_run(rows, limit=1)) == 1
