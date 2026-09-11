import asyncio

from app import repo


class _Cursor:
    def __init__(self, statements):
        self.statements = statements
        self.sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql, params=None):
        self.sql = " ".join(sql.split()).lower()
        self.statements.append((self.sql, params))

    async def fetchone(self):
        if self.sql.startswith("select id from jobs"):
            return {"id": "job-1"}
        if self.sql.startswith("select coalesce(max(sort_order)"):
            return {"v": 0}
        if self.sql.startswith("select coalesce(max(version)"):
            return {"v": 0}
        if self.sql.startswith("insert into wardrobe_images"):
            return {"id": "wardrobe-1"}
        return None


class _Conn:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _Cursor(self.statements)


def _asset_insert(conn):
    return next(
        (sql, params)
        for sql, params in conn.statements
        if sql.startswith("insert into assets")
    )


def test_detail_finalize_persists_server_written_real_derived_marker(monkeypatch):
    async def fake_release(*_args, **_kwargs):
        return 9

    monkeypatch.setattr(repo, "release_credits", fake_release)
    conn = _Conn()

    asyncio.run(repo.finalize_detail_page_success(
        conn,
        job_id="job-1",
        lease_token="lease",
        user_id="user-1",
        project_id="project-1",
        editor_blocks=[],
        cut_assets=[{
            "asset_id": "asset-1",
            "bucket": "r2",
            "key": "ai/asset-1.png",
            "mime": "image/png",
            "metadata": {"facemarket_real_derived": True},
        }],
        reserved=0,
        charge=0,
        metadata={},
    ))

    sql, params = _asset_insert(conn)
    assert "height, checksum, metadata)" in sql
    assert params[-1].obj == {"facemarket_real_derived": True}


def test_editor_finalize_persists_server_written_real_derived_marker(monkeypatch):
    async def fake_consume(*_args, **_kwargs):
        return 9

    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)
    conn = _Conn()

    asyncio.run(repo.finalize_editor_image_success(
        conn,
        job_id="job-1",
        lease_token="lease",
        user_id="user-1",
        project_id="project-1",
        image={
            "asset_id": "asset-1",
            "bucket": "r2",
            "key": "ai/asset-1.png",
            "mime": "image/png",
            "metadata": {"facemarket_real_derived": True},
        },
        group=None,
        cut_type="styling",
        reserved=0,
        charge=0,
        metadata={},
    ))

    sql, params = _asset_insert(conn)
    assert "height, checksum, metadata)" in sql
    assert params[-1].obj == {"facemarket_real_derived": True}


def test_mannequin_finalize_marks_cuts_as_not_real_derived(monkeypatch):
    """마네킹컷은 구조상 실인물 파생이 아니다 — 마커를 서버가 직접 박는다.

    마커가 없으면 `_asset_is_real_derived` 의 보수적 폴백(source == 'ai')이 걸려
    `/assets/{id}/file` 이 R2 공개 URL 대신 `private, no-store` 인 `/bytes` 로 302 한다.
    보관함 커버가 그 경로를 타면서 한 장마다 API 왕복 + 무캐시가 됐다(회귀).
    """
    async def fake_consume(*_args, **_kwargs):
        return 9

    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)
    conn = _Conn()

    asyncio.run(repo.finalize_mannequin_success(
        conn,
        job_id="job-1",
        lease_token="lease",
        user_id="user-1",
        project_id="project-1",
        candidates=[{
            "asset_id": "asset-1",
            "bucket": "r2",
            "key": "ai/asset-1.png",
            "mime": "image/png",
            "candidate": "A",
            "base_fit": "regular",
            "generation_metadata": {"generationPath": "fresh"},
        }],
        reserved=0,
        charge=0,
        metadata={},
    ))

    sql, params = _asset_insert(conn)
    assert "height, metadata)" in sql
    assert params[-1].obj["facemarket_real_derived"] is False
    # 생성 계보는 그대로 남아야 한다 — 마커는 덧붙이는 것이지 덮어쓰는 게 아니다.
    assert params[-1].obj["generationPath"] == "fresh"


def test_mannequin_adjust_finalize_marks_cut_as_not_real_derived(monkeypatch):
    """조정컷도 같은 파이프라인 산출물이다. 여기만 빠지면 조정 후 커버가 다시 느려진다."""
    async def fake_consume(*_args, **_kwargs):
        return 9

    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)
    conn = _Conn()

    asyncio.run(repo.finalize_mannequin_adjust_success(
        conn,
        job_id="job-1",
        lease_token="lease",
        user_id="user-1",
        project_id="project-1",
        base_candidate="A",
        cut={
            "asset_id": "asset-1",
            "bucket": "r2",
            "key": "ai/asset-1.png",
            "mime": "image/png",
            "base_fit": "regular",
        },
        reserved=0,
        charge=0,
        metadata={},
    ))

    sql, params = _asset_insert(conn)
    assert "height, metadata)" in sql
    assert params[-1].obj["facemarket_real_derived"] is False
