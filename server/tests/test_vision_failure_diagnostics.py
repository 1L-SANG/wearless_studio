"""A Vision failure has to say why, without saying the key.

Between 2026-08-04 and 08-07 the landmark extractor failed nine times and not one of them
could be classified afterwards. The database held `기하 추출 실패: VisionError` and nothing
else, while the HTTP status, the response body and the failure category all existed at the
moment of failure. Three layers dropped them in turn:

    _call_gemini            raised VisionError("Gemini <status>: <body>")
    analyze_with_fallback   logged provider/category, re-raised a generic message
    _read_source_geometry   kept type(exc).__name__

The message was withheld on purpose — provider errors quote the request URL, and the key
rides in its query string. That was right. What was missing is that nothing took its place.
These tests pin both halves: the category survives, the secret does not.
"""
import asyncio
import types

import httpx
import pytest

from app.agents import vision_llm as vl
from app.agents.vision_llm import VisionError

SECRET = "AIzaSyDUMMYKEY_abcdefghijklmnop123456"


def settings(**over):
    base = {"openai_api_key": None, "gemini_api_key": SECRET,
            "analysis_model_order": "gemini,gpt", "model_text": "gpt-x",
            "model_text_gemini": "gemini-x", "analysis_timeout_seconds": 1.0,
            "analysis_thinking_level": "low"}
    base.update(over)
    return types.SimpleNamespace(**base)


def run(coro):
    return asyncio.run(coro)


def surface(err):
    """Drive the real fallback path with one provider that fails as given."""
    async def failing(*a, **kw):
        raise err

    original = vl._PROVIDERS["gemini"]
    vl._PROVIDERS["gemini"] = (failing, original[1], original[2])
    try:
        with pytest.raises(VisionError) as caught:
            run(vl.analyze_with_fallback(settings(), "p", [], {"type": "object"}))
        return caught.value
    finally:
        vl._PROVIDERS["gemini"] = original


# ---------- the status decides the category ----------

@pytest.mark.parametrize("status,expected", [
    (429, vl.CATEGORY_RATE_LIMIT),
    (500, vl.CATEGORY_PROVIDER_UNAVAILABLE),
    (503, vl.CATEGORY_PROVIDER_UNAVAILABLE),
    (400, vl.CATEGORY_PROVIDER_REJECTED),
    (404, vl.CATEGORY_PROVIDER_REJECTED),   # the stale-model-id failure of 2026-07
    (401, vl.CATEGORY_PROVIDER_REJECTED),
])
def test_provider_status_reaches_the_caller_as_a_category(status, expected):
    """Retryable, provider-side and our-fault used to collapse into one word."""
    err = VisionError(f"Gemini {status}: body", provider="gemini", status=status)
    surfaced = surface(err)
    assert surfaced.category == expected
    assert surfaced.status == status
    assert surfaced.provider == "gemini"


def test_timeout_is_distinguishable_from_a_provider_error():
    surfaced = surface(httpx.ReadTimeout("timed out"))
    assert surfaced.category == vl.CATEGORY_TIMEOUT


def test_transport_failure_is_distinguishable():
    surfaced = surface(httpx.ConnectError("connection reset"))
    assert surfaced.category == vl.CATEGORY_TRANSPORT


def test_malformed_and_empty_responses_keep_their_own_categories():
    assert vl._failure_category(
        VisionError("x", provider="gemini", category=vl.CATEGORY_MALFORMED)
    ) == vl.CATEGORY_MALFORMED
    with pytest.raises(VisionError) as e:
        vl._parse_json("", "Gemini")
    assert e.value.category == vl.CATEGORY_EMPTY
    with pytest.raises(VisionError) as e:
        vl._parse_json("not json", "Gemini")
    assert e.value.category == vl.CATEGORY_MALFORMED


# ---------- and the secret still does not travel ----------

def test_the_surfaced_error_carries_no_body_and_no_key():
    """Provider bodies quote the request URL; the key is in its query string."""
    body = f"upstream said: GET https://x/v1beta/models/m:generateContent?key={SECRET} failed"
    surfaced = surface(VisionError(f"Gemini 500: {body}", provider="gemini", status=500))
    text = f"{surfaced} {surfaced.__dict__} {vl.failure_summary(surfaced)}"
    assert SECRET not in text
    assert "generateContent" not in text
    assert "upstream said" not in text
    assert str(surfaced) == "상품 분석에 실패했어요. 잠시 후 다시 시도해 주세요."


def test_the_original_exception_is_not_chained_into_a_traceback():
    """`raise ... from original` would put the body back into any printed traceback."""
    surfaced = surface(VisionError("Gemini 500: secret body", provider="gemini", status=500))
    assert surfaced.__cause__ is None


def test_failure_summary_is_a_safe_single_line():
    err = VisionError("Gemini 503: body", provider="gemini", status=503)
    assert vl.failure_summary(err) == "VisionError gemini 503 provider_unavailable"
    assert vl.failure_summary(httpx.ReadTimeout("t")) == "ReadTimeout timeout"


# ---------- the worker keeps it ----------

def test_the_worker_records_the_summary_not_just_the_class_name():
    """`기하 추출 실패: VisionError` was every one of the nine failures."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app/workers/mannequin_job.py"
    tree = ast.parse(src.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "failure_summary"]
    assert len(calls) == 2, "both landmark catch sites must preserve the summary"
    assert "type(exc).__name__" not in src.read_text().split("기하 추출 실패")[1][:120]


def test_no_key_case_is_its_own_category():
    s = settings(gemini_api_key=None, openai_api_key=None)
    with pytest.raises(VisionError) as e:
        run(vl.analyze_with_fallback(s, "p", [], {"type": "object"}))
    assert e.value.category == vl.CATEGORY_NO_KEY


def test_existing_single_argument_raises_still_work():
    """Every other agent raises VisionError('...') positionally; that must not break."""
    err = VisionError("axis_qc: axisPass 배열 아님")
    assert err.provider is None and err.status is None and err.category is None
    # status 가 없어도 vision 계층 실패라는 기존 분류는 그대로 유지된다
    assert vl._failure_category(err) == vl.CATEGORY_PROVIDER_ERROR
