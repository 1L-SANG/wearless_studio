import asyncio
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from app import image_usage
from app.agents.image_cost import estimate_cost
from app.config import load_settings
from app.services import fal_api
from app.services.segmentation_types import SamUnavailable
from conftest import make_settings


@pytest.mark.parametrize("field,value", [
    ("matching_cutout", "on"),
    ("mannequin_tone_editor", "on"),
    ("matching_flatlay_provider", "qwen"),
])
def test_enabled_provider_requires_nonblank_key(field, value):
    for key in (None, "", "  "):
        with pytest.raises(ValueError, match="FAL_KEY"):
            make_settings(**{field: value, "fal_key": key})
    assert make_settings(**{field: value, "fal_key": "test"}).fal_key == "test"


def test_default_providers_do_not_require_fal(monkeypatch):
    for name in ("FAL_KEY", "MATCHING_CUTOUT_PROVIDER", "MATCHING_FLATLAY_PROVIDER",
                 "SAM_PREPROCESS_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert (s.matching_flatlay_provider, s.fal_key) == ("gemini", None)


def test_provider_environment_is_loaded(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    monkeypatch.setenv("MATCHING_CUTOUT_PROVIDER", "fal")
    monkeypatch.setenv("MATCHING_FLATLAY_PROVIDER", "qwen")
    monkeypatch.setenv("SAM_PREPROCESS_PROVIDER", "fal")
    s = load_settings()
    assert s.matching_flatlay_provider == "qwen"


def test_preparation_rotates_scales_and_preserves_alpha():
    image = Image.new("RGB", (3000, 1500), "red")
    exif = image.getexif()
    exif[274] = 6
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    prepared = fal_api.prepare_image(out.getvalue())
    assert prepared.image.size == (1024, 2048)
    prefix, data = prepared.url.split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    assert Image.open(io.BytesIO(base64.b64decode(data))).size == (1024, 2048)
    rgba = Image.new("RGBA", (20, 30), (2, 3, 4, 125))
    out = io.BytesIO()
    rgba.save(out, "PNG")
    prepared = fal_api.prepare_image(out.getvalue())
    assert prepared.url.startswith("data:image/png;base64,")
    assert prepared.image.getpixel((1, 1)) == (2, 3, 4, 125)


def _transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(fal_api.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_once_and_record_spend_without_secret(monkeypatch, caplog, status):
    posts, recorded = [], []
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    def handler(request):
        if posts:
            sleep.assert_awaited_once_with(1.5)
        posts.append(request)
        assert request.headers["authorization"] == "Key super-secret"
        assert request.extensions["timeout"]["read"] == 60.0
        if len(posts) == 1:
            return httpx.Response(status)
        return httpx.Response(200, json={"masks": [{"url": "https://fake/mask.png"}],
                                        "scores": [0.9]})

    _transport(monkeypatch, handler)
    monkeypatch.setattr(image_usage, "record", lambda **kw: recorded.append(kw))
    with caplog.at_level("INFO"):
        body = asyncio.run(fal_api.run_model(SimpleNamespace(fal_key="super-secret"),
                                             "fal-ai/sam-3/image", {"image_url": "data:test"}))
    assert len(posts) == 2
    sleep.assert_awaited_once_with(1.5)
    assert str(posts[0].url) == "https://fal.run/fal-ai/sam-3/image"
    assert json.loads(posts[0].content) == {"image_url": "data:test", "sync_mode": True}
    assert body["scores"] == [0.9]
    assert recorded[0]["model"] == "fal-ai/sam-3/image"
    events = [json.loads(r.message) for r in caplog.records if r.name == "wearless.fal"]
    assert len(events) == 2
    assert all(e["provider"] == "fal" and e["estimated_cost_usd"] == 0.005
               and e["latency_ms"] >= 0 for e in events)
    assert "super-secret" not in caplog.text


@pytest.mark.parametrize("status,count", [(429, 2), (502, 2), (400, 1)])
def test_http_failure_uses_recoverable_path(monkeypatch, status, count):
    seen = []
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="secret from upstream")

    _transport(monkeypatch, handler)
    with pytest.raises(SamUnavailable) as exc:
        asyncio.run(fal_api.run_model(SimpleNamespace(fal_key="test"), "fal-ai/sam-3/image", {}))
    assert len(seen) == count
    if count == 2:
        sleep.assert_awaited_once_with(1.5)
    else:
        sleep.assert_not_awaited()
    assert "secret from upstream" not in str(exc.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_download_retries_without_sending_api_key(monkeypatch, status):
    seen = []
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    def handler(request):
        if seen:
            sleep.assert_awaited_once_with(1.5)
        seen.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(status if len(seen) == 1 else 200, content=b"image")

    _transport(monkeypatch, handler)
    assert asyncio.run(fal_api.fetch_image("https://fake/image.png")) == b"image"
    assert len(seen) == 2
    sleep.assert_awaited_once_with(1.5)


@pytest.mark.parametrize("status,count", [(429, 2), (502, 2), (400, 1)])
def test_download_failure_uses_recoverable_path(monkeypatch, status, count):
    seen = []
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    _transport(monkeypatch, handler)
    with pytest.raises(SamUnavailable):
        asyncio.run(fal_api.fetch_image("https://fake/image.png"))
    assert len(seen) == count
    if count == 2:
        sleep.assert_awaited_once_with(1.5)
    else:
        sleep.assert_not_awaited()


def test_timeout_and_invalid_json_use_recoverable_failure(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("private request detail")

    _transport(monkeypatch, timeout)
    with pytest.raises(SamUnavailable, match="unavailable"):
        asyncio.run(fal_api.run_model(SimpleNamespace(fal_key="test"), "fal-ai/sam-3/image", {}))
    # Reset before installing a second MockTransport, so wrappers do not recurse.
    monkeypatch.undo()
    _transport(monkeypatch, lambda request: httpx.Response(200, text="not JSON"))
    with pytest.raises(SamUnavailable, match="non-JSON"):
        asyncio.run(fal_api.run_model(SimpleNamespace(fal_key="test"), "fal-ai/sam-3/image", {}))


@pytest.mark.parametrize("model,usage,cost", [
    ("fal-ai/sam-3/image", {}, 0.005),
    ("fal-ai/birefnet/v2", {}, 0.0006),
    ("fal-ai/qwen-image-edit-2511", {"megapixels": 0.786432}, 0.023593),
])
def test_fal_estimates_fit_existing_usage_ledger(model, usage, cost):
    assert estimate_cost(model, "768x1024", usage).usd == cost


@pytest.mark.parametrize("model", [fal_api.SAM_MODEL, fal_api.QWEN_MODEL])
def test_all_models_force_inline_output_and_keep_usage_record(monkeypatch, model):
    posts, recorded = [], []

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"result": "ok"})

    _transport(monkeypatch, handler)
    monkeypatch.setattr(image_usage, "record", lambda **kw: recorded.append(kw))
    payload = {"sync_mode": False, "image_size": {"width": 768, "height": 1024}}
    body = asyncio.run(fal_api.run_model(SimpleNamespace(fal_key="test"), model, payload))
    assert body == {"result": "ok"}
    assert json.loads(posts[0].content) == {
        "sync_mode": True, "image_size": {"width": 768, "height": 1024}}
    assert payload["sync_mode"] is False, "caller payload is not mutated"
    assert len(recorded) == 1
    assert recorded[0]["model"] == model
    assert recorded[0]["image_size"] == "768x1024"
    assert recorded[0]["usage"] == {"provider": "fal", "megapixels": 0.786432}


def test_inline_image_is_decoded_without_creating_http_client(monkeypatch):
    def no_client(**kwargs):
        pytest.fail("data URI must not create an HTTP client")

    monkeypatch.setattr(fal_api.httpx, "AsyncClient", no_client)
    raw = io.BytesIO()
    Image.new("RGB", (2, 3), (10, 20, 30)).save(raw, "PNG")
    url = "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode("ascii")
    assert asyncio.run(fal_api.fetch_image(url)) == raw.getvalue()


@pytest.mark.parametrize("url", [
    "data:image/png;base64", "data:image/png,AA==", "data:image/png;base64,",
    "data:image/png;base64,!!!!", "data:image/png;base64,A",
    "data:image/png;base64,AA==\n", "data:image/png;base64,é",
])
def test_broken_inline_image_uses_recoverable_failure_without_network(monkeypatch, url):
    def no_client(**kwargs):
        pytest.fail("broken data URI must not create an HTTP client")

    monkeypatch.setattr(fal_api.httpx, "AsyncClient", no_client)
    with pytest.raises(SamUnavailable):
        asyncio.run(fal_api.fetch_image(url))
