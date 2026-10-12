import asyncio
from copy import deepcopy

from app.agents import detail_recommendations as dr, feature_extractor
from app.workers import analyze_job
from conftest import make_settings
from test_public_fast_entry import DISTRIBUTED, PNG
from test_detail_recommendations import candidate


def test_core_only_limits_consistency_and_never_calls_ag08(monkeypatch):
    cancelled = []
    async def analyst(*args, **kwargs):
        return deepcopy(DISTRIBUTED), "gemini"
    async def forbidden(*args, **kwargs):
        raise AssertionError("core called AG-08")
    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append("consistency")
            raise
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(feature_extractor, "extract", forbidden)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", slow)
    result = asyncio.run(analyze_job.analyze_core_image_bytes(make_settings(analysis_aux_timeout_seconds=.01), [(PNG, "image/png")]))
    assert result["result_data"]["aiSuggestedPoints"] == ["골지 짜임"]
    assert dr.PERSISTED_KEY not in result["result_data"]
    assert dr.PERSISTED_KEY not in result["analysis_payload"]
    assert cancelled == ["consistency"]


def test_legacy_result_shape_and_parallelism_survive_split(monkeypatch):
    shrink_calls, prepared_calls = [], []
    analyst_started = asyncio.Event()
    feature_started = asyncio.Event()
    def shrink(data, mime):
        shrink_calls.append(data)
        return b"shrunk", "image/jpeg"
    async def analyst(*args, **kwargs):
        analyst_started.set()
        await feature_started.wait()
        return deepcopy(DISTRIBUTED), "gemini"
    async def features(*args, **kwargs):
        feature_started.set()
        await analyst_started.wait()
        return feature_extractor.FeaturePoints(["단추 여밈"], [candidate()]), "gpt"
    async def consistency(*args, **kwargs):
        return None
    async def prepared(size):
        prepared_calls.append(size)
    monkeypatch.setattr(analyze_job, "shrink_for_vision", shrink)
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(feature_extractor, "extract", features)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    result = asyncio.run(analyze_job.analyze_image_bytes(make_settings(), [(PNG, "image/png")], slots=["Front"], on_prepared=prepared))
    assert shrink_calls == [PNG] and prepared_calls == [6]
    contract = dr.build_contract([candidate()], [(PNG, "image/png")], ["Front"])
    assert result == {
        "result_data": {"aiSuggestedPoints": ["단추 여밈"], "fit": "regular", "detailRecommendations": dr.public_summary(contract),
                        "styleTags": ["daily"], "swatchSuggestions": [], "clothingType": "top", "measurements": []},
        "analysis_payload": {"aiSuggestedPoints": ["단추 여밈"], "fit": "regular", "detailRecommendations": contract,
                             "styleTags": ["daily"], "swatchSuggestions": []},
        "clothing_type": "top", "provider": "gemini", "feature_provider": "gpt", "consistency": None, "bytes_out": 6,
    }
