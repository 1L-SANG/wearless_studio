"""GET /v1/assets/{id}/file — capability URL 서빙 (2026-07-11 무인증 전환).

브라우저 <img src> 는 Bearer 를 붙일 수 없으므로 이 라우트는 인증 없이 302 해야 한다
(마네킹컷·에디터 이미지가 화면에 뜨는 유일한 경로). 회귀 방지:
① 무인증 302 + Location=R2 public URL ② 형식이상 id 404 ③ 없는 asset 404.
"""
import asyncio
import contextlib
import uuid

import app.routes as routes


class _Conn:
    pass


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


class _FakeR2:
    def __init__(self):
        self.public_url_calls = []

    def public_url(self, key):
        self.public_url_calls.append(key)
        return f"https://pub.example.com/{key}"

    def get_bytes(self, key):
        return f"bytes:{key}".encode()


def test_public_asset_lookup_loads_server_written_privacy_marker():
    seen = {}

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, sql, params):
            seen["sql"] = " ".join(sql.split()).lower()
            seen["params"] = params

        async def fetchone(self):
            return {"metadata": {"facemarket_real_derived": True}}

    class Conn:
        def cursor(self):
            return Cursor()

    asset_id = str(uuid.uuid4())
    row = asyncio.run(routes.repo.get_asset_public(Conn(), asset_id))

    assert "source, metadata from assets" in seen["sql"]
    assert seen["params"] == (asset_id,)
    assert row["metadata"]["facemarket_real_derived"] is True


def test_asset_file_serves_without_auth(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return {
            "id": asset_id,
            "r2_key": "u1/p1/cut.png",
            "mime_type": "image/png",
            "source": "ai",
            "metadata": {"facemarket_real_derived": False},
        }

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()

    aid = str(uuid.uuid4())
    res = client.get(f"/v1/assets/{aid}/file", follow_redirects=False)  # Authorization 헤더 없음
    assert res.status_code == 302, res.text
    assert res.headers["location"] == "https://pub.example.com/u1/p1/cut.png"
    assert "immutable" in res.headers["cache-control"]


def test_real_derived_asset_file_redirect_is_never_cached(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return {
            "id": asset_id,
            "r2_key": "u1/p1/real-cut.png",
            "mime_type": "image/png",
            "source": "ai",
            "metadata": {"facemarket_real_derived": True},
        }

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()
    asset_id = uuid.uuid4()

    res = client.get(f"/v1/assets/{asset_id}/file?e=2", follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers["location"] == f"/v1/assets/{asset_id}/bytes?e=2"
    assert client.app.state.r2.public_url_calls == []


def test_legacy_unmarked_ai_asset_is_conservatively_never_cached(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return {
            "id": asset_id,
            "r2_key": "u1/p1/legacy-ai.png",
            "mime_type": "image/png",
            "source": "ai",
            "metadata": {"legacy": True},
        }

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()
    asset_id = uuid.uuid4()

    res = client.get(f"/v1/assets/{asset_id}/file?e=2", follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers["location"] == f"/v1/assets/{asset_id}/bytes?e=2"
    assert client.app.state.r2.public_url_calls == []


def test_sensitive_file_redirect_reaches_no_store_bytes_without_loop(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return {
            "id": asset_id,
            "r2_key": "u1/p1/real-cut.png",
            "mime_type": "image/png",
            "source": "ai",
            "metadata": {"facemarket_real_derived": True},
        }

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()
    asset_id = uuid.uuid4()

    res = client.get(f"/v1/assets/{asset_id}/file?e=2")

    assert res.status_code == 200
    assert res.content == b"bytes:u1/p1/real-cut.png"
    assert res.headers["cache-control"] == "private, no-store"
    assert len(res.history) == 1
    assert res.history[0].headers["location"] == f"/v1/assets/{asset_id}/bytes?e=2"


def test_asset_file_invalid_id_is_404_before_db(client):
    client.app.state.r2 = _FakeR2()
    res = client.get("/v1/assets/not-a-uuid/file", follow_redirects=False)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_asset_file_missing_asset_404(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return None

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    fake_r2 = _FakeR2()
    client.app.state.r2 = fake_r2

    res = client.get(f"/v1/assets/{uuid.uuid4()}/file?e=2", follow_redirects=False)
    assert res.status_code == 404
    assert fake_r2.public_url_calls == []
