"""GET /v1/assets/{id}/bytes — 에디터 다운로드용 바이트 직접 서빙.

`/file`(302→R2)과 같은 capability URL 계약이지만 API가 바이트를 직접 실어 보낸다.
캔버스 픽셀 읽기(toBlob)에 CORS 가 필요한데 R2 쪽 헤더는 보장할 수 없기 때문(_tone_bytes 와
같은 근거). 회귀 방지:
① 무인증 200 + 바이트 + mime ② 형식이상 id 404 ③ 없는 asset 404 ④ 스토리지 장애 503(asset_unavailable — _tone_bytes 와 동일 계약).
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
    def __init__(self, data=b"png-bytes", fail=False):
        self._data = data
        self._fail = fail

    def get_bytes(self, key):
        if self._fail:
            raise RuntimeError("r2 down")
        return self._data


def _stub_asset(monkeypatch, row):
    async def fake_get_asset_public(conn, asset_id):
        return dict(row, id=asset_id) if row is not None else None
    monkeypatch.setattr(routes.repo, "get_asset_public", fake_get_asset_public)


def test_asset_bytes_serves_without_auth(client, monkeypatch):
    _stub_asset(monkeypatch, {"r2_key": "u1/p1/cut.png", "mime_type": "image/png", "source": "ai"})
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2(data=b"fake-png")

    res = client.get(f"/v1/assets/{uuid.uuid4()}/bytes")  # Authorization 헤더 없음
    assert res.status_code == 200, res.text
    assert res.content == b"fake-png"
    assert res.headers["content-type"].startswith("image/png")
    assert "immutable" in res.headers.get("cache-control", "")


def test_asset_bytes_invalid_id_is_404_before_db(client):
    client.app.state.r2 = _FakeR2()
    res = client.get("/v1/assets/not-a-uuid/bytes")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_asset_bytes_missing_asset_404(client, monkeypatch):
    _stub_asset(monkeypatch, None)
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2()

    res = client.get(f"/v1/assets/{uuid.uuid4()}/bytes")
    assert res.status_code == 404


def test_asset_bytes_storage_failure_is_503(client, monkeypatch):
    _stub_asset(monkeypatch, {"r2_key": "u1/p1/cut.png", "mime_type": "image/png", "source": "ai"})
    _no_db(monkeypatch)
    client.app.state.r2 = _FakeR2(fail=True)

    res = client.get(f"/v1/assets/{uuid.uuid4()}/bytes")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "asset_unavailable"
