"""Promotion compares signed input facts, independently of later color autofill."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.services import outfit
from conftest import auth_headers
from test_outfit_routes import state


def color(swatch=None, name="", *, base=False, identifier="public"):
    return {"id": identifier, "swatchId": swatch, "name": name, "isBase": base,
            "images": [{"id": "front", "slot": "Front"}] if base else []}


@pytest.mark.parametrize("before,after,accepted", [
    ([color("gray", base=True)], [color("gray", "차콜", base=True)], True),
    ([color("black", base=True)], [color("black", "워시드 블랙 스톤", base=True)], True),
    ([color("gray", base=True)], [color("gray", "다크 그레이", base=True)], True),
    ([color("gray", base=True)], [color("gray", "washed charcoal", base=True)], True),
    ([color("brown", base=True)], [color("brown", "모카", base=True)], True),
    ([color("beige", base=True)], [color("beige", "오트밀", base=True)], True),
    ([color("navy", base=True)], [color("navy", "인디고", base=True)], True),
    ([color("black", "블랙", base=True), color(identifier="unknown")],
     [color("black", "블랙", base=True), color("gray", "차콜", identifier="unknown")], True),
    ([color(base=True), color("black", "블랙", identifier="known")],
     [color("gray", "차콜", base=True), color("black", "블랙", identifier="known")], True),
    ([color("gray", "차콜", base=True)], [color("gray", "차콜", base=True, identifier="owned")], True),
    ([color("gray", "차콜", base=True)], [color("black", "차콜", base=True)], False),
    ([color("gray", base=True)], [color("black", "차콜", base=True)], False),
    ([color("gray", "그레이", base=True)], [color("gray", "차콜", base=True)], False),
    ([color("gray", base=True)], [color("gray", "레드", base=True)], False),
    ([color("gray", "차콜", base=True), color("black", "블랙", identifier="second")],
     [color("gray", "차콜"), color("black", "블랙", base=True, identifier="second")], False),
    ([color("black", "블랙", base=True), color("gray", "차콜", identifier="second")],
     [color("black", "블랙", base=True)], False),
    ([color(name="차콜", base=True)], [color("gray", "레드", base=True)], False),
    ([color("gray", "차콜", base=True), color("gray", "차콜", identifier="second")],
     [color("gray", "차콜", base=True), color(identifier="second")], False),
], ids=["swatch-name-autofill", "free-name-autofill", "dark-gray-autofill", "english-shade-autofill",
        "mocha-autofill", "oatmeal-autofill", "indigo-autofill", "mixed-unknown", "unknown-base", "group-id-reassignment", "shade-kept-swatch-changed",
        "swatch-changed", "known-name-changed", "incompatible-name-autofill", "base-changed",
        "known-color-deleted", "name-only-changed", "duplicate-known-color-removed"])
def test_color_facts_at_promotion(client, make_token, state, monkeypatch, before, after, accepted):
    saved, product, items = state
    saved["swatchSuggestions"] = [{"colorGroupId": "public", "swatchId": "gray", "colorName": "차콜"}]
    original = {"clothingType": "top", "colors": deepcopy(before)}
    fields = outfit.prepare_recommendation(client.app.state.settings, client.app.state.r2, original, saved,
        [(b"\x89PNG-bytes", "image/png")], items, slots=["Front"])
    token = outfit.issue_handoff(fields, [(b"\x89PNG-bytes", "image/png")], ["Front"],
        client.app.state.settings.r2_secret_access_key, context=outfit.product_context(original, saved))
    product["colors"] = deepcopy(after)
    if accepted:
        # Uploaded assets and color groups receive new IDs, with identical bytes.
        for entry in product["colors"]:
            entry["id"] = f"owned-{entry['id']}"
            for image in entry["images"]:
                image["id"] = f"owned-{image['id']}"
    provider = AsyncMock(side_effect=AssertionError("promotion must not start paid recommendations"))
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", provider)
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote",
        headers=auth_headers(make_token), json={"handoff": token})
    assert response.status_code == (200 if accepted else 409), response.text
    if not accepted:
        assert response.json()["error"]["code"] == "outfit_context_changed"
        assert "outfitRecommendation" not in saved
    else:
        repeated = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote",
            headers=auth_headers(make_token), json={"handoff": token})
        assert repeated.status_code == 200, repeated.text
    provider.assert_not_awaited()


def test_product_color_uses_same_known_name_as_color_list():
    product = {"clothingType": "top", "colors": [color("gray", base=True)]}
    context = outfit.product_context(product, {"swatchSuggestions": [
        {"colorGroupId": "public", "swatchId": "gray", "colorName": "차콜"}]})
    assert context["productColor"] == outfit.color_harmony.matching_color(
        context["colors"][0]["swatchId"], context["colors"][0]["name"]) == "gray"


def test_pre_fix_signed_context_also_accepts_compatible_autofill():
    signed = outfit.product_context({"colors": [color("gray", base=True)]}, {})
    # Tokens already issued before deployment included the inferred shade here.
    signed["productColor"] = "charcoal"
    current = outfit.product_context({"colors": [color("gray", "차콜", base=True)]}, {})
    assert outfit.promotion_context_matches(signed, current)


def test_existing_recommendation_conflict_still_rejects(client, make_token, state):
    saved, product, items = state
    fields = outfit.prepare_recommendation(client.app.state.settings, client.app.state.r2, product, saved,
        [(b"\x89PNG-bytes", "image/png")], items, slots=["Front"])
    token = outfit.issue_handoff(fields, [(b"\x89PNG-bytes", "image/png")], ["Front"],
        client.app.state.settings.r2_secret_access_key, context=outfit.product_context(product, saved))
    saved["outfitRecommendation"] = {**deepcopy(fields["outfitRecommendation"]), "contextKey": "newer-context"}
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote",
        headers=auth_headers(make_token), json={"handoff": token})
    assert response.status_code == 409, response.text
    assert saved["outfitRecommendation"]["contextKey"] == "newer-context"


def test_missing_recommendation_reads_and_generation_use_saved_selections_without_provider(client, make_token, state, monkeypatch):
    saved, product, items = state
    choices = {"version": 1, "optional": {"bag": True}, "slots": {
        "bottom": {"itemId": "bottom", "origin": "user"}, "bag": {"itemId": "bag", "origin": "user"}}}
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
        json={"outfitSelections": choices})
    assert response.status_code == 200, response.text
    assert "outfitRecommendation" not in saved
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key="mock", outfit_recommendation_enabled=True)
    provider = AsyncMock(side_effect=AssertionError("reading must not call a provider"))
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", provider)
    for path in ("analysis", "analysis/outfit-recommendations", "analysis/outfit-candidates"):
        response = client.get(f"/v1/projects/p1/{path}", headers=auth_headers(make_token))
        assert response.status_code == 200, response.text
        if path.endswith("outfit-candidates"):
            assert response.json()["candidates"]["bottom"][0]["id"] == "bottom"
        else:
            assert response.json()["outfitSelections"]["slots"]["bottom"] == choices["slots"]["bottom"]
    snapshot = outfit.generation_snapshot(saved, "top", items)
    assert snapshot["slots"]["bottom"] == choices["slots"]["bottom"]
    assert snapshot["slots"]["bag"] == choices["slots"]["bag"]
    provider.assert_not_awaited()
