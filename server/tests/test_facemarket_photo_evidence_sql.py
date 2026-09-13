"""Execute evidence joins, not a fake that assumes a legacy front row exists.

SQLite only substitutes PostgreSQL casts/placeholders and row locking. These
tests cover relational selection; concurrent PostgreSQL locking is separate.
"""
import asyncio
import os
import re
import sqlite3
import uuid
from urllib.parse import urlparse

import pytest
import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException

from app import facemarket


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            create table fm_biometric_enrollments (
                id text, user_id text, model_id text, status text,
                match_policy_version text, body_type text, created_at text);
            create table fm_models (
                id text, user_id text, status text, did text, assets_status text,
                current_enrollment_id text, reverification_batch_id text);
            create table fm_licenses (
                id text, model_id text, enrollment_id text, created_at text,
                reverification_batch_id text);
            create table fm_cutover_batches (id text, status text, completed_at text);
            create table fm_biometric_enrollment_photos (
                enrollment_id text, angle text, r2_key text, image_digest text,
                storage_state text);
            create table fm_model_assets (
                model_id text, view text, r2_key text, source_enrollment_id text,
                evidence_version text);
            insert into fm_biometric_enrollments values
                ('enrollment', 'owner', 'model', 'license_pending', 'policy', null, '2026-09-01');
            insert into fm_models values
                ('model', 'owner', 'pending', null, 'ready', 'enrollment', null);
            insert into fm_licenses values
                ('license', 'model', 'enrollment', '2026-09-02', null);
            insert into fm_model_assets values
                ('model', 'face_front', 'face-asset', 'enrollment', 'policy'),
                ('model', 'grid_sedcard', 'grid-asset', 'enrollment', 'policy');
        """)

    def cursor(self):
        return Cursor(self.db)

    def photo(self, angle, state="approved"):
        self.db.execute("insert into fm_biometric_enrollment_photos values (?,?,?,?,?)",
                        ("enrollment", angle, "key-" + angle, "sha256-" + angle, state))


class Cursor:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params):
        sql = re.sub(r"::text", "", sql)
        sql = re.sub(r"for update of [\w, ]+$", "", sql.strip())
        self.result = self.db.execute(sql.replace("%s", "?"), params)

    async def fetchone(self):
        row = self.result.fetchone()
        return dict(row) if row is not None else None


class PostgresConnection:
    """Run identical joins and fixtures with PostgreSQL locks/casts intact."""
    def __init__(self, dsn):
        parsed = urlparse(dsn)
        if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != 55482:
            raise ValueError("PR280 evidence tests require the isolated localhost:55482 database")
        self.db = psycopg.connect(dsn, row_factory=dict_row)
        schema = f'evidence_{uuid.uuid4().hex}'
        self.db.execute(psycopg.sql.SQL('create schema {}').format(psycopg.sql.Identifier(schema)))
        self.db.execute(psycopg.sql.SQL('set local search_path to {}').format(psycopg.sql.Identifier(schema)))
        seed = Connection()
        try:
            for table in seed.db.execute("select name, sql from sqlite_master where type='table'").fetchall():
                self.db.execute(table['sql'])
                for row in seed.db.execute(f"select * from {table['name']}").fetchall():
                    self.db.execute(f"insert into {table['name']} values ({','.join(['%s'] * len(row))})", tuple(row))
        finally:
            seed.db.close()

    def photo(self, angle, state="approved"):
        self.db.execute("insert into fm_biometric_enrollment_photos values (%s,%s,%s,%s,%s)",
                        ("enrollment", angle, "key-" + angle, "sha256-" + angle, state))

    def cursor(self):
        class AsyncCursor:
            async def __aenter__(inner):
                return inner

            async def __aexit__(inner, *_args):
                return False

            async def execute(inner, sql, params):
                inner.result = self.db.execute(sql, params)

            async def fetchone(inner):
                return inner.result.fetchone()
        return AsyncCursor()


@pytest.fixture(params=['sqlite', 'postgres'])
def evidence_connection(request):
    if request.param == 'postgres':
        dsn = os.environ.get('PR280_TEST_DATABASE_URL')
        if not dsn:
            pytest.skip('isolated PostgreSQL not requested')
        connection = PostgresConnection(dsn)
    else:
        connection = Connection()
    yield connection
    connection.db.close()


@pytest.mark.parametrize("angles,want", [
    (["face01"], "key-face01"),
    (["front"], "key-front"),
    (["front", "face01"], "key-face01"),
    (["face01", "front"], "key-face01"),
])
def test_issuance_activation_and_catalog_select_the_same_photo(evidence_connection, angles, want):
    conn = evidence_connection
    try:
        for angle in angles:
            conn.photo(angle)
        evidence = asyncio.run(facemarket._load_license_evidence(conn, "owner", "enrollment"))
        assert facemarket._checked_license_evidence(evidence)[1] == want
        active = asyncio.run(facemarket._load_activation_evidence_for_update(
            conn, "owner", "license", "enrollment"))
        assert facemarket._checked_license_evidence(active)[1] == want
        rows = conn.db.execute("select p.r2_key from fm_models m " + facemarket._CURRENT_CARD_JOINS).fetchall()
        assert [row['r2_key'] for row in rows] == [want]
    finally:
        conn.db.close()


def test_pending_canonical_photo_never_falls_back_to_old_approved_photo(evidence_connection):
    conn = evidence_connection
    try:
        conn.photo("front")
        conn.photo("face01", "delete_pending")
        evidence = asyncio.run(facemarket._load_license_evidence(conn, "owner", "enrollment"))
        with pytest.raises(HTTPException) as failure:
            facemarket._checked_license_evidence(evidence)
        assert failure.value.detail["code"] == "approved_front_missing"
    finally:
        conn.db.close()
