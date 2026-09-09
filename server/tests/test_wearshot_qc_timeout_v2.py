"""The v2 judges need their own finite deadline without changing legacy calls."""
import asyncio
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import httpx
import pytest

from app import config
from app.agents import cut_identity_review, vision_llm, wearshot_qc, wearshot_runtime
from app.agents.cut_output_qc import LabeledReference
from conftest import make_settings
from test_wearshot_contract_v2 import packet, png
from test_wearshot_qc_v2 import observed


def face_observation():
    return dict(viewAdequate=True, selectedFaceRelation="clear_target",
                strongestTargetEvidence="Visible target geometry matches.",
                strongestSourceEvidence="Source geometry differs.", remainingAmbiguity="None observed.")


@pytest.mark.parametrize("override,expected", [(None, 180), (75.5, 75.5)])
def test_real_settings_apply_dedicated_deadline_to_both_judges_without_mutation(monkeypatch, override, expected):
    # Reverting either v2 judge to the legacy 30s deadline breaks this boundary contract.
    settings = make_settings(openai_api_key="test")
    if override is not None:
        settings = replace(settings, wearshot_qc_timeout_seconds=override)
    before = asdict(settings)
    contract, candidate, deadlines = packet(), png(), []
    async def transport(configured, model, prompt, images, schema, timeout):
        deadlines.append((configured.analysis_timeout_seconds, timeout))
        return observed(contract) if "garments" in schema["properties"] else face_observation()
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(wearshot_runtime.review_candidate(settings, contract, candidate))
    assert deadlines == [(expected, expected), (expected, expected)]
    assert result["timeoutSeconds"] == result["identityReview"]["timeoutSeconds"] == expected
    assert wearshot_runtime.release_allowed(result, contract, candidate)
    assert result["errorCategory"] is None
    assert settings.analysis_timeout_seconds == 30 and asdict(settings) == before


def test_direct_primary_uses_v2_default_but_direct_legacy_face_keeps_30(monkeypatch):
    settings, contract, candidate, deadlines = make_settings(openai_api_key="test"), packet(), png(), []
    async def transport(configured, model, prompt, images, schema, timeout):
        deadlines.append(timeout)
        return observed(contract) if "garments" in schema["properties"] else face_observation()
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    primary = asyncio.run(wearshot_qc.verdict(settings, contract, candidate))
    refs = [LabeledReference(ref.role, ref.image) for ref in contract.references]
    legacy = asyncio.run(cut_identity_review.verdict(settings, refs, candidate))
    assert primary["valid"] and legacy["status"] == "PASS"
    assert deadlines == [180, 30]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), None, True, "secret-invalid"])
def test_invalid_v2_deadline_holds_before_provider(monkeypatch, value):
    settings = make_settings(openai_api_key="test")
    # setattr is deliberately avoided: production Settings is frozen.
    settings = replace(settings, wearshot_qc_timeout_seconds=value)
    calls = []
    async def transport(*args):
        calls.append(args)
        raise AssertionError("invalid deadline submitted")
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(wearshot_runtime.review_candidate(settings, packet(), png()))
    assert not result["valid"] and result["status"] == "UNJUDGEABLE"
    assert result["errorCategory"] == "invalid_timeout" and result["timeoutSeconds"] is None
    assert calls == [] and "secret-invalid" not in json.dumps(result)


@pytest.mark.parametrize("failure,category", [
    (TimeoutError("secret URL/key"), "deadline_exceeded"),
    (httpx.ReadTimeout("secret URL/key"), "deadline_exceeded"),
    (RuntimeError("secret URL/key"), "provider_unavailable"),
])
def test_primary_transport_failure_is_sanitized_and_not_visual_failure(monkeypatch, caplog, failure, category):
    async def transport(*args):
        raise failure
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    contract, candidate = packet(), png()
    result = asyncio.run(wearshot_qc.verdict(make_settings(openai_api_key="test"), contract, candidate))
    assert result["errorCategory"] == category and result["timeoutSeconds"] == 180
    assert not result["valid"] and not wearshot_qc.release_allowed(result, contract, candidate)
    assert result["status"] == "UNJUDGEABLE"
    assert "secret URL/key" not in json.dumps(result) + caplog.text


def test_missing_key_is_not_a_timeout(monkeypatch):
    calls = []
    async def transport(*args):
        calls.append(args)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(wearshot_qc.verdict(make_settings(openai_api_key=None), packet(), png()))
    assert result["errorCategory"] == "provider_unavailable"
    assert result["timeoutSeconds"] == 180 and calls == []


@pytest.mark.parametrize("failure,category", [
    (TimeoutError("secret URL/key"), "deadline_exceeded"),
    (httpx.ReadTimeout("secret URL/key"), "deadline_exceeded"),
    (RuntimeError("secret URL/key"), "provider_unavailable"),
])
def test_focused_transport_failure_records_category_and_holds_release(monkeypatch, caplog, failure, category):
    contract, candidate = packet(), png()
    async def transport(configured, model, prompt, images, schema, timeout):
        if "garments" in schema["properties"]:
            return observed(contract)
        raise failure
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(wearshot_runtime.review_candidate(make_settings(openai_api_key="test"), contract, candidate))
    focus = result["identityReview"]
    assert focus["errorCategory"] == category and focus["timeoutSeconds"] == 180
    assert focus["status"] == "UNJUDGEABLE" and not wearshot_runtime.release_allowed(result, contract, candidate)
    assert "secret URL/key" not in json.dumps(result) + caplog.text


def test_legacy_face_missing_key_is_provider_unavailable_and_keeps_30(monkeypatch):
    calls = []
    async def transport(*args):
        calls.append(args)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    refs = [LabeledReference(ref.role, ref.image) for ref in packet().references]
    result = asyncio.run(cut_identity_review.verdict(make_settings(openai_api_key=None), refs, png()))
    assert result["errorCategory"] == "provider_unavailable" and result["timeoutSeconds"] == 30
    assert result["status"] == "UNJUDGEABLE" and calls == []


def test_legacy_face_preflight_still_holds_without_transport_configuration():
    refs = [LabeledReference(ref.role, ref.image) for ref in packet().references]
    result = asyncio.run(cut_identity_review.verdict(SimpleNamespace(openai_api_key=None), refs, png()))
    assert result["status"] == "UNJUDGEABLE" and result["errorCategory"] == "provider_unavailable"
    assert result["timeoutSeconds"] is None


def test_invalid_environment_deadline_is_sanitized_hold(monkeypatch):
    monkeypatch.setattr(config.os, "environ", {"WEARSHOT_QC_TIMEOUT_SECONDS": "secret-invalid", "OPENAI_API_KEY": "test"})
    settings = config.load_settings()
    calls = []
    async def transport(*args):
        calls.append(args)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(wearshot_qc.verdict(settings, packet(), png()))
    assert result["errorCategory"] == "invalid_timeout" and calls == []
    assert "secret-invalid" not in json.dumps(result)


@pytest.mark.parametrize("env,expected", [({}, 180), ({"WEARSHOT_QC_TIMEOUT_SECONDS": "87.5"}, 87.5)])
def test_load_settings_configures_v2_deadline_independently(monkeypatch, env, expected):
    # Replace the environment mapping with synthetic values; never read credential files.
    monkeypatch.setattr(config.os, "environ", env)
    settings = config.load_settings()
    assert settings.wearshot_qc_timeout_seconds == expected
    assert settings.analysis_timeout_seconds == 30
