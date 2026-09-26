"""The optional wall tone must not replace the existing generation recipe/provider."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg, cut_plan, horizon_background as hb
from app.agents.gemini_image import InlineImage
from conftest import make_settings


def _spec(**patch):
    return {"cutType": "horizon", "shot": "medium", "direction": "front", "pose": "auto",
            "exampleId": "ss_horizon_sequence_06_women_top_linen_01",
            "spaceGroupId": "ssg1__horizon-sequence-06-women-top-linen__test",
            "_spaceSetContinuity": False, **patch}


def _palette():
    return {"mode": "garment-tone", "wallHex": "#e6ecf1", "targetHex": "#6b809a",
            "reason": "measured-color", "paletteBand": "light", "policyVersion": hb.POLICY["version"]}


def _manifest(**options):
    return cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False,
                             mood_count=0, example_scope="all", **options)


@pytest.mark.parametrize("patch", [
    {}, {"horizonBackgroundMode": "reference"}, {"horizonBackgroundMode": "garment-tone"},
    {"horizonBackgroundMode": "reference", "_horizonBackground": _palette(), "_horizonLayoutReference": True},
    {"horizonBackgroundMode": "garment-tone", "_horizonBackground": {"wallHex": "not-a-color"}, "_horizonLayoutReference": True},
    {"_horizonMasterReference": {"shot": "full", "direction": "back"}, "_horizonLayoutReference": True},
])
def test_inactive_or_unverified_option_preserves_normalized_spec_prompt_and_plan(patch):
    baseline = _spec()
    candidate = _spec(**patch)
    assert cg.normalize_spec(candidate, clothing_type="outer") == cg.normalize_spec(baseline, clothing_type="outer")
    product = {"clothingType": "outer", "colors": []}
    assert cg.build_prompt(candidate, product, manifest=_manifest()) == cg.build_prompt(baseline, product, manifest=_manifest())
    normalized = cg.apply_reference_compatibility(cg.normalize_spec(candidate, clothing_type="outer"))
    plan = cut_plan.compile_cut_plan(normalized, "outer").to_dict()
    assert plan["referenceMode"] == "all"
    assert "horizonBackground" not in plan and "horizonMasterReference" not in plan


@pytest.mark.parametrize("model", ["gemini-3-pro-image", "gpt-image-2-2026-04-21"])
@pytest.mark.parametrize("active", [False, True])
def test_selected_tone_keeps_configured_model_size_provider_and_pose_contract(model, active):
    spec = _spec(**({"horizonBackgroundMode": "garment-tone", "_horizonBackground": _palette()} if active else {}))
    manifest = _manifest()
    request = cg._base_request(make_settings(model_image_high=model, detail_cut_image_size="4K", mannequin_aspect_ratio="3:4"),
                               spec, {"clothingType": "top", "colors": []}, manifest=manifest)
    assert request.model == model
    assert request.image_size == "4K"
    assert request.provider_kwargs == {"aspect_ratio": "3:4"}
    assert request.spec["refScope"] == "all"
    assert "MASTER" not in request.prompt
    assert ("HORIZON WALL TONE" in request.prompt) is active
    normalized = cg.apply_reference_compatibility(request.spec)
    plan = cut_plan.compile_cut_plan(normalized, "top").to_dict()
    assert plan["attributeOwners"]["pose"] == "reference"
    assert ("backgroundTone" in plan["attributeOwners"]) is active


def test_complete_reference_manifest_keeps_order_without_layout_input():
    manifest = _manifest()
    assert manifest.startswith("1. PRODUCT")
    assert "2. EXAMPLE REFERENCE (scope: all)" in manifest
    assert "BACKGROUND LAYOUT" not in manifest and "POSE CONTROL" not in manifest


@pytest.mark.parametrize("recipe", ["styling", "mirror", "product"])
def test_other_cut_types_ignore_tone_and_keep_model_contract(recipe):
    settings = make_settings(model_image_high="configured-model")
    original = {"cutType": recipe}
    option = {**original, "horizonBackgroundMode": "garment-tone", "_horizonBackground": _palette()}
    assert cg.normalize_spec(option) == cg.normalize_spec(original)
    assert cg._resolve_generation_model(settings, option) == "configured-model"


def test_existing_signature_model_selection_remains_configured():
    spec = {"cutType": "horizon", "exampleId": "sig_opening"}
    settings = make_settings(model_image_high="ordinary-model", model_image_signature="gpt-image-signature", openai_api_key="test-key")
    assert cg._resolve_generation_model(settings, spec) == "gpt-image-signature"
    assert cg._resolve_generation_model(make_settings(model_image_high="ordinary-model", model_image_signature="gpt-image-signature", openai_api_key=None), spec) == "ordinary-model"


def test_unsupported_or_unavailable_runtime_decision_returns_original_recipe():
    raw = _spec(horizonBackgroundMode="garment-tone")
    for reason in ("set-unsupported", "measurement-unavailable", "measurement-stale"):
        restored = hb.apply_runtime(raw, {(raw["spaceGroupId"], None): hb.reference(reason)})
        assert cg.normalize_spec(restored) == cg.normalize_spec(_spec())
        assert cg.build_prompt(restored, {"clothingType": "top"}, manifest=_manifest()) == cg.build_prompt(_spec(), {"clothingType": "top"}, manifest=_manifest())


def test_repair_keeps_existing_configured_provider_parameters():
    seen = {}
    class Client:
        async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
            seen.update(model=model, image_size=image_size, **kwargs)
            return SimpleNamespace(image=b"result", mime="image/png")
    asyncio.run(cg.repair(make_settings(model_image_high="configured-model", detail_cut_image_size="4K", mannequin_aspect_ratio="3:4"),
        Client(), _spec(), {"clothingType": "top"}, InlineImage("image/png", b"candidate"), qc_corrections=("Restore framing.",)))
    assert seen == {"model": "configured-model", "image_size": "4K", "aspect_ratio": "3:4"}
