"""LoRA 전달 = 요청마다 presigned URL — 워커(클라이언트) 쪽 계약.

파드에 R2 자격증명을 두지 않는 대신, 렌더 요청이 만료 15분짜리 서명 URL 과 sha256 을 싣는다.
서명 URL 은 **로그·job_events·DB 어디에도 남지 않아야 한다**(잡 원장에 bearer URL 이 쌓이면
그 자체가 유출 경로다 — r2.preview_url 주석의 F3 지적과 같은 이유).
"""

import logging
from io import BytesIO

import pytest
from PIL import Image

from app.agents import face_identity as fi
from conftest import make_settings

LORA = "facemarket/loras/m/v1.safetensors"
SHA = "a" * 64
URL = "https://acct.r2.cloudflarestorage.com/wearless-face/v1.safetensors?X-Amz-Signature=zzz"


def _png():
    buf = BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _control():
    with Image.open(BytesIO(_png())) as im:
        im.load()
        return im.convert("RGB")


def _capture(monkeypatch):
    sent = {}

    class _Res:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            import base64
            return {"image_png": base64.b64encode(_png()).decode()}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(body=json or {}, headers=headers or {})
        return _Res()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_payload_carries_key_url_and_sha(monkeypatch):
    sent = _capture(monkeypatch)
    made = []
    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA, lora_sha256=SHA,
                                 url_provider=lambda key: made.append(key) or URL)
    backend.render(_control(), "ohwx man, photograph", 42)
    assert sent["body"]["lora"] == LORA          # 캐시 식별자
    assert sent["body"]["lora_sha256"] == SHA
    assert sent["body"]["lora_url"] == URL
    assert made == [LORA]                        # URL 은 호출 시점에 만든다(만료가 짧다)


def test_url_is_minted_per_request_not_reused(monkeypatch):
    _capture(monkeypatch)
    calls = []
    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA,
                                 url_provider=lambda key: calls.append(key) or URL)
    backend.render(_control(), "p", 42)
    backend.render(_control(), "p", 43)
    assert len(calls) == 2


def test_provider_failure_falls_back_to_pod_cache(monkeypatch, caplog):
    """URL 을 못 만들어도 렌더는 계속 간다 — 파드 캐시에 이미 있으면 성공한다."""
    sent = _capture(monkeypatch)

    def boom(_key):
        raise RuntimeError("no r2 credentials")

    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA, url_provider=boom)
    with caplog.at_level(logging.WARNING):
        backend.render(_control(), "p", 42)
    assert "lora_url" not in sent["body"]
    assert sent["body"]["lora"] == LORA
    assert not any(URL in r.getMessage() for r in caplog.records)


def test_presigned_url_never_reaches_logs(monkeypatch, caplog):
    sent = _capture(monkeypatch)
    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA, url_provider=lambda k: URL)
    with caplog.at_level(logging.DEBUG):
        backend.render(_control(), "p", 42)
    assert sent["body"]["lora_url"] == URL
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "X-Amz-Signature" not in joined and URL not in joined


def test_spec_from_lora_row_carries_sha256():
    spec = fi.face_identity_from_lora_row(
        {"lora_r2_key": LORA, "trigger_token": "ohwx man", "lora_sha256": SHA.upper()})
    assert (spec.lora_path, spec.token, spec.sha256) == (LORA, "ohwx man", SHA)
    # 값이 없어도 spec 은 만들어진다(파드 캐시 히트 경로) — 검증만 못 할 뿐이다
    assert fi.face_identity_from_lora_row({"lora_r2_key": LORA}).sha256 is None


def test_resolve_backend_wires_presigned_provider(monkeypatch):
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True,
                             face_identity_backend_url="http://pod:8000/render",
                             face_identity_backend_token="t",
                             r2_face_bucket="wearless-face",
                             r2_endpoint="https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(fi, "_BACKENDS", {})
    spec = fi.FaceIdentitySpec(LORA, "ohwx man", sha256=SHA)
    backend = fi.resolve_backend(settings, spec)
    assert isinstance(backend, fi.HttpFaceBackend)
    assert backend.lora_sha256 == SHA and backend.token == "t"
    assert callable(backend.url_provider)


def test_presigned_provider_is_none_without_face_bucket():
    settings = make_settings(gemini_api_key="x", r2_bucket="b", r2_face_bucket=None)
    assert fi._presigned_lora_url(settings) is None


def test_presigned_expiry_is_short():
    """만료가 길수록 유출 창이 길다. 렌더 1회(≈35초) + 재시도 여유면 된다."""
    assert 300 <= fi.LORA_URL_EXPIRES_S <= 1800


def test_code_version_mismatch_warns_but_does_not_block(monkeypatch, caplog):
    sent = _capture(monkeypatch)

    class _H:
        def json(self):
            return {"code_version": "old-sha"}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _H())
    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA, expected_version="new-sha")
    with caplog.at_level(logging.WARNING):
        backend.render(_control(), "p", 42)
    assert sent["body"]["lora"] == LORA          # 렌더는 그대로 나갔다
    assert any("code_version" in r.getMessage() for r in caplog.records)


def test_code_version_probe_runs_once(monkeypatch):
    _capture(monkeypatch)
    hits = []

    class _H:
        def json(self):
            return {"code_version": "same"}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: hits.append(url) or _H())
    backend = fi.HttpFaceBackend("http://pod:8000/render", lora=LORA, expected_version="same")
    backend.render(_control(), "p", 42)
    backend.render(_control(), "p", 43)
    assert hits == ["http://pod:8000/healthz"]
