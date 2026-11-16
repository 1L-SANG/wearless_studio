import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings

RAW = {"clothingType": "top", "fit": "regular", "styleTags": ["basic"]}


@pytest.mark.parametrize('header,expected', [
    ('7', 7), ('10', 10), ('60', 10), ('0', 0), (' 3 ', 3),
    (None, 2), ('', 2), ('bad', 2), ('1.5', 2), ('-1', 2),
    ('Thu, 08 Oct 2026 00:00:06 GMT', 6),
    ('Thu, 08 Oct 2026 00:01:00 GMT', 10),
    ('Wed, 07 Oct 2026 23:59:59 GMT', 0),
])
def test_rate_limit_retains_bounded_retry_after(monkeypatch, header, expected):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 8, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(vision_llm.time, 'time', lambda: now)
    response = httpx.Response(429, headers={'Retry-After': header} if header is not None else {},
                              json={'error': {'code': 'rate_limit_exceeded'}})
    monkeypatch.setattr(vision_llm.httpx, 'AsyncClient', fake_client_factory(lambda url: response))
    with pytest.raises(vision_llm.VisionError) as result:
        run(vision_llm._call_gpt(make_settings(openai_api_key='test'), 'judge', 'inspect', [], {}, 5))
    assert result.value.unknown_cause == 'provider_error'
    assert getattr(result.value, 'retry_after_seconds', None) == expected


@pytest.mark.parametrize('status,code,cause', [
    (500, None, 'provider_error'), (503, None, 'provider_error'),
    (429, 'insufficient_quota', 'configuration'),
    (429, 'credit_balance_exhausted', 'configuration'),
    (429, 'billing_hard_limit_reached', 'configuration'),
])
def test_other_provider_failures_have_no_rate_limit_wait(monkeypatch, status, code, cause):
    response = httpx.Response(status, headers={'Retry-After': '8'}, json={'error': {'code': code}})
    monkeypatch.setattr(vision_llm.httpx, 'AsyncClient', fake_client_factory(lambda url: response))
    with pytest.raises(vision_llm.VisionError) as result:
        run(vision_llm._call_gpt(make_settings(openai_api_key='test'), 'judge', 'inspect', [], {}, 5))
    assert result.value.unknown_cause == cause
    assert getattr(result.value, 'retry_after_seconds', None) is None


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


def _reset_openai_fallback(monkeypatch):
    from app import openai_key_fallback

    monkeypatch.setattr(openai_key_fallback, "_primary_unavailable", False)
    monkeypatch.setattr(openai_key_fallback, "_switch_alerted", False)


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


def test_gpt_quota_switches_to_fallback_and_later_starts_there(monkeypatch):
    _reset_openai_fallback(monkeypatch)
    seen = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, _url, **kwargs):
            authorization = kwargs["headers"]["Authorization"]
            seen.append(authorization)
            if authorization == "Bearer school-key":
                return FakeResp(429, {"error": {"code": "insufficient_quota"}})
            return _gpt_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(
        openai_api_key="school-key", openai_api_key_fallback="owner-key")
    args = (settings, "gpt-test", "prompt", _images(), {"type": "object"}, 30)

    assert run(vision_llm._call_gpt(*args)) == RAW
    assert run(vision_llm._call_gpt(*args)) == RAW
    assert seen == ["Bearer school-key", "Bearer owner-key", "Bearer owner-key"]


def test_gpt_rate_limit_does_not_switch(monkeypatch):
    _reset_openai_fallback(monkeypatch)
    seen = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, _url, **kwargs):
            seen.append(kwargs["headers"]["Authorization"])
            return FakeResp(429, {"error": {"code": "rate_limit_exceeded"}})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(
        openai_api_key="school-key", openai_api_key_fallback="owner-key")
    args = (settings, "gpt-test", "prompt", _images(), {"type": "object"}, 30)

    with pytest.raises(vision_llm.VisionError, match="rate_limit_exceeded"):
        run(vision_llm._call_gpt(*args))
    with pytest.raises(vision_llm.VisionError, match="rate_limit_exceeded"):
        run(vision_llm._call_gpt(*args))
    assert seen == ["Bearer school-key", "Bearer school-key"]


def test_gpt_without_fallback_raises_original_quota_error(monkeypatch):
    _reset_openai_fallback(monkeypatch)
    seen = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, _url, **kwargs):
            seen.append(kwargs["headers"]["Authorization"])
            return FakeResp(429, {"error": {"code": "insufficient_quota"}})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(openai_api_key="school-key", openai_api_key_fallback=None)

    with pytest.raises(vision_llm.VisionError, match="insufficient_quota"):
        run(vision_llm._call_gpt(
            settings, "gpt-test", "prompt", _images(), {"type": "object"}, 30))
    assert seen == ["Bearer school-key"]


def test_gpt_both_accounts_exhausted_raises_fallback_error_without_key_leak(monkeypatch):
    _reset_openai_fallback(monkeypatch)
    seen = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, _url, **kwargs):
            authorization = kwargs["headers"]["Authorization"]
            seen.append(authorization)
            code = "insufficient_quota" if authorization == "Bearer school-key" else "credit_balance_exhausted"
            return FakeResp(429, {"error": {"code": code}})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(
        openai_api_key="school-key", openai_api_key_fallback="owner-key")

    with pytest.raises(vision_llm.VisionError, match="credit_balance_exhausted") as raised:
        run(vision_llm._call_gpt(
            settings, "gpt-test", "prompt", _images(), {"type": "object"}, 30))
    assert seen == ["Bearer school-key", "Bearer owner-key"]
    assert "school-key" not in str(raised.value)
    assert "owner-key" not in str(raised.value)


def test_gpt_provider_gate_accepts_fallback_only_configuration(monkeypatch):
    _reset_openai_fallback(monkeypatch)
    seen = []

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, _url, **kwargs):
            seen.append(kwargs["headers"]["Authorization"])
            return _gpt_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(
        openai_api_key=None,
        openai_api_key_fallback="owner-key",
        gemini_api_key=None,
        analysis_model_order="gpt,gemini",
    )

    raw, provider = run(_run(settings, None))

    assert raw == RAW
    assert provider == "gpt"
    assert seen == ["Bearer owner-key"]


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


def test_property_ordering_is_opt_in_per_object_and_unmarked_schema_bytes_are_unchanged():
    import copy
    import json
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "state": {"type": "object", "properties": {"posture": {"type": "string"}}, "required": ["posture"]},
        "choice": {"type": ["string", "null"], "enum": ["none"], "description": "choice"},
        "items": {"type": "array", "items": {"type": "string"}},
    }, "required": ["state", "choice", "items"]}
    expected = {"type": "OBJECT", "properties": {
        "state": {"type": "OBJECT", "properties": {"posture": {"type": "STRING"}}, "required": ["posture"]},
        "choice": {"type": "STRING", "nullable": True, "enum": ["none"], "description": "choice"},
        "items": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["state", "choice", "items"]}
    original = copy.deepcopy(schema)
    assert json.dumps(vision_llm._to_gemini_schema(schema)) == json.dumps(expected)
    schema["x-propertyOrdering"] = True
    schema["properties"]["state"]["x-propertyOrdering"] = True
    converted = vision_llm._to_gemini_schema(schema)
    assert converted["propertyOrdering"] == ["state", "choice", "items"]
    assert converted["properties"]["state"]["propertyOrdering"] == ["posture"]
    assert "propertyOrdering" not in converted["properties"]["items"]
    schema["x-propertyOrdering"] = False
    assert "propertyOrdering" not in vision_llm._to_gemini_schema(schema)
    assert original["properties"] == {k: {key: value for key, value in node.items() if not key.startswith("x-")}
                                        for k, node in schema["properties"].items()}


def test_gpt_strips_schema_extensions_recursively_without_mutating_source():
    import copy
    import json
    from app.agents import similar_cut
    schema = {"type": "object", "x-propertyOrdering": True, "properties": {
        "plans": {"type": "array", "items": similar_cut._SCHEMA},
        "optional": {"anyOf": [{"type": "string", "x-note": "private"}, {"type": "null"}]},
    }, "required": ["plans", "optional"], "additionalProperties": False}
    original = copy.deepcopy(schema)
    body = vision_llm._gpt_body("gpt-test", "prompt", [], schema)
    assert '"x-' not in json.dumps(body)
    sent = body["response_format"]["json_schema"]
    assert sent["strict"] is True
    assert sent["schema"]["properties"]["plans"]["items"]["properties"]["sourceState"]["type"] == "object"
    assert schema == original


def test_similar_schema_emits_state_before_choice_for_gemini():
    from app.agents import similar_cut
    converted = vision_llm._gemini_body("prompt", [], similar_cut._SCHEMA, "low")["generationConfig"]["responseSchema"]
    assert converted["propertyOrdering"][:2] == ["sourceState", "choice"]


@pytest.fixture
def rejection_watch(monkeypatch):
    clock = [0.0]
    watch = vision_llm._RejectionWatch(clock=lambda: clock[0])
    monkeypatch.setattr(vision_llm, "_rejection_watch", watch)
    return clock, watch


@pytest.fixture
def rejected_gemini(monkeypatch, rejection_watch):
    response = FakeResp(400, {"error": {"status": "INVALID_ARGUMENT", "message": "private-response"}})
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(lambda url: response))

    def reject(schema=None, model="gemini-test"):
        with pytest.raises(vision_llm.VisionError) as result:
            run(vision_llm._call_gemini(
                make_settings(gemini_api_key="private-api-key"), model, "private-prompt", _images(),
                schema if schema is not None else {"type": "object"}, 5))
        assert str(result.value) == f"Gemini 400: {response.text[:300]}"
        assert result.value.status_code == 400

    return reject


def _rejection_warnings(caplog):
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and record.name == "wearless.vision_llm"
            and record.getMessage().startswith("vision_llm repeated rejection:")]


def test_gemini_400_third_rejection_warns_with_only_safe_fields(rejected_gemini, caplog):
    schema = {"type": "object", "properties": {"색": {"type": "string"}},
              "additionalProperties": False}
    for _ in range(3):
        rejected_gemini(schema)
    sent_schema = {"type": "OBJECT", "properties": {"색": {"type": "STRING"}}}
    encoded = json.dumps(sent_schema, sort_keys=True).encode("utf-8")
    assert _rejection_warnings(caplog) == [
        "vision_llm repeated rejection: provider=gemini model=gemini-test status=400 "
        f"error_status=INVALID_ARGUMENT schema_hash={hashlib.sha256(encoded).hexdigest()[:12]} "
        f"schema_bytes={len(encoded)} images=1 count=3 window_seconds=600 suppressed=0"]
    assert len(caplog.records) == 1
    for private in ("private-api-key", "private-prompt", "private-response", "색", "iVBORw=="):
        assert private not in caplog.text


def test_gemini_400_cooldown_retains_suppressed_count(rejection_watch, rejected_gemini, caplog):
    clock, _ = rejection_watch
    for _ in range(3):
        rejected_gemini()
    for at in (1, 2, 1799):
        clock[0] = at
        rejected_gemini()
    assert len(_rejection_warnings(caplog)) == 1
    clock[0] = 1800
    rejected_gemini()  # Cooldown expired, but only two rejections in the current window.
    assert len(_rejection_warnings(caplog)) == 1
    clock[0] = 1801
    rejected_gemini()
    assert len(_rejection_warnings(caplog)) == 2
    assert _rejection_warnings(caplog)[1].endswith("count=3 window_seconds=600 suppressed=3")
    clock[0] = 3601
    for _ in range(3):
        rejected_gemini()
    assert _rejection_warnings(caplog)[2].endswith("suppressed=0")


def test_gemini_400_schema_and_model_counts_are_separate(rejected_gemini, caplog):
    for _ in range(2):
        rejected_gemini({"type": "object"})
        rejected_gemini({"type": "string"})
        rejected_gemini({"type": "object"}, model="gemini-other")
    assert not _rejection_warnings(caplog)


def test_gemini_400_old_rejections_expire(rejection_watch, rejected_gemini, caplog):
    clock, _ = rejection_watch
    for at in (0, 601, 1202):
        clock[0] = at
        rejected_gemini()
    assert not _rejection_warnings(caplog)


@pytest.mark.parametrize("status", [429, 500])
def test_other_gemini_errors_do_not_call_rejection_watch(monkeypatch, rejection_watch, status):
    calls = []
    monkeypatch.setattr(rejection_watch[1], "record", lambda *args: calls.append(args))
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: FakeResp(status, text="original-error")))
    with pytest.raises(vision_llm.VisionError) as result:
        run(vision_llm._call_gemini(make_settings(gemini_api_key="test"), "gemini-test",
                                    "prompt", [], {}, 5))
    assert str(result.value) == f"Gemini {status}: original-error"
    assert result.value.status_code == status
    assert calls == []


def test_gemini_400_still_falls_back_without_opening_breaker(monkeypatch, rejection_watch, caplog):
    calls = []

    def handler(url):
        calls.append(url)
        return FakeResp(400, text="original-error") if "generativelanguage" in url else _gpt_ok()

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", fake_client_factory(handler))
    settings = make_settings(gemini_api_key="test", openai_api_key="test",
                             analysis_model_order="gemini,gpt", ai_breaker_failures=1)
    for _ in range(4):
        assert run(_run(settings, None)) == (RAW, "gpt")
    assert len(calls) == 8
    assert vision_llm._breaker._states == {}
    assert not vision_llm._breaker.should_skip("gemini", settings.model_text_gemini, has_next=True)
    failures = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith("vision_llm provider failed:")]
    assert len(failures) == 4
    assert all(message.startswith(f"vision_llm provider failed: gemini model={settings.model_text_gemini} "
                                 "VisionError(Gemini 400: original-error) after ")
               and message.endswith(" images=1") for message in failures)


def test_gemini_400_watch_failure_preserves_original_error(monkeypatch, rejection_watch, caplog):
    def broken(*args):
        raise RuntimeError("private-watch-exception")

    monkeypatch.setattr(rejection_watch[1], "record", broken)
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient",
                        fake_client_factory(lambda url: FakeResp(400, text="original-error")))
    with caplog.at_level(logging.DEBUG, logger="wearless.vision_llm"):
        with pytest.raises(vision_llm.VisionError) as result:
            run(vision_llm._call_gemini(make_settings(gemini_api_key="test"), "gemini-test",
                                        "prompt", [], {}, 5))
    assert str(result.value) == "Gemini 400: original-error"
    assert result.value.status_code == 400
    assert any(record.levelno == logging.DEBUG and record.getMessage() == "vision_llm rejection watch failed"
               for record in caplog.records)
    assert "private-watch-exception" not in caplog.text


@pytest.mark.parametrize("status,expected", [
    ("INVALID_ARGUMENT", "INVALID_ARGUMENT"), ("A" * 40, "A" * 40),
    ("A" * 41, "unknown"), ("invalid_argument", "unknown"),
    ("INVALID\nARGUMENT", "unknown"), ("PRIVATE PROMPT", "unknown"),
    ("오류A", "unknown"), (None, "unknown"), (400, "unknown"),
])
def test_rejection_error_status_is_bounded_and_safe(rejection_watch, caplog, status, expected):
    watch = rejection_watch[1]
    for _ in range(3):
        watch.record("gemini-test", {}, FakeResp(400, {"error": {"status": status}}))
    assert f"error_status={expected} schema_hash=none schema_bytes=0 images=0" in _rejection_warnings(caplog)[0]


@pytest.mark.parametrize("payload,bad_json", [
    ({}, False), ({"error": None}, False), (["not-an-object"], False), ({}, True),
])
def test_rejection_malformed_response_still_warns(rejection_watch, caplog, payload, bad_json):
    for _ in range(3):
        rejection_watch[1].record("gemini-test", {}, FakeResp(400, payload, bad_json=bad_json))
    assert "error_status=unknown" in _rejection_warnings(caplog)[0]


def test_rejection_watch_evicts_oldest_of_65_schema_keys(rejection_watch):
    watch = rejection_watch[1]
    keys = []
    for index in range(65):
        body = {"generationConfig": {"responseSchema": {"title": str(index)}}}
        watch.record("gemini-test", body, FakeResp(400))
        keys.append(next(reversed(watch._states)))
    assert len(watch._states) == 64
    assert keys[0] not in watch._states
    assert list(watch._states) == keys[1:]


def test_rejection_watch_sorts_schema_keys_and_bounds_concurrent_history(rejection_watch, caplog):
    watch = rejection_watch[1]

    def reject(index):
        schema = {"type": "object", "properties": {}} if index % 2 else {"properties": {}, "type": "object"}
        watch.record("gemini-test", {"generationConfig": {"responseSchema": schema}}, FakeResp(400))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reject, range(100)))
    assert len(watch._states) == 1
    state = next(iter(watch._states.values()))
    assert len(state.timestamps) == 50
    assert state.suppressed == 97
    assert len(_rejection_warnings(caplog)) == 1
