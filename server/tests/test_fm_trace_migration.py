"""추적 층 마이그레이션 계약(2026-09-26) — 추가만 하는가, 멱등 키가 맞는가.

텍스트 검사는 DB 없는 CI 에서도 돈다. FACEMARKET_TEST_DATABASE_URL 이 있으면(로컬 Postgres —
provenance 마이그레이션 선행 조건이 깔린 DB) 실제로 적용해 제약을 확인한다.
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
MIGRATION = MIGRATIONS / "20260926213000_fm_trace_watermark_fingerprints.sql"
PROVENANCE = MIGRATIONS / "20260904000000_facemarket_provenance.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def _sql() -> str:
    sql = MIGRATION.read_text(encoding="utf-8")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def test_migration_sorts_after_provenance_ledgers():
    assert MIGRATION.exists()
    assert PROVENANCE.name < MIGRATION.name


def test_migration_is_additive_only():
    sql = _sql()
    for forbidden in ("drop table", "drop column", "alter column", "truncate", "delete from",
                      "update public.", "rename"):
        assert forbidden not in sql, forbidden
    # drop constraint 는 이 파일이 **새로 만드는** 제약의 재실행 멱등용뿐이다.
    dropped = set(re.findall(r"drop constraint if exists (\w+)", sql))
    added = set(re.findall(r"add constraint (\w+)", sql))
    assert dropped <= added


def test_wm_columns_and_unique_code_index():
    sql = _sql()
    for col in ("wm_code bigint", "wm_status text", "wm_sha256 text"):
        assert f"add column if not exists {col}" in sql
    assert ("create unique index if not exists fm_publication_records_wm_code_key "
            "on public.fm_publication_records (wm_code) where wm_code is not null") in sql


def test_fingerprint_table_rls_and_idempotency_keys():
    sql = _sql()
    assert "create table if not exists public.fm_image_fingerprints" in sql
    assert "alter table public.fm_image_fingerprints enable row level security" in sql
    assert "create policy" not in sql            # service-role 전용
    assert "fm_image_fingerprints_cut_key" in sql
    assert "fm_image_fingerprints_publication_key" in sql


async def _apply(conn):
    await conn.execute(PROVENANCE.read_text(encoding="utf-8"))
    await conn.execute(MIGRATION.read_text(encoding="utf-8"))
    await conn.execute(MIGRATION.read_text(encoding="utf-8"))     # 재실행 멱등


@requires_database
def test_constraints_on_real_postgres():
    async def run():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True,
                                             row_factory=dict_row)
        try:
            await _apply(conn)
            ins = ("insert into fm_publication_records (seller_id, license_ref, model_id, kind, "
                   "image_sha256, wm_code) values (%s, %s, %s, 'long_png', %s, %s) "
                   "returning id")
            p1 = (await (await conn.execute(ins, (uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
                                                  uuid.uuid4().hex, 77))).fetchone())["id"]
            with pytest.raises(UniqueViolation):
                await conn.execute(ins, (uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
                                         uuid.uuid4().hex, 77))
            with pytest.raises(CheckViolation):
                await conn.execute(ins, (uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
                                         uuid.uuid4().hex, 1 << 32))
            with pytest.raises(CheckViolation):
                await conn.execute("update fm_publication_records set wm_status = 'weird' "
                                   "where id = %s", (p1,))
            fp = ("insert into fm_image_fingerprints (publication_id, kind, region_y0, "
                  "region_y1, phash, dhash) values (%s, %s, %s, %s, %s, %s) on conflict do nothing")
            await conn.execute(fp, (p1, "publication", None, None, -1, 2))
            await conn.execute(fp, (p1, "publication", None, None, -1, 2))   # 멱등
            await conn.execute(fp, (p1, "strip", 0, 800, 3, 4))
            await conn.execute(fp, (p1, "strip", 0, 800, 3, 4))
            n = (await (await conn.execute(
                "select count(*) as n from fm_image_fingerprints where publication_id = %s",
                (p1,))).fetchone())["n"]
            assert n == 2
            with pytest.raises(CheckViolation):     # 띠는 구간이 있어야 한다
                await conn.execute(fp, (p1, "strip", None, None, 1, 1))
            with pytest.raises(CheckViolation):     # 컷 지문은 배포본에 달 수 없다
                await conn.execute(fp, (p1, "cut", None, None, 1, 1))
            # 부모가 지워지면 지문도 간다(원장은 실무상 안 지운다)
            await conn.execute("delete from fm_publication_records where id = %s", (p1,))
            n = (await (await conn.execute(
                "select count(*) as n from fm_image_fingerprints where publication_id = %s",
                (p1,))).fetchone())["n"]
            assert n == 0
        finally:
            await conn.close()

    asyncio.run(run())
