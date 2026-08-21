import asyncio
import os
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row


MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260821000000_facemarket_biometric_runtime.sql"
)
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def test_biometric_tables_are_service_private_and_raw_free():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "create table if not exists public.fm_biometric_enrollments" in sql
    assert "create table if not exists public.fm_biometric_enrollment_photos" in sql
    assert sql.count("enable row level security") >= 2
    enrollment_schema = sql.split(
        "create table if not exists public.fm_biometric_enrollments", 1
    )[1]
    enrollment_schema = enrollment_schema.split(");", 1)[0]
    for forbidden in ("portrait", "reference_image", "embedding", "raw_token", "confidence", "score"):
        assert forbidden not in enrollment_schema


def test_status_and_current_evidence_links_are_constrained():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "update public.fm_models" not in sql
    assert "reverification_required" in sql
    assert "current_enrollment_id" in sql
    assert "source_enrollment_id" in sql
    assert "evidence_version" in sql
    assert "primary key (enrollment_id, angle)" in sql
    assert "angle in ('front', 'angle45', 'side')" in sql
    assert "storage_state in ('quarantine', 'approved', 'delete_pending')" in sql
    assert "device_digest text not null" in sql
    assert "fm_biometric_failure_device_window" in sql


def test_photo_deletion_and_superseded_keys_have_durable_retry_state():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "storage_state in ('quarantine', 'approved', 'delete_pending')" in sql
    assert (
        "create table if not exists public.fm_biometric_enrollment_photo_cleanup"
        in sql
    )
    cleanup_schema = sql.split(
        "create table if not exists public.fm_biometric_enrollment_photo_cleanup", 1
    )[1].split(");", 1)[0]
    assert "r2_key text not null" in cleanup_schema
    assert "not_before timestamptz not null default now()" in cleanup_schema
    assert "primary key (enrollment_id, r2_key)" in cleanup_schema


def test_legacy_model_asset_cleanup_has_a_private_bounded_due_outbox():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "create table if not exists public.fm_model_asset_cleanup" in sql
    schema = sql.split(
        "create table if not exists public.fm_model_asset_cleanup", 1
    )[1].split(");", 1)[0]
    assert "model_id uuid not null" in schema
    assert "r2_key text not null" in schema
    assert "reason text not null check (reason in ('superseded'))" in schema
    assert "not_before timestamptz not null default now()" in schema
    assert "created_at timestamptz not null default now()" in schema
    assert "primary key (model_id, r2_key)" in schema
    assert "fm_model_asset_cleanup_due" in sql
    assert "alter table public.fm_model_asset_cleanup enable row level security" in sql
    assert "policy" not in sql.split(
        "alter table public.fm_model_asset_cleanup enable row level security", 1
    )[1]


def test_liveness_nonce_and_session_digests_are_unique():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "fm_biometric_liveness_nonce_unique" in sql
    assert "fm_biometric_liveness_session_unique" in sql


@requires_database
def test_biometric_migration_executes_and_enforces_one_photo_per_angle():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(MIGRATION.read_text())
            enrollment = await conn.execute(
                "insert into fm_biometric_enrollments "
                "(user_id, device_digest, consent_version) "
                "values (null, 'sha256-device', '2026-08-v1') returning id"
            )
            enrollment_id = (await enrollment.fetchone())["id"]
            values = (enrollment_id, "front", "private/key.jpg", "sha256-test", "image/jpeg", 10)
            await conn.execute(
                "insert into fm_biometric_enrollment_photos "
                "(enrollment_id, angle, r2_key, image_digest, mime_type, byte_size) "
                "values (%s,%s,%s,%s,%s,%s)",
                values,
            )
            with pytest.raises(UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_biometric_enrollment_photos "
                        "(enrollment_id, angle, r2_key, image_digest, mime_type, byte_size) "
                        "values (%s,%s,%s,%s,%s,%s)",
                        values,
                    )
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
