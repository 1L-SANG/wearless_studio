import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.services import outfit
from conftest import make_settings, FakeR2


def row(identifier, kind, **overrides):
    return {"id": identifier, "name": identifier, "clothing_type": kind,
            "gender": "women", "is_active": True, "is_custom": False,
            "image_asset_id": identifier + "-image", "image_key": identifier + ".png",
            "thumb_key": identifier + "-thumb.png", "sort_order": 0, **overrides}


@pytest.mark.parametrize("kind,roles", [
    ("outer", ("inner", "bottom", "footwear", "bag", "accessory")),
    ("top", ("bottom", "footwear", "bag", "accessory")),
    ("bottom", ("top", "footwear", "bag", "accessory")),
    ("dress", ("footwear", "bag", "accessory")),
])
def test_role_branches(kind, roles):
    assert outfit.eligible_slots(kind, "shirt") == roles


@pytest.mark.parametrize("product_color,expected", [
    ("white", ["red", "green", "ivory", "gold", "silver"]),
    ("ivory", ["red", "green", "ivory", "gold", "silver"]),
    ("silver", ["gold", "green", "ivory", "red", "silver"]),
    ("gold", ["gold", "green", "ivory", "red", "silver"]),
    ("green", ["ivory", "green", "gold", "silver", "red"]),
    ("red", ["ivory", "red", "gold", "silver", "green"]),
])
def test_october_catalog_colors_keep_role_ranking_and_neutral_metal_ties(product_color, expected):
    colors = ["ivory", "silver", "gold", "green", "red"]
    kinds = {"inner": "inner", "bottom": "bottom", "footwear": "shoes", "bag": "bag", "accessory": "accessory"}
    items = [row(f"{role}-{color}", kind, color_group=color, category="가방" if role == "bag" else None)
             for role, kind in kinds.items() for color in colors]
    pools = outfit.candidate_pools(items, {"clothingType": "outer", "productColor": product_color,
                                          "targetGenders": ["women"], "styleTags": []})
    for role in kinds:
        assert [item["color_group"] for item in pools[role]] == expected


def test_normalization_preserves_user_empty_and_prunes_impossible_slots():
    value = {"version": 1, "slots": {"footwear": {"itemId": None, "origin": "user"},
             "bottom": {"itemId": "removed", "origin": "user"}}}
    assert outfit.normalize_selections(value, "dress", allowed_items=[], strict=True) == {
        "version": 1, "slots": {"footwear": {"itemId": None, "origin": "user"}}, "setIndex": 0, "optional": {"bag": False, "accessory": False}}


@pytest.mark.parametrize("role,item", [
    ("footwear", row("bad", "shoes", is_custom=True)),
    ("inner", row("bad", "top")),
    ("bag", row("bad", "bag", is_active=False)),
    ("bag", row("bad", "bag", image_key=None)),
])
def test_strict_rejects_invalid_role_inactive_or_private_custom(role, item):
    with pytest.raises(outfit.OutfitSelectionError):
        outfit.normalize_selections({"version": 1, "slots": {role: {"itemId": "bad", "origin": "user"}}},
                                    "outer", allowed_items=[item], strict=True)


def test_strict_rejects_forged_id_and_snapshot_has_only_ids():
    with pytest.raises(outfit.OutfitSelectionError):
        outfit.normalize_selections({"version": 1, "slots": {"bag": {"itemId": "forged", "origin": "user"}}},
                                    "top", allowed_items=[row("bag", "bag")], strict=True)
    snapshot = outfit.generation_snapshot({"matchClothing": [{"id": "bottom", "selected": True,
                                              "imageUrl": "https://evil.test/fake"}]},
                                          "top", [row("bottom", "bottom", is_custom=True)])
    assert snapshot == {"version": 1, "slots": {"bottom": {"itemId": "bottom", "origin": "user"}}}


def test_empty_catalog_skips_model_call(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(make_settings(), FakeR2(), {"clothingType": "top"}, {},
                                        [(b"photo", "image/png")], [], slots=["Front"]))
    assert result["outfitRecommendation"]["status"] == "unavailable"
    call.assert_not_called()


def test_visual_result_validated_and_user_fixed_slots_kept(monkeypatch):
    outfit.clear_cache()
    call = AsyncMock(return_value={"sets": [{"slots": {"bottom": "b2", "footwear": None}, "reason": "편안한 조합", "concept": "casual"}]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    items = [row("b1", "bottom"), row("b2", "bottom"), row("shoe", "shoes"), row("bag", "bag")]
    analysis = {"subCategory": "knit", "outfitSelections": {"version": 1, "slots": {
        "footwear": {"itemId": None, "origin": "user"}, "bag": {"itemId": "bag", "origin": "user"}}}}
    settings = replace(make_settings(), gemini_api_key="mock-key", outfit_recommendation_enabled=True)
    result = asyncio.run(outfit.recommend(settings, FakeR2(), {"clothingType": "top"}, analysis,
                                         [(b"photo-visual", "image/png")], items, slots=["Front"]))
    assert result["outfitRecommendation"]["status"] == "ready"
    assert result["outfitSelections"]["slots"]["bottom"] == {"itemId": "b2", "origin": "ai"}
    assert result["outfitSelections"]["slots"]["footwear"] == {"itemId": None, "origin": "user"}
    assert result["outfitSelections"]["slots"]["bag"] == {"itemId": "bag", "origin": "user"}
    assert call.call_args.kwargs["context"]["subCategory"] == "knit"


def test_invalid_model_ids_fall_back_to_real_first_candidate(monkeypatch):
    outfit.clear_cache()
    call = AsyncMock(return_value={"sets": [{"slots": {"bottom": "invented"}, "reason": "bad"}]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(replace(make_settings(), gemini_api_key="mock", outfit_recommendation_enabled=True), FakeR2(),
        {"clothingType": "top"}, {}, [(b"fallback", "image/png")], [row("real", "bottom")], slots=["Front"]))
    assert result["outfitRecommendation"]["status"] == "fallback"
    assert result["outfitSelections"]["slots"]["bottom"]["itemId"] == "real"


def test_signed_handoff_binds_photos_and_does_not_trust_display_urls():
    fields = {"outfitRecommendation": {"version": 1, "status": "fallback", "model": "gemini-3.8-flash",
              "contextKey": "key", "reason": "설명", "candidates": {"bottom": ["id"]}},
              "outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": "id", "origin": "ai"}}}}
    token = outfit.issue_handoff(fields, [(b"photo", "image/png")], ["Front"], "long-secret-123456789")
    verified = outfit.verify_handoff(token, "long-secret-123456789")
    assert outfit.source_binding_matches(verified, [(b"photo", "image/png")], ["Front"])
    assert not outfit.source_binding_matches(verified, [(b"changed", "image/png")], ["Front"])
    forged = deepcopy(token)
    forged["contract"]["outfitSelections"]["slots"]["bottom"]["itemId"] = "fake"
    with pytest.raises(outfit.OutfitSelectionError):
        outfit.verify_handoff(forged, "long-secret-123456789")


def test_stale_ai_patch_cannot_replace_user_selection():
    old = {"version": 1, "slots": {"bag": {"itemId": "user-bag", "origin": "user"}}}
    stale = {"version": 1, "slots": {"bag": {"itemId": "ai-bag", "origin": "ai"}}}
    assert outfit.merge_selections(old, stale, "top") == {**old, "setIndex": 0, "optional": {"bag": False, "accessory": False}}


def test_optional_props_are_empty_in_fallback_and_can_be_empty_in_visual_result(monkeypatch):
    outfit.clear_cache()
    items = [row("shoe-optional", "shoes"), row("bag-optional", "bag"), row("accessory-optional", "accessory")]
    result = asyncio.run(outfit.recommend(make_settings(), FakeR2(), {"clothingType": "dress"}, {},
                       [(b"optional", "image/png")], items, slots=["Front"]))
    assert result["outfitSelections"]["optional"]["bag"] is False
    assert result["outfitSelections"]["optional"]["accessory"] is False
    outfit.clear_cache()
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", AsyncMock(return_value={
        "sets": [{"slots": {"footwear": "shoe-optional"}, "reason": "상품을 살리는 간결한 코디", "concept": "minimal"}]}))
    result = asyncio.run(outfit.recommend(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True),
                       FakeR2(), {"clothingType": "dress"}, {}, [(b"optional-ai", "image/png")], items, slots=["Front"]))
    assert result["outfitRecommendation"]["status"] == "ready"


def test_parallel_same_context_joins_one_call_and_ui_has_full_catalog(monkeypatch):
    outfit.clear_cache()
    calls = 0
    async def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"sets": [{"slots": {"bottom": "a", "footwear": None}, "reason": "조화로운 코디", "concept": "tonal"}]}
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", model)
    items = [row(letter, "bottom") for letter in ("a", "b", "c", "d")]
    async def run():
        args = (make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True), FakeR2(),
                {"clothingType": "top"}, {}, [(b"join", "image/png")], items)
        return await asyncio.gather(*(outfit.recommend(*args, slots=["Front"]) for _ in range(3)))
    results = asyncio.run(run())
    assert calls == 1
    assert len(results[0]["outfitRecommendation"]["candidates"]["bottom"]) == 4


def test_model_call_is_gemini_only_low_4096_with_bounded_image_count(monkeypatch):
    from app.agents import outfit_recommender as agent
    call = AsyncMock(return_value={"sets": [{"slots": {}, "reason": "test"}]})
    monkeypatch.setattr(agent, "_call_gemini", call)
    settings = make_settings(gemini_api_key="mock")
    pools = {role: [row(role + str(index), kind) for index in range(2)]
             for role, kind in outfit.ROLE_TYPES.items() if role != "top"}
    context = {"clothingType": "outer", "subCategory": "shirt", "fit": "oversized", "materials": [{"name": "코튼", "ratio": 100}]}
    from io import BytesIO
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buffer, format="PNG")
    class ImageR2(FakeR2):
        def get_bytes(self, key): return buffer.getvalue()
    asyncio.run(agent.recommend(settings, ImageR2(), [(buffer.getvalue(), "image/png")], pools,
                context=context, fixed={"version": 1, "slots": {}}))
    assert call.await_count == 1
    assert call.call_args.args[1] == "gemini-3.8-flash"
    assert len(call.call_args.args[3]) == 7
    assert call.call_args.kwargs["thinking_level"] == "low"
    assert call.call_args.kwargs["max_output_tokens"] == 4096
    assert "outerwear shirt worn over an inner layer" in call.call_args.args[2]
    assert "oversized" in call.call_args.args[2]


def test_timeout_calls_once_and_falls_back(monkeypatch):
    outfit.clear_cache()
    async def delayed(*args, **kwargs):
        await asyncio.sleep(1)
    call = AsyncMock(side_effect=delayed)
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", call)
    result = asyncio.run(outfit.recommend(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True,
                       outfit_timeout_seconds=0.01), FakeR2(), {"clothingType": "top"}, {},
                       [(b"timeout", "image/png")], [row("bottom", "bottom")], slots=["Front"]))
    assert result["outfitRecommendation"]["status"] == "fallback"
    call.assert_awaited_once()


def test_promotion_context_compares_color_fit_materials_without_group_ids():
    signed = outfit.product_context({"clothingType": "top", "colors": [{"id": "public", "swatchId": "black", "name": "블랙", "isBase": True}]},
                                   {"fit": "regular", "materials": [{"name": "코튼", "ratio": 100}]})
    same = outfit.product_context({"clothingType": "top", "colors": [{"id": "owned", "swatchId": "black", "name": "블랙", "isBase": True}]},
                                 {"fit": "regular", "materials": [{"name": "코튼", "ratio": 100}]})
    assert outfit.promotion_context_matches(signed, same)
    assert not outfit.promotion_context_matches(signed, {**same, "productColor": "white"})
    assert not outfit.promotion_context_matches(signed, {**same, "fit": "slim"})
    assert not outfit.promotion_context_matches(signed, {**same, "materials": [{"name": "폴리에스터", "ratio": 100}]})


def test_promotion_context_accepts_autofilled_fields_previously_unknown():
    signed = outfit.product_context({"clothingType": "top"}, {})
    current = outfit.product_context({"clothingType": "top", "colors": [{"swatchId": "black", "isBase": True}]},
                                    {"subCategory": "knit", "fit": "regular", "materials": [{"name": "코튼", "ratio": 100}], "styleTags": ["daily"]})
    assert outfit.promotion_context_matches(signed, current)


def test_context_change_reranks_ai_selection_but_keeps_user_choice_first(monkeypatch):
    monkeypatch.setattr(outfit.color_harmony, "outfit_color_score", lambda product, item, *_args: 1.0 if product == item else 0.0)
    items = [row("dark", "bottom", color_group="black"), row("light", "bottom", color_group="white")]
    context = {"clothingType": "top", "productColor": "white", "styleTags": [], "targetGenders": ["women"]}
    old_ai = {"version": 1, "slots": {"bottom": {"itemId": "dark", "origin": "ai"}}}
    old_user = {"version": 1, "slots": {"bottom": {"itemId": "dark", "origin": "user"}}}
    assert outfit.candidate_pools(items, context, old_ai)["bottom"][0]["id"] == "light"
    assert outfit.candidate_pools(items, context, old_user)["bottom"][0]["id"] == "dark"
