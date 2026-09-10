"""얼굴 렌더 서비스(파드) — HttpFaceBackend 가 보내는 것을 그대로 받는가.

계약이 갈리면 프로덕션에서만 500 이 난다(파드는 로컬 테스트를 안 거친다).
그래서 여기서는 **HttpFaceBackend 가 실제로 만드는 payload** 를 그대로 서비스에 먹인다.
"""

import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import face_render_service as svc
from app.agents import face_identity

TOKEN = "s3cret-internal"
LORA_KEY = "facemarket/loras/m/v1.safetensors"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(svc, "TOKEN", TOKEN)
    return TestClient(svc.app)


class _Backend:
    """QwenLocalBackend 대역 — control 을 그대로 돌려준다(파이프라인 로드 없이 계약만 본다)."""

    steps = 25
    guidance_scale = 4.0
    negative_prompt = ""

    def __init__(self):
        self.calls = []

    def render(self, control, prompt, seed):
        self.calls.append({"prompt": prompt, "seed": seed, "steps": self.steps,
                           "guidance": self.guidance_scale, "negative": self.negative_prompt,
                           "size": control.size})
        return control.copy()


def _png(color=(200, 120, 60), size=(1024, 1024)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _sent_payload(monkeypatch) -> dict:
    """HttpFaceBackend.render 가 실제로 POST 하는 body 를 가로채서 돌려준다."""
    sent = {}

    class _Res:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"image_png": base64.b64encode(_png()).decode()}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(body=json, headers=headers or {}, url=url)
        return _Res()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    backend = face_identity.HttpFaceBackend("http://pod:8000/render", lora=LORA_KEY, token=TOKEN)
    with Image.open(BytesIO(_png())) as im:
        im.load()
        backend.render(im.convert("RGB"), "ohwx man, photograph", 42)
    return sent


def test_healthz_is_open_and_reports_load_state(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["token_configured"] is True
    assert body["loaded"] is False


def test_render_requires_token(client, monkeypatch):
    payload = {"control_png": base64.b64encode(_png()).decode(), "prompt": "p", "lora": LORA_KEY}
    assert client.post("/render", json=payload).status_code == 401
    assert client.post("/render", json=payload,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_render_is_closed_when_token_unset(monkeypatch):
    """토큰 미설정이면 열지 않는다 — 인증 없는 GPU 엔드포인트를 만들지 않는다."""
    monkeypatch.setattr(svc, "TOKEN", None)
    res = TestClient(svc.app).post("/render", json={
        "control_png": base64.b64encode(_png()).decode(), "prompt": "p", "lora": LORA_KEY})
    assert res.status_code == 503


def test_http_backend_payload_is_accepted_verbatim(client, monkeypatch):
    """HttpFaceBackend 가 보내는 body 를 서비스가 그대로 받고, 렌더 인자로 전달한다."""
    sent = _sent_payload(monkeypatch)
    assert sent["headers"]["Authorization"] == f"Bearer {TOKEN}"
    backend = _Backend()
    monkeypatch.setattr(svc, "_backend", lambda key: backend)

    res = client.post("/render", json=sent["body"], headers={"Authorization": f"Bearer {TOKEN}"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["seed"] == 42 and body["lora"] == LORA_KEY
    with Image.open(BytesIO(base64.b64decode(body["image_png"]))) as im:
        assert im.size == (1024, 1024)
    call = backend.calls[0]
    # 렌더 설정은 **요청이 준 값**이 정본이다(파드가 임의로 바꾸면 시드 재현이 깨진다).
    assert call["steps"] == face_identity.RENDER_STEPS
    assert call["guidance"] == face_identity.RENDER_GUIDANCE
    assert call["negative"] == face_identity.RENDER_NEGATIVE
    assert call["prompt"] == "ohwx man, photograph"


def test_response_is_what_http_backend_expects(client, monkeypatch):
    """서비스 응답이 HttpFaceBackend 의 파싱(image_png b64 → PIL)과 맞물리는가."""
    backend = _Backend()
    monkeypatch.setattr(svc, "_backend", lambda key: backend)
    sent = _sent_payload(monkeypatch)
    res = client.post("/render", json=sent["body"], headers={"Authorization": f"Bearer {TOKEN}"})
    data = base64.b64decode(res.json()["image_png"])
    with Image.open(BytesIO(data)) as im:
        im.load()
        assert im.convert("RGB").size == (1024, 1024)


def test_lora_cache_path_is_flattened(tmp_path, monkeypatch):
    """r2 키를 파일 경로로 그대로 쓰지 않는다(경로 탈출·디렉터리 생성 방지)."""
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    target = None
    for key in ("facemarket/loras/m/v1.safetensors", "../../etc/passwd"):
        got = None
        try:
            got = svc._lora_file(key)
        except Exception as exc:  # 다운로드는 이 테스트의 관심이 아니다(버킷 미설정 → 503)
            assert "R2_FACE_BUCKET" in str(exc)
            continue
        target = got
    # 캐시 디렉터리 밖으로 나가는 경로가 만들어지지 않았다
    assert target is None or target.startswith(str(tmp_path))
    assert not (tmp_path / "facemarket").exists()
