"""QC-only Claude transport, with no live provider requests."""

import asyncio
import base64
import copy
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings


def picture(size=(67, 89), mode="RGB"):
    image = Image.new(mode, size, "green")
    out = BytesIO()
    image.save(out, format="PNG")
    return InlineImage("image/png", out.getvalue())


def response(**overrides):
    return {"model": "claude-sonnet-5-5", "stop_reason": "end_turn",
            "usage": {"input_tokens": 123, "output_tokens": 45,
                      "cache_read_input_tokens": 10},
            "content": [{"type": "text", "text": '{"gates": []}'}], **overrides}


def fake_client(monkeypatch, payload=None, *, status=200, headers=None, error=None, raw_body=None):
    seen = []

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json, headers):
            seen.append({"url": url, "body": json, "headers": headers, "timeout": self.timeout})
            if error is not None:
                raise error
            if raw_body is not None:
                return httpx.Response(status, content=raw_body, request=httpx.Request("POST", url))
            return httpx.Response(status, json=payload if payload is not None else response(),
                                  headers=response_headers, request=httpx.Request("POST", url))

    response_headers = headers
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    return seen


def call(**kwargs):
    settings = kwargs.pop("settings", make_settings(anthropic_api_key="test-claude-key"))
    return asyncio.run(vision_llm._call_claude(
        settings, "claude-sonnet-5-5", "judge this", kwargs.pop("images", [picture()]),
        kwargs.pop("schema", {"type": "object", "properties": {"gates": {"type": "array", "items": {"type": "string"}}}}),
        90, effort=kwargs.pop("effort", "xhigh"), **kwargs))


@pytest.mark.parametrize("system_prompt", [None, "privileged QC policy"])
def test_request_matches_claude_contract_and_preserves_pixels(monkeypatch, system_prompt):
    seen = fake_client(monkeypatch)
    schema = {"type": "object", "properties": {"gates": {"type": "array", "items": {"type": "string"}}}}
    original = copy.deepcopy(schema)
    source = picture(mode="RGBA")
    cpu_calls = []

    async def immediate(func, *args, **kwargs):
        cpu_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(vision_llm, "run_cpu_bound", immediate)
    assert call(images=[source], schema=schema, system_prompt=system_prompt) == {"gates": []}
    expected_jpeg = BytesIO()
    with Image.open(BytesIO(source.data)) as image:
        image.convert("RGB").save(expected_jpeg, format="JPEG", quality=95)
    expected = {
        "model": "claude-sonnet-5-5", "max_tokens": 32000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "xhigh", "format": {"type": "json_schema", "schema": {
            **schema, "additionalProperties": False, "required": ["gates"]}}},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "judge this"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(expected_jpeg.getvalue()).decode()}}]}],
    }
    if system_prompt is not None:
        expected["system"] = system_prompt
    assert seen == [{"url": "https://api.anthropic.com/v1/messages", "body": expected,
                     "headers": {"x-api-key": "test-claude-key", "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"}, "timeout": 90}]
    with Image.open(BytesIO(base64.b64decode(expected["messages"][0]["content"][1]["source"]["data"]))) as image:
        assert image.format == "JPEG" and image.size == (67, 89)
    assert schema == original
    assert len(cpu_calls) == 2  # JPEG/schema/body and response parsing stay off the event loop.
    assert "claude" not in vision_llm._PROVIDERS


def test_schema_constraints_are_removed_only_from_schema_nodes(monkeypatch):
    seen = fake_client(monkeypatch)
    unsupported = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                   "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
                   "minProperties", "maxProperties")
    schema = {"type": "object", "properties": {
        "minimum": {"type": "integer", **dict.fromkeys(unsupported, 1)},
        "items": {"type": "array", "maxItems": 2, "items": {
            "anyOf": [{"type": "object", "properties": {"evidence": {"type": "string", "minLength": 1}}},
                      {"type": "null"}]}},
        "optional": {"type": ["object", "null"], "properties": {"value": {"type": "number", "maximum": 1}}},
    }, "required": ["minimum"], "additionalProperties": True,
        "$defs": {"nested": {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}}}}}
    original = copy.deepcopy(schema)
    call(schema=schema, effort="max", max_tokens=1000)
    body = seen[0]["body"]
    clean = body["output_config"]["format"]["schema"]
    assert clean["properties"]["minimum"] == {"type": "integer"}
    assert clean["required"] == ["minimum", "items", "optional"] and clean["additionalProperties"] is False
    nested = clean["properties"]["items"]["items"]["anyOf"][0]
    assert nested == {"type": "object", "properties": {"evidence": {"type": "string"}},
                      "required": ["evidence"], "additionalProperties": False}
    assert clean["properties"]["optional"]["required"] == ["value"]
    assert clean["$defs"]["nested"]["additionalProperties"] is False
    assert clean["$defs"]["nested"]["properties"]["x"] == {"type": "integer"}
    assert schema == original
    assert body["output_config"]["effort"] == "max" and body["max_tokens"] == 1000


def test_specialist_thirteen_images_remain_jpeg_at_original_dimensions(monkeypatch):
    seen = fake_client(monkeypatch)
    images = [picture((128 + i, 160 + i)) for i in range(13)]
    call(images=images)
    content = seen[0]["body"]["messages"][0]["content"]
    assert len(content) == 14
    for i, part in enumerate(content[1:]):
        assert part["source"]["media_type"] == "image/jpeg"
        with Image.open(BytesIO(base64.b64decode(part["source"]["data"]))) as image:
            assert image.size == (128 + i, 160 + i)


def test_request_larger_than_limit_is_rejected_before_transport(monkeypatch):
    seen = fake_client(monkeypatch)
    monkeypatch.setattr(vision_llm, "_CLAUDE_MAX_REQUEST_BYTES", 100)
    with pytest.raises(vision_llm.VisionError) as exc:
        call()
    assert exc.value.unknown_cause == "invalid_input" and seen == []


def test_thinking_is_ignored_text_blocks_joined_and_usage_compatible(monkeypatch):
    fake_client(monkeypatch, response(content=[{"type": "thinking", "thinking": "do not parse"},
                                             {"type": "text", "text": '{"gates":'},
                                             {"type": "text", "text": " []}"}]))
    metadata = {}
    assert call(metadata=metadata) == {"gates": []}
    assert metadata == {"requested_model": "claude-sonnet-5-5", "returned_model": "claude-sonnet-5-5",
                        "finish_reason": "end_turn", "usage": {
                            "input_tokens": 123, "output_tokens": 45, "cache_read_input_tokens": 10,
                            "prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}}


@pytest.mark.parametrize("stop_reason,cause", [("max_tokens", "invalid_response"),
    ("refusal", "provider_rejection"), ("tool_use", "invalid_response"),
    ("pause_turn", "invalid_response"), (None, "invalid_response")])
def test_incomplete_or_refused_response_never_accepts_complete_json(monkeypatch, stop_reason, cause):
    fake_client(monkeypatch, response(stop_reason=stop_reason))
    metadata = {}
    with pytest.raises(vision_llm.VisionError) as exc:
        call(metadata=metadata)
    assert exc.value.unknown_cause == cause
    assert metadata["finish_reason"] == stop_reason


@pytest.mark.parametrize("payload", [[], response(content=None), response(content={}), response(content=[None]),
    response(content=[{"type": "text", "text": None}]), response(content=[{"type": "text", "text": 42}]),
    response(content=[{"type": "thinking", "thinking": "only thinking"}]),
    response(content=[{"type": "text", "text": "not JSON"}]),
    response(content=[{"type": "text", "text": "[]"}])])
def test_malformed_content_or_json_is_invalid_response(monkeypatch, payload):
    fake_client(monkeypatch, payload)
    with pytest.raises(vision_llm.VisionError) as exc:
        call()
    assert exc.value.unknown_cause == "invalid_response"


def test_non_json_response_envelope_is_invalid_response(monkeypatch):
    fake_client(monkeypatch, raw_body=b"<html>proxy error</html>")
    with pytest.raises(vision_llm.VisionError) as exc:
        call()
    assert exc.value.unknown_cause == "invalid_response"


@pytest.mark.parametrize("usage", [{"input_tokens": "123", "output_tokens": 45},
                                  {"input_tokens": None, "output_tokens": 45}])
def test_malformed_usage_is_invalid_response_instead_of_unexpected_error(monkeypatch, usage):
    fake_client(monkeypatch, response(usage=usage))
    with pytest.raises(vision_llm.VisionError) as exc:
        call(metadata={})
    assert exc.value.unknown_cause == "invalid_response"


def test_malformed_image_is_invalid_input_before_transport(monkeypatch):
    seen = fake_client(monkeypatch)
    with pytest.raises(vision_llm.VisionError) as exc:
        call(images=[InlineImage("image/png", b"not an image")])
    assert exc.value.unknown_cause == "invalid_input" and seen == []


@pytest.mark.parametrize("status,cause,retry", [(429, "provider_error", 3), (529, "provider_error", 3),
    (500, "provider_error", None), (408, "provider_error", None), (409, "provider_error", None),
    (401, "configuration", None), (403, "configuration", None), (404, "configuration", None),
    (400, "invalid_input", None), (413, "invalid_input", None)])
def test_http_error_mapping_does_not_expose_response_body(monkeypatch, status, cause, retry):
    fake_client(monkeypatch, {"error": "test-claude-key judge this image data"},
                status=status, headers={"retry-after": "3"})
    with pytest.raises(vision_llm.VisionError) as exc:
        call()
    assert exc.value.unknown_cause == cause and exc.value.retry_after_seconds == retry
    assert "test-claude-key" not in str(exc.value) and "judge this" not in str(exc.value)


@pytest.mark.parametrize("error", [httpx.ConnectError("sensitive network error"),
                                  httpx.ReadTimeout("sensitive network error")])
def test_network_errors_are_provider_errors_without_sensitive_text(monkeypatch, error):
    fake_client(monkeypatch, error=error)
    with pytest.raises(vision_llm.VisionError) as exc:
        call()
    assert exc.value.unknown_cause == "provider_error"
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_is_configuration_error_without_request(monkeypatch, key):
    seen = fake_client(monkeypatch)
    with pytest.raises(vision_llm.VisionError) as exc:
        call(settings=make_settings(anthropic_api_key=key))
    assert exc.value.unknown_cause == "configuration" and seen == []


def test_claude_settings_default_and_environment(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DETAIL_QC_CLAUDE_EFFORT", raising=False)
    settings = load_settings()
    assert settings.anthropic_api_key == "" and settings.detail_qc_claude_effort == "xhigh"
    assert settings.model_detail_core == settings.model_detail_specialist == "gpt-5.4-2026-03-05"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-claude-key")
    monkeypatch.setenv("DETAIL_QC_CLAUDE_EFFORT", "high")
    settings = load_settings()
    assert settings.anthropic_api_key == "test-claude-key" and settings.detail_qc_claude_effort == "high"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_claude_effort_allowed_values(effort):
    assert make_settings(detail_qc_claude_effort=effort).detail_qc_claude_effort == effort


@pytest.mark.parametrize("effort", ["", "typo", "HIGH"])
def test_invalid_claude_effort_is_configuration_error(monkeypatch, effort):
    with pytest.raises(ValueError, match="DETAIL_QC_CLAUDE_EFFORT"):
        make_settings(detail_qc_claude_effort=effort)
    monkeypatch.setenv("DETAIL_QC_CLAUDE_EFFORT", effort)
    with pytest.raises(ValueError, match="DETAIL_QC_CLAUDE_EFFORT"):
        load_settings()
