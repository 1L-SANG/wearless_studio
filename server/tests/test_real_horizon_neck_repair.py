import asyncio
import base64
import hashlib
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.agents import face_identity, gemini_image, real_horizon_neck_repair as neck
from app.agents.gemini_image import GeminiError, InlineImage
from app.config import load_settings
from conftest import make_settings

REAL = "22222222-2222-4222-8222-222222222222"
IDENTITY = face_identity.FaceIdentitySpec("lora", references=((1.0, 0.0),))


def png(size=(60, 90), color="white"):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def test_production_default_is_enabled_and_environment_can_disable_it(monkeypatch):
    monkeypatch.delenv("REAL_HORIZON_NECK_REPAIR_ENABLED", raising=False)
    assert load_settings().real_horizon_neck_repair_enabled
    monkeypatch.setenv("REAL_HORIZON_NECK_REPAIR_ENABLED", "false")
    assert not load_settings().real_horizon_neck_repair_enabled


@pytest.mark.parametrize("cut,model,attached,outcome,generator,enabled,expected", [
    ("horizon", REAL, True, "applied", "gpt-image-2", True, True),
    ("styling", REAL, True, "applied", "gpt-image-2", True, False),
    ("mirror", REAL, True, "applied", "gpt-image-2", True, False),
    ("product", REAL, True, "applied", "gpt-image-2", True, False),
    ("horizon", "mA", True, "applied", "gpt-image-2", True, False),
    ("horizon", REAL, False, "applied", "gpt-image-2", True, False),
    ("horizon", REAL, True, "skipped:too_small", "gpt-image-2", True, False),
    ("horizon", REAL, True, "fallback:gate_failed", "gpt-image-2", True, False),
    ("horizon", REAL, True, "applied", "gemini-3-pro-image", True, False),
    ("horizon", REAL, True, "applied", "gpt-image-2", False, False),
])
def test_scope_is_only_gpt_real_horizon_after_an_applied_face_pass(
    cut, model, attached, outcome, generator, enabled, expected,
):
    assert neck.eligible(
        make_settings(real_horizon_neck_repair_enabled=enabled),
        {"cutType": cut, "modelId": model}, generation_model=generator,
        real_identity_attached=attached, outcome={"face_pass": outcome},
    ) is expected


@pytest.mark.parametrize("score,reason", [(0.9, None), (0.2, "identity_low"), (None, "identity_unavailable")])
def test_repair_returns_raw_api_bytes_only_when_identity_survives(monkeypatch, score, reason):
    original = InlineImage("image/png", png())
    raw_output = png((120, 180), "gray")
    calls = []

    class Client:
        async def edit_image(self, model, prompt, image, **kwargs):
            calls.append((model, prompt, image, kwargs))
            return SimpleNamespace(image=raw_output, mime="image/png")

    monkeypatch.setattr(face_identity, "identity_score", lambda *_a, **_k: score)
    result, metadata = asyncio.run(neck.repair(make_settings(), Client(), original, IDENTITY))
    assert len(calls) == 1
    model, prompt, image, kwargs = calls[0]
    assert model == "gpt-image-2.5-sunburst"
    assert image is original  # 전체 원본, 크롭·리사이즈 없음.
    assert kwargs == {"size": "auto", "quality": "high"}
    assert "neck-to-garment boundary" in prompt
    assert metadata["inputSha256"] == hashlib.sha256(original.data).hexdigest()
    assert metadata["outputSha256"] == hashlib.sha256(raw_output).hexdigest()
    if reason:
        assert result is original and not metadata["applied"]
        assert metadata["reason"] == reason
    else:
        assert result.data == raw_output  # 합성·재인코딩 없이 API 바이트 그대로.
        assert metadata["applied"]
        assert metadata["outputDimensions"] == [120, 180]


def test_changed_aspect_ratio_is_rejected_before_identity_check(monkeypatch):
    original = InlineImage("image/png", png())

    class Client:
        async def edit_image(self, *_a, **_k):
            return SimpleNamespace(image=png((90, 60)), mime="image/png")

    def no_identity(*_a, **_k):
        raise AssertionError("잘못된 구도는 동일인 검사 전에 탈락한다")

    monkeypatch.setattr(face_identity, "identity_score", no_identity)
    result, metadata = asyncio.run(neck.repair(make_settings(), Client(), original, IDENTITY))
    assert result is original
    assert metadata["reason"] == "aspect_ratio_changed"


@pytest.mark.parametrize("failure", [GeminiError("timeout", billable=True), ValueError("bad image")])
def test_provider_or_validation_failure_preserves_qwen_without_retry(failure):
    original = InlineImage("image/png", png())
    calls = []

    class Client:
        async def edit_image(self, *_a, **_k):
            calls.append(1)
            if isinstance(failure, GeminiError):
                raise failure
            return SimpleNamespace(image=b"not an image", mime="image/png")

    result, metadata = asyncio.run(neck.repair(make_settings(), Client(), original, IDENTITY))
    assert result is original
    assert calls == [1]
    assert not metadata["applied"] and metadata["reason"] == "repair_unavailable"


def test_missing_reference_bank_uses_the_already_applied_qwen_face(monkeypatch):
    original = InlineImage("image/png", png())
    bank = ((0.5, 0.5),)
    seen = {}

    class Client:
        async def edit_image(self, *_a, **_k):
            return SimpleNamespace(image=png(), mime="image/png")

    def references(images, model_dir):
        assert images == [original.data]
        return bank

    def score(image, references, **kwargs):
        seen["bank"] = references
        return 0.9

    monkeypatch.setattr(face_identity, "reference_embeddings", references)
    monkeypatch.setattr(face_identity, "identity_score", score)
    _, metadata = asyncio.run(neck.repair(
        make_settings(), Client(), original, replace(IDENTITY, references=None),
    ))
    assert metadata["applied"] and seen["bank"] == bank


@pytest.mark.parametrize("status", [200, 429, 504])
def test_edit_request_preserves_multipart_bytes_and_is_one_shot(monkeypatch, status):
    calls, usage = [], []
    original = InlineImage("image/jpeg", b"unaltered-jpeg")
    raw_output = png()

    def handle(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(status, headers={"Retry-After": "0"}, json={"error": "unavailable"})
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(raw_output).decode()}],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kwargs: usage.append(kwargs))
    settings = make_settings(gemini_api_key="test", openai_api_key="test-openai")
    client = gemini_image.GeminiImageClient(settings)
    if status == 200:
        output = asyncio.run(client.edit_image(neck.MODEL, "prompt", original))
        assert output.image == raw_output
        assert usage[0]["model"] == neck.MODEL and usage[0]["image_size"] == "auto"
    else:
        with pytest.raises(GeminiError):
            asyncio.run(client.edit_image(neck.MODEL, "prompt", original))
    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "https://api.openai.com/v1/images/edits"
    body = request.content
    assert b"unaltered-jpeg" in body and b"image/jpeg" in body
    for field, value in [(b"quality", b"high"), (b"size", b"auto"), (b"output_format", b"png")]:
        assert b'name="' + field + b'"\r\n\r\n' + value + b"\r\n" in body
    assert b'name="mask"' not in body and b'name="input_fidelity"' not in body
