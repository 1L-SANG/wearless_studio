"""추천은 기존 특징 호출 한 번의 결과로 저장·응답하고 칩 목록에는 섞지 않는다."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.agents import feature_extractor as fx
from app.agents.vision_llm import VisionError
from app.workers import analyze_job
from conftest import fake_worker_app, make_settings, worker_job
from test_public_fast_entry import FILES, RUN_ID, SECRET
from test_feature_suggestions import candidate


SUGGESTIONS = [{"point": "소매 리본", "evidence": "소매 끝에 리본이 달려 있어요"},
               {"point": "밑단 프릴", "evidence": "밑단에 프릴이 보여요"}]


def agents(monkeypatch, provider="gpt", failed=False):
    calls = []
    async def analyst(*args, **kwargs):
        return {"product": {"clothingType": "top"}, "analysis": {"fit": "regular", "aiSuggestedPoints": ["기존 분석 칩"]},
                "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "gemini"
    async def consistency(*args, **kwargs):
        return None
    async def request(*args, **kwargs):
        calls.append(args)
        if failed:
            raise VisionError("fake unavailable")
        return {"candidates": [candidate("배색 카라"), candidate("가슴 포켓"),
            candidate("소매 리본", SUGGESTIONS[0]["evidence"]), candidate("밑단 프릴", SUGGESTIONS[1]["evidence"])],
            "selected": ["배색 카라", "가슴 포켓"], "detailCandidates": []}, provider
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    monkeypatch.setattr(fx, "analyze_with_fallback", request)
    return calls


@pytest.mark.parametrize("provider,failed,wanted", [("gpt", False, SUGGESTIONS), ("gemini", False, []), ("gpt", True, [])])
def test_full_analysis_preserves_recommendations_and_existing_chip_behavior(monkeypatch, provider, failed, wanted):
    calls = agents(monkeypatch, provider, failed)
    core = asyncio.run(analyze_job.analyze_image_bytes(make_settings(), [(b"photo", "image/png")], slots=["Front"]))
    assert core["analysis_payload"]["featureSuggestions"] == core["result_data"]["featureSuggestions"] == wanted
    assert core["result_data"]["aiSuggestedPoints"] == (["기존 분석 칩"] if failed else ["배색 카라", "가슴 포켓"])
    assert "sellingPoints" not in core["analysis_payload"]
    assert len(calls) == 1


def test_worker_stores_recommendations_in_analysis_and_the_done_result(monkeypatch):
    calls = agents(monkeypatch)
    monkeypatch.setattr(analyze_job.repo, "get_product", AsyncMock(return_value={"colors": [{"isBase": True,
        "images": [{"slot": "Front", "id": "photo"}]}]}))
    monkeypatch.setattr(analyze_job.repo, "get_asset_for_user", AsyncMock(return_value={"r2_key": "photo", "mime_type": "image/png"}))
    monkeypatch.setattr(analyze_job.repo, "get_analysis", AsyncMock(return_value={}))
    monkeypatch.setattr(analyze_job.repo, "get_analysis_for_update", AsyncMock(return_value={}))
    monkeypatch.setattr(analyze_job.outfit, "load_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(analyze_job, "_emit", AsyncMock())
    final = AsyncMock(return_value={"result": {}})
    monkeypatch.setattr(analyze_job.repo, "finalize_analyze_success", final)
    asyncio.run(analyze_job.run_analyze_job(fake_worker_app(make_settings()), worker_job()))
    assert final.await_count == 1 and len(calls) == 1
    assert final.call_args.kwargs["analysis_payload"]["featureSuggestions"] == SUGGESTIONS
    assert final.call_args.kwargs["result"]["data"]["featureSuggestions"] == SUGGESTIONS


@pytest.mark.parametrize("provider,wanted", [("gpt", SUGGESTIONS), ("gemini", [])])
def test_public_feature_response_places_recommendations_next_to_points(client, monkeypatch, provider, wanted):
    calls = agents(monkeypatch, provider)
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=SECRET)
    response = client.post("/v1/public/analyze/features", files=FILES, data={"runId": RUN_ID, "slots": "Front"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["points"] == {"status": "ready", "items": ["배색 카라", "가슴 포켓"]}
    assert data["featureSuggestions"] == wanted
    assert "detailRecommendations" in data and len(calls) == 1


def test_public_core_has_no_recommendations_before_the_feature_call(client, monkeypatch):
    calls = agents(monkeypatch)
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=SECRET)
    response = client.post("/v1/public/analyze", files=FILES, data={"parts": "core", "runId": RUN_ID, "slots": "Front"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["featureSuggestions"] == []
    assert response.json()["data"]["aiSuggestedPoints"] == ["기존 분석 칩"]
    assert calls == []
