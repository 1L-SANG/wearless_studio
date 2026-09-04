"""층① 컷 원장 — finalize 트랜잭션 안에서만 행이 생긴다.

lease 를 뺏기면 워커는 방금 올린 R2 객체를 지운다. 원장 insert 가 그 트랜잭션 밖에 있으면
버려진 이미지의 원장 행이 남아 정산 근거로 쓸 수 없다. 여기서 그걸 못박는다.
"""
import hashlib

import pytest

from app import repo


class RecordingCursor:
    def __init__(self, lease_ok=True):
        self.lease_ok = lease_ok
        self.statements = []
        self._last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if "from jobs where id" in sql and "locked_by" in sql:
            self._last = {"id": "j1"} if self.lease_ok else None
        elif "coalesce(max(" in sql:
            self._last = {"v": 1}
        else:
            self._last = {"id": "x"}

    async def fetchone(self):
        return self._last

    async def fetchall(self):
        return []


def ledger_inserts(cur):
    return [s for s in cur.statements if "insert into fm_output_records" in s[0]]


def test_insert_output_records_writes_one_row_per_cut():
    cur = RecordingCursor()
    records = [
        {
            "asset_id": "a1", "job_id": "j1", "license_id": "l1", "license_ref": "l1",
            "model_id": "m1", "seller_id": "u1",
            "image_sha256": hashlib.sha256(b"one").hexdigest(), "byte_size": 3,
        },
        {
            "asset_id": "a2", "job_id": "j1", "license_id": "l1", "license_ref": "l1",
            "model_id": "m1", "seller_id": "u1",
            "image_sha256": hashlib.sha256(b"two").hexdigest(), "byte_size": 3,
        },
    ]
    import asyncio
    asyncio.run(repo.insert_output_records(cur, records=records))
    assert len(ledger_inserts(cur)) == 2


def test_insert_output_records_skips_when_no_provenance():
    cur = RecordingCursor()
    import asyncio
    asyncio.run(repo.insert_output_records(cur, records=[]))
    assert ledger_inserts(cur) == []


def test_finalize_detail_page_writes_ledger_inside_lease_fence():
    """lease 를 잃으면 원장 행도 안 생긴다."""
    import asyncio

    class Conn:
        def __init__(self, cur):
            self._cur = cur

        def cursor(self):
            return self._cur

    cur = RecordingCursor(lease_ok=False)
    out = asyncio.run(repo.finalize_detail_page_success(
        Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": "c" * 64,
            "provenance": {"license_id": "l1", "model_id": "m1"},
        }], reserved=0, charge=0, metadata={},
    ))
    assert out is None
    assert ledger_inserts(cur) == []
