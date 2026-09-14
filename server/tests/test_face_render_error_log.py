"""파드 렌더가 HTTP 로 거절당하면 **상태코드와 응답 앞머리**를 남긴다.

지금까지 남은 건 "fallback:backend_error" 한 줄뿐이라, 401(토큰 틀림)·413(본문 큼)·500(OOM)이
로그에서 구분되지 않았다. 상태코드 하나만 있으면 대부분 바로 갈린다.

★ 대신 URL·토큰·presigned 는 절대 안 나간다 — 그 규칙은 test_face_presigned_lora 가 잠근다.
  여기서는 응답 **본문**만 120자까지 자른다(파드 트레이스백이 통째로 쏟아지지 않게).
"""

import base64
import logging
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.agents import face_identity as fi

TOKEN = "super-secret-token"
URL = "https://acct.r2.cloudflarestorage.com/w/v1.safetensors?X-Amz-Signature=zzz"


def _control():
    return Image.new("RGB", (64, 64), (10, 20, 30))


def _fail(monkeypatch, status: int, body: str):
    request = httpx.Request("POST", "https://pod-8000.proxy.runpod.net/render")
    response = httpx.Response(status, text=body, request=request)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: response)
    return response


def test_the_status_code_and_a_short_body_reach_the_log(monkeypatch, caplog):
    _fail(monkeypatch, 500, "Traceback (most recent call last):\n  CUDA out of memory\n" + "x" * 500)
    backend = fi.HttpFaceBackend("https://pod-8000.proxy.runpod.net/render", token=TOKEN)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(httpx.HTTPStatusError):
            backend.render(_control(), "ohwx man, photograph", 42)
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "500" in line
    assert "CUDA out of memory" in line
    assert "x" * 200 not in line, "응답을 통째로 쏟으면 안 된다"


@pytest.mark.parametrize("status", [401, 413, 502])
def test_every_rejected_status_is_named(monkeypatch, caplog, status):
    _fail(monkeypatch, status, "nope")
    backend = fi.HttpFaceBackend("https://pod-8000.proxy.runpod.net/render", token=TOKEN)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(httpx.HTTPStatusError):
            backend.render(_control(), "p", 42)
    assert str(status) in "\n".join(r.getMessage() for r in caplog.records)


def test_the_token_and_the_signed_url_never_reach_the_log(monkeypatch, caplog):
    _fail(monkeypatch, 403, "forbidden")
    backend = fi.HttpFaceBackend("https://pod-8000.proxy.runpod.net/render", token=TOKEN,
                                 lora="facemarket/loras/m/v1.safetensors",
                                 url_provider=lambda key: URL)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(httpx.HTTPStatusError):
            backend.render(_control(), "p", 42)
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in line and URL not in line and "X-Amz-Signature" not in line


def test_a_success_still_returns_the_image(monkeypatch):
    """로그를 붙이다가 정상 경로를 막지 않았는지 — 200 은 그대로 그림을 돌려준다."""
    buf = BytesIO()
    Image.new("RGB", (fi.CROP, fi.CROP), (7, 8, 9)).save(buf, "PNG")
    payload = {"image_png": base64.b64encode(buf.getvalue()).decode()}
    request = httpx.Request("POST", "https://pod-8000.proxy.runpod.net/render")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(200, json=payload, request=request))
    out = fi.HttpFaceBackend("https://pod-8000.proxy.runpod.net/render").render(_control(), "p", 42)
    assert out.size == (fi.CROP, fi.CROP) and out.mode == "RGB"
