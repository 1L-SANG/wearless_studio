"""사진 주소의 자산 기록으로 설정을 찾고 오래된 자료를 순서대로 보완한다."""
import asyncio
from copy import deepcopy
from importlib import import_module

import pytest


A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
FIELDS = {
    "sectionRole", "contentRole", "cutType", "shot", "direction", "sideStyle", "colorId",
    "faceExposure", "modelId", "matchIds", "matchIdsOrigin", "refAssetIds", "refScope",
    "exampleId", "exampleSelectionOrigin", "outerClosureState", "detailTargetId",
    "detailTargetOrigin", "spaceGroupId", "spaceVariation", "spaceSetMemberOrder",
    "horizonBackgroundMode",
}


def module():
    from app.agents import content_roles
    assert hasattr(content_roles, "GENERATION_RECIPE_FIELDS"), "공용 레시피 필드가 필요하다"
    return import_module("app.agents.asset_recipe")


def asset(asset_id=A, **values):
    return {"id": asset_id, "source": "ai", "metadata": {}, **values}


def storyboard(**values):
    return {"id": "original", "source": "ai", "cutType": "styling", "shot": "full",
            "direction": "front", "colorId": "base", **values}


def test_generation_record_wins_and_projects_complete_recipe_without_mutating_input():
    row = asset(metadata={"generation": {"version": 1, "origin": "editor_new",
                "recipeFrom": "self", "sourceAssetId": None,
                "recipe": {"cutType": "horizon", "direction": "side", "colorId": "blue",
                           "pose": "old", "thumb": "view-only"}},
                "facemarket_real_derived": True})
    original = deepcopy(row)
    result = module().recipe_for_asset(row, page_map={A: "original"},
        storyboard_by_id={"original": storyboard()}, producing_job={"payload": storyboard()},
        wardrobe_row={"color_id": "black", "cut_type": "product"})
    assert result["origin"] == "editor_new"
    assert result["recipeFrom"] == "self"
    assert result["recipe"]["direction"] == "side"
    assert set(result["recipe"]) == FIELDS
    assert result["recipe"]["detailTargetId"] is None
    assert result["colorId"] == "blue" and result["realDerived"] is True
    assert row == original


def test_completed_page_result_uses_source_block_and_not_asset_or_slot_copy():
    row = asset(sourceBlockId="wrong")
    result = module().recipe_for_asset(row, page_map={A: "original"},
        storyboard_by_id={"original": storyboard(cutType="horizon"),
                          "wrong": storyboard(cutType="product", shot="ghost")},
        producing_job={"payload": {"mode": "new", "cutType": "mirror"}})
    assert result["origin"] == "page" and result["recipe"]["cutType"] == "horizon"
    assert set(result["recipe"]) == FIELDS
    assert result["recipe"]["sectionRole"] == "studio"
    no_page = module().recipe_for_asset(row, storyboard_by_id={"wrong": storyboard()},
                                      wardrobe_row={"cut_type": "product", "color_id": "base"})
    assert no_page["recipe"] is None and no_page["cutType"] == "product"


@pytest.mark.parametrize("retry", [False, True])
def test_old_editor_request_uses_retry_storyboard_before_requested_fields(retry):
    payload = {"mode": "new", "cutType": "horizon", "colorId": "blue"}
    if retry:
        payload["retryBlockId"] = "original"
    result = module().recipe_for_asset(asset(), producing_job={"payload": payload},
        storyboard_by_id={"original": storyboard(colorId="base")})
    assert result["origin"] == ("editor_retry" if retry else "editor_new")
    assert result["recipe"]["colorId"] == ("base" if retry else "blue")
    assert set(result["recipe"]) == FIELDS


def test_old_similar_request_inherits_only_resolved_anchor_recipe():
    payload = {"mode": "vary", "variationMode": "similar",
               "source": {"src": f"/v1/assets/{B}/file", "cutType": "mirror"},
               "_similarCut": {"sourceAssetId": B, "rootAssetId": B}}
    anchor = {"recipe": {"cutType": "horizon", "colorId": "blue"},
              "cutType": "horizon", "colorId": "blue", "realDerived": False}
    result = module().recipe_for_asset(asset(), producing_job={"payload": payload},
                                       anchor_resolved=anchor)
    assert result["origin"] == "editor_similar" and result["recipeFrom"] == "anchor"
    assert result["sourceAssetId"] == B and result["recipe"]["cutType"] == "horizon"
    unavailable = module().recipe_for_asset(asset(), producing_job={"payload": payload})
    assert unavailable["recipe"] is None


def test_old_similar_planned_retry_prefers_stored_storyboard_recipe():
    payload = {"mode": "vary", "variationMode": "similar", "retryBlockId": "original",
               "source": {"src": f"/v1/assets/{B}/file"}}
    result = module().recipe_for_asset(asset(), producing_job={"payload": payload},
        storyboard_by_id={"original": storyboard(colorId="base")},
        anchor_resolved={"recipe": {"cutType": "mirror", "colorId": "wrong"}})
    assert result["origin"] == "editor_retry" and result["sourceAssetId"] == B
    assert result["recipe"]["colorId"] == "base"


def test_upload_and_unknown_only_expose_wardrobe_cut_and_color():
    wardrobe = {"cut_type": "horizon", "color_id": "blue"}
    upload = module().recipe_for_asset(asset(source="upload"), wardrobe_row=wardrobe)
    assert upload["origin"] == "upload" and upload["upload"] is True
    assert upload["recipe"] is None
    unknown = module().recipe_for_asset(asset(), wardrobe_row=wardrobe)
    assert unknown["recipe"] is None and unknown["upload"] is False
    assert unknown["cutType"] == "horizon" and unknown["colorId"] == "blue"


def test_legacy_page_real_model_provenance_survives_without_asset_marker():
    result = module().recipe_for_asset(asset(), producing_job={"kind": "detail_page", "payload": {
        "_facemarket": {"modelId": "real-model", "licenseId": "license"}}},
        page_map={A: "original"}, storyboard_by_id={"original": storyboard()})
    assert result["realDerived"] is True and result["origin"] == "page"


def test_page_map_parses_capability_urls_and_ignores_current_editor_copies():
    result = module().page_asset_map({"data": [{"elements": [
        {"type": "image", "src": f"https://app.test/v1/assets/{A}/file?token=x", "sourceBlockId": "original"},
        {"type": "image", "src": f"/v1/assets/{B}/file", "sourceBlockId": "second"},
        {"type": "image", "src": f"/other/v1/assets/{C}/file", "sourceBlockId": "bad"},
    ]}]})
    assert result == {A: "original", B: "second"}


class RecipeConn:
    def __init__(self, assets, jobs=None, page=None):
        self.assets = assets
        self.jobs = jobs or {}
        self.page = page
        self.queries = []
        self.rows = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, query, params):
        self.queries.append((query, params))
        if "from assets" in query:
            requested = params[2]
            self.rows = [self.assets[aid] for aid in requested if aid in self.assets]
        elif "kind = 'detail_page'" in query:
            self.rows = [{"result": self.page}] if self.page is not None else []
        elif "kind in ('editor_image', 'detail_page')" in query:
            self.rows = [self.jobs[jid] for jid in params[2] if jid in self.jobs]
        else:
            raise AssertionError(query)

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


def test_db_resolver_reads_one_anchor_hop_and_scopes_all_queries(monkeypatch):
    m = module()
    async def get_storyboard(*args):
        return []
    async def wardrobe(*args):
        return [{"asset_id": B, "cut_type": "horizon", "color_id": "blue"}]
    monkeypatch.setattr(m.repo, "get_storyboard", get_storyboard)
    monkeypatch.setattr(m.repo, "list_wardrobe_images", wardrobe)
    first_payload = {"mode": "vary", "variationMode": "similar",
                     "source": {"src": f"/v1/assets/{B}/file"}}
    second_payload = {"mode": "vary", "variationMode": "similar",
                      "source": {"src": f"/v1/assets/{C}/file"}}
    conn = RecipeConn({A: asset(r2_key=f"users/u/projects/p/ai/job-a/{A}.png"),
                       B: asset(B, r2_key=f"users/u/projects/p/ai/job-b/{B}.png"),
                       C: asset(C, metadata={"generation": {"version": 1,
                                "recipe": {"cutType": "styling"}}})},
                      {"job-a": {"id": "job-a", "payload": first_payload},
                       "job-b": {"id": "job-b", "payload": second_payload}})
    result = asyncio.run(m.resolve_asset_recipes(conn, "u", "p", [A]))
    assert set(result) == {A} and result[A]["recipe"] is None
    assert result[A]["sourceAssetId"] == B and result[A]["colorId"] == "blue"
    asset_reads = [params[2] for query, params in conn.queries if "from assets" in query]
    assert asset_reads == [[A], [B]], "오래된 비슷한 컷은 한 단계까지만 찾는다"
    assert all(query.lstrip().startswith("select") for query, _ in conn.queries)
    assert all(params[:2] == ("u", "p") for _, params in conn.queries)


def test_db_resolver_missing_owned_asset_does_not_expose_other_records(monkeypatch):
    m = module()
    conn = RecipeConn({})
    result = asyncio.run(m.resolve_asset_recipes(conn, "u", "p", [A, "invalid-id"]))
    assert result == {}


def test_db_resolver_uses_done_page_and_producing_real_model_snapshot(monkeypatch):
    m = module()
    async def get_storyboard(*args):
        return [storyboard()]
    async def wardrobe(*args):
        return [{"asset_id": A, "cut_type": "styling", "color_id": "base"}]
    monkeypatch.setattr(m.repo, "get_storyboard", get_storyboard)
    monkeypatch.setattr(m.repo, "list_wardrobe_images", wardrobe)
    conn = RecipeConn({A: asset(r2_key=f"users/u/projects/p/ai/page-job/{A}.png")},
        {"page-job": {"id": "page-job", "kind": "detail_page", "payload": {
            "_facemarket": {"modelId": "real-model", "licenseId": "license"}}}},
        {"data": [{"elements": [{"type": "image", "src": f"/v1/assets/{A}/file",
                                   "sourceBlockId": "original"}]}]})
    record = asyncio.run(m.resolve_asset_recipes(conn, "u", "p", [A]))[A]
    assert record["origin"] == "page" and record["realDerived"] is True
    assert record["recipe"]["colorId"] == "base"
    page_query = next(query for query, _ in conn.queries if "kind = 'detail_page'" in query)
    assert "status = 'done'" in page_query and "finished_at desc" in page_query and "limit 1" in page_query
    assert all("user_id = %s and project_id = %s" in query for query, _ in conn.queries)
