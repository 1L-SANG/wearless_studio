import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agents import mannequin_specialist_qc as qc, vision_llm
from test_mannequin_specialist_qc import REFS, CANDIDATE, passed


def test_flash_qc_uses_three_independent_gemini_calls_and_never_openai(monkeypatch):
    calls = []
    async def gemini(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append((model, timeout, kwargs))
        return passed()
    async def forbidden(*args, **kwargs):
        pytest.fail("QC must never call OpenAI or an Astra fallback")
    monkeypatch.setattr(vision_llm, "_call_gemini", gemini)
    monkeypatch.setattr(vision_llm, "_call_gpt", forbidden)
    settings = SimpleNamespace(mannequin_specialist_model="gemini-3.8-flash", mannequin_specialist_timeout_seconds=25)
    result = asyncio.run(qc.judge(settings, REFS, CANDIDATE, clothing_type="top"))
    assert result["complete"] and result["verdict"] == "pass"
    assert len(calls) == 3
    assert all(model == "gemini-3.8-flash" and timeout == 25 for model, timeout, _ in calls)
    assert all(options["thinking_level"] == "low" and options["max_output_tokens"] == 2048 for _, _, options in calls)


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "RECITATION", None])
def test_complete_looking_gemini_json_cannot_hide_an_incomplete_qc_response(finish):
    response = httpx.Response(200, json={"modelVersion":"gemini-3.8-flash", "candidates":[{"finishReason":finish,"content":{"parts":[{"text":json.dumps(passed())}]}}]})
    with pytest.raises(vision_llm.VisionError):
        vision_llm._parse_gemini_response(response, metadata={})


def test_qc_envelope_captures_usage_and_ignores_thinking_text():
    payload = {"modelVersion":"gemini-3.8-flash", "usageMetadata":{"promptTokenCount":120,"totalTokenCount":150}, "candidates":[{"finishReason":"STOP","content":{"parts":[{"thought":True,"text":"private non-JSON thinking"},{"text":json.dumps(passed())}]}}]}
    metadata = {}
    result = vision_llm._parse_gemini_response(httpx.Response(200,json=payload), metadata=metadata, requested_model="gemini-3.8-flash")
    assert result == passed()
    assert metadata["usage"] == payload["usageMetadata"]
    assert metadata["finish_reason"] == "STOP"


def test_output_limit_is_opt_in_and_does_not_change_ag01_body():
    args = ("same prompt", [], {"type":"object","properties":{}}, "low")
    regular = vision_llm._gemini_body(*args)
    bounded = vision_llm._gemini_body(*args, max_output_tokens=2048)
    assert "maxOutputTokens" not in regular["generationConfig"]
    assert bounded["generationConfig"].pop("maxOutputTokens") == 2048
    assert bounded == regular


def test_source_and_candidate_schema_instructions_cannot_alias_or_send_integer_enums():
    manifest = [{"imageIndex":1,"kind":"source"}, {"imageIndex":2,"kind":"candidate"}]
    schema = vision_llm._to_gemini_schema(qc.request_schema(manifest))
    fields = schema["properties"]["issues"]["items"]["properties"]
    source = fields["sourceEvidence"]["properties"]["imageIndex"]
    candidate = fields["candidateEvidence"]["properties"]["imageIndex"]
    assert "[1]" in source["description"] and "[2]" in candidate["description"]
    assert "enum" not in source and "enum" not in candidate


def test_stale_or_missing_source_contract_does_not_become_a_qc_instruction():
    from test_mannequin_photo_structure import refs, contract
    source = refs()
    stored = contract()
    prompt, _, _ = qc._prepare(source, CANDIDATE, "top", None, None, "details", product_evidence=stored)
    assert "rounded neckline with narrow binding" in prompt and "SOURCE FEATURES ALREADY RECORDED" in prompt
    stored["hardFacts"][0]["value"] = "Nine buttons"
    with pytest.raises(ValueError):
        qc._prepare(source, CANDIDATE, "top", None, None, "details", product_evidence=stored)
    plain, _, _ = qc._prepare(source, CANDIDATE, "top", None, None, "details")
    assert "rounded neckline with narrow binding" not in plain


def test_old_astra_setting_is_rejected_without_any_provider_call(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("A stale Astra setting must not reach either provider")
    monkeypatch.setattr(vision_llm, "_call_gpt", forbidden)
    monkeypatch.setattr(vision_llm, "_call_gemini", forbidden)
    settings = SimpleNamespace(mannequin_specialist_model="gpt-6-astra", mannequin_specialist_timeout_seconds=25)
    result = asyncio.run(qc.judge(settings, REFS, CANDIDATE, clothing_type="top"))
    assert result["verdict"] == "review" and not result["complete"]
