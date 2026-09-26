"""추적 층 DB 헬퍼(fm_fingerprint_store) + 배선 계약(2026-09-26).

되돌아가면: 코드 충돌에 sign 이 500 이 되거나, 지문 기록 실패가 컷 종결을 되돌리거나, 앵커가
워터마크 전 해시를 체인에 올린다. 전부 조용한 사고라 여기서 잠근다.
"""
import asyncio
import contextlib
from pathlib import Path

import pytest
from psycopg.errors import UniqueViolation

from app import fm_fingerprint_store as S
from app.services import fm_fingerprint

APP = Path(__file__).resolve().parents[1] / "app"


class Cur:
    def __init__(self, conn):
        self.conn = conn
        self._row = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.conn.calls.append((" ".join(sql.split()), params))
        effect = self.conn.effects.pop(0) if self.conn.effects else None
        if isinstance(effect, Exception):
            raise effect
        self._row = effect

    async def executemany(self, sql, seq):
        seq = list(seq)
        self.conn.calls.append((" ".join(sql.split()), seq))
        if self.conn.fail_many:
            raise RuntimeError("relation does not exist")

    async def fetchone(self):
        return self._row


class Conn:
    def __init__(self, effects=(), fail_many=False):
        self.effects = list(effects)
        self.fail_many = fail_many
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return Cur(self)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.asynccontextmanager
    async def connection(self):
        yield self.conn


def test_allocate_retries_on_unique_violation_then_returns_code():
    conn = Conn([UniqueViolation("dup"), {"wm_code": 424242}])
    code = asyncio.run(S.allocate_wm_code(conn, "pub-1"))
    assert code == 424242
    assert conn.rollbacks == 1 and conn.commits == 1
    assert all("coalesce(wm_code" in sql for sql, _ in conn.calls)


def test_allocate_gives_up_softly_on_other_errors():
    conn = Conn([RuntimeError("column wm_code does not exist")])
    assert asyncio.run(S.allocate_wm_code(conn, "pub-1")) is None
    assert conn.rollbacks == 1


def test_save_publication_mark_converts_to_signed_bigint_and_commits():
    conn = Conn()
    fps = [{"kind": "strip", "region_y0": 0, "region_y1": 800, "phash": (1 << 64) - 1, "dhash": 5}]
    assert asyncio.run(S.save_publication_mark(
        conn, "pub-1", wm_status="embedded", wm_sha256="ab" * 32, fingerprints=fps))
    (_, upd), (_, rows) = conn.calls
    assert upd == ("embedded", "ab" * 32, "pub-1")
    assert rows == [("pub-1", "strip", 0, 800, -1, 5)]
    assert conn.commits == 1


def test_save_publication_mark_failure_is_swallowed():
    conn = Conn(fail_many=True)
    ok = asyncio.run(S.save_publication_mark(
        conn, "pub-1", wm_status="failed", wm_sha256=None,
        fingerprints=[{"kind": "publication", "phash": 1, "dhash": 2}]))
    assert ok is False and conn.rollbacks == 1


def test_record_cut_fingerprints_only_ledgered_cuts_and_never_raises():
    cuts = [
        {"asset_id": "a1", "provenance": {"license_id": "l"}, "fingerprint": {"phash": 1 << 63, "dhash": 7}},
        {"asset_id": "a2", "fingerprint": {"phash": 3, "dhash": 4}},            # 원장 없음(VIRTUAL)
        {"asset_id": "a3", "provenance": {"license_id": "l"}},                   # 지문 계산 실패
    ]
    items = S.cut_fingerprint_items(cuts)
    assert items == [{"asset_id": "a1", "phash": 1 << 63, "dhash": 7}]
    conn = Conn()
    assert asyncio.run(S.record_cut_fingerprints(Pool(conn), items)) == 1
    sql, rows = conn.calls[0]
    assert "from fm_output_records r where r.asset_id = %s" in sql
    assert rows == [(fm_fingerprint.to_signed(1 << 63), 7, "a1")]
    broken = Conn(fail_many=True)
    assert asyncio.run(S.record_cut_fingerprints(Pool(broken), items)) == 0
    assert asyncio.run(S.record_cut_fingerprints(None, items)) == 0


def _after(source: str, first: str, then: str) -> bool:
    return source.index(first) < source.index(then)


def test_workers_record_cut_fingerprints_only_after_finalize_commit():
    for name, finalize in (("detail_page_job.py", "finalize_detail_page_success"),
                           ("editor_image_job.py", "finalize_editor_image_success")):
        src = (APP / "workers" / name).read_text()
        assert "fm_fingerprint.safe_image_hashes" in src, name
        tail = src.split(f"repo.{finalize}(", 1)[1]
        assert _after(tail, "await conn.commit()", "record_cut_fingerprints("), name


def test_anchor_puts_the_watermarked_hash_on_chain():
    src = (APP / "workers" / "fm_publication_anchor.py").read_text()
    assert "coalesce(r.wm_sha256, r.image_sha256) as image_sha256" in src


def test_sign_marks_before_c2pa_signing():
    src = (APP / "facemarket_provenance.py").read_text()
    body = src.split("async def sign(", 1)[1]
    assert _after(body, "_mark_publication(", "sign_bytes, signer")
    zip_branch = body.split('elif kind == "zip":', 1)[1].split("else:", 1)[0]
    assert _after(zip_branch, "_mark_publication(", "r2.put_bytes")
