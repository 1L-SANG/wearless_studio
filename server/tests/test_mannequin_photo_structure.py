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
        "hardFacts": [{"code": "arbitrary_product_specific_code", "value": "Five front buttons", "evidenceOrdinals": [1]}],
        "uncertainties": [{"code": "sleeve_construction", "value": "Cap sleeve or broad sleeveless shoulder", "reason": "Attachment is not clear", "evidenceOrdinals": [1]}],
        "visibleSurfacePlan": "FRONT is dominant; BACK is context only."}, binding)


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
    assert "Five front buttons" in text
    assert "Cap sleeve or broad sleeveless shoulder" in text
    assert "Attachment is not clear" in text
    assert "not seller confirmation" in text
    assert "FRONT" in text


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
    assert "Five front buttons" in prompt and "not seller confirmation" in prompt
