"""Actual QC calls must reject incomplete envelopes before validating their JSON."""

import asyncio

import pytest

from app.agents import image_qc, vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from test_vision_qc_policy_boundary import offline_http, gpt_response, gemini_response


KINDS = ("unscored", "scene", "pick")
QC_RAW = {"verdict": "pass", "mismatches": [], "correctionPrompt": None}
PICK_RAW = {"chosenIndex": 1, "reason": "Complete candidate comparison."}


def raw_for(kind):
    return PICK_RAW if kind == "pick" else QC_RAW


def invoke(kind, config):
    source = InlineImage("image/png", b"SOURCE")
    current = InlineImage("image/png", b"CURRENT")
    if kind == "unscored":
        return image_qc.verdict(config, [source], current)
    if kind == "scene":
        return image_qc.scene_verdict(config, source, current)
    return image_qc.pick_best(config, [source], [current, InlineImage("image/png", b"CANDIDATE2")])


def config(order="gemini"):
    return make_settings(openai_api_key="fake-local", openai_api_key_fallback=None,
        gemini_api_key="fake-local", analysis_model_order=order)


def invalid_envelope(kind, invalid):
    payload = gemini_response(raw_for(kind))
    if invalid == "duplicate":
        payload["candidates"] *= 2
    elif invalid == "blocked":
        payload["promptFeedback"] = {"blockReason": "SAFETY"}
    else:
        payload["candidates"][0]["finishReason"] = {
            "partial": "MAX_TOKENS", "safety": "SAFETY", "missing_finish": None,
        }[invalid]
    return payload


@pytest.mark.parametrize("kind", KINDS)
def test_actual_qc_requests_complete_envelope_and_accepts_complete_stop(monkeypatch, kind):
    seen = offline_http(monkeypatch, lambda host, body: gemini_response(raw_for(kind)))
    real_analyze = image_qc.analyze_with_fallback
    flags = []

    async def observe_and_delegate(*args, **kwargs):
        flags.append(kwargs.get("require_complete_envelope"))
        return await real_analyze(*args, **kwargs)

    monkeypatch.setattr(image_qc, "analyze_with_fallback", observe_and_delegate)
    result = asyncio.run(invoke(kind, config()))
    assert flags == [True]
    assert result == raw_for(kind)
    assert len(seen) == 1
    assert "systemInstruction" in seen[0][1]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("invalid", ["partial", "safety", "missing_finish", "duplicate", "blocked"])
def test_actual_qc_never_folds_incomplete_success_json_into_pass_or_pick(monkeypatch, kind, invalid):
    seen = offline_http(monkeypatch, lambda host, body: invalid_envelope(kind, invalid))
    with pytest.raises(vision_llm.VisionError):
        asyncio.run(invoke(kind, config()))
    assert len(seen) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_actual_qc_incomplete_gemini_falls_back_once_to_complete_gpt(monkeypatch, kind):
    seen = offline_http(monkeypatch, lambda host, body: (
        invalid_envelope(kind, "partial") if host == "generativelanguage.googleapis.com"
        else gpt_response(raw_for(kind))))
    assert asyncio.run(invoke(kind, config("gemini,gpt"))) == raw_for(kind)
    assert [host for host, body in seen] == ["generativelanguage.googleapis.com", "api.openai.com"]
    assert seen[1][1]["messages"][0]["role"] == "developer"


def test_general_analysis_keeps_its_default_completion_contract(monkeypatch):
    seen = offline_http(monkeypatch, lambda host, body: invalid_envelope("unscored", "partial"))
    result = asyncio.run(vision_llm.analyze_with_fallback(config(), "ordinary analysis", [],
        {"type": "object"}))
    assert result == (QC_RAW, "gemini")
    assert len(seen) == 1 and "systemInstruction" not in seen[0][1]
