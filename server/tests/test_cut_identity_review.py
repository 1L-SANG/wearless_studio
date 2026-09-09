"""Visible-face certification fails closed, using only explicit face references."""

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
    if not importlib.util.find_spec("app.agents.cut_identity_review"):
        return None
    return importlib.import_module("app.agents.cut_identity_review")


def face_raw(relation="clear_target", **changes):
    return {
        "viewAdequate": True, "selectedFaceRelation": relation,
        "strongestTargetEvidence": "Visible upper eyelid fold and nose bridge match the target.",
        "strongestSourceEvidence": "Source eye folds differ from the candidate.",
        "remainingAmbiguity": "No material ambiguity in the visible regions.", **changes,
    }


@pytest.mark.parametrize("raw,status", [
    (face_raw(), "PASS"), (face_raw("source_retained"), "FAIL"),
    (face_raw("mixed"), "FAIL"), (face_raw("uncertain"), "UNJUDGEABLE"),
    (face_raw(viewAdequate=False), "UNJUDGEABLE"),
    (face_raw("mixed", viewAdequate=False), "UNJUDGEABLE"),
    (face_raw(viewAdequate="true"), "UNJUDGEABLE"),
    (face_raw(strongestTargetEvidence="  "), "UNJUDGEABLE"),
    (face_raw(strongestTargetEvidence="눈매와 콧대가 일치함."), "PASS"),
    (face_raw(strongestSourceEvidence=None), "UNJUDGEABLE"),
    (face_raw(unexpected="field"), "UNJUDGEABLE"), ({}, "UNJUDGEABLE"),
    (None, "UNJUDGEABLE"), ([], "UNJUDGEABLE"),
])
def test_normalization_cannot_certify_missing_or_ambiguous_evidence(reviewer, raw, status):
    assert reviewer is not None, "independent face reviewer is missing"
    result = reviewer.validate(raw)
    assert result["status"] == status
    assert result["raw"] == raw
    assert len(result["evidence"]) <= 1200


@pytest.mark.parametrize("with_source", [True, False])
def test_only_supplied_face_and_source_are_sent_in_labeled_roles(reviewer, monkeypatch, with_source):
    assert reviewer is not None, "independent face reviewer is missing"
    async def transport(settings, model, prompt, images, schema, timeout, **kwargs):
        assert model == "gpt-configured-face"
        assert [image.data for image in images] == ([b"FACE", b"SOURCE", b"CANDIDATE"] if with_source else [b"FACE", b"CANDIDATE"])
        assert "1. MODEL FACE" in prompt
        assert ("2. EXAMPLE" in prompt) if with_source else ("No EXAMPLE source reference was supplied" in prompt)
        assert f"{len(images)}. CANDIDATE" in prompt
        assert "hairstyle" in prompt and "visible" in prompt and "cropped" in prompt
        assert schema["additionalProperties"] is False
        assert len(schema["required"]) == 5
        return face_raw()

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    refs = [LabeledReference(role, InlineImage("image/png", data)) for role, data in [
        ("modelBody", b"BODY"), ("modelFace", b"FACE"), ("product", b"PRODUCT"),
        *([("example", b"SOURCE")] if with_source else []),
    ]]
    settings = make_settings(openai_api_key="test", analysis_model_order="gemini,gpt")
    object.__setattr__(settings, "cut_identity_review_model", "gpt-configured-face")
    result = asyncio.run(reviewer.verdict(settings, refs, InlineImage("image/png", b"CANDIDATE")))
    assert result["status"] == "PASS"
    assert result["provider"] == "gpt" and result["model"] == "gpt-configured-face"
    assert result["candidateSha256"] == "6306148e33dda5003ac7184ce136f2e4a74df285ba25a945bf9ff79c4de0f848"


@pytest.mark.parametrize("failure", ["missing_face", "no_key", "provider_failure"])
def test_unavailable_review_holds_without_exposing_exception(reviewer, monkeypatch, failure, caplog):
    assert reviewer is not None, "independent face reviewer is missing"
    async def transport(*args, **kwargs):
        assert failure == "provider_failure", "preflight should avoid a paid call"
        raise RuntimeError("secret-provider-body")

    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    settings = make_settings(openai_api_key=None if failure == "no_key" else "test")
    refs = [] if failure == "missing_face" else [LabeledReference("modelFace", InlineImage("image/png", b"FACE"))]
    result = asyncio.run(reviewer.verdict(settings, refs, InlineImage("image/png", b"CANDIDATE")))
    assert result["status"] == "UNJUDGEABLE"
    assert "secret-provider-body" not in str(result) + caplog.text


def test_review_model_setting_is_configurable(monkeypatch):
    monkeypatch.delenv("CUT_IDENTITY_REVIEW_MODEL", raising=False)
    assert getattr(load_settings(), "cut_identity_review_model", None) == "gpt-6-astra"
    monkeypatch.setenv("CUT_IDENTITY_REVIEW_MODEL", "gpt-configured-face")
    assert load_settings().cut_identity_review_model == "gpt-configured-face"
