"""배포 중 구 서버의 24시간 기록도 72시간으로 보정되는지 실제 Postgres에서 검사한다."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg import sql


DB_URL = os.environ.get(
    "FM_TTL_TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
assert urlparse(DB_URL).hostname in {"127.0.0.1", "localhost"}, "로컬 DB에서만 검사한다"

GUARD_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260927190000_fm_enrollment_ttl_72h_writes.sql"
)


@pytest.fixture
def enrollment_db():
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("CI"):
            pytest.fail("등록 기한 검사에 필요한 로컬 DB에 연결하지 못했습니다.")
        pytest.skip("로컬 Supabase DB가 없어 등록 기한 DB 검사를 건너뜁니다.")

    schema = f"ttl_rollout_{uuid.uuid4().hex}"
    try:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("set local search_path to {}, public").format(sql.Identifier(schema)))
        conn.execute("""
            create table fm_biometric_enrollments (
                id uuid primary key default gen_random_uuid(),
                status text not null,
                photo_revision integer not null default 0,
                created_at timestamptz not null default now(),
                expires_at timestamptz not null default (now() + interval '24 hours')
            )
        """)
        if GUARD_MIGRATION.exists():
            conn.execute(GUARD_MIGRATION.read_text().replace("public.", f"{schema}."))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_old_server_insert_cannot_leave_a_24_hour_deadline(enrollment_db):
    created = datetime.now(timezone.utc)
    row = enrollment_db.execute(
        "insert into fm_biometric_enrollments (status, created_at, expires_at) "
        "values ('identity_pending', %s, %s) returning expires_at",
        (created, created + timedelta(hours=24)),
    ).fetchone()
    assert row[0] == created + timedelta(hours=72)


def test_old_server_photo_reopen_gets_72_hours_from_reopen(enrollment_db):
    row_id = enrollment_db.execute(
        "insert into fm_biometric_enrollments (status, created_at, expires_at) "
        "values ('license_pending', now() - interval '5 days', now() - interval '4 days') "
        "returning id"
    ).fetchone()[0]
    before = enrollment_db.execute("select clock_timestamp()").fetchone()[0]
    row = enrollment_db.execute(
        "update fm_biometric_enrollments "
        "set status = 'photos_pending', photo_revision = photo_revision + 1, expires_at = %s "
        "where id = %s returning expires_at",
        (before + timedelta(hours=24), row_id),
    ).fetchone()
    after = enrollment_db.execute("select clock_timestamp()").fetchone()[0]
    assert before + timedelta(hours=72) <= row[0] <= after + timedelta(hours=72)


def test_review_approval_keeps_its_separate_short_processing_window(enrollment_db):
    row_id = enrollment_db.execute(
        "insert into fm_biometric_enrollments (status) values ('review_pending') returning id"
    ).fetchone()[0]
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    row = enrollment_db.execute(
        "update fm_biometric_enrollments set status = 'processing', expires_at = %s "
        "where id = %s returning expires_at",
        (deadline, row_id),
    ).fetchone()
    assert row[0] == deadline
