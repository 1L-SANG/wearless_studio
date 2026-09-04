"""층① 컷 원장 — finalize 트랜잭션 안에서만 행이 생긴다.

lease 를 뺏기면 워커는 방금 올린 R2 객체를 지운다. 원장 insert 가 그 트랜잭션 밖에 있으면
버려진 이미지의 원장 행이 남아 정산 근거로 쓸 수 없다. 여기서 그걸 못박는다.

Fix round 2 (리뷰 Minor 승격): "provenance 없으면 원장 행도 없다" 는 브리프가 못박은 최우선
불변식인데, 그 결정(`prov = c.get("provenance"); if prov and c.get("sha256"):`)은 두 finalizer
안에만 있고 아무 테스트도 그 분기를 실행하지 않았다 — 빈 리스트 테스트는 for 루프 바디가
무엇이든 항상 참이라 이름이 약속하는 걸 검증하지 못했다. 컷 1장에 대해 청구되지 않았거나
라이선스를 소비하지 않은 원장 행은 거짓 정산 근거이고, Task 4/7/9/10 이 이 테이블을 그대로
읽는다(Task 4 의 C2PA 서명용 `_resolve_project_license` 포함) — 잘못된 행 하나가 회수 불가능한
서명 파일로 번진다. 그래서 asset insert 는 항상 일어나고 원장 insert 만 조건부라는 것, 그리고
두 finalizer 가 lease 펜스를 동일하게 지킨다는 것을 바인딩된 파라미터까지 확인한다.
"""
import asyncio
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


class _Conn:
    """conn.cursor() 를 여러 번 불러도 같은 커서를 돌려주는 최소 fake — finalize 함수들이
    lease 펜스 블록과 크레딧 확정 뒤 job 종결 블록에서 각각 새 `async with conn.cursor()`를
    여는데, 두 호출 모두 같은 RecordingCursor 로 향해야 statements 가 한 곳에 쌓인다."""

    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


def ledger_inserts(cur):
    return [s for s in cur.statements if "insert into fm_output_records" in s[0]]


def asset_inserts(cur):
    return [s for s in cur.statements if s[0].startswith("insert into assets")]


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
    asyncio.run(repo.insert_output_records(cur, records=records))
    assert len(ledger_inserts(cur)) == 2


def test_insert_output_records_writes_nothing_for_empty_list():
    """이름이 약속하는 건 "빈 입력 → 빈 출력" 뿐이다 — for 루프 바디가 무엇이든 항상 참이라
    provenance 판단 로직 자체는 검증하지 않는다. 그 판단은 아래 finalize_* 테스트들이 맡는다."""
    cur = RecordingCursor()
    asyncio.run(repo.insert_output_records(cur, records=[]))
    assert ledger_inserts(cur) == []


def test_finalize_detail_page_writes_ledger_inside_lease_fence():
    """lease 를 잃으면 원장 행도 안 생긴다."""
    cur = RecordingCursor(lease_ok=False)
    out = asyncio.run(repo.finalize_detail_page_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": "c" * 64,
            "provenance": {"license_id": "l1", "model_id": "m1"},
        }], reserved=0, charge=0, metadata={},
    ))
    assert out is None
    assert ledger_inserts(cur) == []


def test_finalize_detail_page_records_asset_but_writes_no_ledger_row_without_provenance(monkeypatch):
    """VIRTUAL/NONE 컷(라이선스 미소비)은 asset 행은 그대로 생기고 checksum 도 채워지지만,
    원장 행은 없어야 한다. asset 이 안 생기면 이미지 자체가 조회 불가라 다른 문제이고, 원장
    행이 생기면 소비하지 않은 라이선스에 대한 거짓 정산 근거가 된다 — 이 테스트는 후자만
    막는다."""
    async def fake_release(*_a, **_kw):
        return 9
    monkeypatch.setattr(repo, "release_credits", fake_release)

    cur = RecordingCursor()
    sha = hashlib.sha256(b"virtual-cut").hexdigest()
    out = asyncio.run(repo.finalize_detail_page_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": sha,
            # provenance 키 자체가 없음 — VIRTUAL/NONE 소스는 라이선스를 소비하지 않는다.
        }], reserved=0, charge=0, metadata={},
    ))
    assert out is not None
    assert ledger_inserts(cur) == []
    rows = asset_inserts(cur)
    assert len(rows) == 1
    assert rows[0][1][9] == sha  # checksum 컬럼이 같은 해시로 채워졌는지


def test_finalize_detail_page_writes_ledger_row_only_for_the_cut_that_has_provenance(monkeypatch):
    """같은 잡 안에 VIRTUAL 컷과 REAL 컷이 섞여도, 원장 행은 정확히 REAL 컷 하나만 생기고
    그 행이 진짜 그 컷의 값(asset_id·license·해시)을 담아야 한다 — 개수만 세면 뒤바뀐 행도
    통과하므로 바인딩된 파라미터까지 확인한다."""
    async def fake_release(*_a, **_kw):
        return 9
    monkeypatch.setattr(repo, "release_credits", fake_release)

    cur = RecordingCursor()
    virtual_sha = hashlib.sha256(b"virtual").hexdigest()
    real_sha = hashlib.sha256(b"real").hexdigest()
    out = asyncio.run(repo.finalize_detail_page_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[
            {
                "asset_id": "a-virtual", "bucket": "b", "key": "k1", "mime": "image/png",
                "size": 3, "width": 1, "height": 1,
                "sha256": virtual_sha,
            },
            {
                "asset_id": "a-real", "bucket": "b", "key": "k2", "mime": "image/png",
                "size": 3, "width": 1, "height": 1,
                "sha256": real_sha,
                "provenance": {"license_id": "l1", "model_id": "m1"},
            },
        ], reserved=0, charge=0, metadata={},
    ))
    assert out is not None
    rows = ledger_inserts(cur)
    assert len(rows) == 1
    params = rows[0][1]
    assert params[0] == "a-real"   # asset_id — REAL 컷만, VIRTUAL 컷(a-virtual)이 아니어야 한다
    assert params[2] == "l1"       # license_id
    assert params[3] == "l1"       # license_ref — 비정규화 값도 같은 license id
    assert params[4] == "m1"       # model_id
    assert params[5] == "u1"       # seller_id == user_id
    assert params[6] == real_sha   # image_sha256 도 REAL 컷의 해시여야 한다(virtual_sha 아님)


def test_finalize_detail_page_skips_ledger_row_when_provenance_present_but_sha256_missing(monkeypatch):
    """provenance 는 있는데 해시가 없는 컷(있어서는 안 되지만)은 원장 행을 만들지 않는다 —
    가드가 `and` 이므로 둘 중 하나만 있어도 기록하지 않는다. 해시 없는 원장 행은 어떤 파일이
    실제로 만들어졌는지 증명할 수 없어 증거로 무의미하다."""
    async def fake_release(*_a, **_kw):
        return 9
    monkeypatch.setattr(repo, "release_credits", fake_release)

    cur = RecordingCursor()
    out = asyncio.run(repo.finalize_detail_page_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            # sha256 키가 없음
            "provenance": {"license_id": "l1", "model_id": "m1"},
        }], reserved=0, charge=0, metadata={},
    ))
    assert out is not None
    assert ledger_inserts(cur) == []


def test_finalize_editor_image_records_asset_but_writes_no_ledger_row_without_provenance(monkeypatch):
    """detail_page 와 같은 불변식의 editor_image 짝 — provenance 가 없으면(REAL 얼굴 미첨부)
    asset 은 생기지만 원장 행은 없다."""
    async def fake_consume(*_a, **_kw):
        return 9
    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)

    cur = RecordingCursor()
    sha = hashlib.sha256(b"virtual-editor").hexdigest()
    out = asyncio.run(repo.finalize_editor_image_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        image={
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": sha,
            # provenance 키 자체가 없음
        },
        group=None, cut_type="styling", reserved=0, charge=0, metadata={},
    ))
    assert out is not None
    assert ledger_inserts(cur) == []
    rows = asset_inserts(cur)
    assert len(rows) == 1
    assert rows[0][1][9] == sha


def test_finalize_editor_image_writes_ledger_inside_lease_fence():
    """detail_page 의 lease-펜스 계약을 editor_image 경로에서도 동일하게 확인한다 — 리뷰가
    코드를 읽고 "둘 다 같은 구조" 라고 수동으로 확인했던 것을, 이후 편집이 깨도 잡히도록
    테스트로 고정한다. lease 를 잃으면 asset 행도 원장 행도 하나도 생기지 않아야 한다."""
    cur = RecordingCursor(lease_ok=False)
    out = asyncio.run(repo.finalize_editor_image_success(
        _Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        image={
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": "c" * 64,
            "provenance": {"license_id": "l1", "model_id": "m1"},
        },
        group=None, cut_type="styling", reserved=0, charge=0, metadata={},
    ))
    assert out is None
    assert ledger_inserts(cur) == []
    assert asset_inserts(cur) == []
