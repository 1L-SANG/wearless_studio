import asyncio

import pytest

from app.agents import carrier_preflight_vision as vision
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError


def _raw(**overrides):
    data = {
        "shirtSilhouette": "shirt",
        "hemPlausible": True,
        "sleevesPlausible": True,
        "lowerBodyPresent": True,
        "matchingGarmentPresent": True,
        "mannequinFramePreserved": True,
        "garmentCategoryMatches": True,
        "confidence": 0.94,
        "uncertainFields": [],
        "evidence": ["full mannequin and lower garment are visible"],
    }
    data.update(overrides)
    return data


def test_schema_is_observation_only_and_strict():
    schema = vision.schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for forbidden in vision.FORBIDDEN_FIELDS:
        assert forbidden not in schema["properties"]


def test_validate_rejects_decision_leak_and_invalid_confidence():
    with pytest.raises(VisionError):
        vision.validate({**_raw(), "decision": "pass"})
    with pytest.raises(VisionError):
        vision.validate(_raw(confidence=1.3))


def test_validate_preserves_null_as_uncertain_not_false():
    out = vision.validate(_raw(matchingGarmentPresent=None))
    assert out["matchingGarmentPresent"] is None
    assert "matchingGarmentPresent" in out["uncertainFields"]


def test_validate_requires_expected_matching_garment_to_be_observable():
    out = vision.validate(_raw(matchingGarmentPresent=False))
    assert out["matchingGarmentPresent"] is False


def test_observe_keeps_canonical_source_match_candidate_order(monkeypatch):
    seen = {}

    async def fake(settings, prompt, images, schema):
        seen["prompt"] = prompt
        seen["images"] = images
        seen["schema"] = schema
        return _raw(), "gemini"

    monkeypatch.setattr(vision, "analyze_with_fallback", fake)
    canonical = InlineImage("image/png", b"canonical")
    source = InlineImage("image/png", b"source")
    match = InlineImage("image/png", b"match")
    candidate = InlineImage("image/png", b"candidate")
    observation, meta = asyncio.run(vision.observe(
        object(), canonical=canonical, product_sources=[source],
        matching_garment=match, candidate=candidate,
    ))
    assert [image.data for image in seen["images"]] == [
        b"canonical", b"source", b"match", b"candidate"]
    assert "IMAGE 1" in seen["prompt"]
    assert "LAST IMAGE" in seen["prompt"]
    assert observation["shirtSilhouette"] == "shirt"
    assert meta["imageCount"] == 4
