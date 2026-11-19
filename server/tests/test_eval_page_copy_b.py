"""Single-mode evaluation and one-time Gemini reserve credentials, without traffic."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from app.agents import vision_llm
from page_copy_test_support import page_copy_prompt_dir
from scripts import eval_page_copy as evaluate
from test_eval_page_copy import evaluation_args, fake_settings, good_response, read_rows, write_json

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


@pytest.mark.parametrize("obsolete", [
    {"mode": "draft"}, {"mode": "draft+polish"},
    {"draft": {"provider": "gpt", "model": "old"}},
    {"polish": {"provider": "claude", "model": "old"}}, {"share_draft": "old"},
])
def test_removed_arm_options_are_rejected_before_dispatch(obsolete):
    with pytest.raises(ValueError, match="single|removed"):
        evaluate.parse_arms(json.dumps([{"provider": "gemini", "model": "test-model", **obsolete}]))


@pytest.mark.parametrize("status,message,want", [
    (402, "Payment required", "http_402"),
    (429, "quota exhausted", "quota_exhausted"),
    (429, "Daily quota exceeded: GenerateRequestsPerDayPerProjectPerModel", "quota_exhausted"),
    (403, "Billing account disabled", "billing"),
    (429, "Insufficient quota, check billing account", "quota_exhausted"),
    (429, "RESOURCE_EXHAUSTED: rate limit exceeded", None),
    (429, "Quota exceeded for requests per minute. Check plan and billing details. Retry in 10s.", None),
    (429, "Quota exceeded for GenerateRequestsPerMinutePerProjectPerModel", None),
    (429, "Too many requests", None),
    (500, "Internal error", None),
    (400, "Invalid billing metadata field", None),
    (429, "Quota documentation at https://example.test/quotas", None),
])
def test_exhaustion_classifier_does_not_rotate_on_transient_limits(status, message, want):
    error = vision_llm.VisionError(message, status_code=status)
    assert evaluate.gemini_exhaustion_reason(error) == want


def gemini_args(tmp_path, *, max_calls=10, cases=2):
    args = evaluation_args(tmp_path, arm={"id": "argon", "provider": "gemini", "model": "test-model"},
        extra=["--send", "--max-calls", str(max_calls), "--max-usd", "10", "--concurrency", "1"])
    first = evaluate.read_rows(args.cases)[0]
    write_json(Path(args.cases), [{**first, "caseId": f"case-{i}"} for i in range(cases)])
    return args


def test_exhaustion_retries_same_request_then_retains_next_and_sanitizes_audit(tmp_path):
    args = gemini_args(tmp_path)
    sent = []

    async def fake(req, timeout):
        sent.append(copy.deepcopy(req))
        if len(sent) == 1:
            raise vision_llm.VisionError("quota exhausted primary-secret next-secret", status_code=429)
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    settings = fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")
    summary = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=settings))
    assert [r["key_source"] for r in sent] == ["primary", "next", "next"]
    assert sent[0]["prompt"] == sent[1]["prompt"] and sent[0]["slots"] == sent[1]["slots"]
    assert summary["calls"] == 3 and summary["stopped"] is None
    logs = read_rows(Path(args.output) / "calls.jsonl")
    assert [r["key_source"] for r in logs] == ["primary", "next", "next"]
    assert len({r["reservation_id"] for r in logs}) == 3
    manifest = evaluate.read_json(Path(args.output) / "run.json")
    assert manifest["gemini_credentials"]["source"] == "next"
    assert len(manifest["gemini_credentials"]["switches"]) == 1
    assert logs[0]["credential_switch"]["to"] == "next"
    for path in Path(args.output).glob("*.json*"):
        assert "primary-secret" not in path.read_text() and "next-secret" not in path.read_text()
    assert settings.gemini_api_key == "primary-secret"


@pytest.mark.parametrize("message,status,sources", [
    ("rate limit exceeded", 429, ["primary", "primary"]),
    ("quota exhausted", 429, ["primary", "next", "next"]),
])
def test_transient_errors_stay_primary_and_next_failure_does_not_rotate_again(tmp_path, message, status, sources):
    args = gemini_args(tmp_path)
    sent = []

    async def fail(req, timeout):
        sent.append(req["key_source"])
        raise vision_llm.VisionError(message, status_code=status)

    result = asyncio.run(evaluate.run_evaluation(args, caller=fail,
        settings=fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")))
    assert sent == sources and result["calls"] == len(sources)
    state = evaluate.read_json(Path(args.output) / "run.json")["gemini_credentials"]
    assert len(state["switches"]) == (1 if "next" in sources else 0)


def test_reserve_retry_cannot_bypass_call_budget_and_resume_keeps_next(tmp_path):
    args = gemini_args(tmp_path, max_calls=1, cases=1)
    seen = []

    async def fail(req, timeout):
        seen.append(req["key_source"])
        raise vision_llm.VisionError("Payment required", status_code=402)

    settings = fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")
    first = asyncio.run(evaluate.run_evaluation(args, caller=fail, settings=settings))
    assert seen == ["primary"] and first["calls"] == 1 and "max-calls" in first["stopped"]
    args.resume, args.max_calls = args.output, 2

    async def success(req, timeout):
        seen.append(req["key_source"])
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    second = asyncio.run(evaluate.run_evaluation(args, caller=success, settings=settings))
    assert seen == ["primary", "next"] and second["calls"] == 2 and second["stopped"] is None
    assert len(evaluate.read_json(Path(args.output) / "run.json")["gemini_credentials"]["switches"]) == 1


def test_env_file_reserve_reaches_fake_gemini_client_without_mutating_settings(tmp_path, monkeypatch):
    args = gemini_args(tmp_path, cases=1)
    env_file = tmp_path / "provider.env"
    env_file.write_text('GEMINI_API_KEY_NEXT="env-next-secret"\nDATABASE_URL=ignored\n')
    args.env_file = str(env_file)
    monkeypatch.delenv("GEMINI_API_KEY_NEXT", raising=False)
    settings = fake_settings(gemini_api_key="primary-secret")
    selected = []

    import httpx
    original_client = httpx.AsyncClient

    def fake_client(request):
        selected.append(request.headers["x-goog-api-key"])
        if len(selected) == 1:
            return httpx.Response(429, json={"error": {"message": "quota exhausted"}})
        response = {"sections": [{"key": "S1", "texts": [
            {"role": "title", "text": "포켓 셔츠"}, {"role": "body", "text": "앞쪽에 포켓을 달았어요"}]}],
            "partDescriptionCopy": [], "featureCopy": []}
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps(response)}]}}],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 30}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(fake_client), **kwargs))
    result = asyncio.run(evaluate.run_evaluation(args, settings=settings))
    assert selected == ["primary-secret", "env-next-secret"]
    assert result["calls"] == 2 and settings.gemini_api_key == "primary-secret"
    for name in ("run.json", "calls.jsonl"):
        assert "env-next-secret" not in (Path(args.output) / name).read_text()


def test_concurrent_primary_failures_switch_once_and_retry_each_inflight_call(tmp_path):
    args = gemini_args(tmp_path, cases=3)
    args.concurrency = 2
    seen = []

    async def fake(req, timeout):
        seen.append((req["key_source"], req["reservation_id"]))
        if req["key_source"] == "primary":
            await asyncio.sleep(0.01)
            raise vision_llm.VisionError("daily quota exhausted", status_code=429)
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake,
        settings=fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")))
    assert [source for source, _ in seen] == ["primary", "primary", "next", "next", "next"]
    assert len({identifier for _, identifier in seen}) == result["calls"] == 5
    calls = read_rows(Path(args.output) / "calls.jsonl")
    assert sum("credential_switch" in row for row in calls) == 1
    assert len(evaluate.read_json(Path(args.output) / "run.json")["gemini_credentials"]["switches"]) == 1
    assert result["unresolved_reservations"] == 0


def test_exhaustion_without_reserve_does_not_retry(tmp_path, monkeypatch):
    args = gemini_args(tmp_path, cases=1)
    args.env_file = str(tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY_NEXT", raising=False)
    seen = []

    async def fake(req, timeout):
        seen.append(req["key_source"])
        raise vision_llm.VisionError("Payment required", status_code=402)

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake,
        settings=fake_settings(gemini_api_key="primary-secret")))
    assert seen == ["primary"] and result["calls"] == 1
    assert evaluate.read_json(Path(args.output) / "run.json")["gemini_credentials"]["source"] == "primary"


def test_retry_uses_only_remaining_page_time(tmp_path):
    args = gemini_args(tmp_path, cases=1)
    args.timeout = 0.1
    timeouts = []

    async def fake(req, timeout):
        timeouts.append(timeout)
        if req["key_source"] == "primary":
            await asyncio.sleep(0.02)
            raise vision_llm.VisionError("quota exhausted", status_code=429)
        await asyncio.sleep(0.2)
        raise AssertionError("request should have been cancelled by the original page deadline")

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake,
        settings=fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")))
    assert len(timeouts) == result["calls"] == 2
    assert timeouts[1] < timeouts[0] - 0.015
    logs = read_rows(Path(args.output) / "calls.jsonl")
    assert [row["status"] for row in logs] == ["error", "error"]
    assert logs[1]["error"] == "TimeoutError"
    assert sum(row["seconds"] for row in logs) < 0.16


def test_dry_run_never_reads_provider_credentials(tmp_path, monkeypatch):
    args = gemini_args(tmp_path, cases=1)
    args.send = False

    def forbidden(*args, **kwargs):
        raise AssertionError("dry run read credential settings")

    monkeypatch.setattr(evaluate, "_provider_keys", forbidden)
    monkeypatch.setattr(evaluate, "_settings", forbidden)
    result = asyncio.run(evaluate.run_evaluation(args))
    assert result["calls"] == 0 and result["planned_calls"] == 1


@pytest.mark.parametrize("message", [
    "Insufficient quota for requests per minute. Check plan and billing details.",
    "You exceeded your current quota, check plan and billing details. See https://ai.google.dev/gemini-api/docs/rate-limits.",
])
def test_generic_quota_and_billing_guidance_is_not_an_exhaustion_signal(message):
    assert evaluate.gemini_exhaustion_reason(vision_llm.VisionError(message, status_code=429)) is None


def test_explicit_daily_exhaustion_is_not_hidden_by_a_rate_limit_documentation_link():
    error = vision_llm.VisionError("Daily quota exhausted. See https://ai.google.dev/rate-limits", status_code=429)
    assert evaluate.gemini_exhaustion_reason(error) == "quota_exhausted"


@pytest.mark.parametrize("metric,expected_sources", [
    ("GenerateRequestsPerDayPerProjectPerModel", ["primary", "next"]),
    ("GenerateRequestsPerMonthPerProjectPerModel", ["primary", "next"]),
    ("GenerateRequestsPerMinutePerProjectPerModel", ["primary"]),
    ("UnspecifiedQuota", ["primary"]),
])
def test_full_gemini_error_body_distinguishes_quota_metric_after_long_boilerplate(tmp_path, monkeypatch, metric, expected_sources):
    import httpx

    args = gemini_args(tmp_path, cases=1)
    settings = fake_settings(gemini_api_key="primary-secret", gemini_api_key_next="next-secret")
    sources = []
    original_client = httpx.AsyncClient

    def handle(request):
        source = "next" if request.headers["x-goog-api-key"] == "next-secret" else "primary"
        sources.append(source)
        if source == "primary":
            return httpx.Response(429, json={"error": {"code": 429,
                "message": "You exceeded your current quota. Check plan and billing details. " + "Guidance. " * 40 + "https://ai.google.dev/gemini-api/docs/rate-limits",
                "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                             "violations": [{"quotaId": metric,
                                 "quotaMetric": "generativelanguage.googleapis.com/generate_content_requests"}]}]}})
        response = {"sections": [{"key": "S1", "texts": [
            {"role": "title", "text": "포켓 셔츠"}, {"role": "body", "text": "앞쪽에 포켓을 달았어요"}]}],
            "partDescriptionCopy": [], "featureCopy": []}
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps(response)}]}}],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 30}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(evaluate.run_evaluation(args, settings=settings))
    assert sources == expected_sources and result["calls"] == len(expected_sources)
    assert settings.gemini_api_key == "primary-secret"
