"""Offline provider-wire and envelope boundaries for the QC policy prototype."""

import asyncio
import json

import httpx
import pytest

from app.agents import cut_output_qc, detail_output_qc, image_qc, vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from test_cut_output_qc import _img, _model_refs, _plan, _raw
from test_detail_output_qc import picture, primary_response, response, settings, specialist_response
from test_image_qc import _paired_raw


RAW = {"ok": True}
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def gpt_response(raw=RAW):
    return {"model": "local-gpt", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(raw)}}]}


def gemini_response(raw=RAW):
    return {"modelVersion": "local-gemini", "candidates": [{"finishReason": "STOP", "content": {
        "parts": [{"text": json.dumps(raw)}]}}]}


def offline_http(monkeypatch, payload_for):
    """Keep real request builders/parsers; all requests end in an in-memory transport."""
    real_client = httpx.AsyncClient
    seen = []

    def handle(request):
        assert request.url.host in {"api.openai.com", "generativelanguage.googleapis.com"}
        body = json.loads(request.content)
        seen.append((request.url.host, body))
        return httpx.Response(200, content=json.dumps(payload_for(request.url.host, body)))

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    from app import openai_key_fallback
    monkeypatch.setattr(openai_key_fallback, "_primary_unavailable", False)
    return seen


def test_none_policy_keeps_legacy_request_bodies():
    image = InlineImage("image/png", b"image")
    assert vision_llm._gpt_body("model", "dynamic", [image], SCHEMA) == {
        "model": "model", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "dynamic"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
        ]}], "response_format": {"type": "json_schema", "json_schema": {
            "name": "product_analysis", "strict": True, "schema": SCHEMA}},
    }
    assert vision_llm._gemini_body("dynamic", [image], SCHEMA, "off") == {
        "contents": [{"role": "user", "parts": [{"text": "dynamic"}, {
            "inline_data": {"mime_type": "image/png", "data": "aW1hZ2U="}}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": {
            "type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]}},
    }


@pytest.mark.parametrize("provider", ["gpt", "gemini"])
def test_optional_policy_is_privileged_on_real_wire_and_dynamic_data_stays_user(monkeypatch, provider):
    seen = offline_http(monkeypatch, lambda host, body: (
        gpt_response() if host == "api.openai.com" else gemini_response()))
    config = make_settings(openai_api_key="fake-local", openai_api_key_fallback=None,
        gemini_api_key="fake-local", analysis_model_order=provider)
    result = asyncio.run(vision_llm.analyze_with_fallback(config, "USER_SENTINEL", [
        InlineImage("image/png", b"image")], SCHEMA, system_prompt="STATIC_POLICY",
        require_complete_envelope=True))
    assert result == (RAW, provider)
    assert len(seen) == 1
    body = seen[0][1]
    if provider == "gpt":
        assert body["messages"][0] == {"role": "developer", "content": "STATIC_POLICY"}
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"][0]["text"] == "USER_SENTINEL"
        assert len(body["messages"][1]["content"]) == 2
    else:
        assert body["systemInstruction"] == {"parts": [{"text": "STATIC_POLICY"}]}
        assert body["contents"][0]["role"] == "user"
        assert body["contents"][0]["parts"][0]["text"] == "USER_SENTINEL"
        assert len(body["contents"][0]["parts"]) == 2


def test_none_policy_does_not_add_provider_options_or_change_model_timeout(monkeypatch):
    seen = []

    async def provider(config, model, prompt, images, schema, timeout, thinking_level=None, **kwargs):
        seen.append((model, timeout, thinking_level, kwargs))
        return RAW

    monkeypatch.setitem(vision_llm._PROVIDERS, "gpt", (provider, lambda s: s.model_text, lambda s: True))
    config = make_settings(model_text="unchanged-model", analysis_timeout_seconds=17,
        analysis_model_order="gpt")
    assert asyncio.run(vision_llm.analyze_with_fallback(config, "data", [], SCHEMA)) == (RAW, "gpt")
    assert seen == [("unchanged-model", 17, None, {})]


MALFORMED_GEMINI = [
    None, [], {"candidates": {}}, {"candidates": "bad"}, {"candidates": [None]},
    {"candidates": [[]]}, {"candidates": [{"finishReason": "STOP", "content": []}]},
    {"candidates": [{"finishReason": "STOP", "content": "bad"}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": {"text": "bad"}}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [None]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": 3}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": None}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": "yes", "text": "{}"}]}}]},
    {"promptFeedback": [], "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]},
]


@pytest.mark.parametrize("payload", MALFORMED_GEMINI)
@pytest.mark.parametrize("complete", [False, True])
def test_malformed_gemini_envelope_is_structured_invalid_response(payload, complete):
    with pytest.raises(vision_llm.VisionError) as caught:
        vision_llm._parse_gemini_response(httpx.Response(200, content=json.dumps(payload)),
            metadata={} if complete else None)
    assert caught.value.unknown_cause == "invalid_response"


@pytest.mark.parametrize("payload", MALFORMED_GEMINI)
@pytest.mark.parametrize("complete", [False, True])
def test_malformed_gemini_reaches_real_gpt_fallback_with_same_policy(monkeypatch, payload, complete):
    seen = offline_http(monkeypatch, lambda host, body: (
        payload if host == "generativelanguage.googleapis.com" else gpt_response()))
    config = make_settings(openai_api_key="fake-local", openai_api_key_fallback=None,
        gemini_api_key="fake-local", analysis_model_order="gemini,gpt")
    assert asyncio.run(vision_llm.analyze_with_fallback(config, "DYNAMIC", [], SCHEMA,
        system_prompt="STATIC", require_complete_envelope=complete)) == (RAW, "gpt")
    assert [host for host, body in seen] == ["generativelanguage.googleapis.com", "api.openai.com"]
    assert seen[0][1]["systemInstruction"]["parts"][0]["text"] == "STATIC"
    assert seen[1][1]["messages"][0] == {"role": "developer", "content": "STATIC"}


@pytest.mark.parametrize("change,cause", [
    ("partial", "invalid_response"), ("duplicate", "invalid_response"),
    ("blocked", "provider_rejection"),
])
def test_complete_gemini_boundary_remains_fail_closed(change, cause):
    payload = gemini_response()
    if change == "partial":
        payload["candidates"][0]["finishReason"] = "MAX_TOKENS"
    elif change == "duplicate":
        payload["candidates"] *= 2
    else:
        payload["promptFeedback"] = {"blockReason": "SAFETY"}
    with pytest.raises(vision_llm.VisionError) as caught:
        vision_llm._parse_gemini_response(httpx.Response(200, json=payload), metadata={})
    assert caught.value.unknown_cause == cause


def test_gemini_thought_parts_never_enter_structured_result():
    payload = gemini_response()
    payload["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": "not JSON"})
    assert vision_llm._parse_gemini_response(httpx.Response(200, json=payload), metadata={}) == RAW


def test_three_qc_callers_send_policy_without_promoting_dynamic_contracts(monkeypatch):
    def payload_for(host, body):
        assert host == "api.openai.com"
        schema = body["response_format"]["json_schema"]["schema"]
        if "verdict" in schema["properties"]:
            raw = _paired_raw()
        elif set(schema["properties"]) == {"gates"}:
            gate_properties = schema["properties"]["gates"]["items"]["properties"]
            raw = _raw() if "gate" in gate_properties else primary_response(detail_output_qc, response(detail_output_qc))
        else:
            raw = specialist_response(detail_output_qc, response(detail_output_qc))
        return gpt_response(raw)

    seen = offline_http(monkeypatch, payload_for)
    config = make_settings(openai_api_key="fake-local", openai_api_key_fallback=None,
        gemini_api_key=None, analysis_model_order="gpt")
    paired = asyncio.run(image_qc.verdict(config, [_img(b"SOURCE")], _img(b"AFTER"),
        scored=True, before_image=_img(b"BEFORE"), edit_goal="DYNAMIC_EDIT_SENTINEL"))
    assert paired["target_resolved"] is True
    refs = [cut_output_qc.LabeledReference("product", _img(b"PRODUCT")), *_model_refs(),
        cut_output_qc.LabeledReference("example", _img(b"EXAMPLE"))]
    assert asyncio.run(cut_output_qc.verdict(config, _plan(), refs, _img(b"CURRENT")))["passed"]
    assert asyncio.run(detail_output_qc.verdict(settings(), [picture()], picture("red")))["passed"]
    assert len(seen) == 4  # image, cut, detail core, detail specialist
    privileged = [body["messages"][0] for host, body in seen]
    assert all(message["role"] == "developer" for message in privileged)
    assert len({message["content"].split("\nVALIDATED_QC_CONTEXT\n", 1)[0]
                for message in privileged}) == 1
    assert [json.loads(message["content"].split("\nVALIDATED_QC_CONTEXT\n", 1)[1])["family"]
            for message in privileged] == ["image", "cut", "detail", "detail"]
    assert "DYNAMIC_EDIT_SENTINEL" not in privileged[0]["content"]
    users = [body["messages"][1] for host, body in seen]
    assert all(message["role"] == "user" for message in users)
    assert "DYNAMIC_EDIT_SENTINEL" in users[0]["content"][0]["text"]
    assert "productTruth" in users[1]["content"][0]["text"]
    assert len(users[0]["content"]) == 4  # text, original, before, current


def test_same_privileged_policy_preserves_distinct_generic_and_confirmed_field_owners(monkeypatch):
    seen = offline_http(monkeypatch, lambda host, body: gpt_response(_raw()))
    monkeypatch.setattr(cut_output_qc, "confirmed_gpt_framing_contract", lambda request: {
        "shot": "full", "requestedFraming": "complete head through both feet",
        "faceExposure": "visible", "roughFraming": "vertical full-body"})
    config = make_settings(openai_api_key="fake-local", openai_api_key_fallback=None,
        gemini_api_key=None, analysis_model_order="gpt")
    plan = _plan()
    plan["attributeOwners"].update(length="productTruth", silhouette="productTruth")
    refs = [cut_output_qc.LabeledReference("product", _img(b"PRODUCT")), *_model_refs(),
        cut_output_qc.LabeledReference("example", _img(b"EXAMPLE")),
        cut_output_qc.LabeledReference("mannequin", _img(b"MANNEQUIN"))]
    generic = asyncio.run(cut_output_qc.verdict(config, plan, refs, _img(b"CURRENT")))
    confirmed = asyncio.run(cut_output_qc.verdict(config, plan, refs, _img(b"CURRENT"),
        authority_profile="confirmed_gpt_v1", confirmed_prompt_input=object()))
    assert generic["contract"]["attributeOwners"]["length"] == "productTruth"
    assert confirmed["contract"]["attributeOwners"]["length"] == "mannequin"
    assert confirmed["contract"]["attributeOwners"]["construction"] == "productTruth"
    policies = [body["messages"][0]["content"].split("\nVALIDATED_QC_CONTEXT\n", 1)
                for host, body in seen]
    assert policies[0][0] == policies[1][0]
    assert json.loads(policies[0][1])["attributeOwners"]["length"] == "productTruth"
    assert json.loads(policies[1][1])["attributeOwners"]["length"] == "mannequin"
    assert '"length":"productTruth"' in seen[0][1]["messages"][1]["content"][0]["text"]
    assert '"length":"mannequin"' in seen[1][1]["messages"][1]["content"][0]["text"]
