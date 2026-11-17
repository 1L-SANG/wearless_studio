"""핏 읽기 부가 호출을 실제 공통 분석 함수와 공개 경로에 연결한다. 외부 응답은 모두 가짜다."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from app.agents import fit_reader
from app.workers import analyze_job
from conftest import fake_worker_app, make_settings, worker_job
from test_fit_reader import reading
from test_public_analyze import PNG


def distributed():
    return {"product": {"clothingType": "top"}, "analysis": {"fit": "slim", "subCategory": "knit", "targetGenders": ["men"]},
            "intermediate": {"styleTags": ["basic"], "swatchSuggestions": []}}


def agents(monkeypatch, *, fit="success"):
    seen = {"fit": 0}
    async def main(settings, product, images, *, slots=None):
        return distributed(), "fake-main"
    async def consistency(*args, **kwargs):
        return None
    async def features(*args, **kwargs):
        return ["골지 짜임"], "gemini"
    async def read_request(settings, prompt, images, schema, **kwargs):
        seen["fit"] += 1
        seen["images"], seen["options"] = images, kwargs
        if fit == "failure":
            raise RuntimeError("fake unavailable")
        if fit == "timeout":
            await asyncio.sleep(1)
        return reading("INVALID") if fit == "invalid" else reading(), "gemini"
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", main)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", features)
    monkeypatch.setattr(fit_reader, "analyze_with_fallback", read_request)
    return seen


@pytest.mark.parametrize("mode,outcome,wanted,calls", [("on", "success", True, 1), ("on", "failure", False, 1),
    ("on", "timeout", False, 1), ("on", "invalid", False, 1), ("off", "success", False, 0)])
def test_analysis_completes_and_only_attaches_a_valid_successful_fit_read(monkeypatch, mode, outcome, wanted, calls):
    seen = agents(monkeypatch, fit=outcome)
    core = asyncio.run(analyze_job.analyze_image_bytes(make_settings(fit_reader_mode=mode, analysis_aux_timeout_seconds=0.01),
        [(PNG, "image/png")], product={"name": "셔츠"}, slots=["Front"]))
    assert core["result_data"]["fit"] == "slim"
    assert core["result_data"]["aiSuggestedPoints"] == ["골지 짜임"]
    assert ("fitRead" in core["result_data"]) is wanted
    assert ("fitRead" in core["analysis_payload"]) is wanted
    assert "fitProfile" not in core["analysis_payload"]
    assert seen["fit"] == calls
    if wanted:
        assert core["result_data"]["fitRead"] == core["analysis_payload"]["fitRead"] == reading()


def test_all_four_readers_start_together_and_share_the_same_reduced_images(monkeypatch):
    async def run():
        started, images_by_agent = set(), {}
        release = asyncio.Event()
        async def arrive(name, images):
            started.add(name)
            images_by_agent[name] = images
            if len(started) == 4:
                release.set()
            await release.wait()
        async def main(settings, product, images, **kwargs):
            await arrive("main", images)
            return distributed(), "fake-main"
        async def consistency(settings, images, slots):
            await arrive("consistency", images)
            return None
        async def features(settings, product, images, **kwargs):
            await arrive("features", images)
            return [], "gemini"
        async def fit(settings, prompt, images, schema, **kwargs):
            await arrive("fit", images)
            return reading(), "gemini"
        monkeypatch.setattr(analyze_job.product_analyst, "analyze", main)
        monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
        monkeypatch.setattr(analyze_job.feature_extractor, "extract", features)
        monkeypatch.setattr(fit_reader, "analyze_with_fallback", fit)
        core = await asyncio.wait_for(analyze_job.analyze_image_bytes(make_settings(fit_reader_mode="on", analysis_aux_timeout_seconds=0.5),
            [(PNG, "image/png")], slots=["Front"]), timeout=1)
        assert started == {"main", "consistency", "features", "fit"}
        assert all(images is images_by_agent["main"] for images in images_by_agent.values())
        assert core["result_data"]["fitRead"] == reading()
    asyncio.run(run())


def test_disabled_reader_does_not_invoke_the_agent(monkeypatch):
    agents(monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError("disabled fit reader invoked")
    monkeypatch.setattr(fit_reader, "read", forbidden)
    core = asyncio.run(analyze_job.analyze_core_image_bytes(make_settings(fit_reader_mode="off"), [(PNG, "image/png")]))
    assert "fitRead" not in core["result_data"]


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
def test_worker_persists_success_and_does_not_carry_a_stale_read_after_reanalysis(monkeypatch, outcome):
    from unittest.mock import AsyncMock
    agents(monkeypatch, fit=outcome)
    old = {"fitRead": reading("hanger"), "fitProfile": {"axes": {"fit": "over"}}}
    monkeypatch.setattr(analyze_job.repo, "get_product", AsyncMock(return_value={"colors": [{"isBase": True,
        "images": [{"slot": "Front", "id": "photo"}]}]}))
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", AsyncMock(return_value={"r2_key": "photo", "mime_type": "image/png"}))
    monkeypatch.setattr(analyze_job.repo, "get_analysis", AsyncMock(return_value=deepcopy(old)))
    monkeypatch.setattr(analyze_job.repo, "get_analysis_for_update", AsyncMock(return_value=deepcopy(old)))
    monkeypatch.setattr(analyze_job.outfit, "load_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(analyze_job, "_emit", AsyncMock())
    final = AsyncMock(return_value={"result": {}})
    failed = AsyncMock()
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", final)
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_failure", failed)
    asyncio.run(analyze_job.run_analyze_job(fake_worker_app(make_settings(fit_reader_mode="on", analysis_aux_timeout_seconds=0.01)), worker_job()))
    assert final.await_count == 1 and failed.await_count == 0
    payload = final.call_args.kwargs["analysis_payload"]
    data = final.call_args.kwargs["result"]["data"]
    assert "fitProfile" not in payload
    if outcome == "success":
        assert payload["fitRead"] == data["fitRead"] == reading()
        assert final.call_args.kwargs["metadata"]["fitReaderProvider"] == "gemini"
    else:
        assert "fitRead" not in payload and "fitRead" not in data


@pytest.mark.parametrize("parts", [None, "core"])
def test_public_analysis_uses_the_same_fit_reading(client, monkeypatch, parts):
    seen = agents(monkeypatch)
    client.app.state.settings = replace(client.app.state.settings, fit_reader_mode="on",
                                        r2_secret_access_key="fit-reader-test-secret-at-least-32-bytes")
    data = {"slots": "Front"}
    if parts:
        data.update(parts=parts, runId="11111111-1111-4111-8111-111111111111")
    response = client.post("/v1/public/analyze", files=[("images", ("front.png", PNG, "image/png"))], data=data)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["fitRead"] == reading()
    assert seen["fit"] == 1


def test_public_feature_refresh_does_not_add_a_fit_reading_call(client, monkeypatch):
    seen = agents(monkeypatch)
    client.app.state.settings = replace(client.app.state.settings, fit_reader_mode="on",
                                        r2_secret_access_key="fit-reader-test-secret-at-least-32-bytes")
    response = client.post("/v1/public/analyze/features", files=[("images", ("front.png", PNG, "image/png"))],
        data={"slots": "Front", "runId": "11111111-1111-4111-8111-111111111111"})
    assert response.status_code == 200, response.text
    assert seen["fit"] == 0
