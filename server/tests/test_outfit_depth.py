"""A2: diverse real candidates, internal concepts and offline request accounting."""
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.config import load_settings
from app.services import outfit
from conftest import FakeR2, make_settings
from test_outfit import row
from test_outfit_routes import state


def catalog(kind="bottom", count=12):
    colors = ["black", "white", "blue", "brown", "gray", "beige"]
    return [row(f"{kind}-{i:02}", kind, color_group=colors[i % 6],
                color_name=colors[i % 6], style_tags=["casual" if i % 2 else "minimal"],
                fit="regular", length="long", sort_order=i) for i in range(count)]


@pytest.mark.parametrize("raw,want", [(None, 6), ("2", 2), ("8", 8), ("1", 2),
                                     ("9", 8), ("invalid", 6), ("", 6), ("3.5", 6)])
def test_candidate_setting_is_bounded_and_invalid_text_uses_default(monkeypatch, raw, want):
    monkeypatch.delenv("OUTFIT_CANDIDATES_PER_ROLE", raising=False)
    if raw is not None:
        monkeypatch.setenv("OUTFIT_CANDIDATES_PER_ROLE", raw)
    assert getattr(load_settings(), "outfit_candidates_per_role", None) == want


def test_paid_recommendations_default_off_even_with_api_key(monkeypatch):
    monkeypatch.delenv("OUTFIT_RECOMMENDATION", raising=False)
    settings = load_settings()
    assert settings.outfit_recommendation_enabled is False
    assert make_settings().outfit_recommendation_enabled is False
    outfit.clear_cache()
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(replace(settings, gemini_api_key="mock"), FakeR2(),
        {"clothingType": "top"}, {}, [(b"off-default", "image/png")], catalog()))
    assert result["outfitRecommendation"]["status"] == "fallback"
    assert len(result["outfitRecommendation"]["sets"]) == 3
    call.assert_not_called()
    monkeypatch.setenv("OUTFIT_RECOMMENDATION", "on")
    assert load_settings().outfit_recommendation_enabled is True


def test_shortlist_is_stable_color_capped_and_keeps_low_rank_fixed_item():
    items = [row(f"black-{i}", "bottom", color_group="black", style_tags=["casual"])
             for i in range(8)] + catalog()
    fixed = {"slots": {"bottom": {"itemId": "black-7", "origin": "user"}}}
    context = {"clothingType": "top", "styleTags": ["casual"]}
    pools = outfit.candidate_pools(items, context, fixed, limit=6)
    assert pools == outfit.candidate_pools(list(reversed(items)), context, fixed, limit=6)
    chosen = pools["bottom"]
    assert len(chosen) == 6
    assert chosen[0]["id"] == "black-7"
    assert max(Counter(i["color_group"] for i in chosen).values()) <= 2
    assert len(outfit.candidate_pools(items, context, fixed)["bottom"]) == len(items)


def test_overlapping_styles_are_deferred_in_equal_score_pool():
    items = [row("a", "bottom", color_group="black", style_tags=["casual"]),
             row("b", "bottom", color_group="white", style_tags=["casual"]),
             row("c", "bottom", color_group="blue", style_tags=["minimal"])]
    chosen = outfit.candidate_pools(items, {"clothingType": "top"}, limit=2)["bottom"]
    assert [i["id"] for i in chosen] == ["a", "c"]


def test_homogeneous_and_unknown_colors_do_not_bypass_cap():
    for group in ("black", None, " BLACK "):
        items = [row(str(i), "bottom", color_group=group) for i in range(12)]
        assert len(outfit.candidate_pools(items, {"clothingType": "top"}, limit=6)["bottom"]) == 2


def test_fallback_varies_multiple_roles_before_minor_variants():
    pools = {"bottom": catalog(count=3), "footwear": catalog("shoes", count=3)}
    actual = outfit.fallback_sets(pools)
    assert len(actual) == 3
    assert len({s["slots"]["bottom"] for s in actual}) == 3
    assert len({s["slots"]["footwear"] for s in actual}) == 3


def test_pending_and_off_fallback_use_same_diverse_shortlist_and_user_locks():
    items = catalog() + catalog("shoes") + catalog("bag")
    analysis = {"outfitSelections": {"version": 1, "slots": {
        "bottom": {"itemId": "bottom-11", "origin": "user"},
        "footwear": {"itemId": None, "origin": "user"}}}}
    settings = make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True)
    values = (FakeR2(), {"clothingType": "top"}, analysis, [(b"pending", "image/png")], items)
    pending = outfit.prepare_recommendation(settings, *values)["outfitRecommendation"]
    fallback = outfit.prepare_recommendation(replace(settings, outfit_recommendation_enabled=False), *values)["outfitRecommendation"]
    assert pending["sets"] == fallback["sets"]
    assert len(pending["candidates"]["bag"]) == 12
    assert len(pending["sets"]) == 1  # Both clothing roles are fixed; props no longer vary sets.
    for entry in pending["sets"]:
        assert entry["slots"]["bottom"] == "bottom-11"
        assert entry["slots"]["footwear"] is None
        assert "bag" not in entry["slots"]
        assert "accessory" not in entry["slots"]


def test_shortlist_setting_and_prompt_version_invalidate_reuse_key(monkeypatch):
    settings = make_settings()
    args = ({"clothingType": "top"}, {}, [(b"key", "image/png")], ["Front"], catalog(), {"slots": {}})
    before = outfit.recommendation_key(settings, *args)
    assert outfit.recommendation_key(replace(settings, outfit_candidates_per_role=8), *args) != before
    monkeypatch.setattr(outfit.outfit_recommender, "PROMPT_VERSION", "test-next-version")
    assert outfit.recommendation_key(settings, *args) != before


def model_set(bottom, *, bag=None, concept="tonal"):
    return {"slots": {"bottom": bottom, "footwear": None},
            "reason": "상품의 색과 실루엣을 살린 코디예요.", "concept": concept}


def test_repeated_sets_are_dropped_on_roles_with_multiple_candidates():
    pools = outfit.candidate_pools(catalog(count=2) + [row("bag", "bag")], {"clothingType": "top"}, limit=6)
    first = model_set("bottom-00", bag="bag")
    # A singleton optional role toggling to null is not a new direction.
    repeated = model_set("bottom-00", concept="contrast")
    third = model_set("bottom-01", concept="casual")
    assert outfit.validate_sets([first, repeated, third], pools) == [first, third]
    assert outfit.validate_sets([first, deepcopy(first)], pools) == [first]


def test_single_choice_roles_yield_one_set_and_repeated_concepts_are_dropped():
    pools = outfit.candidate_pools(catalog(count=1) + [row("bag", "bag")], {"clothingType": "top"})
    first = model_set("bottom-00", bag="bag")
    assert outfit.validate_sets([first, model_set("bottom-00", concept="contrast")], pools) == [first]
    pools = outfit.candidate_pools(catalog(count=2), {"clothingType": "top"})
    assert outfit.validate_sets([model_set("bottom-00"), model_set("bottom-01")], pools) == [model_set("bottom-00")]


@pytest.mark.parametrize("concept", [None, "", "한국어", "a" * 49, "<script>", 4])
def test_invalid_internal_concepts_are_rejected(concept):
    pools = outfit.candidate_pools(catalog(count=2), {"clothingType": "top"})
    with pytest.raises(outfit.OutfitSelectionError):
        outfit.validate_sets([{**model_set("bottom-00"), "concept": concept}], pools)


def test_paid_path_keeps_one_ready_set_and_stores_concept(monkeypatch):
    outfit.clear_cache()
    first = model_set("bottom-00")
    model = AsyncMock(return_value={"sets": [first, first, first]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", model)
    result = asyncio.run(outfit.recommend(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True),
        FakeR2(), {"clothingType": "top"}, {}, [(b"concept", "image/png")], catalog()))
    assert result["outfitRecommendation"]["status"] == "ready"
    assert result["outfitRecommendation"]["sets"] == [first]
    assert len(model.call_args.args[3]["bottom"]) == 6
    public = outfit.read_fields(result)
    assert public["outfitRecommendation"]["sets"] == [{"slots": first["slots"], "reason": first["reason"]}]
    assert result["outfitRecommendation"]["sets"][0]["concept"] == "tonal"


@pytest.mark.parametrize("path", ["/analysis", "/analysis/outfit-recommendations"])
def test_public_reads_hide_concept_without_mutating_storage_or_calling_model(client, make_token, state, monkeypatch, path):
    from conftest import auth_headers
    saved, product, items = state
    saved.update(outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved,
        [(b"read-concept", "image/png")], items))
    saved["outfitRecommendation"]["sets"][0]["concept"] = "tonal"
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    response = client.get("/v1/projects/p1" + path, headers=auth_headers(make_token))
    assert response.status_code == 200
    assert "concept" not in response.json()["outfitRecommendation"]["sets"][0]
    assert saved["outfitRecommendation"]["sets"][0]["concept"] == "tonal"
    call.assert_not_called()


def test_selection_patch_preserves_stored_concept_but_does_not_expose_it(client, make_token, state):
    from conftest import auth_headers
    saved, product, items = state
    saved.update(outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved,
        [(b"patch-concept", "image/png")], items))
    saved["outfitRecommendation"]["sets"][0]["concept"] = "tonal"
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
        json={"outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": "bottom", "origin": "user"}}}})
    assert response.status_code == 200
    assert "concept" not in response.json()["outfitRecommendation"]["sets"][0]
    assert saved["outfitRecommendation"]["sets"][0]["concept"] == "tonal"


def png(size, color="white"):
    from io import BytesIO
    from PIL import Image
    data = BytesIO()
    Image.new("RGBA", size, color).save(data, format="PNG")
    return data.getvalue()


def test_model_receives_small_tidy_thumbs_original_product_and_v2_schema(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from app.agents import outfit_recommender as agent
    class Images(FakeR2):
        def get_bytes(self, key):
            assert key.endswith("-thumb.png")
            return png((1024, 1024), "red")
    call = AsyncMock(return_value={"sets": [model_set("bottom-00")]})
    monkeypatch.setattr(agent, "_call_gemini", call)
    pools = outfit.candidate_pools(catalog(), {"clothingType": "top"}, limit=6)
    asyncio.run(agent.recommend(make_settings(), Images(), [(png((768, 1024), "blue"), "image/png")],
        pools, context={"clothingType": "top"}, fixed={"slots": {}}))
    images = call.call_args.args[3]
    decoded = [Image.open(BytesIO(i.data)) for i in images]
    assert [i.size for i in decoded] == [(384, 512)] + [(256, 256)] * 6
    assert decoded[0].getpixel((0, 0))[2] > 240
    schema = call.call_args.args[4]["properties"]["sets"]["items"]
    assert "concept" in schema["required"]
    assert schema["properties"]["reason"]["maxLength"] == 200
    assert schema["properties"]["concept"]["maxLength"] == 48
    assert call.call_args.kwargs["max_output_tokens"] == 4096
    from app.agents.vision_llm import _gemini_body
    wire = _gemini_body(call.call_args.args[2], images, call.call_args.args[4], "low", 4096)
    sets_schema = wire["generationConfig"]["responseSchema"]["properties"]["sets"]
    assert sets_schema.get("maxItems") == 3
    assert sets_schema["items"]["properties"]["concept"].get("maxLength") == 48
    role_schema = sets_schema["items"]["properties"]["slots"]["properties"]["bottom"]
    assert role_schema["nullable"] is True
    assert all(isinstance(value, str) for value in role_schema["enum"])


def test_unreadable_images_fail_closed_instead_of_sending_unbounded_original(monkeypatch):
    from app.agents import outfit_recommender as agent
    call = AsyncMock()
    monkeypatch.setattr(agent, "_call_gemini", call)
    with pytest.raises(ValueError, match="outfit_image_unreadable"):
        asyncio.run(agent.recommend(make_settings(), FakeR2(), [(b"broken", "image/png")],
            {"bottom": catalog(count=1)}, context={"clothingType": "top"}, fixed={"slots": {}}))
    call.assert_not_called()


def test_offline_dry_run_counts_actual_prepared_request_without_provider(monkeypatch):
    from scripts import outfit_dry_run
    from app.agents import outfit_recommender as agent
    call = AsyncMock(side_effect=AssertionError("dry-run must not call provider"))
    monkeypatch.setattr(agent, "_call_gemini", call)
    rows = asyncio.run(outfit_dry_run.representative_runs())
    assert [r["image_count"] for r in rows] == [7, 13, 19, 7, 7, 7]
    assert [sum(r["candidates_per_role"].values()) for r in rows] == [6, 12, 18, 6, 6, 6]
    for measured in rows:
        assert measured["product_image"] == {"width": 384, "height": 512}
        assert measured["candidate_image_sizes"] == [{"width": 256, "height": 256}]
        assert measured["prompt_chars"] == measured["user_prompt_chars"] + measured["system_prompt_chars"]
        assert measured["schema_chars"] > 100
        assert measured["max_output_tokens"] == (1024 if measured["scenario"].startswith("props_") else 4096)
    largest = asyncio.run(outfit_dry_run.representative_runs(8))[2]
    assert largest["image_count"] == 25
    call.assert_not_called()


def test_dry_run_prompt_size_matches_live_mocked_boundary(monkeypatch):
    from scripts import outfit_dry_run
    from app.agents import outfit_recommender as agent
    context = {"clothingType": "top"}
    pools = outfit.candidate_pools(catalog(count=2), context, limit=6)
    class Images(FakeR2):
        def get_bytes(self, key): return png((700, 900))
    images = [(png((1000, 500)), "image/png")]
    fixed = {"slots": {}}
    measured = asyncio.run(outfit_dry_run.measure_request(Images(), images, pools, context=context, fixed=fixed))
    call = AsyncMock(return_value={})
    monkeypatch.setattr(agent, "_call_gemini", call)
    asyncio.run(agent.recommend(make_settings(), Images(), images, pools, context=context, fixed=fixed))
    assert measured["user_prompt_chars"] == len(call.call_args.args[2])
    assert measured["system_prompt_chars"] == len(call.call_args.kwargs["system_prompt"])
    assert measured["image_count"] == len(call.call_args.args[3]) == 3
    assert measured["product_image"] == {"width": 512, "height": 256}
    assert measured["candidate_image_sizes"] == [{"width": 199, "height": 256}]


def test_recommendation_uses_original_front_even_when_source_order_differs(monkeypatch):
    outfit.clear_cache()
    call = AsyncMock(return_value={"sets": [model_set("bottom-00")]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True),
        FakeR2(), {"clothingType": "top"}, {}, [(b"back", "image/png"), (b"front", "image/png")],
        catalog(), slots=["Back", "Front"]))
    assert result["outfitRecommendation"]["status"] == "ready"
    assert call.call_args.args[2] == [(b"front", "image/png")]


def test_no_front_returns_free_fallback(monkeypatch):
    outfit.clear_cache()
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True),
        FakeR2(), {"clothingType": "top"}, {}, [(b"back-only", "image/png")], catalog(), slots=["Back"]))
    assert result["outfitRecommendation"]["status"] == "fallback"
    call.assert_not_called()


def test_off_request_does_not_join_paid_pending_cache(monkeypatch):
    outfit.clear_cache()
    async def run():
        settings = make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True)
        call = AsyncMock()
        monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
        args = (FakeR2(), {"clothingType": "top"}, {}, [(b"switch", "image/png")], catalog())
        pending = outfit.start_recommendation(settings, *args, semaphore=asyncio.Semaphore(0))
        task = outfit._inflight[pending["outfitRecommendation"]["contextKey"]]
        try:
            fallback = outfit.start_recommendation(replace(settings, outfit_recommendation_enabled=False), *args)
            assert fallback["outfitRecommendation"]["status"] == "fallback"
            assert fallback["outfitRecommendation"]["contextKey"] != pending["outfitRecommendation"]["contextKey"]
            call.assert_not_called()
        finally:
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_custom_upload_keeps_stored_concept_out_of_analysis_response(client, make_token, monkeypatch):
    from conftest import auth_headers
    from test_custom_match_item import _asset_ids, _patch_post
    asset_ids = _asset_ids(1)
    _, saved, _ = _patch_post(monkeypatch, asset_ids)
    saved["payload"]["outfitRecommendation"] = {"status": "ready", "sets": [model_set("curated-1")]}
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    response = client.post("/v1/projects/p1/analysis/custom-match-item", headers=auth_headers(make_token),
                           json={"assetIds": asset_ids})
    assert response.status_code == 200, response.text
    assert "concept" not in response.json()["analysis"]["outfitRecommendation"]["sets"][0]
    assert saved["payload"]["outfitRecommendation"]["sets"][0]["concept"] == "tonal"
    call.assert_not_called()
