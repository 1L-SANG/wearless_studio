"""렌더 파드 준비 판정 = **베이스 모델이 올라왔는가**(base_loaded). LoRA 는 요청마다 붙는다.

실측(2026-09-11 파드 mksow4h9n6zlq7): 캐시가 비어 preload=none 으로 뜨면 /healthz loaded 는 첫 렌더가
끝나야 true 가 됐다. 준비를 loaded 로 판정하는 wait_for_backend·준비 칩·autoscale describe 는 그동안
"아직 안 떴다"고 봤다 — 서비스는 멀쩡히 응답하고 있었는데도.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import face_render_service as svc
from app.agents import face_identity
from app.agents.face_identity_qwen import QwenLocalBackend

TOKEN = "s3cret-internal-0123456789abcdefghij"
LORA_KEY = "facemarket/loras/m/v1.safetensors"


class _Pipe:
    """diffusers 파이프라인 대역 — 어떤 메서드가 불렸는지만 남긴다."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*a, **k):
            self.calls.append(name)
            return self
        return record


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(svc, "TOKEN", TOKEN)
    monkeypatch.setattr(svc, "PRELOAD_LORA", None)
    monkeypatch.setattr(svc, "_state", {"lora": None, "backend": None, "base": None,
                                        "loaded_at": None, "renders": 0})
    loads = []

    def fake_load_base(model_id, device, **kw):
        loads.append((model_id, device, kw))
        return _Pipe()

    monkeypatch.setattr(svc, "load_base_pipeline", fake_load_base)
    return loads


# ── 서비스: 기동 때 베이스를 올린다 ──
def test_startup_preloads_the_base_and_healthz_separates_base_from_lora(fresh):
    with TestClient(svc.app) as client:          # with = lifespan 실행
        body = client.get("/healthz").json()
    assert len(fresh) == 1 and fresh[0][0] == svc.MODEL_ID
    assert body["base_loaded"] is True
    assert body["loaded"] is False               # LoRA 는 아직 — 첫 요청이 붙인다
    assert body["lora"] is None


def test_first_render_fuses_the_lora_onto_the_preloaded_base(fresh, monkeypatch):
    monkeypatch.setattr(svc, "_lora_file", lambda key, url=None, sha256=None: "/tmp/x.safetensors")
    with TestClient(svc.app) as client:
        backend = svc._backend(LORA_KEY)
        body = client.get("/healthz").json()
    assert isinstance(backend, QwenLocalBackend)
    pipe = backend.pipeline()
    assert "from_pretrained" not in pipe.calls                     # 베이스를 다시 받지 않는다
    assert pipe.calls[:3] == ["load_lora_weights", "set_adapters", "fuse_lora"]
    assert len(fresh) == 1                                          # 베이스 적재는 기동 때 1회뿐
    assert body["base_loaded"] is True and body["loaded"] is True and body["lora"] == LORA_KEY


def test_without_preload_env_the_base_is_not_loaded(fresh, monkeypatch):
    monkeypatch.setattr(svc, "PRELOAD_BASE", False)
    with TestClient(svc.app) as client:
        body = client.get("/healthz").json()
    assert fresh == [] and body["base_loaded"] is False and body["loaded"] is False


# ── 백엔드: 베이스를 받으면 LoRA 만 붙인다 ──
def test_backend_with_a_base_pipe_only_loads_and_fuses_the_lora():
    base = _Pipe()
    backend = QwenLocalBackend("/tmp/x.safetensors", base_pipe=base)
    assert backend.pipeline() is base
    assert base.calls == ["load_lora_weights", "set_adapters", "fuse_lora"]
    assert backend.pipeline() is base and len(base.calls) == 3      # 두 번째 호출은 그대로


# ── 판정: wait_for_backend·준비 칩·autoscale 전부 base_loaded 를 본다 ──
@pytest.mark.parametrize("payload,ready", [
    ({"ok": True, "base_loaded": True, "loaded": False, "lora": None}, True),
    ({"ok": True, "base_loaded": False, "loaded": False}, False),
    ({"ok": True, "loaded": True}, True),           # base_loaded 를 모르는 예전 묶음의 파드
    ({"ok": True, "loaded": False}, False),
])
def test_probe_ready_judges_by_base_loaded(monkeypatch, payload, ready):
    class _Res:
        status_code = 200

        def json(self):
            return payload

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout=5.0: _Res())
    assert face_identity._probe_ready("https://pod-8000.proxy.runpod.net/healthz") is ready
    assert face_identity.healthz_ready(payload) is ready


def test_autoscale_running_judges_by_base_loaded():
    from app.services.face_autoscale import RunpodAutoscaleAdapter, RunpodTarget
    from conftest import make_settings

    class _Health:
        def get(self, url):
            class _R:
                status_code = 200

                def json(self):
                    return {"ok": True, "base_loaded": True, "loaded": False}
            return _R()

    class _Client:
        def get(self, path):
            class _R:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"desiredStatus": "RUNNING", "lastStartedAt": "2026-09-11 09:37:07.198 +0000 UTC"}
            return _R()

    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_autoscale="on",
                             face_runpod_pod_id="mksow4h9n6zlq7", face_runpod_api_key="k")
    adapter = RunpodAutoscaleAdapter(settings, client=_Client(), health_client=_Health())
    state = asyncio.run(adapter.describe(RunpodTarget("mksow4h9n6zlq7")))
    assert (state.desired, state.running, state.pending) == (1, 1, 0)
