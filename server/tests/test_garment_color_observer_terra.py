"""Offline contract for the dedicated Terra garment-region observer."""
import asyncio
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from app import config
from app.agents import garment_color_evidence as evidence, garment_color_observer as observer, vision_llm
from app.agents import horizon_background as hb
from conftest import make_settings
from test_garment_color_transport import _measure_env, _post, png, regions, tone_block


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
# Owner's prompts/v1.dev.txt and v1.user.txt; tests need no checkout-external files.
PROMPT_HASHES = {
    "garment_color_regions_v2.dev.txt": "495de3ae34f3d798f4207a5d7c22cf4d2add604410227f584d1e68f06f0b6e25",
    "garment_color_regions_v2.user.txt": "3f45efb7af11784cdb7a29598451da8a9ccdcb5db5641e30d5d6af427b7d8a04",
}


@pytest.fixture(autouse=True)
def no_fallback_or_gemini(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("garment observer must call GPT directly, without provider fallback")

    monkeypatch.setattr(vision_llm, "analyze_with_fallback", forbidden)
    monkeypatch.setattr(vision_llm, "_call_gemini", forbidden)
    for name, (_, model_of, key_of) in vision_llm._PROVIDERS.items():
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (forbidden, model_of, key_of))


def source(index=4, *, color="blue", slot="Front"):
    return {"sourceIndex": index, "colorId": "base", "slot": slot,
            "data": png(color), "mime": "image/png"}


@pytest.mark.parametrize("filename,expected_hash", PROMPT_HASHES.items())
def test_v2_prompt_bytes_match_owner_original(filename, expected_hash):
    assert sha256((PROMPTS / filename).read_bytes()).hexdigest() == expected_hash


@pytest.mark.parametrize("configured", [None, "gpt-observer-override"])
def test_observer_model_defaults_and_env_override(monkeypatch, configured):
    monkeypatch.delenv("MODEL_GARMENT_COLOR_OBSERVER", raising=False)
    assert make_settings().model_garment_color_observer == "gpt-6.1-terra"
    if configured is not None:
        monkeypatch.setenv("MODEL_GARMENT_COLOR_OBSERVER", configured)
    settings = config.load_settings()
    seen = []

    async def gpt(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.append(model)
        return {"regions": []}

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    assert asyncio.run(observer.observe(settings, [source()])) == []
    assert seen == [configured or "gpt-6.1-terra"]


@pytest.mark.parametrize("product,category", [
    ({"clothingType": "outer", "clothing_type": "top"}, "outer"),
    ({"clothing_type": "bottom"}, "bottom"),
    ({"clothingType": "dress"}, "dress"),
    ({"clothingType": "top"}, "top"),
    ({"clothingType": "invalid"}, "unknown"),
    (None, "unknown"),
])
def test_direct_gpt_receives_v2_prompts_schema_and_original_image_order(monkeypatch, product, category, caplog):
    inputs = [source(4, color="blue", slot="Back"),
              {**source(2), "data": b"broken image"}, source(7, color="red")]
    expected_regions = regions([inputs[0], inputs[2]])
    seen = []
    usage = {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}

    async def gpt(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.append((model, prompt, images, schema, timeout, kwargs))
        kwargs["metadata"].update(requested_model=model, returned_model="terra-returned",
                                  usage=usage, finish_reason="stop")
        return {"regions": expected_regions}

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    caplog.set_level("INFO", logger="wearless.vision_llm")
    settings = make_settings(analysis_model_order="gemini", gemini_api_key="fake",
                             model_text="unrelated-gpt", model_text_gemini_analysis="unrelated-gemini",
                             analysis_timeout_seconds=19, analysis_aux_timeout_seconds=7)
    assert asyncio.run(observer.observe(settings, inputs, product=product)) == expected_regions
    assert len(seen) == 1
    model, prompt, images, schema, timeout, kwargs = seen[0]
    assert model == "gpt-6.1-terra"
    assert timeout == 19  # Caller still caps the whole observation with its auxiliary timeout.
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["system_prompt"] == (PROMPTS / "garment_color_regions_v2.dev.txt").read_text()
    expected_user = (PROMPTS / "garment_color_regions_v2.user.txt").read_text().replace(
        "${clothingType}", category).replace(
        "${sourceMap}", "Image 1: sourceIndex=4; slot=Back\nImage 2: sourceIndex=7; slot=Front")
    assert prompt == expected_user
    assert "${" not in prompt and "${" not in kwargs["system_prompt"]
    assert [(im.mime, im.data) for im in images] == [
        ("image/png", inputs[0]["data"]), ("image/png", inputs[2]["data"])]
    assert schema == {"type": "object", "additionalProperties": False,
                      "properties": {"regions": evidence.regions_schema()}, "required": ["regions"]}
    records = [r for r in caplog.records if getattr(r, "returned_model", None) == "terra-returned"]
    assert len(records) == 1
    assert records[0].requested_model == model and records[0].usage == usage
    assert records[0].finish_reason == "stop" and records[0].provider == "gpt"


@pytest.mark.parametrize("error,breaker_reason", [
    (vision_llm.VisionError("rate limited", status_code=429), "http_429"),
    (httpx.ReadTimeout("read timed out"), "timeout"),
    (httpx.ConnectError("connection failed"), "connection_error"),
    (TimeoutError("timed out"), "timeout"),
    (vision_llm.VisionError("bad JSON", unknown_cause="invalid_response"), None),
])
def test_terra_failure_keeps_vision_error_logging_and_breaker_accounting(monkeypatch, caplog, error, breaker_reason):
    attempts = []

    async def gpt(*args, **kwargs):
        attempts.append(args[1])
        raise error

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    settings = make_settings(ai_breaker_failures=1)
    with pytest.raises(vision_llm.VisionError):
        asyncio.run(observer.observe(settings, [source()]))
    assert attempts == ["gpt-6.1-terra"]
    assert vision_llm._breaker.should_skip("gpt", "gpt-6.1-terra", has_next=True) is (breaker_reason is not None)
    assert "vision_llm provider failed: gpt model=gpt-6.1-terra" in caplog.text
    assert f"{type(error).__name__}({error})" in caplog.text and "images=1" in caplog.text


def test_open_breaker_still_attempts_only_terra_and_success_clears_it(monkeypatch):
    settings = make_settings(ai_breaker_failures=1)
    vision_llm._breaker.failure(settings, "gpt", "gpt-6.1-terra", "timeout")
    attempts = []

    async def gpt(settings, model, *args, **kwargs):
        attempts.append(model)
        return {"regions": []}

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    assert asyncio.run(observer.observe(settings, [source()])) == []
    assert attempts == ["gpt-6.1-terra"]
    assert not vision_llm._breaker.should_skip("gpt", "gpt-6.1-terra", has_next=True)


def test_caller_cancellation_propagates_without_provider_retry(monkeypatch):
    async def gpt(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(observer.observe(make_settings(), [source()]))


@pytest.mark.parametrize("failure", ["provider", "timeout"])
def test_real_observer_failure_keeps_measurement_unavailable_and_reference_background(
        client, make_token, monkeypatch, failure):
    real_observe = observer.observe
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()])
    monkeypatch.setattr(observer, "observe", real_observe)
    if failure == "timeout":
        client.app.state.settings = replace(client.app.state.settings, analysis_aux_timeout_seconds=0.01)

    async def gpt(*args, **kwargs):
        if failure == "timeout":
            await asyncio.sleep(1)
        raise httpx.ReadTimeout("Terra unavailable")

    monkeypatch.setattr(vision_llm, "_call_gpt", gpt)
    response = _post(client, make_token)
    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "garmentColorEvidence": None}
    assert not seen["saves"]
    assert hb.resolve_for_block(tone_block(), {"clothing_type": "top"})["mode"] == "reference"
