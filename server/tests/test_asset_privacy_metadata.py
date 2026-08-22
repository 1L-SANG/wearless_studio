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
    assert "height, metadata)" in sql
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
    assert "height, metadata)" in sql
    assert params[-1].obj == {"facemarket_real_derived": True}
