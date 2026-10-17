import asyncio
import contextlib
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app import repo, routes, public_routes
from app.services import outfit
from conftest import auth_headers, FakeConn, FakeR2
from test_outfit import row
from test_analysis_server_owned_fields import _save


@pytest.fixture
def state(monkeypatch, client):
    saved = {"subCategory": "knit", "targetGenders": ["women"], "matchClothing": []}
    product = {"clothingType": "top", "colors": [{"isBase": True, "images": [{"id": "front", "slot": "Front"}]}]}
    items = [row("bottom", "bottom"), row("bag", "bag")]
    @contextlib.asynccontextmanager
    async def conn(_request):
        yield FakeConn()
    monkeypatch.setattr(routes, "get_conn", conn)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(side_effect=lambda *args: deepcopy(product)))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(side_effect=lambda *args: deepcopy(saved)))
    monkeypatch.setattr(repo, "list_active_matching_items", AsyncMock(side_effect=lambda *args: deepcopy(items)))
    async def save(conn, pid, payload, **kwargs):
        saved.update(deepcopy(payload))
        return {"project_id": pid, "payload": deepcopy(saved)}
    async def save_server(conn, pid, fields, **kwargs):
        saved.update(deepcopy(fields))
        return deepcopy(saved)
    monkeypatch.setattr(repo, "save_analysis", save)
    monkeypatch.setattr(repo, "save_outfit_recommendation", save_server)
    monkeypatch.setattr(repo, "get_asset_for_user", AsyncMock(return_value={"r2_key": "front.png", "mime_type": "image/png"}))
    client.app.state.r2 = FakeR2()
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key="outfit-secret-long-enough")
    return saved, product, items


def test_client_cannot_forge_outfit_recommendation():
    original = {"version": 1, "status": "fallback", "reason": "server"}
    assert "outfitRecommendation" not in _save(None, {"outfitRecommendation": {"reason": "client"}})
    assert _save({"outfitRecommendation": original}, {"outfitRecommendation": {"reason": "client"}})["outfitRecommendation"] == original


def test_selection_patch_restores_catalog_urls_and_syncs_one_legacy_match(client, make_token, state):
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={
        "outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": "bottom", "origin": "user"},
            "bag": {"itemId": None, "origin": "user"}}},
        "matchClothing": [{"id": "bottom", "selected": True, "imageUrl": "https://evil.test"}]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["matchClothing"][0]["imageUrl"] == "https://r2.test/bottom.png"
    assert data["matchIds"] == ["bottom"]
    assert data["outfitSelections"]["slots"]["bag"] == {"itemId": None, "origin": "user"}


def test_selection_patch_rejects_forged_or_other_project_ids(client, make_token, state):
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={
        "outfitSelections": {"version": 1, "slots": {"bag": {"itemId": "other-owner", "origin": "user"}}}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "outfit_item_unavailable"


def test_context_refresh_uses_saved_product_and_rejects_stale_context(client, make_token, state, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(outfit, "recommend", model)
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations", headers=auth_headers(make_token),
                           json={"clothingType": "bottom"})
    assert response.status_code == 409
    model.assert_not_called()


def test_catalog_get_never_calls_model(client, make_token, state, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(outfit, "recommend", model)
    response = client.get("/v1/projects/p1/analysis/outfit-candidates", headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json()["candidates"]["bag"][0]["id"] == "bag"
    assert response.json()["candidates"]["footwear"] == []
    model.assert_not_called()


def test_public_core_decorates_outfit_inside_existing_slot(client, monkeypatch):
    async def analyze(*args, **kwargs):
        return {"result_data": {"clothingType": "top", "subCategory": "knit"},
                "analysis_payload": {"subCategory": "knit"}, "clothing_type": "top"}
    monkeypatch.setattr(public_routes, "analyze_core_image_bytes", analyze)
    monkeypatch.setattr(repo, "list_curated_matching_items", AsyncMock(return_value=[]))
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key="outfit-secret-long-enough")
    response = client.post("/v1/public/analyze", files=[("images", ("front.png", b"\x89PNG\r\n\x1a\nphoto", "image/png"))],
        data={"parts": "core", "runId": "outfit-run-123"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outfitRecommendation"]["status"] == "unavailable"
    assert data["outfitSelections"] == {"version": 1, "slots": {}, "setIndex": 0, "optional": {"bag": False, "accessory": False}}
    assert outfit.verify_handoff(data[outfit.HANDOFF_KEY], "outfit-secret-long-enough")


def test_mannequin_join_compares_outfit_snapshot():
    base = {"mode": "generate", "fitProfileSnapshot": {"version": 1, "profile": {}}}
    one = {"version": 1, "slots": {"bag": {"itemId": "one", "origin": "user"}}}
    two = {"version": 1, "slots": {"bag": {"itemId": "two", "origin": "user"}}}
    assert not routes._mannequin_payload_matches({"status": "running", "payload": {**base, "outfitSelectionsSnapshot": one}},
                                               {**base, "outfitSelectionsSnapshot": two})


def test_auth_refresh_persists_server_result_and_current_catalog(client, make_token, state):
    outfit.clear_cache()
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations", headers=auth_headers(make_token), json={})
    assert response.status_code == 200, response.text
    assert response.json()["outfitSelections"]["slots"]["bottom"]["itemId"] == "bottom"
    assert state[0]["outfitRecommendation"]["contextKey"] == response.json()["outfitRecommendation"]["contextKey"]


def test_signed_promotion_rebuilds_catalog_and_preserves_user_empty(client, make_token, state):
    saved, product, items = state
    fields = asyncio.run(outfit.recommend(client.app.state.settings, client.app.state.r2, product, saved,
        [(b"\x89PNG-bytes", "image/png")], items, slots=["Front"]))
    token = outfit.issue_handoff(fields, [(b"\x89PNG-bytes", "image/png")], ["Front"], "outfit-secret-long-enough")
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote", headers=auth_headers(make_token), json={
        "handoff": token, "outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": None, "origin": "user"}}}})
    assert response.status_code == 200, response.text
    assert response.json()["outfitSelections"]["slots"]["bottom"] == {"itemId": None, "origin": "user"}
    assert response.json()["matchIds"] == []


def test_signed_promotion_rejects_source_drift(client, make_token, state):
    saved, product, items = state
    fields = asyncio.run(outfit.recommend(client.app.state.settings, client.app.state.r2, product, saved,
        [(b"old-photo", "image/png")], items, slots=["Front"]))
    token = outfit.issue_handoff(fields, [(b"old-photo", "image/png")], ["Front"], "outfit-secret-long-enough")
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote", headers=auth_headers(make_token), json={"handoff": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "analysis_handoff_source_drift"


def test_custom_delete_clears_selection_with_user_empty():
    analysis = {"matchClothing": [{"id": "custom", "selected": True}],
                "outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": "custom", "origin": "user"},
                                                         "bag": {"itemId": "bag", "origin": "ai"}}}}
    patch = routes._analysis_without_custom(analysis, "custom")
    assert patch["outfitSelections"]["slots"]["bottom"] == {"itemId": None, "origin": "user"}
    assert patch["outfitSelections"]["slots"]["bag"]["itemId"] == "bag"


def test_public_catalog_query_is_ownerless_and_asset_public_seed_only():
    from test_matching_repo_custom import _Cursor, _Conn, _sql_without_comments
    cursor = _Cursor(rows=[])
    asyncio.run(repo.list_curated_matching_items(_Conn(cursor)))
    sql = _sql_without_comments(cursor.raw_calls[0][0])
    assert "mi.owner_user_id is null and mi.project_id is null" in sql
    assert "mi.is_active" in sql
    for alias in ("img", "thb"):
        assert f"{alias}.source = 'seed'" in sql
        assert f"{alias}.visibility = 'public'" in sql
        assert f"{alias}.deleted_at is null" in sql
    assert "union" not in sql


def test_failed_cut_outfit_is_from_original_owner_scoped_server_job():
    from test_matching_repo_custom import _Cursor, _Conn
    selected = {"version": 1, "slots": {"bag": {"itemId": "original", "origin": "user"}}}
    cursor = _Cursor(row={"payload": {"outfitSelectionsSnapshot": {"version": 1, "slots": {}}},
                         "metadata": {"outfitRecipes": {"b1": {"outfitSelectionsSnapshot": selected,
                              "outfitReferenceAssets": [{"role": "bag", "itemId": "original", "assetId": "photo", "imageSha256": "hash"}]}}}})
    result = asyncio.run(repo.get_failed_cut_outfit_recipe(_Conn(cursor), "owner", "project", "b1"))
    assert result["outfitSelectionsSnapshot"] == selected
    assert result["outfitReferenceAssets"][0]["assetId"] == "photo"
    sql, params = cursor.calls[0]
    assert "user_id = %s and project_id = %s" in sql
    assert "kind = 'detail_page'" in sql
    assert "metadata->'outfitRecipes' ? %s" in sql
    assert params == ("owner", "project", "b1", "b1")


def test_promotion_rejects_same_photos_with_changed_product_color(client, make_token, state):
    saved, product, items = state
    black = deepcopy(product)
    black["colors"][0].update(id="public-group", name="블랙", swatchId="black")
    product["colors"][0].update(id="owned-group", name="화이트", swatchId="white")
    fields = asyncio.run(outfit.recommend(client.app.state.settings, client.app.state.r2, black, saved,
        [(b"\x89PNG-bytes", "image/png")], items, slots=["Front"]))
    token = outfit.issue_handoff(fields, [(b"\x89PNG-bytes", "image/png")], ["Front"], "outfit-secret-long-enough", context=outfit.product_context(black, saved))
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations:promote", headers=auth_headers(make_token), json={"handoff": token})
    assert response.status_code == 409, response.text
    assert "outfitRecommendation" not in saved


def test_public_refresh_keeps_known_fit_and_material_context(client, monkeypatch):
    seen = {}
    def recommend(settings, r2, product, analysis, source_images, items, *, slots, semaphore=None):
        seen.update(outfit.product_context(product, analysis))
        return {"outfitRecommendation": {"version": 1, "status": "unavailable", "model": "gemini-3.8-flash", "contextKey": "key", "reason": "등록된 코디 아이템이 없어요.", "candidates": {}},
                "outfitSelections": {"version": 1, "slots": {}}}
    monkeypatch.setattr(outfit, "start_recommendation", recommend)
    response = client.post("/v1/public/outfit-recommendations", files=[("images", ("front.png", b"\x89PNG\r\n\x1a\nphoto", "image/png"))],
        data={"analysisContext": __import__("json").dumps({"clothingType": "top", "fit": "oversized", "materials": [{"name": "코튼", "ratio": 100}]})})
    assert response.status_code == 200, response.text
    assert seen["fit"] == "oversized"
    assert seen["materials"] == [{"name": "코튼", "ratio": 100}]


def test_completed_mannequin_cache_requires_current_inner_asset_binding():
    selected = {"version": 1, "slots": {"inner": {"itemId": "inner", "origin": "ai"}}}
    cut = {"outfit_selections_snapshot": selected,
           "outfit_reference_assets": [{"role": "inner", "itemId": "inner", "assetId": "old-image"}]}
    assert not routes._mannequin_cached_outfit_matches(cut, selected, "new-image")
    assert routes._mannequin_cached_outfit_matches(cut, selected, "old-image")
    assert not routes._mannequin_cached_outfit_matches({"outfit_selections_snapshot": selected}, selected, "old-image")
    assert routes._mannequin_cached_outfit_matches({}, {"version": 1, "slots": {}}, None)


@pytest.mark.parametrize("parts", [None, "core"])
def test_initial_public_analysis_runs_one_auto_judgement_for_real_catalog(client, monkeypatch, parts):
    from conftest import FakePool
    outfit.clear_cache()
    async def analyze(*args, **kwargs):
        return {"result_data": {"clothingType": "top", "subCategory": "knit", "fit": "regular"},
                "analysis_payload": {"subCategory": "knit", "fit": "regular"}, "clothing_type": "top"}
    monkeypatch.setattr(public_routes, "analyze_core_image_bytes", analyze)
    monkeypatch.setattr(public_routes, "analyze_image_bytes", analyze)
    monkeypatch.setattr(repo, "list_curated_matching_items", AsyncMock(return_value=[row("real-bottom", "bottom")]))
    model = AsyncMock(return_value={"sets": [{"slots": {"bottom": "real-bottom", "footwear": None, "bag": None, "accessory": None}, "reason": "편안한 코디"}]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", model)
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key="mock", outfit_recommendation_enabled=True,
                                        r2_secret_access_key="outfit-secret-long-enough")
    data = {"parts": "core", "runId": "outfit-run-123"} if parts == "core" else {}
    response = client.post("/v1/public/analyze", files=[("images", ("front.png", b"\x89PNG\r\n\x1a\nsource", "image/png"))], data=data)
    assert response.status_code == 200, response.text
    fields = response.json()["data"]
    assert fields["outfitRecommendation"]["status"] == "pending"
    assert fields["outfitSelections"]["slots"]["bottom"] == {"itemId": "real-bottom", "origin": "ai"}
    model.assert_awaited_once()


def test_initial_authenticated_analysis_keeps_user_change_arriving_during_judgement(monkeypatch):
    from app.workers import analyze_job
    from conftest import fake_worker_app, make_settings, worker_job
    outfit.clear_cache()
    product = {"clothingType": "outer", "colors": [{"isBase": True, "images": [{"id": "front", "slot": "Front"}]}]}
    core = {"analysis_payload": {"subCategory": "jacket", "targetGenders": ["women"]},
            "result_data": {"clothingType": "outer", "subCategory": "jacket"},
            "clothing_type": "outer", "provider": "gemini", "feature_provider": None, "consistency": None}
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value=product))
    monkeypatch.setattr(repo, "get_asset_for_user", AsyncMock(return_value={"r2_key": "front.png", "mime_type": "image/png"}))
    monkeypatch.setattr(analyze_job, "analyze_image_bytes", AsyncMock(return_value=core))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value={}))
    late = {"outfitSelections": {"version": 1, "slots": {"bottom": {"itemId": "user-bottom", "origin": "user"},
                                                        "footwear": {"itemId": None, "origin": "user"}}}}
    monkeypatch.setattr(repo, "get_analysis_for_update", AsyncMock(return_value=late))
    items = [row("inner", "inner"), row("ai-bottom", "bottom"), row("user-bottom", "bottom"), row("shoes", "shoes")]
    monkeypatch.setattr(repo, "list_active_matching_items", AsyncMock(return_value=items))
    model = AsyncMock(return_value={"sets": [{"slots": {"inner": "inner", "bottom": "ai-bottom", "footwear": "shoes", "bag": None, "accessory": None}, "reason": "재킷과 어울리는 코디"}]})
    monkeypatch.setattr(outfit.outfit_recommender, "recommend", model)
    finalized = AsyncMock(return_value={"result": {}})
    monkeypatch.setattr(repo, "finalize_analyze_success", finalized)
    monkeypatch.setattr(analyze_job, "_emit", AsyncMock())
    app = fake_worker_app(make_settings(gemini_api_key="mock", outfit_recommendation_enabled=True))
    asyncio.run(analyze_job.run_analyze_job(app, worker_job()))
    payload = finalized.call_args.kwargs["analysis_payload"]
    assert payload["outfitSelections"]["slots"]["inner"] == {"itemId": "inner", "origin": "ai"}
    assert payload["outfitSelections"]["slots"]["bottom"] == {"itemId": "user-bottom", "origin": "user"}
    assert payload["outfitSelections"]["slots"]["footwear"] == {"itemId": None, "origin": "user"}
    assert payload["matchIds"] == ["user-bottom"]
    model.assert_awaited_once()


@pytest.mark.parametrize("change", [{"fit": "slim"}, {"materials": [{"name": "코튼", "ratio": 100}]}])
def test_auth_refresh_rejects_stale_known_fit_or_material_context_before_call(client, make_token, state, monkeypatch, change):
    model = AsyncMock()
    monkeypatch.setattr(outfit, "recommend", model)
    response = client.post("/v1/projects/p1/analysis/outfit-recommendations", headers=auth_headers(make_token), json=change)
    assert response.status_code == 409, response.text
    model.assert_not_called()
