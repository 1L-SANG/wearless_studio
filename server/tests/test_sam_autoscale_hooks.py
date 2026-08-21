"""prewarm 훅 — 업로드 서명 발급과 SamUnavailable 에서 '지금 켜라'를 쏜다. 실패해도 응답은 그대로."""

import asyncio
import contextlib

import pytest

import app.routes as routes
from app.services import sam_client


class _Scaler:
    def __init__(self, fail=False):
        self.soon_calls = 0
        self.prewarm_calls = 0
        self.fail = fail

    def prewarm_soon(self):
        self.soon_calls += 1                   # 라우트는 이것만 부른다(동기, 참조 보관은 실물이 한다)
        if self.fail:
            raise RuntimeError("aws down")

    async def prewarm(self):
        self.prewarm_calls += 1
        if self.fail:
            raise RuntimeError("aws down")


class _R2:
    def presigned_put(self, key, mime):
        return f"https://upload.test/{key}"


class _Conn:
    async def commit(self):
        return None


def _issue(client, make_token, monkeypatch):
    """draft_slot 용도 — 프로젝트 조회를 건너뛰므로 DB 대역이 가장 작다."""
    client.app.state.r2 = _R2()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)
    return client.post(
        "/v1/assets/upload-url",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"filename": "a.jpg", "mime": "image/jpeg", "size": 10,
              "projectId": None, "purpose": "draft_slot"})


# ── 업로드 라우트 ──

def test_upload_url_calls_prewarm_soon(client, make_token, monkeypatch):
    sc = _Scaler()
    client.app.state.sam_autoscaler = sc
    r = _issue(client, make_token, monkeypatch)
    assert r.status_code == 200, r.text
    assert sc.soon_calls == 1


def test_upload_url_succeeds_even_when_the_hook_raises(client, make_token, monkeypatch):
    client.app.state.sam_autoscaler = _Scaler(fail=True)
    r = _issue(client, make_token, monkeypatch)
    assert r.status_code == 200, r.text


def test_upload_url_works_without_a_scaler_on_state(client, make_token, monkeypatch):
    if hasattr(client.app.state, "sam_autoscaler"):
        del client.app.state.sam_autoscaler
    r = _issue(client, make_token, monkeypatch)
    assert r.status_code == 200, r.text


# ── SamUnavailable 중앙 훅 ──

class _Unconfigured:
    sam_service_url = None
    sam_internal_token = None


def test_sam_unavailable_fires_the_hook_once_per_raise():
    sc = _Scaler()
    sam_client.install_prewarm_hook(sc.prewarm)
    try:
        with pytest.raises(sam_client.SamUnavailable):
            asyncio.run(sam_client.segment_garment(_Unconfigured(), {"Front": "k"}))
        assert sc.prewarm_calls == 1
    finally:
        sam_client.install_prewarm_hook(None)


def test_hook_failure_does_not_mask_sam_unavailable():
    sam_client.install_prewarm_hook(_Scaler(fail=True).prewarm)
    try:
        with pytest.raises(sam_client.SamUnavailable):
            asyncio.run(sam_client.segment_garment(_Unconfigured(), {"Front": "k"}))
    finally:
        sam_client.install_prewarm_hook(None)


def test_no_hook_installed_is_fine():
    sam_client.install_prewarm_hook(None)
    with pytest.raises(sam_client.SamUnavailable):
        asyncio.run(sam_client.segment_garment(_Unconfigured(), {"Front": "k"}))


def test_cause_chain_is_preserved_through_the_hook(monkeypatch):
    """`from e` 가 있던 자리는 계속 __cause__ 를 들고 있어야 한다 — 디버깅 정보가 사라지면 안 된다."""
    import httpx

    class _S:
        sam_service_url = "http://sam2:8080"
        sam_internal_token = "t"
        sam_request_timeout_s = 1.0

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Client)
    with pytest.raises(sam_client.SamUnavailable) as ei:
        asyncio.run(sam_client.segment_garment(_S(), {"Front": "k"}))
    assert isinstance(ei.value.__cause__, httpx.ConnectError)
