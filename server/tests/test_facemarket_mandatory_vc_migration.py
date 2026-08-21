import asyncio
import os
from pathlib import Path
import re
import uuid

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.rows import dict_row


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821010000_facemarket_mandatory_vc.sql"
)
PREDECESSOR = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821000000_facemarket_biometric_runtime.sql"
)
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def _executable_sql(path: Path) -> str:
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def test_sql_normalization_excludes_line_and_block_comments(tmp_path):
    migration = tmp_path / "migration.sql"
    migration.write_text(
        "-- update fm_licenses set status = 'active';\n"
        "select 1; /* create policy public_read on fm_vc_revocation_jobs; */",
        encoding="utf-8",
    )

    assert _executable_sql(migration) == "select 1;"


def _sql() -> str:
    return _executable_sql(MIGRATION)


def test_predecessor_owns_the_expanded_license_status_check():
    sql = _executable_sql(PREDECESSOR)
    assert "fm_licenses_status_check" in sql
    assert "'pending'" in sql and "'reverification_required'" in sql


def test_license_status_defaults_pending_without_rewriting_existing_rows():
    sql = _sql()
    assert "alter column status set default 'pending'" in sql
    assert "fm_licenses_status_check" not in sql
    assert not re.search(r"\bupdate\s+(?:public\s*\.\s*)?fm_licenses\b", sql)


def test_revocation_queue_is_durable_idempotent_and_service_private():
    sql = _sql()
    assert "create table if not exists public.fm_vc_revocation_jobs" in sql
    schema = sql.split(
        "create table if not exists public.fm_vc_revocation_jobs (", 1
    )[1].split(");", 1)[0]
    assert (
        "license_id uuid not null references public.fm_licenses(id) on delete restrict"
        in schema
    )
    assert (
        "model_id uuid not null references public.fm_models(id) on delete restrict"
        in schema
    )
    assert "vc_id text not null unique" in schema
    assert "status in ('pending', 'processing', 'retry', 'revoked')" in schema
    assert "attempts integer not null default 0 check (attempts >= 0)" in schema
    assert "next_attempt_at timestamptz not null default now()" in schema
    assert "lease_token uuid" in schema
    assert "lease_expires_at timestamptz" in schema


def test_revocation_queue_claim_index_targets_only_due_retryable_rows():
    sql = _sql()
    assert (
        "create index if not exists fm_vc_revocation_jobs_claim_idx "
        "on public.fm_vc_revocation_jobs (next_attempt_at, created_at) "
        "where status in ('pending', 'retry')"
    ) in sql


def test_revocation_queue_has_rls_without_client_policies_and_updates_timestamps():
    sql = _sql()
    assert (
        "alter table public.fm_vc_revocation_jobs enable row level security" in sql
    )
    assert not re.search(
        r"\bcreate\s+policy\b.*\bfm_vc_revocation_jobs\b", sql
    )
    assert (
        "create trigger fm_vc_revocation_jobs_set_updated_at "
        "before update on public.fm_vc_revocation_jobs "
        "for each row execute function public.set_updated_at()"
    ) in sql


@requires_database
def test_migration_executes_twice_and_enforces_queue_contract():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(PREDECESSOR.read_text(encoding="utf-8"))
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))

            model = await conn.execute(
                "insert into fm_models (display_name) values (%s) returning id",
                (f"mandatory-vc-migration-{uuid.uuid4()}",),
            )
            model_id = (await model.fetchone())["id"]
            license_row = await conn.execute(
                "insert into fm_licenses "
                "(model_id, face_image_uri, face_image_digest, license_valid_until) "
                "values (%s, %s, %s, now() + interval '1 day') returning id, status",
                (model_id, "/private/test-face", "sha256-test"),
            )
            license_row = await license_row.fetchone()
            assert license_row["status"] == "pending"

            vc_id = f"vc-{uuid.uuid4()}"
            job = await conn.execute(
                "insert into fm_vc_revocation_jobs (license_id, model_id, vc_id) "
                "values (%s, %s, %s) returning status, attempts",
                (license_row["id"], model_id, vc_id),
            )
            assert await job.fetchone() == {"status": "pending", "attempts": 0}

            with pytest.raises(CheckViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_vc_revocation_jobs "
                        "(license_id, model_id, vc_id, status) "
                        "values (%s, %s, %s, 'invalid')",
                        (license_row["id"], model_id, f"vc-{uuid.uuid4()}"),
                    )
            with pytest.raises(CheckViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_vc_revocation_jobs "
                        "(license_id, model_id, vc_id, attempts) "
                        "values (%s, %s, %s, -1)",
                        (license_row["id"], model_id, f"vc-{uuid.uuid4()}"),
                    )
            with pytest.raises(UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_vc_revocation_jobs "
                        "(license_id, model_id, vc_id) values (%s, %s, %s)",
                        (license_row["id"], model_id, vc_id),
                    )
            with pytest.raises(ForeignKeyViolation):
                async with conn.transaction():
                    await conn.execute(
                        "delete from fm_licenses where id = %s", (license_row["id"],)
                    )
            with pytest.raises(ForeignKeyViolation):
                async with conn.transaction():
                    await conn.execute("delete from fm_models where id = %s", (model_id,))

            rls = await conn.execute(
                "select c.relrowsecurity from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relname = 'fm_vc_revocation_jobs'"
            )
            assert (await rls.fetchone())["relrowsecurity"] is True
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
