"""Saved AG-01 evidence is reused without an additional analysis call."""
from copy import deepcopy
from io import BytesIO
import pytest
from PIL import Image
from app.agents import mannequin_photo_structure as photo, product_evidence_contract as pec
from app.agents.gemini_image import InlineImage
from app.agents.product_reference import ProductReference


def refs():
    out = BytesIO()
    Image.new("RGB", (100, 200), (210, 150, 160)).save(out, "PNG")
    return [ProductReference("Front", "front-1", InlineImage("image/png", out.getvalue()))]


def contract():
    images = [(r.image.data, r.image.mime) for r in refs()]
    binding = pec.build_input_binding(images, images, ["Front"])
    return pec.validate_and_bind({
        "panels": [{"evidenceOrdinal": 1, "detail": "front body", "judgeability": "usable", "judgeabilityReasons": ["clear_enough"]}],
        "hardFacts": [{"code": "neck_shape", "value": "rounded neckline with narrow binding", "evidenceOrdinals": [1]}],
        "uncertainties": [{"code": "sleeve_construction", "value": "Cap sleeve or broad sleeveless shoulder", "reason": "Attachment is not clear", "evidenceOrdinals": [1]}],
        "hem_shape": {"value": "straight", "evidenceOrdinals": [1]},
        "cuff": {"value": "none", "evidenceOrdinals": [1]},
        "button_count_visible": {"value": 5, "evidenceOrdinals": [1]},
        "pattern_structure": {"value": "none", "evidenceOrdinals": [1]},
        "surface_texture": {"value": "fine ribbing is visible", "evidenceOrdinals": [1]},
        "seam_lines": {"value": "unknown", "evidenceOrdinals": []}}, binding)


def test_saved_evidence_is_used_without_a_new_analysis():
    stored = contract()
    assert photo.from_analysis({pec.PERSISTED_KEY: stored}, refs()) == stored
    assert photo.from_analysis({}, refs()) is None


def test_wrong_original_and_tampered_facts_cannot_be_reused():
    stored = contract()
    with pytest.raises(ValueError):
        photo.from_analysis({pec.PERSISTED_KEY: stored}, [ProductReference("Front", "new", InlineImage("image/png", b"new pixels"))])
    altered = deepcopy(stored)
    altered["hardFacts"][0]["value"] = "Nine buttons"
    with pytest.raises(ValueError):
        photo.from_analysis({pec.PERSISTED_KEY: altered}, refs())


def test_free_code_values_and_uncertainties_survive_rendering():
    text = photo.prompt_block(contract())
    payload = __import__("json").loads(text.splitlines()[-1])
    assert "rounded neckline with narrow binding" in text
    assert payload["button_count_visible"] == {"value": 5, "sourceViews": ["FRONT"]}
    assert "Cap sleeve or broad sleeveless shoulder" in text
    assert "Attachment is not clear" in text
    assert "not seller confirmation" in text
    assert "FRONT" in text
    assert "visibleSurfacePlan" not in text
    assert len(text.splitlines()[-1]) <= 2400


def test_legacy_contract_renders_missing_fixed_fields_unknown_without_rehashing():
    from test_product_evidence_contract import _legacy_contract
    legacy = _legacy_contract()
    before_hash = legacy["contractSha256"]
    text = photo.prompt_block(legacy)
    payload = __import__("json").loads(text.splitlines()[-1])
    assert all(payload[field] == {"value": "unknown", "sourceViews": []} for field in (
        "hem_shape", "cuff", "button_count_visible", "pattern_structure",
        "surface_texture", "seam_lines",
    ))
    assert "single front button placket" in text
    assert legacy["contractSha256"] == before_hash


def test_front_product_block_does_not_pass_back_only_design_facts():
    source = refs()[0].image
    binding = pec.build_input_binding(
        [(source.data, source.mime), (source.data + b"back", source.mime)],
        [(source.data, source.mime), (source.data + b"back", source.mime)],
        ["Front", "Back"],
    )
    raw = {
        "panels": [
            {"evidenceOrdinal": 1, "detail": "front", "judgeability": "usable", "judgeabilityReasons": ["clear_enough"]},
            {"evidenceOrdinal": 2, "detail": "back", "judgeability": "usable", "judgeabilityReasons": ["clear_enough"]},
        ],
        "hardFacts": [
            {"code": "neck_shape", "value": "rounded front neckline", "evidenceOrdinals": [1]},
            {"code": "back_yoke", "value": "deep curved back yoke", "evidenceOrdinals": [2]},
        ],
        "uncertainties": [{"code": "hidden_side", "value": "side construction", "reason": "not visible", "evidenceOrdinals": [1]}],
        "hem_shape": {"value": "straight", "evidenceOrdinals": [1]},
        "cuff": {"value": "unknown", "evidenceOrdinals": []},
        "button_count_visible": {"value": None, "evidenceOrdinals": []},
        "pattern_structure": {"value": "none", "evidenceOrdinals": [1]},
        "surface_texture": {"value": "unknown", "evidenceOrdinals": []},
        "seam_lines": {"value": "unknown", "evidenceOrdinals": []},
    }
    text = photo.prompt_block(pec.validate_and_bind(raw, binding))
    assert "rounded front neckline" in text
    assert "deep curved back yoke" not in text


def test_short_block_cap_keeps_all_fixed_fields_and_complete_seam_endpoints():
    from test_product_evidence_contract import _binding, _raw
    raw = _raw()
    raw["hardFacts"].extend({
        "code": f"extra_fact_{index}",
        "value": f"source-proven distinguishing construction {index} " + "x" * 170,
        "evidenceOrdinals": [1],
    } for index in range(1, 12))
    stored = pec.validate_and_bind(raw, _binding())
    text = photo.prompt_block(stored)
    encoded = text.splitlines()[-1]
    payload = __import__("json").loads(encoded)
    assert len(encoded) <= 2400
    assert set(pec.FIXED_OBSERVATION_FIELDS).issubset(payload)
    assert payload["seam_lines"] == {
        "value": "two front seam lines start below the placket and continue to the hem",
        "sourceViews": ["FRONT"],
    }


def test_source_crops_preserve_bytes_and_explicit_parent_provenance():
    original = refs()
    prepared = photo.prepare(original)
    assert prepared[:1] == original and len(prepared) == 4
    for crop in prepared[1:]:
        assert "Front crop" in crop.slot and "same pixels" in crop.slot
        with Image.open(BytesIO(crop.image.data)) as im:
            assert im.width == 100 and im.height < 200
            assert im.getpixel((0, 0)) == (210, 150, 160)


def test_missing_front_does_not_crop_back_as_front():
    back = ProductReference("Back", "back", refs()[0].image)
    assert photo.prepare([back]) == [back]


def test_photo_structure_render_omits_unverified_title_and_sleeve_assumption():
    from app.agents.prompts import MannequinPromptContext, render_mannequin_prompt
    ctx = MannequinPromptContext("top", 1, "women", photo_structure=contract())
    template = "The sleeves and torso must connect naturally through the armholes as one continuous garment."
    prompt = render_mannequin_prompt(template, ctx, {"name": "반팔 티셔츠"}, {"subCategory": "tshirt"}, material_policy="photo_evidence")
    assert "반팔 티셔츠" not in prompt and "tshirt" not in prompt
    assert template not in prompt
    assert "rounded neckline with narrow binding" in prompt
    assert '"value":5' in prompt and "not seller confirmation" in prompt
