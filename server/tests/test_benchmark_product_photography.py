"""Offline spending and input-integrity checks; never call a provider."""

import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

from PIL import Image
import pytest


@pytest.fixture
def benchmark():
    return importlib.import_module("scripts.benchmark_product_photography")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (24, 32), "white").save(path)
    return path


def test_dry_plan_has_twelve_sealed_requests_and_never_imports_provider(benchmark, source, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.agents.gemini_image", None)
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    assert len(plan["requests"]) == 12
    assert {row["presentation"] for row in plan["requests"]} == {"ghost", "flat", "natural_light"}
    assert {row["model"] for row in plan["requests"]} == set(benchmark.MODELS)
    assert all(row["prompt"] and row["prompt_sha256"] for row in plan["requests"])
    assert plan["estimated_reserve_usd"] == 3
    assert plan["http_calls"] == 0
    assert plan["automatic_retries"] == 0
    assert plan["judge_calls"] == 0


@pytest.mark.parametrize("kwargs", [{}, {"max_calls": 11, "max_usd": 3},
    {"max_calls": 12, "max_usd": 2.99}, {"max_calls": 12, "max_usd": float("nan")}])
def test_missing_or_insufficient_explicit_budget_is_rejected(benchmark, source, kwargs):
    with pytest.raises(ValueError):
        benchmark.build_plan(source, **kwargs)


def test_plan_and_prompt_files_cannot_be_overwritten(benchmark, source, tmp_path):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    folder = tmp_path / "run"
    path = benchmark.save_plan(plan, folder)
    assert json.loads(path.read_text()) == plan
    with pytest.raises(FileExistsError):
        benchmark.save_plan(plan, folder)


def test_changed_original_is_rejected_before_constructing_provider(benchmark, source, tmp_path, monkeypatch):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    benchmark.save_plan(plan, tmp_path / "run")
    source.write_bytes(b"changed")
    monkeypatch.setattr(benchmark, "make_client", lambda: pytest.fail("provider was constructed"))
    with pytest.raises(ValueError, match="source"):
        asyncio.run(benchmark.execute_plan(plan, tmp_path / "run"))


def test_actual_usage_calculates_nb21_including_thinking(benchmark):
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 1120,
        "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}],
        "thoughtsTokenCount": 100}
    assert benchmark.cost_usd("gemini-nano-banana-2.1", "1K", usage) == pytest.approx(.03585)
    assert benchmark.cost_usd("gemini-nano-banana-2.1", "1K", {}) is None


def test_unknown_usage_stops_after_one_result_and_run_cannot_be_repeated(benchmark, source, tmp_path):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    folder = tmp_path / "run"
    benchmark.save_plan(plan, folder)
    sent = []

    async def generate(row, image):
        sent.append(row["id"])
        return SimpleNamespace(image=source.read_bytes(), mime="image/png", usage={})

    result = asyncio.run(benchmark.execute_plan(plan, folder, generate=generate))
    assert sent == [plan["requests"][0]["id"]]
    assert result["status"] == "stopped_unknown_cost"
    assert result["provider_calls"] == 1
    assert len(list(folder.glob("outputs/*"))) == 1
    with pytest.raises(FileExistsError):
        asyncio.run(benchmark.execute_plan(plan, folder, generate=generate))
    assert len(sent) == 1


def test_over_reserve_usage_stops_without_second_call(benchmark, source, tmp_path):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    folder = tmp_path / "run"
    benchmark.save_plan(plan, folder)
    sent = []

    async def generate(row, image):
        sent.append(row["id"])
        return SimpleNamespace(image=source.read_bytes(), mime="image/png", usage={
            "promptTokenCount": 1_000_000, "candidatesTokenCount": 1120,
            "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]})

    result = asyncio.run(benchmark.execute_plan(plan, folder, generate=generate))
    assert len(sent) == 1
    assert result["status"] == "stopped_budget_exceeded"


def test_first_provider_failure_is_recorded_without_retry(benchmark, source, tmp_path):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    folder = tmp_path / "run"
    benchmark.save_plan(plan, folder)
    sent = []

    async def generate(row, image):
        sent.append(row["id"])
        raise TimeoutError("do not retry")

    result = asyncio.run(benchmark.execute_plan(plan, folder, generate=generate))
    assert len(sent) == 1
    assert result["status"] == "stopped_provider_error"
    assert result["results"][0]["error_type"] == "TimeoutError"
    assert result["known_usage_usd"] == 0
    assert result["unknown_billable_calls"] == 1


def test_transport_blocks_any_second_send_before_http(benchmark):
    guard = benchmark.OneSend()
    asyncio.run(guard.before_request(object()))
    with pytest.raises(RuntimeError, match="second"):
        asyncio.run(guard.before_request(object()))


def test_modified_prompt_or_matrix_is_rejected(benchmark, source):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    plan["requests"][0]["model"] = "unapproved-model"
    with pytest.raises(ValueError, match="request"):
        benchmark.validate_plan(plan)


def category_sources(source):
    return [{"id": category, "clothing_type": category,
        "images": [{"role": "Front", "path": str(source)}]}
        for category in ("top", "bottom", "outer", "dress")]


def test_four_category_plan_has_gpt_twice_gemini_and_separate_tuning(benchmark, source):
    plan = benchmark.build_plan(sources=category_sources(source), max_calls=72, max_usd=15)
    assert len(plan["requests"]) == 72
    assert plan["provider_call_counts"] == {"gemini": 24, "gpt": 48}
    assert plan["estimated_reserve_usd"] == pytest.approx(8.76)
    assert sum(row["prompt_profile"] == "common" for row in plan["requests"]) == 48
    assert sum(row["prompt_profile"] == "model" for row in plan["requests"]) == 24
    for category in ("top", "bottom", "outer", "dress"):
        for mode in benchmark.PRESENTATIONS:
            common = [row["prompt"] for row in plan["requests"]
                if row["clothing_type"] == category and row["presentation"] == mode
                and row["prompt_profile"] == "common"]
            assert len(common) == 4 and len(set(common)) == 1


def test_four_independent_category_caps_sum_to_approved_fifteen_dollars(benchmark, source):
    plans = [benchmark.build_plan(sources=[item], max_calls=18, max_usd=3.75)
        for item in category_sources(source)]
    assert sum(plan["max_usd"] for plan in plans) == 15
    assert sum(plan["provider_call_counts"]["gpt"] for plan in plans) == 48
    assert sum(plan["provider_call_counts"]["gemini"] for plan in plans) == 24
    assert all(plan["estimated_reserve_usd"] == 2.19 for plan in plans)
    assert all(benchmark.validate_plan(plan) is None for plan in plans)


def test_category_plan_rejects_duplicate_categories_or_insufficient_budget(benchmark, source):
    cases = category_sources(source)
    with pytest.raises(ValueError, match="unique"):
        benchmark.build_plan(sources=[cases[0], {**cases[0], "id": "duplicate"}], max_calls=36, max_usd=8)
    with pytest.raises(ValueError, match="Explicit"):
        benchmark.build_plan(sources=cases, max_calls=72, max_usd=8.75)


def test_shared_provider_client_is_called_once_and_blocks_automatic_http_retry(benchmark, source, monkeypatch):
    import httpx
    from app.agents import gemini_image

    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    row = plan["requests"][0]
    actual_posts = []

    async def rejected(request):
        actual_posts.append(str(request.url))
        return httpx.Response(429, json={"error": {"code": "rate_limit"}})

    original = httpx.AsyncClient

    def intercepted(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(rejected), **kwargs)

    async def no_delay(seconds):
        pass

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", intercepted)
    monkeypatch.setattr(gemini_image.asyncio, "sleep", no_delay)
    settings = SimpleNamespace(gemini_api_key="offline-test", vertex_project=None,
        vertex_location="global", openai_api_key="offline-test", openai_api_key_fallback=None)
    client = gemini_image.GeminiImageClient(settings)
    with pytest.raises(RuntimeError, match="second"):
        asyncio.run(benchmark.provider_generate(client, row, plan["input_manifest"]))
    assert len(actual_posts) == 1
    assert actual_posts[0].endswith("gemini-nano-banana-2.1:generateContent")


@pytest.mark.parametrize("model", ["gemini-3-pro-image", "gemini-nano-banana-2.1"])
@pytest.mark.parametrize("usage", [
    {"promptTokenCount": 100},
    {"candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]},
    {"promptTokenCount": True, "candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]},
    {"promptTokenCount": 100, "candidatesTokenCount": 1120, "candidatesTokensDetails": []},
    {"promptTokenCount": 100, "candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": -1}]},
    {"promptTokenCount": 100, "candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1121}]},
    {"promptTokenCount": 100, "candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}], "thoughtsTokenCount": -1},
    {"promptTokenCount": 100, "candidatesTokenCount": 1120, "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}], "totalTokenCount": 9999},
])
def test_partial_or_malformed_gemini_usage_is_unknown(benchmark, model, usage):
    assert benchmark.cost_usd(model, "1K", usage) is None


def test_valid_pro_usage_includes_residual_text_and_thinking(benchmark):
    usage = {"promptTokenCount": 951, "candidatesTokenCount": 1251,
        "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}],
        "thoughtsTokenCount": 155, "totalTokenCount": 2357}
    assert benchmark.cost_usd("gemini-3-pro-image", "1K", usage) == pytest.approx(.139734)


@pytest.mark.parametrize("model", ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"])
@pytest.mark.parametrize("usage", [
    {}, {"input_tokens": 100, "output_tokens": 343},
    {"input_tokens": 100, "input_tokens_details": {"text_tokens": 100, "image_tokens": 0},
        "output_tokens": 343, "output_tokens_details": {"image_tokens": 342}},
    {"input_tokens": 100, "input_tokens_details": {"text_tokens": 100, "image_tokens": 0},
        "output_tokens": 343, "output_tokens_details": {"image_tokens": 343}, "total_tokens": 444},
    {"input_tokens": True, "input_tokens_details": {"text_tokens": 1, "image_tokens": 0},
        "output_tokens": 343, "output_tokens_details": {"image_tokens": 343}},
])
def test_malformed_openai_usage_is_unknown(benchmark, model, usage):
    assert benchmark.cost_usd(model, "1K", usage) is None


def test_intermediate_result_stays_running_until_batch_finishes(benchmark, source, tmp_path):
    plan = benchmark.build_plan(source, max_calls=12, max_usd=3)
    folder = tmp_path / "run"
    benchmark.save_plan(plan, folder)
    sends = []

    async def generate(row, images):
        sends.append(row["id"])
        if len(sends) == 2:
            intermediate = json.loads((folder / "results.json").read_text())
            assert intermediate["status"] == "running"
            assert intermediate["provider_calls"] == 1
            raise TimeoutError()
        return SimpleNamespace(image=source.read_bytes(), mime="image/png", usage={
            "promptTokenCount": 100, "candidatesTokenCount": 1120,
            "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]})

    result = asyncio.run(benchmark.execute_plan(plan, folder, generate=generate))
    assert result["status"] == "stopped_provider_error"
    assert result["results"][-1]["error_type"] == "TimeoutError"


def test_second_batch_reserves_and_caps_leave_room_for_recorded_first_batch(benchmark, source):
    plans = [benchmark.build_plan(sources=[item], max_calls=18, max_usd=2.75)
        for item in category_sources(source)]
    assert all(plan["estimated_reserve_usd"] == pytest.approx(2.19) for plan in plans)
    assert 3.534941 + sum(plan["max_usd"] for plan in plans) < 15
    assert sum(plan["provider_call_counts"]["gpt"] for plan in plans) == 48
    assert sum(plan["provider_call_counts"]["gemini"] for plan in plans) == 24
    for plan in plans:
        assert {row["model"]: row["per_call_reserve_usd"] for row in plan["requests"]} == {
            "gemini-nano-banana-2.1": .08, "gemini-3-pro-image": .23,
            "gpt-image-2.5-flare": .06, "gpt-image-2.5-sunburst": .15}
