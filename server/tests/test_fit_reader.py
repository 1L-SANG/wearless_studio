"""핏 읽기 DTO와 Gemini 전용 요청. 모든 판독 응답은 가짜다."""

import asyncio
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import _to_gemini_schema
from app.config import load_settings
from conftest import make_settings


def reader():
    from app.agents import fit_reader
    return fit_reader


def reading(photo="worn", sleeve_type="long"):
    return {"photoType": photo, "sleeveType": sleeve_type,
        "topLength": {"value": "basic", "confident": True},
        "hemLength": {"value": "midi", "confident": True},
        "pantsLength": {"value": "ankle_cover", "confident": True},
        "sleeve": {"value": "wrist", "confident": True},
        "frontHem": {"value": "curved", "confident": True}}


@pytest.mark.parametrize("photo,top,sleeve,pants", [("worn", True, True, True),
    ("flat", True, False, False), ("hanger", False, False, False)])
def test_server_confidence_rules_for_long_sleeves_and_photo_kind(photo, top, sleeve, pants):
    raw = reading(photo)
    before = deepcopy(raw)
    actual = reader().normalize_fit_read(raw)
    assert actual["topLength"] == {"value": "basic", "confident": top}
    assert actual["sleeve"] == {"value": "wrist", "confident": sleeve}
    assert actual["pantsLength"] == {"value": "ankle_cover", "confident": pants}
    assert actual["hemLength"] == {"value": "midi", "confident": True}
    assert raw == before and actual is not raw


@pytest.mark.parametrize("kind,value,wanted", [("sleeveless", "cap", None), ("short", "wrist", None),
    ("long", "short", None), ("short", "elbow", "elbow"), ("long", "above_wrist", "above_wrist"),
    (None, "wrist", None), ("INVALID", "elbow", None)])
def test_sleeve_value_must_belong_to_the_selected_type_group(kind, value, wanted):
    raw = reading(sleeve_type=kind)
    raw["sleeve"]["value"] = value
    actual = reader().normalize_fit_read(raw)
    assert actual["sleeve"] == {"value": wanted, "confident": wanted is not None}
    assert actual["sleeveType"] == (kind if kind in ("sleeveless", "short", "long") else None)


@pytest.mark.parametrize("photo", [None, "INVALID", "WORN", True, {}, []])
def test_invalid_photo_type_discards_the_entire_reading(photo):
    assert reader().normalize_fit_read(reading(photo)) is None


@pytest.mark.parametrize("field,bad", [("topLength", "long"), ("hemLength", "crop"),
    ("pantsLength", "floor"), ("sleeve", "sleeveless"), ("frontHem", "diagonal"),
    ("topLength", []), ("hemLength", 1)])
def test_invalid_value_discards_only_that_item(field, bad):
    raw = reading()
    raw[field] = {"value": bad, "confident": True}
    actual = reader().normalize_fit_read(raw)
    assert actual[field] == {"value": None, "confident": False}
    assert actual["photoType"] == "worn"
    if field != "frontHem":
        assert actual["frontHem"] == {"value": "curved", "confident": True}


@pytest.mark.parametrize("value,confidence,wanted", [(None, True, False), ("basic", "true", False),
    ("basic", 1, False), ("basic", False, False), ("basic", True, True)])
def test_null_or_nonboolean_confidence_cannot_become_a_fact(value, confidence, wanted):
    raw = reading()
    raw["topLength"] = {"value": value, "confident": confidence}
    raw["unknown"] = "SELLER ATTACK"
    actual = reader().normalize_fit_read(raw)
    assert actual["topLength"] == {"value": value, "confident": wanted}
    assert set(actual) == {"photoType", "sleeveType", "topLength", "hemLength", "pantsLength", "sleeve", "frontHem"}


def test_schema_has_fixed_order_and_nullable_string_enums_for_gemini():
    schema = reader().response_schema()
    order = ["photoType", "sleeveType", "topLength", "hemLength", "pantsLength", "sleeve", "frontHem"]
    assert list(schema["properties"]) == schema["required"] == order
    assert schema["additionalProperties"] is False
    converted = _to_gemini_schema(schema)
    assert converted["propertyOrdering"] == order
    assert converted["properties"]["sleeveType"]["nullable"] is True
    for field in order[2:]:
        item = schema["properties"][field]
        assert item["required"] == ["value", "confident"] and item["additionalProperties"] is False
        value = converted["properties"][field]["properties"]["value"]
        assert value["nullable"] is True and all(isinstance(token, str) for token in value["enum"])


def test_prompt_is_unchanged_and_manifest_labels_do_not_prioritize_closeups():
    path = Path(__file__).resolve().parents[1] / "prompts/fit_reader_v1.txt"
    assert sha256(path.read_bytes()).hexdigest() == "724193aaeb786be4d173a90192cc830f5708e6617a2084f22a0aa7872edbe4b9"
    prompt = reader().build_prompt(["Front", "Back", "Detail", "BackDetail", "SELLER ATTACK"])
    assert "1. front view of the whole product" in prompt
    assert "2. back view of the whole product" in prompt
    assert "3. front-side close-up, usually only part of the product" in prompt
    assert "4. back-side close-up, usually only part of the product" in prompt
    assert "5. product view" in prompt
    assert "ATTACK" not in prompt and "${" not in prompt
    assert "${" not in reader().build_prompt(None)


@pytest.mark.parametrize("override,analysis,base,wanted", [("fit-flash", "analysis-flash", "base-flash", "fit-flash"),
    ("", "analysis-flash", "base-flash", "analysis-flash"), ("", "", "base-flash", "base-flash")])
def test_reader_uses_one_gemini_low_call_with_analysis_images_and_model_fallback(monkeypatch, override, analysis, base, wanted):
    observed = []
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed.append((prompt, images, kwargs))
        return reading(), "gemini"
    module = reader()
    monkeypatch.setattr(module, "analyze_with_fallback", fake_judge)
    images = [InlineImage("image/png", b"front"), InlineImage("image/jpeg", b"back")]
    actual, provider = asyncio.run(module.read(make_settings(fit_reader_mode="on", model_text_gemini_fit_reader=override,
        model_text_gemini_analysis=analysis, model_text_gemini=base, analysis_thinking_level="high"), images, slots=["Front", "Back"]))
    assert actual == reading() and provider == "gemini"
    assert len(observed) == 1 and observed[0][1] is images
    assert observed[0][2]["models"] == {"gemini": wanted}
    assert observed[0][2]["order"] == ["gemini"] and observed[0][2]["thinking_level"] == "low"


def test_flag_defaults_on_but_off_makes_zero_calls(monkeypatch):
    monkeypatch.delenv("FIT_READER_MODE", raising=False)
    assert load_settings().fit_reader_mode == "on"
    monkeypatch.setenv("FIT_READER_MODE", "off")
    settings = load_settings()
    async def forbidden(*args, **kwargs):
        raise AssertionError("unexpected paid fit reading")
    monkeypatch.setattr(reader(), "analyze_with_fallback", forbidden)
    assert asyncio.run(reader().read(settings, [InlineImage("image/png", b"front")], slots=["Front"])) == (None, None)
