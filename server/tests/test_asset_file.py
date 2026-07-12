"""GET /v1/assets/{id}/file — capability URL 서빙 (2026-07-11 무인증 전환).

브라우저 <img src> 는 Bearer 를 붙일 수 없으므로 이 라우트는 인증 없이 302 해야 한다
(마네킹컷·에디터 이미지가 화면에 뜨는 유일한 경로). 회귀 방지:
① 무인증 302 + Location=R2 public URL ② 형식이상 id 404 ③ 없는 asset 404.
"""
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
    def public_url(self, key):
        return f"https://pub.example.com/{key}"


def test_asset_file_serves_without_auth(client, monkeypatch):
    async def fake_get_asset_public(conn, asset_id):
        return {"id": asset_id, "r2_key": "u1/p1/cut.png", "mime_type": "image/png", "source": "ai"}

    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()

    aid = str(uuid.uuid4())
    res = client.get(f"/v1/assets/{aid}/file", follow_redirects=False)  # Authorization 헤더 없음
    assert res.status_code == 302, res.text
    assert res.headers["location"] == "https://pub.example.com/u1/p1/cut.png"


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
    client.app.state.r2 = _FakeR2()

    res = client.get(f"/v1/assets/{uuid.uuid4()}/file", follow_redirects=False)
    assert res.status_code == 404
