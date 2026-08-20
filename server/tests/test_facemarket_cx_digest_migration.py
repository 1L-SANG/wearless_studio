import asyncio
import hashlib
import os
from pathlib import Path
import uuid

import pytest
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.facemarket import _take_simulation_rate_slot


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260820000000_facemarket_cx_token_digest.sql"
)
RATE_LIMIT_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260820010000_facemarket_settlement_simulation_rate_limit.sql"
)
SIGNER_INTENT_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260820020000_facemarket_settlement_signer_intents.sql"
)
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def test_cx_digest_migration_is_rolling_deploy_safe():
    sql = " ".join(MIGRATION.read_text().split()).lower()

    assert "rename column cx_tx_id" not in sql
    assert "cx_tx_id_format" in sql
    assert "'raw'" in sql and "'sha256-v1'" in sql
    assert "before insert or update of cx_tx_id" in sql
    assert "set cx_tx_id = cx_tx_id" in sql
    assert "where cx_tx_id_format = 'raw'" in sql
    assert "^cxsha256:[0-9a-f]{64}$" in sql
    assert "fm_identity_verifications_cx_tx_id_digest" in sql
    assert "where cx_tx_id !~" not in sql


def test_settlement_simulation_rate_limit_is_shared_and_private():
    sql = " ".join(RATE_LIMIT_MIGRATION.read_text().split()).lower()

    assert "create table if not exists public.fm_settlement_simulation_limits" in sql
    assert "primary key (scope, key_hash, window_start)" in sql
    assert "fm_settlement_simulation_limits_window_idx" in sql
    assert "enable row level security" in sql
    schema = sql.split("create table", 1)[1]
    assert "user_id" not in schema and "client_ip" not in schema


def test_settlement_signer_intent_is_durable_and_service_private():
    sql = " ".join(SIGNER_INTENT_MIGRATION.read_text().split()).lower()

    assert "create table if not exists public.fm_settlement_signer_intents" in sql
    assert "payment_id text primary key" in sql
    assert "'queued'" in sql and "'broadcasting'" in sql
    assert "status in ('queued', 'broadcasting', 'confirmed')" in sql
    assert "'failed'" not in sql
    assert "enable row level security" in sql


@requires_database
def test_cx_digest_migration_executes_and_blocks_raw_replay():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            model = await conn.execute(
                "insert into fm_models (display_name, ci_hash, status) "
                "values (%s, %s, 'verified') returning id",
                ("CX digest migration test", uuid.uuid4().hex),
            )
            model_id = (await model.fetchone())["id"]
            existing_raw = f"legacy-raw-cx-{uuid.uuid4()}"
            marker_like_raw = f"sha256:{'a' * 64}"
            await conn.execute(
                "drop trigger if exists fm_identity_verifications_digest_cx_tx_id "
                "on fm_identity_verifications"
            )
            await conn.execute(
                "alter table fm_identity_verifications drop constraint if exists "
                "fm_identity_verifications_cx_tx_id_digest"
            )
            await conn.execute(
                "alter table fm_identity_verifications drop column if exists cx_tx_id_format"
            )
            await conn.execute(
                "insert into fm_identity_verifications (model_id, cx_tx_id) values (%s, %s)",
                (model_id, existing_raw),
            )
            await conn.execute(
                "insert into fm_identity_verifications (model_id, cx_tx_id) values (%s, %s)",
                (model_id, marker_like_raw),
            )
            await conn.execute(MIGRATION.read_text())
            stored = await conn.execute(
                "select cx_tx_id, cx_tx_id_format from fm_identity_verifications "
                "where model_id = %s order by cx_tx_id",
                (model_id,),
            )
            rows = await stored.fetchall()
            assert {
                (row["cx_tx_id"], row["cx_tx_id_format"]) for row in rows
            } == {
                (f"cxsha256:{hashlib.sha256(existing_raw.encode()).hexdigest()}", "sha256-v1"),
                (f"cxsha256:{hashlib.sha256(marker_like_raw.encode()).hexdigest()}", "sha256-v1"),
            }
            new_raw = f"new-raw-cx-{uuid.uuid4()}"
            await conn.execute(
                "insert into fm_identity_verifications (model_id, cx_tx_id) values (%s, %s)",
                (model_id, new_raw),
            )
            new_digest = f"cxsha256:{hashlib.sha256(new_raw.encode()).hexdigest()}"
            with pytest.raises(UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_identity_verifications (model_id, cx_tx_id) "
                        "values (%s, %s)",
                        (model_id, new_raw),
                    )
            with pytest.raises(UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_identity_verifications "
                        "(model_id, cx_tx_id, cx_tx_id_format) values (%s, %s, %s)",
                        (model_id, new_digest, "sha256-v1"),
                    )
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())


@requires_database
def test_settlement_rate_limit_migration_executes_and_blocks_sixth_request():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(RATE_LIMIT_MIGRATION.read_text())
            user_id = str(uuid.uuid4())
            client_ip = f"198.51.100.{uuid.uuid4().int % 200 + 1}"
            for _ in range(5):
                await _take_simulation_rate_slot(
                    conn, user_id=user_id, client_ip=client_ip, pepper="test-pepper"
                )
            with pytest.raises(HTTPException) as error:
                await _take_simulation_rate_slot(
                    conn, user_id=user_id, client_ip=client_ip, pepper="test-pepper"
                )
            assert error.value.status_code == 429
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())


@requires_database
def test_settlement_signer_intent_migration_executes():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(SIGNER_INTENT_MIGRATION.read_text())
            payment_id = f"migration-intent-{uuid.uuid4()}"
            await conn.execute(
                "insert into fm_settlement_signer_intents "
                "(payment_id, model_id, total_amount) values (%s, %s, %s)",
                (payment_id, str(uuid.uuid4()), 10000),
            )
            stored = await conn.execute(
                "select status from fm_settlement_signer_intents where payment_id = %s",
                (payment_id,),
            )
            assert (await stored.fetchone())["status"] == "queued"
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
