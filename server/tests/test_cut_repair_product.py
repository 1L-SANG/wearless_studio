"""마네킹 없는 편집의 사진·고정값·모델 계약. 모든 이미지 생성은 가짜다."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agents import cut_generator as cg, cut_output_qc as qc
from app.agents.analysis_fit import FIT_VALUE_SENTENCES
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from test_confirmed_gpt_prompt import _top_request
from test_cut_output_qc import _raw


SPEC = {"cutType": "horizon", "shot": "medium", "direction": "back", "faceExposure": "hide"}
PRODUCT = {"clothingType": "top", "name": "SELLER ATTACK"}
BASELINE = Path(__file__).parent / "fixtures/cut_repair_mannequin_baseline.json"


def result():
    raw = _raw()
    raw["gates"][qc.GATES.index("framingDirectionFacePose")].update(status="FAIL", evidence="JUDGE ATTACK")
    out = qc.validate(raw)
    out["repairContext"] = {"poseParts": ["arms", "hands"], "crop": "JUDGE ATTACK"}
    return out


@pytest.mark.parametrize("has_example", [True, False])
def test_product_repair_prompt_has_only_fixed_plan_parts_and_passed_requirements(has_example):
    prompt = cg.build_qc_repair_prompt(SPEC, PRODUCT, (qc._CORRECTIONS["framingDirectionFacePose"],),
                                      has_mannequin=False, has_example=has_example, qc_result=result())
    assert "ATTACK" not in prompt
    assert "FRAMING TARGET FROM CUT PLAN" in prompt
    assert "head to hip" in prompt
    assert "POSE PARTS TO ADJUST: arms, hands" in prompt
    keep = prompt.split("KEEP FROM STAGE-1", 1)[1]
    assert "garmentConstruction" in keep
    assert "framingDirectionFacePose" not in keep
    assert ("Image 2 is EXAMPLE" in prompt) is has_example
    if has_example:
        assert "composition and pose only" in prompt
        assert "person, face, hair, clothing or footwear" in prompt


def test_bottom_medium_crop_comes_from_the_plan_and_ignores_prose():
    spec = {"cutType": "styling", "shot": "medium", "direction": "front", "crop": "SELLER ATTACK"}
    prompt = cg.build_qc_repair_prompt(spec, {"clothingType": "bottom"}, (qc._CORRECTIONS["framingCrop"],),
                                      has_mannequin=False, qc_result=result())
    assert "waist through both feet" in prompt and "ATTACK" not in prompt


def test_chip_correction_is_the_exact_fixed_value_sentence():
    sentence = FIT_VALUE_SENTENCES["top", "sleeve", "fingers"]
    prompt = cg.build_qc_repair_prompt(SPEC, PRODUCT, (sentence,), has_mannequin=False)
    assert sentence in prompt
    with pytest.raises(ValueError, match="invalid_qc_corrections"):
        cg.build_qc_repair_prompt(SPEC, PRODUCT, ("JUDGE ATTACK",), has_mannequin=False)


@pytest.mark.parametrize("example", [None, InlineImage("image/png", b"EXAMPLE")])
@pytest.mark.parametrize("confirmed", [False, True])
def test_product_repair_uses_configured_sunburst_and_source_then_example(example, confirmed):
    calls = []
    class FakeImageClient:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            calls.append((model, prompt, images))
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    request = _top_request() if confirmed else None
    spec = {"cutType": "styling", "shot": request.cut_lock.shot, "direction": "front"} if confirmed else SPEC
    output = asyncio.run(cg.repair(make_settings(model_image_high="gpt-image-2.5-flare",
        model_cut_repair="gpt-image-2.5-sunburst-config"), FakeImageClient(), spec, PRODUCT,
        InlineImage("image/png", b"STAGE1"), qc_corrections=(qc._CORRECTIONS["framingCrop"],),
        has_mannequin=False, example_image=example, confirmed_prompt_input=request, qc_result=result()))
    assert output == (b"EDITED", "image/png")
    assert calls[0][0] == "gpt-image-2.5-sunburst-config"
    assert [im.data for im in calls[0][2]] == [b"STAGE1"] + ([b"EXAMPLE"] if example else [])
    assert "ATTACK" not in calls[0][1]


@pytest.mark.parametrize("case", json.loads(BASELINE.read_text()))
def test_mannequin_repair_prompt_images_and_model_stay_identical(case):
    calls = []
    class FakeImageClient:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            calls.append((model, prompt, images))
            return SimpleNamespace(image=b"EDITED", mime="image/png")
    request = _top_request() if case["confirmed"] else None
    asyncio.run(cg.repair(make_settings(model_image_high="gpt-image-2.5-flare"), FakeImageClient(),
        case["spec"], PRODUCT, InlineImage("image/png", b"STAGE1"),
        qc_corrections=tuple(case["corrections"]), has_mannequin=True,
        example_image=InlineImage("image/png", b"MUST NOT ATTACH"),
        confirmed_prompt_input=request, qc_result=result()))
    assert calls[0][0] == "gpt-image-2.5-flare"
    assert calls[0][1].encode() == case["prompt"].encode()
    assert [im.data for im in calls[0][2]] == [b"STAGE1"]
