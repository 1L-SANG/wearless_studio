"""Approved local-edit colors require specific, bound before/after evidence."""

import asyncio
import importlib

import pytest

from app.agents import vision_llm
from app.agents.cut_output_qc import LabeledReference
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings


@pytest.fixture
def reviewer():
    if not importlib.util.find_spec("app.agents.cut_color_review"):
        return None
    return importlib.import_module("app.agents.cut_color_review")


def color_raw(target="preserved", matching="preserved"):
    return {
        "target": {"verdict": target, "evidence": "Corresponding broad target panels retain the baseline hue and saturation."},
        "matching": {"verdict": matching, "evidence": "Supporting garment visible panels retain the baseline color."},
    }


@pytest.mark.parametrize("axis,value,want", [
    ("target", {"verdict": "preserved", "evidence": "색조가 유지됨."}, "PASS"),
    ("target", {"verdict": "shifted", "evidence": "Broad panels shifted hue."}, "FAIL"),
    ("matching", {"verdict": "uncertain", "evidence": "Insufficient corresponding visible area."}, "UNJUDGEABLE"),
    ("target", {"verdict": "preserved", "evidence": " "}, "UNJUDGEABLE"),
    ("target", {"verdict": "PASS", "evidence": "Visible."}, "UNJUDGEABLE"),
    ("target", None, "UNJUDGEABLE"),
])
def test_required_axes_cannot_pass_shifted_uncertain_or_missing_evidence(reviewer, axis, value, want):
    assert reviewer is not None, "color reviewer is missing"
    raw = color_raw()
    raw[axis] = value
    result = reviewer.validate(raw, protected_axes=("target", "matching"))
    assert result["status"] == want
    assert result["axes"][axis]["status"] == want


def test_unprotected_axes_are_owned_by_caller_and_raw_is_bounded(reviewer):
    assert reviewer is not None, "color reviewer is missing"
    raw = color_raw(matching="shifted")
    raw["target"]["evidence"] = "visible " * 10000
    result = reviewer.validate(raw, protected_axes=("target",))
    assert result["status"] == "PASS"
    assert result["axes"]["matching"]["status"] == "NA"
    assert len(str(result)) < 7000
    for malformed in (None, [], {}, {**color_raw(), "extra": True}):
        assert reviewer.validate(malformed, protected_axes=("target",))["status"] == "UNJUDGEABLE"


def test_review_sends_exact_baseline_candidate_then_only_identifying_refs(reviewer, monkeypatch):
    assert reviewer is not None, "color reviewer is missing"
    calls = []

    async def transport(settings, model, prompt, images, schema, timeout):
        calls.append([image.data for image in images])
        assert model == "gpt-configured-color"
        assert "1. BEFORE" in prompt and "2. AFTER" in prompt
        assert "3. PRODUCT" in prompt and "4. MATCHING" in prompt
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"target", "matching"}
        assert schema["properties"]["target"]["additionalProperties"] is False
        return color_raw()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    settings = make_settings(openai_api_key="test", analysis_model_order="gemini,gpt")
    object.__setattr__(settings, "cut_color_review_model", "gpt-configured-color")
    refs = [LabeledReference(role, InlineImage("image/png", data)) for role, data in (
        ("modelFace", b"FACE"), ("matching", b"SUPPORT"), ("product", b"PRODUCT"), ("example", b"SOURCE"),
    )]
    result = asyncio.run(reviewer.verdict(settings, refs, InlineImage("image/png", b"BEFORE"),
        InlineImage("image/png", b"AFTER"), protected_axes=("target", "matching")))
    assert calls == [[b"BEFORE", b"AFTER", b"PRODUCT", b"SUPPORT"]]
    assert result["status"] == "PASS" and result["provider"] == "gpt"
    assert result["model"] == "gpt-configured-color"
    assert result["baselineSha256"] != result["candidateSha256"]
    assert len(result["baselineSha256"]) == len(result["candidateSha256"]) == 64


@pytest.mark.parametrize("failure", ["no_key", "missing_product", "missing_matching", "empty_baseline", "invalid_mime", "no_axes", "bad_axes", "provider"])
def test_unavailable_input_or_transport_holds_with_at_most_one_call(reviewer, monkeypatch, failure, caplog):
    assert reviewer is not None, "color reviewer is missing"
    calls = []

    async def transport(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("private-provider-body")

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    refs = [LabeledReference(role, InlineImage("image/png", b"REFERENCE"))
            for role in ("product", "matching") if failure != "missing_" + role]
    baseline = InlineImage("text/plain" if failure == "invalid_mime" else "image/png", b"" if failure == "empty_baseline" else b"BEFORE")
    axes = () if failure == "no_axes" else ("unknown",) if failure == "bad_axes" else ("target", "matching")
    result = asyncio.run(reviewer.verdict(make_settings(openai_api_key=None if failure == "no_key" else "test"),
        refs, baseline, InlineImage("image/png", b"AFTER"), protected_axes=axes))
    assert result["status"] == "UNJUDGEABLE"
    assert calls == ([1] if failure == "provider" else [])
    assert "private-provider-body" not in str(result) + caplog.text


def test_model_configuration(monkeypatch):
    monkeypatch.delenv("CUT_COLOR_REVIEW_MODEL", raising=False)
    assert getattr(load_settings(), "cut_color_review_model", None) == "gpt-6-astra"
    monkeypatch.setenv("CUT_COLOR_REVIEW_MODEL", "gpt-configured-color")
    assert load_settings().cut_color_review_model == "gpt-configured-color"


@pytest.mark.parametrize("baseline", [None, InlineImage("image/png", None), InlineImage(None, b"BEFORE")])
def test_corrupt_baseline_returns_unjudgeable_without_transport(reviewer, monkeypatch, baseline):
    async def forbidden(*args, **kwargs):
        pytest.fail("corrupt baseline must not reach a paid transport")

    monkeypatch.setattr(vision_llm, "_call_gpt", forbidden)
    refs = [LabeledReference("product", InlineImage("image/png", b"PRODUCT"))]
    result = asyncio.run(reviewer.verdict(make_settings(openai_api_key="test"), refs, baseline,
        InlineImage("image/png", b"AFTER"), protected_axes=("target",)))
    assert result["status"] == "UNJUDGEABLE"
