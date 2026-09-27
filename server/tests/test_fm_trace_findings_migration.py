"""자동 출처 추적 원장 마이그레이션 계약(2026-09-27) — 추가만 하는가, 약관·멱등 키가 맞는가.

텍스트 검사는 DB 없는 CI 에서도 돈다. FACEMARKET_TEST_DATABASE_URL 이 있으면 **한 트랜잭션 안에서**
실제로 적용해 제약을 확인하고 롤백한다(공용 로컬 DB 를 더럽히지 않는다).
"""
import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20260927110000_fm_trace_findings.sql"
TRACE = MIGRATIONS / "20260926213000_fm_trace_watermark_fingerprints.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def _sql() -> str:
    sql = MIGRATION.read_text(encoding="utf-8")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def test_migration_sorts_after_trace_layer():
    assert MIGRATION.exists()
    assert TRACE.name < MIGRATION.name


def test_migration_is_additive_only():
    sql = _sql()
    for forbidden in ("drop table", "drop column", "alter column", "truncate", "delete from",
                      "update public.", "rename"):
        assert forbidden not in sql, forbidden


def test_tables_enable_rls_without_policies():
    sql = _sql()
    for table in ("fm_trace_findings", "fm_trace_known_stores", "fm_trace_patrol_runs"):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"revoke all on public.{table} from anon, authenticated" in sql
    assert "create policy" not in sql


def test_patrol_dedupe_key_is_unique():
    sql = _sql()
    assert ("create unique index if not exists fm_trace_findings_dedupe_key "
            "on public.fm_trace_findings (dedupe_key) where dedupe_key is not null") in sql


def test_naver_fields_have_purge_deadline_column():
    """네이버 검색 API 특약 2.4 — 서버 보관은 이력 조회 목적 최대 21일. 지울 시각을 행에 둔다."""
    sql = _sql()
    assert "external_purge_at timestamptz" in sql
    assert "fm_trace_findings_purge_idx" in sql


def test_fingerprints_accept_cut_crop_rows_idempotently():
    sql = _sql()
    assert "check (kind in ('publication', 'strip', 'cut', 'cut_crop'))" in sql
    assert ("create unique index if not exists fm_image_fingerprints_cut_crop_key "
            "on public.fm_image_fingerprints (output_record_id, region_y0, region_y1) "
            "where kind = 'cut_crop'") in sql
    dropped = set(re.findall(r"drop constraint if exists (\w+)", sql))
    added = set(re.findall(r"add constraint (\w+)", sql))
    assert dropped and dropped <= added


def _finding(**kw):
    base = {
        "source": "patrol", "platform": "zigzag", "dedupe_key": uuid.uuid4().hex,
        "target": "cut", "method": "phash", "confidence": "medium",
    }
    base.update(kw)
    cols = ", ".join(base)
    marks = ", ".join(["%s"] * len(base))
    return f"insert into fm_trace_findings ({cols}) values ({marks}) returning id", tuple(
        base.values())


@requires_database
def test_constraints_on_real_postgres_rolled_back():
    async def run():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            has_fp = (await (await conn.execute(
                "select to_regclass('public.fm_image_fingerprints') as t")).fetchone())["t"]
            if has_fp is None:
                await conn.execute(TRACE.read_text(encoding="utf-8"))
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))     # 재실행 멱등
            sql, params = _finding(dedupe_key="k1")
            await conn.execute(sql, params)
            await conn.execute("savepoint s")
            with pytest.raises(UniqueViolation):
                await conn.execute(sql, params)
            await conn.execute("rollback to savepoint s")
            for bad in ({"source": "crawler"}, {"platform": "29cm"}, {"status": "weird"},
                        {"source": "model_report", "platform": "zigzag"},
                        {"source": "patrol", "platform": "report"},
                        {"source": "patrol", "target": None}):
                sql, params = _finding(**bad)
                with pytest.raises(CheckViolation):
                    await conn.execute(sql, params)
                await conn.execute("rollback to savepoint s")
            # 제보는 매칭이 없어도 남는다(관리자가 봐야 한다) — 대상·방법 없이 들어간다.
            sql, params = _finding(source="model_report", platform="report", dedupe_key=None,
                                   target=None, method=None, confidence=None)
            await conn.execute(sql, params)
            ks = ("insert into fm_trace_known_stores (seller_id, platform, store_key) "
                  "values (%s, 'naver', 'abc') on conflict do nothing")
            seller = uuid.uuid4()
            await conn.execute(ks, (seller,))
            await conn.execute(ks, (seller,))
            n = (await (await conn.execute(
                "select count(*) as n from fm_trace_known_stores where seller_id = %s",
                (seller,))).fetchone())["n"]
            assert n == 1
            out_id = (await (await conn.execute(
                "insert into fm_output_records (license_ref, model_id, seller_id, image_sha256) "
                "values (%s, %s, %s, 'x') returning id", (uuid.uuid4(), uuid.uuid4(),
                                                          uuid.uuid4()))).fetchone())["id"]
            crop = ("insert into fm_image_fingerprints (output_record_id, kind, region_y0, "
                    "region_y1, phash, dhash) values (%s, 'cut_crop', %s, %s, 1, 2) "
                    "on conflict do nothing")
            await conn.execute(crop, (out_id, 0, 860))
            await conn.execute(crop, (out_id, 0, 860))                    # 멱등
            await conn.execute(crop, (out_id, 202, 1062))
            n = (await (await conn.execute(
                "select count(*) as n from fm_image_fingerprints where output_record_id = %s",
                (out_id,))).fetchone())["n"]
            assert n == 2
            await conn.execute("savepoint c")
            with pytest.raises(CheckViolation):                           # 크롭은 구간이 있어야
                await conn.execute(crop.replace("%s, %s, 1, 2", "null, null, 1, 2"), (out_id,))
            await conn.execute("rollback to savepoint c")
            run_sql = ("insert into fm_trace_patrol_runs (run_date) values (date '2026-09-27') "
                       "returning id")
            await conn.execute(run_sql)
            with pytest.raises(UniqueViolation):
                await conn.execute(run_sql)
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(run())


def test_migration_versions_are_unique_across_the_repo():
    """Supabase 는 파일명 앞 숫자(버전)로 적용 여부를 판단한다. 같은 버전이 둘이면 먼저 적용된 쪽만
    기록되고 다른 쪽은 '이미 적용됨'으로 조용히 빠진다(2026-09-27 #432 와 이 파일이 둘 다
    20260927090000 이었다 — 머지 직전에 발견)."""
    from collections import Counter
    versions = Counter(p.name.split("_", 1)[0] for p in MIGRATIONS.glob("*.sql"))
    dupes = sorted(v for v, n in versions.items() if n > 1)
    assert not dupes, f"같은 마이그레이션 버전이 여러 파일에 있다: {dupes}"
