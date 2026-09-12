import json

import httpx
import pytest

from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings

RAW = {"clothingType": "top", "fit": "regular", "styleTags": ["basic"]}


def test_specialist_options_and_usage_are_opt_in(monkeypatch):
    seen = []
    payload = {"model": "gpt-6-astra", "usage": {"prompt_tokens": 10, "completion_tokens": 4},
               "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(RAW)}}]}

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            seen.append(kwargs["json"])
            return FakeResp(payload=payload)

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    metadata = {}
    args = (make_settings(openai_api_key="test"), "gpt-6-astra", "inspect", _images(), {"type": "object"}, 60)
    assert run(vision_llm._call_gpt(*args, reasoning_effort="medium", image_detail="high",
                                  max_completion_tokens=1800, metadata=metadata)) == RAW
    assert seen[0]["reasoning_effort"] == "medium"
    assert seen[0]["max_completion_tokens"] == 1800
    assert seen[0]["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    assert metadata == {"requested_model": "gpt-6-astra", "returned_model": "gpt-6-astra",
                        "usage": payload["usage"], "finish_reason": "stop"}
    run(vision_llm._call_gpt(*args))
    assert "reasoning_effort" not in seen[1] and "max_completion_tokens" not in seen[1]
    assert "detail" not in seen[1]["messages"][0]["content"][1]["image_url"]


@pytest.mark.parametrize("finish,refusal", [("length", None), ("content_filter", None), ("stop", "refused")])
def test_complete_looking_json_with_incomplete_or_refused_envelope_is_not_accepted(finish, refusal):
    response = FakeResp(payload={"choices": [{"finish_reason": finish,
        "message": {"content": json.dumps(RAW), "refusal": refusal}}]})
    with pytest.raises(vision_llm.VisionError):
        vision_llm._parse_gpt_response(response)


class FakeResp:
    def __init__(self, status_code=200, payload=None, text="", bad_json=False):
        self.status_code = status_code
        self._payload = payload or {}
        self._bad_json = bad_json
        self.text = text or json.dumps(self._payload)

    def json(self):
        if self._bad_json:  # 200+비JSON(프록시 HTML 등) 시뮬레이션
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def _gpt_ok(raw=RAW):
    return FakeResp(200, {"choices": [{"message": {"content": json.dumps(raw)}}]})


def _gemini_ok(raw=RAW):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(raw)}]}}]})


def fake_client_factory(handler):
    """handler(url) -> FakeResp | raises. vision_llm.httpx.AsyncClient 를 대체."""
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return handler(url)

    return _Client


def _images():
    return [InlineImage("image/png", b"\x89PNG")]


async def _run(settings, handler):
    return await vision_llm.analyze_with_fallback(settings, "prompt", _images(), {"type": "object"})


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_gpt_success(monkeypatch):
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: _gpt_ok()))
    raw, provider = run(_run(s, None))
    assert provider == "gpt"
    assert raw["clothingType"] == "top"


def test_gpt_request_and_response_cpu_work_is_offloaded(monkeypatch):
    calls = []

    async def immediate(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    s = make_settings(openai_api_key="sk-x", gemini_api_key=None,
                      analysis_model_order="gpt,gemini")
    monkeypatch.setattr(vision_llm, "run_cpu_bound", immediate)
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: _gpt_ok()))

    raw, provider = run(_run(s, None))

    assert provider == "gpt" and raw["clothingType"] == "top"
    assert len(calls) == 2  # request base64/schema + response JSON parsing


def test_gemini_success(monkeypatch):
    s = make_settings(openai_api_key=None, gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")
    # gpt 키 없음 → skip → gemini 사용
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: _gemini_ok()))
    raw, provider = run(_run(s, None))
    assert provider == "gemini"


def test_fallback_gpt_to_gemini(monkeypatch):
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")

    def handler(url):
        if "openai" in url:
            return FakeResp(500, {}, "boom")
        return _gemini_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    raw, provider = run(_run(s, None))
    assert provider == "gemini"


def test_both_fail_raises(monkeypatch):
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: FakeResp(500, {}, "boom")))
    with pytest.raises(vision_llm.VisionError):
        run(_run(s, None))


def test_no_keys_raises(monkeypatch):
    s = make_settings(openai_api_key=None, gemini_api_key=None, analysis_model_order="gpt,gemini")
    called = False

    def handler(url):
        nonlocal called
        called = True
        return _gpt_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    with pytest.raises(vision_llm.VisionError):
        run(_run(s, None))
    assert called is False  # 키 전무 → 네트워크 호출 자체가 없어야


def test_http_error_falls_back(monkeypatch):
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")

    def handler(url):
        if "openai" in url:
            raise httpx.ConnectError("down")
        return _gemini_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    _, provider = run(_run(s, None))
    assert provider == "gemini"


def test_order_respected(monkeypatch):
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gemini,gpt")
    seen = []

    def handler(url):
        seen.append("openai" if "openai" in url else "gemini")
        return _gemini_ok() if "generativelanguage" in url else _gpt_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    _, provider = run(_run(s, None))
    assert provider == "gemini"
    assert seen[0] == "gemini"  # 순서대로 gemini 먼저


def test_malformed_200_body_falls_back(monkeypatch):
    # GPT 가 200 을 주지만 본문이 비JSON(프록시 HTML 등) → 폴백이 우회되면 안 됨(F1 회귀)
    s = make_settings(openai_api_key="sk-x", gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")

    def handler(url):
        if "openai" in url:
            return FakeResp(200, {}, "<html>gateway</html>", bad_json=True)
        return _gemini_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    _, provider = run(_run(s, None))
    assert provider == "gemini"


def test_complete_json_text_only_gpt(monkeypatch):
    # 이미지 없이(images=[]) 텍스트 전용 구조화 호출 — AG-02/03 재사용 기반
    s = make_settings(openai_api_key="sk-x", gemini_api_key=None, analysis_model_order="gpt,gemini")
    payload = {"texts": [{"role": "headline", "text": "포근한 니트"}]}
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: _gpt_ok(payload)))
    raw, provider = run(vision_llm.complete_json(s, "write copy", {"type": "object"}))
    assert provider == "gpt"
    assert raw["texts"][0]["role"] == "headline"


def test_complete_json_text_only_gemini_fallback(monkeypatch):
    s = make_settings(openai_api_key=None, gemini_api_key="AIza-x", analysis_model_order="gpt,gemini")
    payload = {"results": [{"blockId": "b1", "verdict": "pass"}]}
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: _gemini_ok(payload)))
    raw, provider = run(vision_llm.complete_json(s, "review copy", {"type": "object"}))
    assert provider == "gemini"
    assert raw["results"][0]["verdict"] == "pass"


def test_gemini_schema_conversion():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "a": {"type": ["string", "null"]},
            "b": {"type": "array", "items": {"type": "string", "enum": ["x"]}},
        },
        "required": ["a", "b"],
    }
    g = vision_llm._to_gemini_schema(schema)
    assert g["type"] == "OBJECT"
    assert "additionalProperties" not in g
    assert g["properties"]["a"]["type"] == "STRING"
    assert g["properties"]["a"]["nullable"] is True
    assert g["properties"]["b"]["type"] == "ARRAY"
    assert g["properties"]["b"]["items"]["enum"] == ["x"]
