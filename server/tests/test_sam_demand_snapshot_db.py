"""SAM 업로드 수요 필터를 실제 PostgreSQL에서 검사한다. CI의 test-db 잡이 실행한다."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import repo
from app.services.sam_autoscale import SAM_KINDS

DB_URL = os.environ.get("SAM_DEMAND_TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
assert urlparse(DB_URL).hostname in ("127.0.0.1", "localhost"), "로컬 DB 에서만 돈다"


def _db_reachable() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


if not _db_reachable():
    if os.environ.get("CI"):
        pytest.fail(f"테스트 DB({DB_URL})에 붙지 못했어요.", pytrace=False)
    pytest.skip("로컬 Supabase DB 가 없어 실제 DB 검사를 건너뛰어요.", allow_module_level=True)


BASE = datetime(2026, 10, 7, tzinfo=timezone.utc)


def _run(body):
    async def main():
        conn = await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)
        try:
            # 실제 스키마를 복제한 임시 테이블로 이 연결의 전역 수요만 격리한다.
            await conn.execute("create temp table assets (like public.assets including defaults including constraints)")
            await conn.execute("create temp table jobs (like public.jobs including defaults including constraints)")
            user_id = str(uuid.uuid4())
            for kind, status, finished_at in [
                ("sam_preprocess", "pending", None),
                ("matching_cutout", "running", None),
                ("editor_garment_mask", "pending", None),
                ("sam_preprocess", "done", BASE),
                ("matching_cutout", "error", BASE + timedelta(minutes=2)),
                ("detail_page", "pending", None),
                ("detail_page", "running", None),
                ("detail_page", "done", BASE + timedelta(minutes=50)),
            ]:
                await conn.execute(
                    "insert into jobs (user_id, kind, status, finished_at) values (%s, %s, %s, %s)",
                    (user_id, kind, status, finished_at))
            await body(conn, user_id)
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(main())


async def _upload(conn, user_id, metadata, minute, *, source="upload", deleted=False):
    cur = await conn.execute(
        "insert into assets (user_id, source, r2_bucket, r2_key, mime_type, metadata, created_at, deleted_at) "
        "values (%s, %s, 'sam-demand-test', %s, 'image/png', %s, %s, %s) returning id",
        (user_id, source, str(uuid.uuid4()), Jsonb(metadata), BASE + timedelta(minutes=minute),
         BASE + timedelta(minutes=minute + 1) if deleted else None))
    return (await cur.fetchone())["id"]


async def _non_sam_uploads(conn, user_id):
    for minute, purpose in enumerate([
        "editor_insert", "storyboard_insert", "mood_reference", "tone_render",
    ], 20):
        await _upload(conn, user_id, {"purpose": purpose}, minute)
    asset_id = await _upload(conn, user_id, {"purpose": "tone_render"}, 30)
    # 톤 적용은 metadata 전체를 교체하므로 purpose 없이 type만 남는 경로도 검사한다.
    await conn.execute("update assets set metadata = %s where id = %s",
                       (Jsonb({"type": "mannequinToneAdjusted"}), asset_id))
    await _upload(conn, user_id, {"purpose": "upload", "type": "mannequinToneAdjusted"}, 31)
    await _upload(conn, user_id, {"purpose": "upload"}, 40, source="ai")


@pytest.mark.parametrize("metadata, deleted", [
    ({}, False),
    ({"purpose": None, "type": None}, False),
    ({"purpose": "unknown"}, False),
    ({"purpose": "upload"}, False),
    ({"purpose": "draft_slot"}, False),
    ({"purpose": "custom_match_source"}, False),
    ({"purpose": "custom_match_grid"}, False),
    ({"purpose": "upload"}, True),
])
def test_last_upload_ignores_newer_non_sam_uploads(metadata, deleted):
    async def body(conn, user_id):
        await _upload(conn, user_id, {}, 1)
        await _upload(conn, user_id, {"purpose": "unknown"}, 2)
        await _upload(conn, user_id, {"purpose": "upload"}, 3)
        await _upload(conn, user_id, {"purpose": "draft_slot"}, 4)
        await _upload(conn, user_id, metadata, 10, deleted=deleted)
        await _non_sam_uploads(conn, user_id)

        snap = await repo.sam_demand_snapshot(conn, SAM_KINDS)
        assert snap.last_upload_at == BASE + timedelta(minutes=10)
        assert snap.active_sam_jobs == 3
        assert snap.last_sam_finished_at == BASE + timedelta(minutes=2)

    _run(body)


def test_only_non_sam_uploads_leave_job_demand_intact():
    async def body(conn, user_id):
        await _non_sam_uploads(conn, user_id)

        snap = await repo.sam_demand_snapshot(conn, SAM_KINDS)
        assert snap.last_upload_at is None
        assert snap.active_sam_jobs == 3
        assert snap.last_sam_finished_at == BASE + timedelta(minutes=2)

    _run(body)
