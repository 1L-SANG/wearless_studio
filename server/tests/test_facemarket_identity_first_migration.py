import asyncio
import os
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql"
)
BASE_MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260821010100_facemarket_biometric_runtime.sql"
)
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def _sql():
    return " ".join(MIGRATION.read_text().split()).lower()


def test_status_check_includes_identity_pending():
    sql = _sql()
    assert "identity_pending" in sql
    # drop/re-add idempotent idiom
    assert "drop constraint if exists" in sql
    # active partial index widened
    assert "fm_biometric_active_per_user" in sql


def test_adds_identity_evidence_and_profile_columns():
    sql = _sql()
    for col in (
        "identity_ci_hash", "identity_name_masked", "identity_birth_year",
        "identity_tx_digest", "identity_contract_version", "profile_image_r2_key",
    ):
        assert col in sql, col


def test_default_status_is_identity_pending():
    sql = _sql()
    assert "default 'identity_pending'" in sql


def test_no_raw_biometric_columns():
    sql = _sql()
    for forbidden in ("portrait", "embedding", "dlphotoimage", "raw_ci"):
        assert forbidden not in sql, forbidden


@requires_database
def test_identity_pending_status_accepted_on_real_pg():
    async def scenario():
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(BASE_MIGRATION.read_text())
            await conn.execute(MIGRATION.read_text())
            # identity_pending 이 CHECK 를 통과해 insert 되는지 (status 는 default 사용)
            await conn.execute(
                "insert into fm_biometric_enrollments (user_id, device_digest, "
                "consent_version, expires_at) values (null, 'd', 'v', now())"
            )
            row = await (
                await conn.execute(
                    "select status from fm_biometric_enrollments "
                    "where device_digest = 'd' order by created_at desc limit 1"
                )
            ).fetchone()
            assert row["status"] == "identity_pending"
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
