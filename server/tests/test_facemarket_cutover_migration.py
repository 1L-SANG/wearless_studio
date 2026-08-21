import asyncio
import os
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")


def test_cutover_migration_declares_private_durable_state():
    sql = MIGRATION.read_text().lower()
    assert "previous_status" in sql
    assert "reverification_batch_id" in sql
    assert "create table if not exists public.fm_cutover_batches" in sql
    assert "alter table public.fm_cutover_batches enable row level security" in sql
    assert "drop not null" in sql
    assert "fm_models_status_check" not in sql
    assert "fm_licenses_status_check" not in sql
    assert "fm_vc_revocation_jobs" not in sql
    assert "on delete set null" not in sql
    assert "fm_cutover_batches_one_active_idx" in sql
    assert "'failed'" in sql.split("fm_cutover_batches_one_active_idx", 1)[1]


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="FACEMARKET_TEST_DATABASE_URL is not configured",
)
def test_cutover_migration_applies_and_exposes_expected_columns():
    async def scenario():
        sql = MIGRATION.read_text()
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(sql)
            rows = await conn.execute(
                """
                select table_name, column_name, is_nullable
                  from information_schema.columns
                 where table_schema = 'public'
                   and table_name in ('fm_models','fm_licenses','fm_cutover_batches')
                """
            )
            columns = {
                (row["table_name"], row["column_name"]): row["is_nullable"]
                async for row in rows
            }
            assert ("fm_models", "previous_status") in columns
            assert ("fm_licenses", "previous_status") in columns
            assert columns[("fm_licenses", "face_image_digest")] == "YES"
            assert ("fm_cutover_batches", "target_digest") in columns
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
