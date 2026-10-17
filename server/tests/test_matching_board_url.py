"""Catalog board art is batch loaded, public-only, and independent of generation art."""
import asyncio
from types import SimpleNamespace

import pytest

from app import repo, routes
from app.services import outfit
from conftest import auth_headers
from test_outfit_routes import state


class Cursor:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    async def fetchall(self):
        return next(self.responses)


@pytest.mark.parametrize("public", [False, True])
def test_lists_fetch_cutouts_once_for_all_catalog_sources(public):
    items = [{"id": f"item-{i}", "image_asset_id": f"source-{i}", "is_custom": False}
             for i in range(60)]
    items += [{"id": "alias", "image_asset_id": "source-0", "is_custom": False},
              {"id": "custom", "image_asset_id": "private-source", "is_custom": True}]
    cursor = Cursor([items, [{"source_asset_id": "source-0", "r2_key": "seed/matching/cutout/item-0.png"}]])
    conn = SimpleNamespace(cursor=lambda: cursor)
    result = asyncio.run(repo.list_curated_matching_items(conn) if public
                         else repo.list_active_matching_items(conn, "user", "project"))
    assert len(cursor.calls) == 2, "One catalog query plus one batched cutout lookup"
    sql, params = cursor.calls[1]
    assert params == ([f"source-{i}" for i in range(60)],)
    for gate in ("source = 'derived'", "visibility = 'public'", "deleted_at is null",
                 "user_id is null", "project_id is null", "metadata->>'canonicalType' = 'catalog_cutout'",
                 "metadata->>'sourceAssetId' = any(%s)"):
        assert gate in sql
    assert result[0]["board_key"] == "seed/matching/cutout/item-0.png"
    assert result[60]["board_key"] == result[0]["board_key"]
    assert result[1].get("board_key") is None
    assert result[-1].get("board_key") is None


def test_empty_or_custom_only_list_does_not_query_catalog_cutouts():
    for rows in ([], [{"id": "custom", "image_asset_id": "private", "is_custom": True}]):
        cursor = Cursor([rows])
        result = asyncio.run(repo.list_active_matching_items(SimpleNamespace(cursor=lambda: cursor), "u", "p"))
        assert result == rows
        assert len(cursor.calls) == 1


@pytest.mark.parametrize("custom,meta,active,board_key,expected", [
    (False, {}, False, "seed/matching/cutout/a.png", "seed/matching/cutout/a.png"),
    (False, {}, False, None, None),
    (True, {"type": "matchingCutout"}, False, None, "tidy-thumb"),
    (True, {"type": "matchingCutout"}, True, "custom-board.png", "custom-board.png"),
    (True, {}, True, "ignored-catalog-key", None),
    (True, {}, False, "ignored-catalog-key", None),
])
def test_both_candidate_contracts_gate_custom_board_url_on_ready(custom, meta, active, board_key, expected):
    r2 = SimpleNamespace(public_url=lambda key: f"https://assets.test/{key}")
    item = {"id": "a", "name": "옷", "gender": "unisex", "thumb_key": "tidy-thumb",
            "image_key": "generation-image", "is_custom": custom, "image_meta": meta,
            "board_key": board_key, "has_active_cutout_job": active}
    for row in (outfit.item_to_api(r2, item), routes._matching_item_to_api(r2, item, has_active_cutout_job=active)):
        assert row.get("boardUrl", "missing") == (f"https://assets.test/{expected}" if expected else None)
        assert row["imageUrl"] == "https://assets.test/generation-image"
        assert row["thumbnailUrl"] == "https://assets.test/tidy-thumb"


def test_saved_recommendation_get_refreshes_board_art_without_changing_sets_or_selection(client, make_token, state):
    saved, _product, items = state
    saved.update(outfitRecommendation={"status": "ready", "sets": [{"slots": {"bottom": "bottom"}, "reason": "saved"}],
                  "candidates": {"bottom": [{"id": "bottom", "boardUrl": "stale"}]}},
                 matchClothing=[{"id": "bottom", "selected": True, "boardUrl": "stale"}])
    items[0]["board_key"] = "seed/matching/cutout/bottom.png"
    for path in ("analysis", "analysis/outfit-recommendations"):
        response = client.get(f"/v1/projects/p1/{path}", headers=auth_headers(make_token))
        assert response.status_code == 200
        data = response.json()
        expected = client.app.state.r2.public_url(items[0]["board_key"])
        assert data["outfitRecommendation"]["candidates"]["bottom"][0]["boardUrl"] == expected
        assert data["matchClothing"][0]["boardUrl"] == expected
        assert data["outfitRecommendation"]["sets"] == saved["outfitRecommendation"]["sets"]
        assert saved["matchClothing"][0]["boardUrl"] == "stale", "GET must not write stored analysis"
    # A model-only save must not restore the stale art from the persisted snapshot.
    response = client.patch("/v1/projects/p1/analysis", headers=auth_headers(make_token), json={"selectedModelId": "mA"})
    assert response.status_code == 200
    assert response.json()["outfitRecommendation"]["candidates"]["bottom"][0]["boardUrl"] == expected


def test_cached_recommendation_refreshes_board_art_without_new_inference(client, state):
    saved, product, items = state
    outfit.clear_cache()
    before = outfit.start_recommendation(client.app.state.settings, client.app.state.r2,
        product, saved, [(b"source", "image/png")], items)
    items[0]["board_key"] = "seed/matching/cutout/bottom.png"
    after = outfit.start_recommendation(client.app.state.settings, client.app.state.r2,
        product, saved, [(b"source", "image/png")], items)
    assert before["outfitRecommendation"]["contextKey"] == after["outfitRecommendation"]["contextKey"]
    assert before["outfitRecommendation"]["sets"] == after["outfitRecommendation"]["sets"]
    assert after["outfitRecommendation"]["candidates"]["bottom"][0]["boardUrl"].endswith("seed/matching/cutout/bottom.png")
    outfit.clear_cache()
