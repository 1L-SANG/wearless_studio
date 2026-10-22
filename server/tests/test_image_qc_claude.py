import asyncio
import base64
import hashlib
import logging
import threading
from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from app.agents import image_qc as iq
from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings


RAW = {
    "verdict": "retry", "mismatches": ["wrong logo"], "correctionPrompt": "restore logo",
    "product_fidelity": 96, "physical_naturalness": 95, "image_quality": 94,
    "series_consistency": None, "critical_errors": [],
    "matching_fidelity": 93, "matching_critical_errors": [],
    "target_resolved": True, "protected_regions_unchanged": True, "regression_reasons": [],
}


def _image(size=(32, 24), color="red", *, orientation=None):
    buf = BytesIO()
    image = Image.new("RGBA", size, color)
    kwargs = {}
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(buf, format="PNG", **kwargs)
    return InlineImage("image/png", buf.getvalue())


def _settings(**kwargs):
    return make_settings(model_image_qc="claude-opus-5-5",
                         anthropic_api_key="test-claude-key", **kwargs)


@pytest.mark.parametrize("scored,matching,paired", [
    (False, False, False), (True, False, False), (True, True, False),
    (True, False, True), (True, True, True),
])
def test_default_verdict_keeps_original_fallback_request(monkeypatch, scored, matching, paired):
    products = [InlineImage("image/png", b"PRODUCT")]
    generated = InlineImage("image/png", b"GENERATED")
    match = InlineImage("image/png", b"MATCH") if matching else None
    before = InlineImage("image/png", b"BEFORE") if paired else None
    goal = "restore hem" if paired else None
    images = products + ([match] if matching else []) + ([before] if paired else []) + [generated]
    fallback = AsyncMock(return_value=(RAW, "gemini"))
    claude = AsyncMock(side_effect=AssertionError("unexpected Claude call"))
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    settings = make_settings()
    out = asyncio.run(iq.verdict(settings, products, generated, scored=scored,
                               match_image=match, before_image=before, edit_goal=goal))
    fallback.assert_awaited_once_with(
        settings, iq.build_prompt(1, scored=scored, matching=matching, paired=paired, edit_goal=goal),
        images, iq.qc_schema(scored=scored, matching=matching, paired=paired),
        timeout_seconds=settings.background_ai_timeout_seconds,
        system_prompt=vision_llm.qc_system_prompt("image", {
            "productCount": 1, "imageRoles": ["PRODUCT"] + (["MATCHING"] if matching else [])
            + (["BEFORE"] if paired else []) + ["CURRENT"],
        }), require_complete_envelope=True,
    )
    assert all(sent is original for sent, original in zip(fallback.await_args.args[2], images))
    claude.assert_not_called()
    assert out["verdict"] == "retry" and out["mismatches"] == ["wrong logo"]


@pytest.mark.parametrize("scored,matching,paired", [
    (False, False, False), (True, False, False), (True, True, False),
    (True, False, True), (True, True, True),
])
def test_claude_verdict_preserves_contract_and_shrinks_in_order(
    monkeypatch, caplog, scored, matching, paired,
):
    products = [_image((1536, 32), "red"), _image((2048, 1024), "blue")]
    generated = _image((1024, 2048), "green")
    match = _image((2048, 1024), "yellow") if matching else None
    before = _image((1024, 2048), "purple") if paired else None
    goal = "restore hem" if paired else None
    originals = products + ([match] if matching else []) + ([before] if paired else []) + [generated]
    settings = _settings(image_qc_timeout_seconds=7.5)
    legacy = AsyncMock(return_value=(RAW, "gemini"))
    monkeypatch.setattr(iq, "analyze_with_fallback", legacy)
    kwargs = dict(scored=scored, match_image=match, before_image=before, edit_goal=goal)
    expected = asyncio.run(iq.verdict(make_settings(), products, generated, **kwargs))
    legacy_args, legacy_kwargs = legacy.await_args
    fallback = AsyncMock(side_effect=AssertionError("unexpected fallback"))
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)

    async def fake_claude(*args, metadata, **kwargs):
        metadata.update(returned_model="claude-opus-returned", usage={
            "input_tokens": 321, "output_tokens": 45})
        return RAW

    claude = AsyncMock(side_effect=fake_claude)
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    cpu_threads = []
    run_cpu_bound = vision_llm.run_cpu_bound

    async def record_cpu(func, *args, **kwargs):
        def record():
            cpu_threads.append(threading.get_ident())
            return func(*args, **kwargs)
        return await run_cpu_bound(record)

    monkeypatch.setattr(vision_llm, "run_cpu_bound", record_cpu)
    with caplog.at_level(logging.INFO, logger="app.agents.image_qc"):
        out = asyncio.run(iq.verdict(settings, products, generated, **kwargs))
    claude.assert_awaited_once()
    args, sent_kwargs = claude.await_args
    assert args[:3] == (settings, "claude-opus-5-5", legacy_args[1])
    assert args[4:] == (legacy_args[3], 7.5)
    assert sent_kwargs["effort"] == "low"
    assert sent_kwargs["system_prompt"] == legacy_kwargs["system_prompt"]
    assert len(args[3]) == len(originals)
    for sent, original in zip(args[3], originals):
        with Image.open(BytesIO(original.data)) as source:
            if max(source.size) <= 1536:
                assert sent is original
            else:
                assert sent.mime == "image/jpeg" and sent is not original
                with Image.open(BytesIO(sent.data)) as shrunk:
                    assert shrunk.format == "JPEG" and shrunk.mode == "RGB"
                    assert max(shrunk.size) == 1536
                    assert shrunk.getpixel((0, 0)) == pytest.approx(source.convert("RGB").getpixel((0, 0)), abs=3)
    assert cpu_threads and all(t != threading.get_ident() for t in cpu_threads)
    fallback.assert_not_called()
    assert out == expected
    if scored:
        assert out["image_hash"] == hashlib.sha256(generated.data).hexdigest()
        assert out["image_hash"] != hashlib.sha256(args[3][-1].data).hexdigest()
    for entry in ("provider=claude", "requested_model=claude-opus-5-5",
                  "returned_model=claude-opus-returned", "effort=low", "elapsed_seconds=",
                  "input_tokens=321", "output_tokens=45"):
        assert entry in caplog.text


@pytest.mark.parametrize("size,expected,identity", [
    ((2048, 1024), (768, 1536), False), ((64, 32), (32, 64), False),
    ((1024, 768), (768, 1024), False),
])
def test_claude_shrink_applies_exif_rotation(monkeypatch, size, expected, identity):
    source = _image(size, orientation=6)
    with Image.open(BytesIO(source.data)) as image:
        buf = BytesIO()
        image.convert("RGB").save(buf, format="JPEG", exif=image.getexif())
    source = InlineImage("image/jpeg", buf.getvalue())
    claude = AsyncMock(return_value=RAW)
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    monkeypatch.setattr(iq, "analyze_with_fallback", AsyncMock(side_effect=AssertionError("fallback")))
    asyncio.run(iq.verdict(_settings(), [source], _image()))
    sent = claude.await_args.args[3][0]
    assert (sent is source) is identity
    assert sent.mime == "image/jpeg"
    with Image.open(BytesIO(sent.data)) as image:
        assert image.size == expected
        assert image.mode == "RGB"
        assert image.getexif().get(274, 1) == 1
    body = vision_llm._claude_body("claude-opus-5-5", "test", [sent], iq.qc_schema(),
                                   "low", None, 32000)
    encoded = body["messages"][0]["content"][1]["source"]["data"]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == expected


@pytest.mark.parametrize("orientation", [None, 1])
def test_claude_shrink_keeps_small_unrotated_original(orientation):
    source = _image((1024, 768), orientation=orientation)
    assert iq._shrink_claude_image(source) is source


@pytest.mark.parametrize("error", [
    vision_llm.VisionError("sensitive request", unknown_cause="invalid_response"),
    httpx.ConnectError("sensitive request"), httpx.ReadTimeout("sensitive request"),
    asyncio.TimeoutError("sensitive request"),
])
def test_claude_failure_falls_back_with_originals_and_safe_log(monkeypatch, caplog, error):
    products = [_image((2048, 1024))]
    match, before, generated = _image(), _image(), _image((1024, 2048))
    settings = _settings()
    fallback = AsyncMock(return_value=(RAW, "gpt"))
    claude = AsyncMock(side_effect=error)
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    with caplog.at_level(logging.WARNING, logger="app.agents.image_qc"):
        out = asyncio.run(iq.verdict(settings, products, generated, scored=True,
                                   match_image=match, before_image=before, edit_goal="restore hem"))
    claude.assert_awaited_once()
    fallback.assert_awaited_once()
    originals = [*products, match, before, generated]
    assert all(sent is original for sent, original in zip(fallback.await_args.args[2], originals))
    assert len(fallback.await_args.args[2]) == len(originals)
    assert fallback.await_args.kwargs["require_complete_envelope"] is True
    assert fallback.await_args.kwargs["timeout_seconds"] == settings.background_ai_timeout_seconds
    assert out["verdict"] == "retry" and out["mismatches"] == ["wrong logo"]
    assert out["image_hash"] == hashlib.sha256(generated.data).hexdigest()
    assert type(error).__name__ in caplog.text and "unknown_cause=" in caplog.text
    assert "elapsed_seconds=" in caplog.text and "sensitive request" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_claude_wall_timeout_cancels_call_and_falls_back(monkeypatch):
    cancelled = []

    async def slow_claude(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    fallback = AsyncMock(return_value=(RAW, "gemini"))
    monkeypatch.setattr(vision_llm, "_call_claude", slow_claude)
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    out = asyncio.run(iq.verdict(_settings(image_qc_timeout_seconds=0.01), [_image()], _image()))
    assert cancelled == [True]
    fallback.assert_awaited_once()
    assert out["mismatches"] == ["wrong logo"]


def test_claude_failure_logs_only_allowed_diagnostics(monkeypatch, caplog):
    error = vision_llm.VisionError("private exception text", unknown_cause="invalid_input",
        status_code=400, provider_error_type="invalid_request_error",
        provider_error_message="Unsupported image format.", provider_request_id="req-diagnostic")
    monkeypatch.setattr(vision_llm, "_call_claude", AsyncMock(side_effect=error))
    monkeypatch.setattr(iq, "analyze_with_fallback", AsyncMock(return_value=(RAW, "gpt")))
    settings = _settings()
    with caplog.at_level(logging.WARNING, logger="app.agents.image_qc"):
        out = asyncio.run(iq.verdict(settings, [_image()], _image(),
                                   before_image=_image(), edit_goal="private edit goal"))
    records = [record for record in caplog.records if record.name == "app.agents.image_qc"]
    assert len(records) == 1
    text = records[0].getMessage()
    for field in ("error=VisionError", "unknown_cause=invalid_input", "elapsed_seconds=",
                  "status=400", "error_type=invalid_request_error", "request_id=req-diagnostic",
                  "message=Unsupported image format."):
        assert field in text
    assert all(value not in text for value in ("private exception text", "private edit goal",
               settings.anthropic_api_key, iq.build_prompt(1)))
    assert records[0].exc_info is None
    assert out["verdict"] == "retry"


@pytest.mark.parametrize("error", [ValueError("sensitive request"), OSError("sensitive request")])
def test_shrink_failure_falls_back_with_originals(monkeypatch, caplog, error):
    def invalid_image(*args, **kwargs):
        raise error

    monkeypatch.setattr(Image, "open", invalid_image)
    claude = AsyncMock(side_effect=AssertionError("Claude must not receive invalid images"))
    fallback = AsyncMock(return_value=(RAW, "gemini"))
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    source, generated = InlineImage("image/png", b"SOURCE"), InlineImage("image/png", b"GENERATED")
    with caplog.at_level(logging.WARNING, logger="app.agents.image_qc"):
        out = asyncio.run(iq.verdict(_settings(), [source], generated))
    claude.assert_not_called()
    fallback.assert_awaited_once()
    assert fallback.await_args.args[2][0] is source
    assert fallback.await_args.args[2][1] is generated
    assert out["mismatches"] == ["wrong logo"]
    assert type(error).__name__ in caplog.text and "sensitive request" not in caplog.text


def test_both_providers_failing_propagates_fallback_error(monkeypatch):
    error = vision_llm.VisionError("fallback failed")
    fallback = AsyncMock(side_effect=error)
    monkeypatch.setattr(vision_llm, "_call_claude", AsyncMock(side_effect=vision_llm.VisionError("primary failed")))
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    with pytest.raises(vision_llm.VisionError) as exc:
        asyncio.run(iq.verdict(_settings(), [_image()], _image()))
    assert exc.value is error
    fallback.assert_awaited_once()


def test_scene_verdict_keeps_fallback_with_claude_configured(monkeypatch):
    claude = AsyncMock(side_effect=AssertionError("unexpected Claude call"))
    fallback = AsyncMock(return_value=(RAW, "gemini"))
    monkeypatch.setattr(vision_llm, "_call_claude", claude)
    monkeypatch.setattr(iq, "analyze_with_fallback", fallback)
    settings = _settings()
    plate, generated = _image(), _image()
    out = asyncio.run(iq.scene_verdict(settings, plate, generated))
    claude.assert_not_called()
    fallback.assert_awaited_once()
    assert fallback.await_args.args[0] is settings
    assert fallback.await_args.args[2] == [plate, generated]
    assert set(out) == {"verdict", "mismatches", "correctionPrompt"}
