import asyncio

import pytest

from app.agents import outer_closure as oc, product_analyst as pa
from app.agents.gemini_image import InlineImage
from conftest import make_settings


def raw_closure(kind="button", range="graded", **changes):
    value = {"kind": kind, "range": range, "secondary": None,
             "evidenceImages": [1], "note": "Visible front buttons"}
    value.update(changes)
    return value


def validated(value, slots=None):
    return pa.validate({"frontClosure": value}, slots=slots)["frontClosure"]


def test_schema_requires_strict_front_closure_without_nullable_enums():
    for confirmed in (False, True):
        schema = pa.analysis_schema(include_confirmed_evidence=confirmed)
        assert "frontClosure" in schema["required"]
        item = schema["properties"]["frontClosure"]
        assert item["type"] == "object" and item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"]) == {
            "kind", "range", "secondary", "evidenceImages", "note",
        }
        assert item["properties"]["kind"]["enum"] == list(oc.KINDS)
        for key in ("range", "secondary", "note"):
            assert item["properties"][key] == {"type": ["string", "null"]}
        assert item["properties"]["evidenceImages"] == {"type": "array", "items": {"type": "integer"}}


@pytest.mark.parametrize("kind,range", [("zipper", "graded"), ("unknown", "graded"),
                                      ("belt", "binary"), ("none", None)])
@pytest.mark.parametrize("model_range", ["graded", "binary", None, "wrong", [], {}])
def test_validate_fixes_range_for_kind(kind, range, model_range):
    result = validated(raw_closure(kind, model_range), ["Front"])
    assert result["kind"] == kind and result["range"] == range
    assert result["evidenceSlots"] == ([] if kind == "unknown" else ["Front"])


@pytest.mark.parametrize("kind", ["button", "snap"])
@pytest.mark.parametrize("range,expected", [("graded", "graded"), ("binary", "binary"),
                                          (None, "graded"), ("wrong", "graded"), ([], "graded")])
def test_validate_button_and_snap_keep_valid_model_range(kind, range, expected):
    assert validated(raw_closure(kind, range))["range"] == expected


@pytest.mark.parametrize("kind", ["zipper", "button", "snap", "belt", "none", "unknown"])
@pytest.mark.parametrize("secondary", ["zipper", "button", "snap", "belt", "none", "unknown", None, [], {}])
def test_validate_secondary_is_different_real_hardware(kind, secondary):
    expected = secondary if secondary in oc.HARDWARE and secondary != kind and kind not in ("none", "unknown") else None
    assert validated(raw_closure(kind, secondary=secondary))["secondary"] == expected


def test_evidence_images_become_unique_slots_in_input_order():
    raw = raw_closure(evidenceImages=[4, 2, 3, 1, 2, 0, -1, 6, True, 1.0, "1", {}, []])
    result = validated(raw, ["Front", "Back", "Detail", "Detail", "BackDetail"])
    assert result["evidenceSlots"] == ["Front", "Back", "Detail"]
    assert "evidenceImages" not in result
    assert validated(raw_closure(evidenceImages=[True, 1.0, "1", 0, 6]), ["Front"])["evidenceSlots"] == []


@pytest.mark.parametrize("images", [None, "1", 1, True, {}, [[], {}, "1", False]])
def test_malformed_evidence_does_not_fail_analysis(images):
    assert validated(raw_closure(evidenceImages=images), ["Front"])["evidenceSlots"] == []


@pytest.mark.parametrize("value", [None, [], "none", 1, True, {},
                                  {"kind": "zip"}, {"kind": {}}, {"kind": []}])
def test_malformed_front_closure_defaults_unknown_graded(value):
    result = validated(value, ["Front"])
    assert result == oc.front_closure_of({})


def test_note_uses_sanitize_then_eighty_character_limit():
    assert validated(raw_closure(note="  Front\n buttons\t visible  "))["note"] == "Front buttons visible"
    assert validated(raw_closure(note="a" * 100))["note"] == "a" * 80
    assert validated(raw_closure(note=" \n "))["note"] is None
    assert validated(raw_closure(note={}))["note"] is None


def test_distribute_stores_versioned_server_front_closure():
    checked = pa.validate({"frontClosure": raw_closure("zipper", secondary="snap")}, slots=["Detail"])
    result = pa.distribute(checked)
    assert result["analysis"]["frontClosure"] == {
        "version": 1, "kind": "zipper", "range": "graded", "secondary": "snap",
        "evidenceSlots": ["Detail"], "note": "Visible front buttons",
    }
    assert "frontClosure" not in result["product"] and "frontClosure" not in result["intermediate"]


@pytest.mark.parametrize("slots,expected", [(None, ["Front", "Detail"]),
                                          (["Back", "Front", "BackDetail"], ["Back", "Front", "BackDetail"])])
def test_analyze_passes_real_or_default_slots_into_validation(monkeypatch, slots, expected):
    async def fake_provider(settings, prompt, images, schema, **kwargs):
        return {"frontClosure": raw_closure(evidenceImages=[3, 2, 1, 4])}, "gemini"

    monkeypatch.setattr(pa, "analyze_with_fallback", fake_provider)
    images = [InlineImage("image/png", b"image")] * 3
    result, _ = asyncio.run(pa.analyze(make_settings(), {}, images, slots=slots))
    assert result["analysis"]["frontClosure"]["evidenceSlots"] == expected


def test_prompt_describes_strict_visual_none_and_numbered_evidence():
    prompt = pa.build_prompt({})
    assert "Never answer none unless both front\n      edges are visible." in prompt
    assert "When unsure, answer unknown." in prompt
    assert "evidenceImages: 1-based numbers" in prompt
    assert "Never repeat kind." in prompt
    assert "frontClosure" in prompt.split("Return a single JSON object with keys:", 1)[1]
