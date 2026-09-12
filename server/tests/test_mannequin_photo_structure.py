import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.agents import mannequin_photo_structure as photo
from app.agents.gemini_image import InlineImage
from app.agents.product_reference import ProductReference


def refs():
    out = BytesIO()
    Image.new("RGB", (100, 200), (210, 150, 160)).save(out, "PNG")
    return [ProductReference("Front", "front-1", InlineImage("image/png", out.getvalue()))]


def raw():
    return {"family": "top", "attributes": [{"name": "attachedSleeves", "value": "absent",
        "certainty": "high", "evidenceIndex": 1, "observation": "continuous shoulder panel"}],
        "uncertainties": []}


def test_source_crops_preserve_bytes_and_explicit_parent_provenance():
    original = refs()
    prepared = photo.prepare(original)
    assert prepared[:1] == original
    assert len(prepared) == 4
    for crop in prepared[1:]:
        assert "Front crop" in crop.slot and "same pixels" in crop.slot
        assert crop.asset_id.startswith("front-1:crop:")
        with Image.open(BytesIO(crop.image.data)) as im:
            assert im.width == 100 and im.height < 200
            assert im.getpixel((0, 0)) == (210, 150, 160)


def test_photo_inference_never_receives_product_name_and_returns_bound_evidence(monkeypatch):
    captured = {}
    async def call(settings, model, prompt, images, schema, timeout, **kwargs):
        captured.update(prompt=prompt, images=images, timeout=timeout)
        kwargs["metadata"].update(returned_model=model, finish_reason="stop")
        return raw()
    monkeypatch.setattr(photo.vision_llm, "_call_gpt", call)
    result = asyncio.run(photo.analyze(SimpleNamespace(mannequin_specialist_model="gpt-6-astra",
        mannequin_specialist_timeout_seconds=90), refs()))
    assert result["attributes"][0]["value"] == "absent"
    assert result["source_hash"]
    assert "Product name" not in captured["prompt"]
    assert len(captured["images"]) == 4
    assert captured["timeout"] == 90
    assert "absent" in photo.prompt_block(result)


@pytest.mark.parametrize("index", [0, 7, True])
def test_unknown_or_noninteger_source_reference_is_rejected(index):
    value = raw()
    value["attributes"][0]["evidenceIndex"] = index
    with pytest.raises(ValueError): photo.validate(value, 4)


def test_uncertain_attribute_is_not_written_as_confirmed_structure():
    value = raw()
    value["attributes"][0].update(certainty="low", value="possible cap sleeve")
    assert "possible cap sleeve" not in photo.prompt_block(value)


def test_missing_front_does_not_crop_back_as_front():
    source = refs()[0]
    back = ProductReference("Back", "back", source.image)
    assert photo.prepare([back]) == [back]


def test_photo_structure_render_omits_unverified_title_and_sleeve_assumption():
    from app.agents.prompts import MannequinPromptContext, render_mannequin_prompt
    ctx = MannequinPromptContext("top", 1, "women", photo_structure=raw())
    template = "The sleeves and torso must connect naturally through the armholes as one continuous garment."
    prompt = render_mannequin_prompt(template, ctx, {"name": "반팔 티셔츠"},
                                     {"subCategory": "tshirt"}, material_policy="photo_evidence")
    assert "반팔 티셔츠" not in prompt and "tshirt" not in prompt
    assert template not in prompt
    assert "absent" in prompt and "not seller confirmation" in prompt
