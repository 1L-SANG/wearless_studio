import asyncio
import os
import re
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")


def _assert_batch_link_fk(sql: str, table: str):
    constraint = f"{table}_reverification_batch_id_fkey"
    compact = " ".join(sql.lower().split())
    match = re.search(
        rf"alter table public\.{table} "
        rf"add constraint {constraint} "
        r"foreign key \(reverification_batch_id\) "
        r"references public\.fm_cutover_batches\(id\)(?P<tail>[^;]*);",
        compact,
    )
    assert match, f"{constraint} must link reverification_batch_id to fm_cutover_batches(id)"
    assert "on delete set null" not in match.group("tail")
    assert "on delete cascade" not in match.group("tail")


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
    _assert_batch_link_fk(sql, "fm_models")
    _assert_batch_link_fk(sql, "fm_licenses")


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
            constraint_rows = await conn.execute(
                """
                select c.conname,
                       source.relname as source_table,
                       target_ns.nspname as target_schema,
                       target.relname as target_table,
                       c.confdeltype,
                       rc.delete_rule,
                       pg_get_constraintdef(c.oid) as definition
                  from pg_constraint c
                  join pg_class source on source.oid = c.conrelid
                  join pg_namespace source_ns on source_ns.oid = source.relnamespace
                  join pg_class target on target.oid = c.confrelid
                  join pg_namespace target_ns on target_ns.oid = target.relnamespace
                  left join information_schema.referential_constraints rc
                    on rc.constraint_schema = source_ns.nspname
                   and rc.constraint_name = c.conname
                 where c.conname in (
                   'fm_models_reverification_batch_id_fkey',
                   'fm_licenses_reverification_batch_id_fkey'
                 )
                """
            )
            constraints = {
                row["conname"]: row async for row in constraint_rows
            }
            assert constraints[
                "fm_models_reverification_batch_id_fkey"
            ]["source_table"] == "fm_models"
            assert constraints[
                "fm_licenses_reverification_batch_id_fkey"
            ]["source_table"] == "fm_licenses"
            for row in constraints.values():
                assert row["target_schema"] == "public"
                assert row["target_table"] == "fm_cutover_batches"
                assert row["confdeltype"] in ("a", "r")
                assert row["delete_rule"] in ("NO ACTION", "RESTRICT")
                assert "foreign key (reverification_batch_id)" in row[
                    "definition"
                ].lower()
                assert "references fm_cutover_batches(id)" in row[
                    "definition"
                ].lower()
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
