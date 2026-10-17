import asyncio
from copy import deepcopy

from app.agents.gemini_image import InlineImage
from app.services import outfit_generation
from app.workers import detail_page_job, editor_image_job
from conftest import worker_job
from test_detail_page_fallback import _block, _item, _run
from test_editor_image_fallback import runtime
from test_outfit_generation import snapshot
from test_editor_generation_record import recording_runtime


def resolve_stub(monkeypatch):
    calls = []

    async def resolve(app, *, snapshot, clothing_type, user_id, project_id, **kwargs):
        calls.append(deepcopy(snapshot))
        refs = []
        for role in outfit_generation.EXTRA_ROLES:
            identifier = (snapshot or {}).get("slots", {}).get(role, {}).get("itemId")
            if identifier:
                refs.append((role, InlineImage("image/png", identifier.encode())))
                if kwargs.get("resolved_assets") is not None:
                    kwargs["resolved_assets"].append({"role": role, "itemId": identifier,
                                                      "assetId": "asset-" + identifier, "imageSha256": "hash"})
        return refs

    monkeypatch.setattr(outfit_generation, "resolve_images", resolve)
    return calls


def test_detail_generation_uses_frozen_outfit_and_records_actual_roles(monkeypatch):
    calls = resolve_stub(monkeypatch)
    selected = snapshot(bottom="bottom", footwear="old-shoes", bag="old-bag", accessory="old-accessory")
    result = _run(monkeypatch, [_block(matchIds=["bottom"])], [_item("bottom")],
                  analysis={"outfitSelections": snapshot(footwear="new-shoes", bag="new-bag")},
                  job_payload={"outfitSelectionsSnapshot": selected})
    generated = result["generated"]["b1"]
    assert generated["images"] == [b"PRODUCT", b"MATCH-bottom", b"old-shoes", b"old-bag", b"old-accessory"]
    assert calls[0] == selected
    assert result["cut_plans"][0]["outfitRoles"] == ["footwear", "bag", "accessory"]
    metadata = result["success"]["cut_assets"][0]["metadata"]
    assert metadata["generation"]["recipe"]["outfitSelectionsSnapshot"] == selected
    assert metadata["generation"]["recipe"]["outfitReferenceAssets"][0]["itemId"] == "old-shoes"


def test_detail_product_images_have_no_outfit_references(monkeypatch):
    calls = resolve_stub(monkeypatch)
    result = _run(monkeypatch, [_block(cutType="product", shot="ghost", matchIds=[])],
                  job_payload={"outfitSelectionsSnapshot": snapshot(footwear="shoes", bag="bag")})
    assert result["generated"]["b1"]["images"] == [b"PRODUCT"]
    assert calls == []
    assert "outfitRoles" not in result["cut_plans"][0]


def test_editor_new_uses_frozen_outfit_and_keeps_matching_override_separate(runtime, monkeypatch):
    calls = resolve_stub(monkeypatch)
    selected = snapshot(bottom="original-bottom", footwear="old-shoes", bag="old-bag")
    payload = {"mode": "new", "cutType": "horizon", "shot": "full", "direction": "front",
               "colorId": "base", "matchIds": ["override-bottom"], "matchIdsOrigin": "user",
               "outfitSelectionsSnapshot": selected}
    asyncio.run(editor_image_job.run_editor_image_job(
        runtime["app"], worker_job(payload=payload, credits_reserved=19)))
    actual = runtime["generations"][0]
    assert actual["images"] == [b"product", b"asset-override-bottom", b"old-shoes", b"old-bag"]
    assert calls[0]["slots"]["bottom"]["itemId"] == "override-bottom"
    assert runtime["qc"][0]["outfitRoles"] == ["footwear", "bag"]
    recipe = runtime["success"]["image"]["metadata"]["generation"]["recipe"]
    assert recipe["outfitSelectionsSnapshot"]["slots"]["footwear"]["itemId"] == "old-shoes"


def test_closure_edit_reveals_the_original_selected_inner(recording_runtime, monkeypatch):
    from app.agents import garment_edit
    from test_canvas_ai_server import A, SRC, FRONT
    from unittest.mock import AsyncMock

    state = recording_runtime
    state["product"]["clothingType"] = "outer"
    state["analysis"].update(frontClosure=deepcopy(FRONT), outfitSelections=snapshot(inner="new-inner"))
    state["anchor"]["recipe"]["outfitSelectionsSnapshot"] = snapshot(inner="original-inner")
    calls = resolve_stub(monkeypatch)
    generated = []

    async def generate(settings, gemini, source, target, cut_type, **kwargs):
        generated.append(kwargs)
        return b"GENERATED", "image/png"

    monkeypatch.setattr(editor_image_job.repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(garment_edit, "generate", generate)
    asyncio.run(editor_image_job.run_editor_image_job(state["app"], worker_job({
        "mode": "vary", "variationMode": "closure", "source": {"src": SRC},
        "outerClosureState": "open"}, credits_reserved=5)))
    assert "failure" not in state
    assert calls[0]["slots"]["inner"]["itemId"] == "original-inner"
    assert generated[0]["selected_inner_image"].data == b"original-inner"
    assert state["success"]["image"]["metadata"]["generation"]["recipe"]["outfitSelectionsSnapshot"] == snapshot(inner="original-inner")


def test_failed_extra_reference_stays_visible_and_retains_original_retry_recipe(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise outfit_generation.OutfitReferenceError("footwear")

    monkeypatch.setattr(outfit_generation, "resolve_images", unavailable)
    selected = snapshot(footwear="original-shoes")
    result = _run(monkeypatch, [_block()], job_payload={"outfitSelectionsSnapshot": selected})
    assert result["generated"] == {}
    failures = [payload for event, payload in result["events"] if payload.get("status") == "cut_failed"]
    assert failures[0]["reason"] == "outfit_asset_unavailable"
    assert result["failure"]["job_metadata"]["outfitRecipes"]["b1"]["outfitSelectionsSnapshot"] == selected
