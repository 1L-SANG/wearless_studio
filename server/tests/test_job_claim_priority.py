"""디스패처의 실제 대기열 우선순위는 DB claim 순서가 결정한다."""

import asyncio

from app import repo


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params):
        self.sql = " ".join(sql.split()).lower()
        self.params = params

    async def fetchone(self):
        return {"id": "mask-job", "kind": "editor_garment_mask"}


class _Conn:
    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur


def test_claim_prioritizes_a_later_editor_mask_over_older_background_jobs():
    """셀러 대면 마스크가 먼저 들어온 배경 전처리 잡을 claim 단계에서 추월한다."""
    conn = _Conn()
    row = asyncio.run(repo.claim_next_job(
        conn, ("sam_preprocess", "matching_cutout", "editor_garment_mask"), "worker"))

    assert row["kind"] == "editor_garment_mask"
    assert "case when kind in ('sam_preprocess', 'matching_cutout') then 1 else 0 end" in conn.cur.sql
    assert "kind = 'editor_garment_mask' then 0" not in conn.cur.sql
    assert "end, created_at" in conn.cur.sql
