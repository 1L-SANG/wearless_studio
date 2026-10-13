"""에디터 성공 기록은 실제 생성 설정과 변주의 원본을 보존한다."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.agents import content_roles
from app.agents.gemini_image import GeminiError, InlineImage
from app.workers import editor_image_job as worker
from conftest import fake_worker_app, make_settings, worker_job
from test_asset_recipe import A, FIELDS


CONTEXT_FILL = {"version": 1, "slot": {"blockId": "block", "elementId": "image"},
                "slotKey": "block:image", "purpose": "coordination", "rule": "section_new",
                "label": "스타일링 · 새 장면", "path": "new", "colorId": "base",
                "anchorAssetId": None, "attempt": 2}


@pytest.fixture
def recording_runtime(monkeypatch):
    state = {"generations": [], "product": {"clothingType": "outer", "colors": [
        {"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "front"}]}]},
        "analysis": {"frontClosure": {"version": 1, "kind": "snap", "range": "binary",
                                      "secondary": None, "evidenceSlots": ["Front"], "note": None}},
        "anchor": {"recipe": {"cutType": "horizon", "colorId": "blue", "shot": "medium",
                              "direction": "side", "outerClosureState": "closed"},
                   "cutType": "horizon", "colorId": "blue", "realDerived": False}}

    async def success(conn, **kwargs):
        state["success"] = kwargs
        return {"id": "output"}

    async def failure(conn, **kwargs):
        state["failure"] = kwargs
        return True

    async def generate(settings, gemini, spec, product, images, **kwargs):
        state["generations"].append(deepcopy(spec))
        if state.get("error"):
            raise state["error"]
        return b"GENERATED", "image/png"

    async def asset(*args):
        return {"id": args[-1], "source": "ai", "r2_key": "input", "mime_type": "image/png",
                "metadata": state.get("source_metadata", {"cut_type": "horizon"})}

    async def recipes(conn, uid, pid, asset_ids):
        state["resolved_ids"] = asset_ids
        return {aid: deepcopy(state["anchor"]) for aid in asset_ids} if state["anchor"] else {}

    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(side_effect=lambda *_: state["product"]))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(side_effect=lambda *_: state["analysis"]))
    monkeypatch.setattr(worker.repo, "get_storyboard", AsyncMock(return_value=[]))
    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", success)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_failure", failure)
    monkeypatch.setattr(worker.cut_generator, "resolve_virtual_model_assets", lambda *_args, **_kw: None)
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(worker.cut_generator, "load_example_image", AsyncMock(return_value=InlineImage("image/png", b"EXAMPLE")))
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    if hasattr(worker, "asset_recipe"):
        monkeypatch.setattr(worker.asset_recipe, "resolve_asset_recipes", recipes)
    state["app"] = fake_worker_app(make_settings(cut_output_qc_mode="off"))
    return state


def run(state, payload):
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job(payload, credits_reserved=19)))
    assert "failure" not in state, state.get("failure")
    return state["success"]["image"]["metadata"]


@pytest.mark.parametrize("extra,origin", [({}, "editor_new"),
    ({"retryBlockId": "failed"}, "editor_retry"),
    ({"_contextFill": CONTEXT_FILL}, "context_fill")])
def test_new_success_records_normalized_binary_closure_and_complete_recipe(recording_runtime, extra, origin):
    request = {"mode": "new", "cutType": "horizon", "colorId": "base", "shot": "full",
               "outerClosureState": "partial", "modelId": "mB", **extra}
    original = deepcopy(request)
    metadata = run(recording_runtime, request)
    assert "generation" in metadata, "성공 자산에 생성 설정 기록이 필요하다"
    record = metadata["generation"]
    assert record["origin"] == origin and record["version"] == 1
    assert record["autoSelected"] is (origin == "context_fill")
    assert record["sourceAssetId"] is None and record["recipeFrom"] == "self"
    assert record["recipe"]["outerClosureState"] == "closed"
    assert record["recipe"]["modelId"] == "mB" and set(record["recipe"]) == FIELDS
    assert request == original
    assert recording_runtime["success"]["charge"] == recording_runtime["success"]["reserved"] == 19
    if origin == "context_fill":
        assert metadata["context_fill"] == {key: value for key, value in CONTEXT_FILL.items() if key != "attempt"}
        assert recording_runtime["success"]["metadata"]["contextFill"] == CONTEXT_FILL


def test_new_record_follows_published_example_direction(recording_runtime):
    metadata = run(recording_runtime, {"mode": "new", "cutType": "horizon", "colorId": "base",
        "shot": "full", "direction": "front", "exampleId": "ex_horizon_men_top_full_back_02"})
    assert metadata.get("generation", {}).get("recipe", {}).get("direction") == "back"


def test_new_record_follows_signature_example_remap(recording_runtime, monkeypatch):
    def repair(specs, **kwargs):
        return [{**specs[0], "exampleId": "ex_horizon_men_top_full_back_02"}], [
            {"fromExampleId": "sig_old", "toExampleId": "ex_horizon_men_top_full_back_02"}]
    monkeypatch.setattr(worker.cut_generator, "repair_signature_cut_selections", repair)
    metadata = run(recording_runtime, {"mode": "new", "cutType": "horizon", "colorId": "base",
                                      "shot": "full", "exampleId": "sig_old"})
    assert metadata.get("generation", {}).get("recipe", {}).get("exampleId") == "ex_horizon_men_top_full_back_02"


@pytest.mark.parametrize("fill,retry,origin", [(False, False, "editor_similar"),
    (True, False, "context_fill"), (False, True, "editor_retry")])
def test_all_similar_successes_inherit_selected_source_recipe_and_wardrobe_color(recording_runtime, monkeypatch, fill, retry, origin):
    envelope = {"version": 1, "rootAssetId": A, "sourceAssetId": A}
    plan = {**envelope, "plan": {"actionKey": "hands-low", "actionSummary": "손을 내리기",
            "instructions": "Lower hands.", "preserve": ["garment"], "avoid": ["relocation"]}}
    monkeypatch.setattr(worker, "_plan_similar_cut", AsyncMock(return_value=(plan, {})))
    monkeypatch.setattr(worker.cut_variator, "generate", AsyncMock(return_value=(b"SIMILAR", "image/png")))
    monkeypatch.setattr(worker.repo, "list_wardrobe_images", AsyncMock(return_value=[
        {"asset_id": A, "color_id": "blue", "cut_type": "horizon"}]))
    recording_runtime["anchor"]["colorId"] = "different-from-wardrobe"
    request = {"mode": "vary", "variationMode": "similar", "_similarCut": envelope,
               "changes": [], "source": {"src": f"/v1/assets/{A}/file", "cutType": "horizon"}}
    if fill:
        request["_contextFill"] = {**CONTEXT_FILL, "path": "similar", "anchorAssetId": A}
    if retry:
        request["retryBlockId"] = "planned-similar"
    metadata = run(recording_runtime, request)
    assert recording_runtime["success"]["group"] == "blue"
    record = metadata["generation"]
    assert record["origin"] == origin
    assert record["sourceAssetId"] == A and record["recipeFrom"] == "anchor"
    assert record["recipe"]["outerClosureState"] == "closed" and set(record["recipe"]) == FIELDS
    assert recording_runtime["resolved_ids"] == [A]


def test_detail_reshoot_records_original_recipe_and_source(recording_runtime, monkeypatch):
    detail = {"version": 1, "shot": "detail", "direction": "back", "colorId": "base",
              "detailTargetId": "pocket", "exampleId": "photography"}
    recording_runtime["source_metadata"] = {"cut_type": "product", "detail_recipe": detail}
    recording_runtime["anchor"] = {"recipe": {"cutType": "product", **detail}, "colorId": "base"}
    monkeypatch.setattr(worker.detail_shot_runtime, "generate", AsyncMock(return_value=(InlineImage("image/png", b"DETAIL"), {})))
    metadata = run(recording_runtime, {"mode": "vary", "source": {"src": f"/v1/assets/{A}/file", "cutType": "product"}})
    assert metadata["generation"]["origin"] == "editor_reshoot"
    assert metadata["generation"]["sourceAssetId"] == A
    assert metadata["generation"]["recipeFrom"] == "anchor"
    assert metadata["generation"]["recipe"]["detailTargetId"] == "pocket"
    assert recording_runtime["success"]["group"] == "base"


def test_new_detail_record_combines_canonical_payload_and_verified_detail_recipe(recording_runtime, monkeypatch):
    monkeypatch.setattr(worker.detail_shot_runtime, "generate", AsyncMock(return_value=(InlineImage("image/png", b"DETAIL"), {})))
    metadata = run(recording_runtime, {"mode": "new", "cutType": "product", "shot": "detail",
        "colorId": "base", "direction": "back", "detailTargetId": "pocket", "detailTargetOrigin": "auto",
        "faceExposure": "show", "matchIds": ["should-not-attach"]})
    record = metadata["generation"]["recipe"]
    assert record["detailTargetId"] == "pocket" and record["direction"] == "back"
    assert record["detailTargetOrigin"] == "auto" and record["faceExposure"] is None
    assert record["matchIds"] == [] and set(record) == FIELDS


def test_generation_failure_releases_reserved_amount_without_publishing_or_charge(recording_runtime):
    recording_runtime["error"] = GeminiError("temporary")
    asyncio.run(worker.run_editor_image_job(recording_runtime["app"], worker_job({
        "mode": "new", "cutType": "horizon", "colorId": "base", "_contextFill": CONTEXT_FILL},
        credits_reserved=19)))
    assert "success" not in recording_runtime
    assert recording_runtime["failure"]["reserved"] == 19
    assert "charge" not in recording_runtime["failure"]
