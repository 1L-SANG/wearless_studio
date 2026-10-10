import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from app.agents import similar_cut, cut_variator
from app.agents.gemini_image import InlineImage, GeminiImageResult
from app.agents.vision_llm import VisionError
from conftest import make_settings


def plan(action="hand_near_chin"):
    return {"actionKey": action, "actionSummary": "턱 가까이 손을 올리고 미소",
            "instructions": "Bring one hand near the chin and give a small smile.",
            "preserve": ["Same dress with a continuous skirt."], "avoid": ["Do not split the skirt into trousers."]}


def test_validate_plan_rejects_empty_or_unbounded_output():
    assert similar_cut.validate_plan(plan()) == plan()
    for bad in ({}, {**plan(), "actionKey": ""}, {**plan(), "instructions": "x" * 2001},
                {**plan(), "preserve": "keep"}, {**plan(), "avoid": [1]}):
        with pytest.raises(VisionError):
            similar_cut.validate_plan(bad)


def test_product_context_uses_known_kind_without_changing_source_color():
    context = similar_cut.product_context(
        {"name": "새틴 원피스", "clothingType": "dress", "colors": [{"name": "red"}]},
        {"material": "satin", "subCategory": "long_dress"},
    )
    assert context["clothingType"] == "dress"
    assert "continuous skirt" in context["constructionRule"]
    assert "red" not in json.dumps(context)


def test_planner_sees_original_and_prior_pending_actions(monkeypatch):
    captured = {}
    source = InlineImage("image/jpeg", b"original")
    previous = {**plan("touch_glasses"), "instructions": "Touch the glasses frame with one hand."}
    history = [{"jobId": "j1", "plan": previous}]

    async def analyze(settings, prompt, images, schema, **kwargs):
        captured.update(prompt=prompt, images=images, kwargs=kwargs)
        return plan(), "gemini"

    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)
    out = asyncio.run(similar_cut.plan_next(make_settings(), source, context={"clothingType": "dress"},
        cut_type="styling", changes=[], history=history))
    assert out["actionKey"] == "hand_near_chin"
    assert captured["images"] == [source]
    assert "touch_glasses" in captured["prompt"]
    assert captured["kwargs"]["require_complete_envelope"] is True
    assert history[0]["plan"]["actionKey"] == "touch_glasses"


def test_duplicate_action_replanned_once_then_fails_without_basic_fallback(monkeypatch):
    calls = []

    async def analyze(*args, **kwargs):
        calls.append(1)
        return plan(), "gemini"

    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)
    with pytest.raises(VisionError, match="different"):
        asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/jpeg", b"source"),
            context={}, cut_type="styling", changes=[], history=[{"plan": plan()}]))
    assert len(calls) == 2


def test_planned_generation_uses_plan_and_does_not_force_global_ratio(monkeypatch):
    captured = {}
    class Gemini:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            captured.update(model=model, prompt=prompt, images=images, **kwargs)
            return GeminiImageResult(image=b"output", mime="image/png", latency_ms=1, usage={})

    source = InlineImage("image/jpeg", b"original")
    data, _ = asyncio.run(cut_variator.generate(make_settings(), Gemini(), source, [], "styling",
        similar_plan=plan(), similar_context={"clothingType": "dress"}))
    assert data == b"output"
    assert captured["images"] == [source]
    assert plan()["instructions"] in captured["prompt"]
    assert "continuous skirt" in captured["prompt"]
    assert captured["aspect_ratio"] is None
    assert "natural, minor variation)" not in captured["prompt"]


def test_mirror_planning_preserves_phone_and_face_visibility(monkeypatch):
    seen = []
    async def analyze(settings, prompt, images, schema, **kwargs):
        seen.append(prompt)
        return plan(), "gemini"
    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)
    asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/jpeg", b"source"),
        context={}, cut_type="mirror", changes=[{"type": "face", "value": "smile"}], history=[]))
    assert "phone" in seen[0] and "face" in seen[0] and "smile" in seen[0]


@pytest.mark.parametrize("dimensions", [(900, 900), (1200, 800), (640, 768), (3000, 1000), (1000, 3000)])
def test_planned_gpt_output_preserves_source_ratio_and_2k_resolution(dimensions):
    buffer = BytesIO()
    Image.new("RGB", dimensions).save(buffer, "PNG")
    captured = {}
    class Gemini:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            captured.update(kwargs)
            return GeminiImageResult(image=b"output", mime="image/png", latency_ms=1, usage={})
    asyncio.run(cut_variator.generate(make_settings(model_image_high="gpt-image-2"), Gemini(),
        InlineImage("image/png", buffer.getvalue()), [], "styling", similar_plan=plan()))
    width, height = map(int, captured["openai_size_override"].split("x"))
    assert abs(width / height - dimensions[0] / dimensions[1]) < .02
    assert width * height > 3_000_000
    assert width % 16 == height % 16 == 0
    assert 1 / 3 <= width / height <= 3


def test_gpt_source_size_override_reaches_existing_provider_without_other_changes(monkeypatch):
    from app.agents.gemini_image import GeminiImageClient
    captured = {}
    async def generate(self, model, prompt, images, image_size, ratio, timeout, **kwargs):
        captured.update(kwargs)
        return GeminiImageResult(image=b"output", mime="image/png", latency_ms=1, usage={})
    monkeypatch.setattr(GeminiImageClient, "_openai_generate", generate)
    client = GeminiImageClient(make_settings())
    asyncio.run(client.generate_content_image("gpt-image-2", "prompt", [], "2K",
        openai_size_override="1888x1888"))
    assert captured == {"preserve_input_bytes": False, "size_override": "1888x1888"}
