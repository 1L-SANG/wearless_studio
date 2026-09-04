"""원장 스키마 계약 테스트 — 원장이 부모보다 오래 사는지가 핵심이다.

fm_models → fm_licenses 는 on delete cascade 다. 2026-08-29 prod 복구 때 모델·라이선스가
실제로 지워졌다. 원장의 FK 가 restrict 였다면 그 복구가 막혔고, cascade 였다면 원장이 통째로
사라졌다. 여기서 검증하는 건 "부모가 지워져도 증빙값이 남는다" 하나다.
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260904000000_facemarket_provenance.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_declares_set_null_fks():
    """계약을 SQL 텍스트 수준에서도 못박는다 — DB 없는 CI 에서도 회귀를 잡는다."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "on delete restrict" not in sql.lower()
    assert "fm_output_records" in sql
    assert "fm_publication_records" in sql
    assert "fm_publication_anchor_jobs" in sql
    # 증빙값은 FK 없는 비정규화 컬럼이어야 한다
    assert "license_ref" in sql
    assert "image_sha256" in sql


@requires_database
def test_ledger_survives_license_delete():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            model_id = uuid.uuid4()
            license_id = uuid.uuid4()
            seller_id = uuid.uuid4()
            await conn.execute(
                "insert into fm_models (id, display_name) values (%s, %s)",
                (model_id, "홍*동"),
            )
            await conn.execute(
                """insert into fm_licenses
                   (id, model_id, face_image_uri, face_image_digest, license_valid_until)
                   values (%s, %s, %s, %s, now() + interval '1 year')""",
                (license_id, model_id, "/gate", "sha256-x"),
            )
            record_id = uuid.uuid4()
            await conn.execute(
                """insert into fm_output_records
                   (id, license_id, license_ref, model_id, seller_id, image_sha256)
                   values (%s, %s, %s, %s, %s, %s)""",
                (record_id, license_id, license_id, model_id, seller_id, "a" * 64),
            )
            # 모델 삭제 → 라이선스 cascade 삭제. 원장은 남아야 한다.
            await conn.execute("delete from fm_models where id = %s", (model_id,))
            cur = await conn.execute(
                "select license_id, license_ref, model_id, image_sha256 "
                "from fm_output_records where id = %s",
                (record_id,),
            )
            row = await cur.fetchone()
            assert row is not None, "원장이 부모와 함께 지워졌다"
            assert row["license_id"] is None       # FK 는 끊긴다
            assert str(row["license_ref"]) == str(license_id)  # 증빙값은 남는다
            assert str(row["model_id"]) == str(model_id)
            assert row["image_sha256"] == "a" * 64
        finally:
            await conn.close()

    asyncio.run(run())


@requires_database
def test_publication_idempotent_per_seller_and_hash():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            seller_id = uuid.uuid4()
            args = (seller_id, uuid.uuid4(), uuid.uuid4(), "long_png", "b" * 64)
            sql = """insert into fm_publication_records
                     (seller_id, license_ref, model_id, kind, image_sha256)
                     values (%s, %s, %s, %s, %s)"""
            await conn.execute(sql, args)
            with pytest.raises(UniqueViolation):
                await conn.execute(sql, args)
        finally:
            await conn.close()

    asyncio.run(run())
