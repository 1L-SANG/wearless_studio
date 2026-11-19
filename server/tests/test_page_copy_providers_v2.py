"""Offline transport checks: exact supplied options, never live provider calls."""

import asyncio
import copy

import httpx
import pytest

from app.agents import vision_llm
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir


SCHEMA = {"type": "object", "properties": {"sections": {"type": "array", "items": {"type": "string"}}}}


@pytest.fixture
def wire(monkeypatch):
    seen = []

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json, headers):
            seen.append({"url": url, "body": json, "timeout": self.timeout})
            if "generativelanguage" in url:
                payload = {"candidates": [{"finishReason": "STOP", "content": {
                    "parts": [{"text": '{"sections": []}'}]}}]}
            elif "anthropic" in url:
                payload = {"model": json["model"], "stop_reason": "end_turn",
                           "usage": {"input_tokens": 12, "output_tokens": 4},
                           "content": [{"type": "text", "text": '{"sections": []}'}]}
            else:
                payload = {"model": json["model"], "choices": [{"finish_reason": "stop",
                           "message": {"content": '{"sections": []}'}}]}
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    return seen


@pytest.mark.parametrize("model,cap", [
    ("gemini-4-hydrogen", 4321), ("gemini-4-argon", 5432),
])
def test_gemini4_sends_supplied_thinking_config_without_model_guessing(wire, model, cap):
    # Synthetic transport keys: this deliberately does not claim a Gemini4 API contract.
    config = {"testOnlyWireKey": "keep-verbatim", "testOnlyNested": {"values": [0, False]}}
    original = copy.deepcopy(config)
    result = asyncio.run(vision_llm.analyze_with_fallback(
        make_settings(gemini_api_key="fake-key"), "temporary", [], SCHEMA,
        order=["gemini"], models={"gemini": model}, thinking_config=config,
        thinking_level="high", thinking_budget=123, max_output_tokens=cap,
        require_complete_envelope=True,
    ))
    assert result == ({"sections": []}, "gemini")
    assert len(wire) == 1
    assert wire[0]["url"] == f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    generation = wire[0]["body"]["generationConfig"]
    assert generation["thinkingConfig"] == original
    assert generation["maxOutputTokens"] == cap
    generation["thinkingConfig"]["testOnlyNested"]["values"].append("changed")
    assert config == original


def test_empty_gemini_thinking_config_overrides_legacy_default(wire):
    result = asyncio.run(vision_llm._call_gemini(
        make_settings(gemini_api_key="fake-key", analysis_thinking_level="low"),
        "gemini-4-hydrogen", "temporary", [], SCHEMA, 5, thinking_config={},
    ))
    assert result == {"sections": []}
    assert wire[0]["body"]["generationConfig"]["thinkingConfig"] == {}


@pytest.mark.parametrize("config", [[], "low", 0, False])
def test_gemini_rejects_non_mapping_thinking_config_before_http(wire, config):
    with pytest.raises(ValueError, match="thinking config"):
        asyncio.run(vision_llm._call_gemini(
            make_settings(gemini_api_key="fake-key"), "gemini-4-argon",
            "temporary", [], SCHEMA, 5, thinking_config=config,
        ))
    assert wire == []


def test_gpt_receives_none_effort_and_output_cap_without_gemini_config(wire):
    result = asyncio.run(vision_llm.analyze_with_fallback(
        make_settings(openai_api_key="fake-key"), "temporary", [], SCHEMA,
        order=["gpt"], models={"gpt": "gpt-6.1-terra"},
        reasoning_effort="none", max_output_tokens=3456,
        thinking_config={"testOnlyWireKey": "gemini-only"},
    ))
    assert result == ({"sections": []}, "gpt")
    body = wire[0]["body"]
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == 3456
    assert "thinkingConfig" not in body and "thinking_config" not in body


def test_claude_opus_low_effort_and_output_cap_reach_messages_api(wire):
    result = asyncio.run(vision_llm._call_claude(
        make_settings(anthropic_api_key="fake-key"), "claude-opus-5-5",
        "temporary", [], SCHEMA, 5, effort="low", max_tokens=5678,
        thinking={"type": "adaptive"},
    ))
    assert result == {"sections": []}
    body = wire[0]["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["max_tokens"] == 5678
    assert body["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize('feature', [False, True])
def test_page_copy_gpt_wire_schema_is_strict_and_editor_keeps_v1_pairs(wire, page_copy_prompt_dir, feature):
    from app.agents import page_copy
    request = page_copy.build_request([], {}, {'seed': 'fixture', 'slots': {}},
                                     'gpt', 'fixture-gpt', feature=feature, prompt_version='v2')
    asyncio.run(page_copy._call(make_settings(openai_api_key='fake-key'), request, 2, {}))
    response_format = wire[0]['body']['response_format']['json_schema']
    assert response_format['strict'] is True
    schema = response_format['schema']
    def check(node):
        if not isinstance(node, dict): return
        if node.get('type') == 'object':
            assert set(node['required']) == set(node['properties'])
            assert node['additionalProperties'] is False
        for value in node.values():
            if isinstance(value, dict): check(value)
            elif isinstance(value, list):
                for item in value: check(item)
    check(schema)
    pair = schema['properties']['featureCopy']['items']
    if feature:
        assert set(pair['properties']) == {'point', 'desc'}
        assert request['prompt_version'] == 'v1'
    else:
        assert set(pair['properties']['label']['type']) == {'string', 'null'}
        assert 'label' not in page_copy.copy_schema()['properties']['featureCopy']['items']['required']
