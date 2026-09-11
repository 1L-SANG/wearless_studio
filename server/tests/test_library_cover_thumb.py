"""GET /v1/projects?view=library — 커버를 CDN 리사이즈 URL 로 내려준다.

보관함 그리드는 카드 한 장이 220px 인데 커버 원본은 848×1264(수백 KB)다. 카드가 쓸
크기로 Cloudflare 가 줄여 주면 그리드 한 화면의 전송량이 한 자릿수 퍼센트로 떨어지고,
`/v1/assets/{id}/file` 302 를 건너뛰므로 커버마다 붙던 us-east-1 왕복도 사라진다.

다만 변환본은 `images.wearless.kr/cdn-cgi/image/...` 로 **다른 경로**에 캐시된다.
`R2Client.purge_public_cache` 의 prefix purge(`images.wearless.kr/{key}`)는 그 경로를
덮지 못하므로, 생체 파생으로 분류된 자산에는 변환본을 절대 만들지 않는다 — 그래야
"purge 가 변환본을 못 지운다"는 사실이 도달 불가능한 경로로 남는다.
"""
import contextlib

import app.routes as routes


class _Conn:
    pass


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


class _FakeR2:
    def __init__(self, public_base="https://images.example.com"):
        self._public_base = public_base

    def public_url(self, key):
        return f"{self._public_base}/{key}"

    def public_thumb_url(self, key, width, **_kwargs):
        if not self._public_base:
            return None
        return (f"{self._public_base}/cdn-cgi/image/"
                f"width={width},quality=80,format=auto,fit=cover/{key}")


def _row(**over):
    row = {
        "id": "project-1",
        "title": "프로젝트",
        "cover": "/v1/assets/asset-1/file",
        "cover_r2_key": "u1/p1/cut.png",
        "cover_source": "ai",
        "cover_metadata": {"facemarket_real_derived": False},
        "clothing_type": "top",
        "block_count": 3,
        "status": "done",
        "updated_at": "2026-09-11T00:00:00+00:00",
    }
    row.update(over)
    return row


def _get(client, monkeypatch, rows, r2=None):
    async def fake_list_library(_conn, _user_id):
        return rows

    monkeypatch.setattr(routes.repo, "list_library", fake_list_library)
    _no_db(monkeypatch)
    client.app.state.r2 = r2 or _FakeR2()
    monkeypatch.setitem(
        client.app.dependency_overrides, routes.require_user, lambda: "user-1")
    res = client.get("/v1/projects?view=library")
    assert res.status_code == 200, res.text
    return res.json()


def test_cover_is_served_through_the_cdn_resize(client, monkeypatch):
    body = _get(client, monkeypatch, [_row()])

    assert body[0]["cover"] == (
        "https://images.example.com/cdn-cgi/image/"
        "width=440,quality=80,format=auto,fit=cover/u1/p1/cut.png"
    )


def test_real_derived_cover_never_gets_a_cdn_variant(client, monkeypatch):
    """민감 자산은 capability URL 그대로 — 변환본이 생기면 prefix purge 를 빠져나간다."""
    body = _get(client, monkeypatch, [_row(
        cover_metadata={"facemarket_real_derived": True},
    )])

    assert body[0]["cover"] == "/v1/assets/asset-1/file"


def test_unmarked_ai_cover_keeps_the_conservative_capability_url(client, monkeypatch):
    """마커 없는 legacy `ai` 자산은 서빙 경로와 같은 보수적 분류를 따른다."""
    body = _get(client, monkeypatch, [_row(cover_metadata={})])

    assert body[0]["cover"] == "/v1/assets/asset-1/file"


def test_cover_falls_back_when_no_public_cdn_is_configured(client, monkeypatch):
    """로컬·dev 처럼 R2 공개 도메인이 없으면 기존 경로를 그대로 쓴다."""
    body = _get(client, monkeypatch, [_row()], r2=_FakeR2(public_base=""))

    assert body[0]["cover"] == "/v1/assets/asset-1/file"


def test_project_without_a_cover_stays_empty(client, monkeypatch):
    body = _get(client, monkeypatch, [_row(
        cover="", cover_r2_key=None, cover_source=None, cover_metadata=None,
    )])

    assert body[0]["cover"] == ""
