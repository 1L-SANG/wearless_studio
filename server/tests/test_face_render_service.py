"""얼굴 렌더 서비스(파드) — HttpFaceBackend 가 보내는 것을 그대로 받는가.

계약이 갈리면 프로덕션에서만 500 이 난다(파드는 로컬 테스트를 안 거친다).
그래서 여기서는 **HttpFaceBackend 가 실제로 만드는 payload** 를 그대로 서비스에 먹인다.
"""

import base64
import hashlib
import pathlib
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import face_render_service as svc
from app.agents import face_identity

# 32자 이상 — 프록시 URL 이 매니페스트에 있어 이 엔드포인트는 사실상 공개 주소다.
TOKEN = "s3cret-internal-0123456789abcdefghij"
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


def test_short_token_is_refused_as_a_config_error(monkeypatch):
    """약한 토큰으로 열지 않는다 — 있으나 마나 한 토큰은 없는 것과 같다."""
    monkeypatch.setattr(svc, "TOKEN", "short-token")
    client = TestClient(svc.app)
    body = client.get("/healthz").json()
    assert body["token_configured"] is False          # 헬스에서 설정 오류가 드러난다
    res = client.post("/render", json={
        "control_png": base64.b64encode(_png()).decode(), "prompt": "p", "lora": LORA_KEY},
        headers={"Authorization": "Bearer short-token"})
    assert res.status_code == 503 and "too short" in res.text
    assert svc.MIN_TOKEN_LEN >= 32


def test_start_script_refuses_placeholder_and_has_no_file_fallback():
    """볼륨 .token 폴백을 두면 그 파일이 곧 유출 지점이다 — 토큰은 파드 env 하나뿐."""
    text = pathlib.Path(svc.__file__).parent.joinpath("deploy/face_render/start.sh").read_text()
    assert "$ROOT/.token" not in text
    assert "PLACEHOLDER* )" in text
    assert "exit 78" in text


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
    monkeypatch.setattr(svc, "_backend", lambda key, url=None, sha=None: backend)

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
    monkeypatch.setattr(svc, "_backend", lambda key, url=None, sha=None: backend)
    sent = _sent_payload(monkeypatch)
    res = client.post("/render", json=sent["body"], headers={"Authorization": f"Bearer {TOKEN}"})
    data = base64.b64decode(res.json()["image_png"])
    with Image.open(BytesIO(data)) as im:
        im.load()
        assert im.convert("RGB").size == (1024, 1024)


def test_lora_cache_path_is_flattened(tmp_path, monkeypatch):
    """r2 키를 파일 경로로 그대로 쓰지 않는다(경로 탈출·디렉터리 생성 방지)."""
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    for key in ("facemarket/loras/m/v1.safetensors", "../../etc/passwd"):
        assert svc._cache_path(key).startswith(str(tmp_path))
    assert not (tmp_path / "facemarket").exists()


# ── LoRA 전달: 요청마다 presigned URL(파드에 R2 자격증명 없음) ──
_URL = "https://acct.r2.cloudflarestorage.com/wearless-face/x.safetensors?X-Amz-Signature=zzz"


def _weights(n=2048):
    return bytes(range(256)) * (n // 256)


class _Stream:
    """httpx.stream 대역 — 컨텍스트 매니저."""

    def __init__(self, status=200, body=b"", record=None):
        self.status_code, self._body, self._record = status, body, record

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, size=None):
        yield self._body


def _patch_stream(monkeypatch, *, status=200, body=b"", boom=None):
    calls = []

    def fake_stream(method, url, **kw):
        calls.append((method, url))
        if boom is not None:
            raise boom
        return _Stream(status, body)

    import httpx

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return calls


def test_cache_hit_never_touches_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    path = svc._cache_path(LORA_KEY)
    pathlib.Path(path).write_bytes(_weights())
    calls = _patch_stream(monkeypatch)
    assert svc._lora_file(LORA_KEY, None, "무관한값") == path      # URL 도 sha 도 안 본다
    assert calls == []


def test_missing_cache_without_url_is_a_clear_400(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        svc._lora_file(LORA_KEY, None, None)
    assert exc.value.status_code == 400 and "lora_url" in exc.value.detail


@pytest.mark.parametrize("url", [
    "http://acct.r2.cloudflarestorage.com/x",             # https 아님
    "https://evil.example.com/x",                          # 우리 호스트 아님
    "https://acct.r2.cloudflarestorage.com.evil.com/x",    # 접미사 위장
])
def test_only_https_r2_hosts_are_downloaded(tmp_path, monkeypatch, url):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    calls = _patch_stream(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        svc._lora_file(LORA_KEY, url, None)
    assert exc.value.status_code == 400
    assert calls == []                                     # 요청 자체를 안 보낸다
    assert list(tmp_path.iterdir()) == []


def test_sha256_mismatch_deletes_the_file_and_400s(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    _patch_stream(monkeypatch, body=_weights())
    with pytest.raises(HTTPException) as exc:
        svc._lora_file(LORA_KEY, _URL, "0" * 64)
    assert exc.value.status_code == 400 and "sha256" in exc.value.detail
    # 검증 실패본이 캐시에 남으면 다음 요청이 그걸 쓴다 — 파일이 없어야 한다
    assert list(tmp_path.iterdir()) == []


def test_sha256_match_caches_file_and_key_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    body = _weights()
    _patch_stream(monkeypatch, body=body)
    path = svc._lora_file(LORA_KEY, _URL, hashlib.sha256(body).hexdigest().upper())
    assert pathlib.Path(path).read_bytes() == body
    # start.sh 가 재기동 때 PRELOAD 할 키를 여기서 읽는다
    assert pathlib.Path(f"{path}.key").read_text() == LORA_KEY


@pytest.mark.parametrize("status", [403, 404, 500])
def test_expired_or_denied_url_is_a_502_naming_the_status(tmp_path, monkeypatch, status):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    _patch_stream(monkeypatch, status=status)
    with pytest.raises(HTTPException) as exc:
        svc._lora_file(LORA_KEY, _URL, None)
    assert exc.value.status_code == 502 and str(status) in exc.value.detail
    assert list(tmp_path.iterdir()) == []


def test_network_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    _patch_stream(monkeypatch, boom=OSError("connection reset"))
    with pytest.raises(HTTPException) as exc:
        svc._lora_file(LORA_KEY, _URL, None)
    assert exc.value.status_code == 502
    assert list(tmp_path.iterdir()) == []


def test_service_needs_no_r2_credentials():
    """파드에 R2 키를 두지 않는 것이 이 설계의 목적 — boto3·R2_* 경로가 남아 있으면 안 된다."""
    source = pathlib.Path(svc.__file__).read_text()
    assert "boto3" not in source
    assert "R2_ACCESS_KEY_ID" not in source and "R2_FACE_BUCKET" not in source
    assert svc.healthz()["ok"] is True          # 자격증명 없이도 뜬다


def test_healthz_reports_code_version():
    body = svc.healthz()
    assert "code_version" in body and body["cache_dir"] == svc.CACHE_DIR
