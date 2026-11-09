"""The target color is applied only after the complete representative-color render."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
import pytest


def product():
    return {"clothingType": "top", "colors": [
        {"id": "base", "isBase": True, "images": [
            {"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"},
            {"id": "detail", "slot": "Detail"}]},
        {"id": "blue", "images": [{"id": "blue-front", "slot": "Front"}]},
        {"id": "empty", "images": []}]}


def test_worn_plan_uses_full_base_evidence_and_retains_target_recipe():
    from app.agents import garment_color
    source = {"id": "cut", "cutType": "styling", "colorId": "blue", "shot": "full"}
    original = deepcopy(source)
    plan = garment_color.plan_for(product(), source)
    assert plan.mode == "base_then_color"
    assert plan.target_color_id == "blue" and plan.render_color_id == "base"
    assert plan.target_asset_ids == ("blue-front",)
    assert garment_color.render_spec(source, plan)["colorId"] == "base"
    assert source == original


@pytest.mark.parametrize("cut_type,color,expected", [
    ("styling", "base", "base"), ("styling", None, "base"), ("product", "blue", "blue")])
def test_base_and_product_cuts_render_directly(cut_type, color, expected):
    from app.agents import garment_color
    plan = garment_color.plan_for(product(), {"cutType": cut_type, "colorId": color})
    assert plan.mode == "direct" and plan.render_color_id == expected


@pytest.mark.parametrize("color,reason", [("deleted", "color_unknown"), ("empty", "color_reference_unavailable")])
def test_explicit_unknown_or_unphotographed_color_fails(color, reason):
    from app.agents import garment_color
    with pytest.raises(garment_color.GarmentColorError) as error:
        garment_color.plan_for(product(), {"cutType": "styling", "colorId": color})
    assert error.value.reason == reason


def test_color_stage_uses_actual_photo_once(monkeypatch):
    from app.agents import garment_color
    from app.agents.gemini_image import InlineImage
    call = AsyncMock(return_value=(b"TARGET", "image/png"))
    monkeypatch.setattr(garment_color.garment_edit, "generate", call)
    plan = garment_color.plan_for(product(), {"cutType": "styling", "colorId": "blue"})
    result = asyncio.run(garment_color.apply(object(), object(), InlineImage("image/png", b"MASTER"),
        InlineImage("image/png", b"REAL-BLUE"), plan, cut_type="styling", clothing_type="top", front_closure={}))
    assert result == (b"TARGET", "image/png")
    args, kwargs = call.call_args
    assert args[2].data == b"MASTER" and args[3].data == b"REAL-BLUE"
    assert kwargs["mode"] == "color"


def test_color_stage_propagates_cancellation_and_never_returns_master(monkeypatch):
    from app.agents import garment_color
    from app.agents.gemini_image import InlineImage
    plan = garment_color.plan_for(product(), {"cutType": "styling", "colorId": "blue"})
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(garment_color.garment_edit, "generate", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(garment_color.apply(None, None, InlineImage("image/png", b"MASTER"),
            InlineImage("image/png", b"BLUE"), plan, cut_type="styling", clothing_type="top", front_closure={}))


def test_detail_pipeline_renders_base_then_applies_target_without_publishing_master(monkeypatch):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    calls = []
    block = {"id": "cut", "source": "ai", "cutType": "styling", "shot": "full", "colorId": "blue"}
    original = deepcopy(block)
    target = worker.InlineImage("image/png", b"BLUE-PHOTO")
    base = worker.InlineImage("image/png", b"BASE-PHOTO")
    async def generate(settings, provider, spec, product, images, **kwargs):
        calls.append(("render", spec["colorId"], [im.data for im in images]))
        return b"BASE-MASTER", "image/png"
    async def color(settings, provider, master, photograph, plan, **kwargs):
        calls.append(("color", master.data, photograph.data))
        return b"FINAL-BLUE", "image/png"
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(garment_color, "apply", color)
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    app = fake_worker_app(make_settings())
    app.state.r2.put_bytes = lambda key, data, *a, **kw: calls.append(("upload", data, kw))
    result = asyncio.run(worker._gen_cuts(app, worker_job(), [(block, [base], "product", False, [base])],
        product(), {}, color_context={"cut": {"plan": garment_color.plan_for(product(), block),
            "target_asset": {"id": "blue-front"}, "target_image": target}}))
    assert calls[0] == ("render", "base", [b"BASE-PHOTO"])
    assert calls[1] == ("color", b"BASE-MASTER", b"BLUE-PHOTO")
    assert len(result[0]) == len(result[1]) == 1
    asset = result[1][0]
    assert asset["metadata"]["generation"]["recipe"]["colorId"] == "blue"
    assert asset["metadata"]["colorExecution"]["colorCheck"]["status"] == "not_run"
    assert len(asset["internal_masters"]) == 1
    assert asset["internal_masters"][0]["sha256"] != asset["sha256"]
    assert block == original


def test_detail_color_failure_never_stores_or_previews_master(monkeypatch):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    block = {"id": "cut", "source": "ai", "cutType": "styling", "shot": "full", "colorId": "blue"}
    image = worker.InlineImage("image/png", b"BASE")
    monkeypatch.setattr(worker.cut_generator, "generate", AsyncMock(return_value=(b"MASTER", "image/png")))
    monkeypatch.setattr(garment_color, "apply", AsyncMock(side_effect=garment_color.GarmentColorError("color_application_failed")))
    emit = AsyncMock()
    monkeypatch.setattr(worker, "_emit", emit)
    app = fake_worker_app(make_settings())
    app.state.r2.put_bytes = lambda *a, **kw: pytest.fail("A failed target must not upload a master")
    failed = {}
    result = asyncio.run(worker._gen_cuts(app, worker_job(), [(block, [image], "product", False, [image])],
        product(), {}, failed_cuts=failed, color_context={"cut": {
            "plan": garment_color.plan_for(product(), block), "target_asset": {"id": "blue-front"}, "target_image": image}}))
    assert result[0] == result[1] == []
    assert failed["cut"]["reason"] == "color_application_failed"
    assert not any(call.args[3].get("previewUrl") for call in emit.call_args_list if len(call.args) > 3)


def test_checkpoint_target_and_evidence_change_cannot_reuse_previous_final(monkeypatch):
    from app.workers import cut_checkpoints as checkpoints
    from test_detail_cut_checkpoints import _app, _IntentDB, _job, STYLING, PRODUCT_IMG
    async def scenario():
        db = _IntentDB()
        db.install(monkeypatch)
        app = _app()
        first = await checkpoints.CutCheckpointStore.open(app, _job("one"), identity="same")
        color = {"version": 1, "mode": "base_then_color", "targetColorId": "blue",
                 "targetEvidence": [{"assetId": "photo", "sha256": "a" * 64}]}
        original = await first.for_cut(STYLING, app.state.settings, product(), [PRODUCT_IMG], {},
            real_identity_attached=False, color_execution=color)
        await original.save_master(b"MASTER", "image/png")
        await original.save_final(b"FINAL", "image/png", {"colorExecution": color, "warnings": [], "facePass": {}})
        db.error_jobs.add("one")
        retry = await checkpoints.CutCheckpointStore.open(app, _job("two"), identity="same")
        reused = await retry.for_cut(STYLING, app.state.settings, product(), [PRODUCT_IMG], {},
            real_identity_attached=False, color_execution=color)
        assert reused.final.image == b"FINAL" and reused.master.image == b"MASTER"
        changed = await retry.for_cut(STYLING, app.state.settings, product(), [PRODUCT_IMG], {},
            real_identity_attached=False, color_execution={**color, "targetEvidence": [{"assetId": "photo", "sha256": "b" * 64}]})
        assert changed.final is None
        missing_key = reused.master.key
        app.state.r2.delete(missing_key)
        missing = await retry.for_cut(STYLING, app.state.settings, product(), [PRODUCT_IMG], {},
            real_identity_attached=False, color_execution=color)
        assert missing.final is None, "A target checkpoint without its master must not be republished"
    asyncio.run(scenario())


@pytest.mark.parametrize("cut_type", ["styling", "product"])
def test_editor_new_loads_base_for_worn_but_target_for_product_and_fences_master(monkeypatch, cut_type):
    from app.workers import editor_image_job as worker
    from app.agents import garment_color
    from conftest import fake_worker_app, make_settings, worker_job
    calls, finalized = [], {}
    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product()))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value={}))
    async def asset(conn, uid, aid):
        return {"id": aid, "r2_key": aid, "mime_type": "image/png"}
    async def project_asset(conn, uid, pid, aid):
        return await asset(conn, uid, aid)
    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", project_asset)
    async def generate(settings, provider, spec, product, images, **kw):
        calls.append(("render", spec["colorId"], [im.data for im in images]))
        return b"MASTER", "image/png"
    async def apply(settings, provider, master, target, plan, **kw):
        calls.append(("color", master.data, target.data))
        return b"FINAL", "image/png"
    async def success(conn, **kwargs):
        finalized.update(kwargs)
        calls.append(("finalize", conn))
        return {"id": "done"}
    async def create(conn, **kwargs):
        calls.append(("master_asset", conn, kwargs))
        return {"id": kwargs["asset_id"]}
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(garment_color, "apply", apply)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", success)
    monkeypatch.setattr(worker.repo, "create_asset", create)
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    app = fake_worker_app(make_settings())
    app.state.r2.get_bytes = lambda key: key.encode()
    app.state.r2.put_bytes = lambda *a, **kw: None
    job = worker_job({"mode": "new", "cutType": cut_type,
                      "shot": "full" if cut_type == "styling" else "ghost", "colorId": "blue"})
    asyncio.run(worker.run_editor_image_job(app, job))
    assert calls[0][0:2] == ("render", "base" if cut_type == "styling" else "blue")
    assert calls[0][2] == ([b"front", b"back", b"detail"] if cut_type == "styling" else [b"blue-front"])
    assert finalized["group"] == "blue" and finalized["charge"] == job["credits_reserved"]
    assert finalized["image"]["metadata"]["generation"]["recipe"]["colorId"] == "blue"
    if cut_type == "styling":
        assert calls[1] == ("color", b"MASTER", b"blue-front")
        final_index = next(i for i, call in enumerate(calls) if call[0] == "finalize")
        master_index = next(i for i, call in enumerate(calls) if call[0] == "master_asset")
        assert master_index > final_index and calls[master_index][1] is calls[final_index][1]
        assert calls[master_index][2]["metadata"]["internalGarmentMaster"] is True
    else:
        assert not any(call[0] in ("color", "master_asset") for call in calls)


@pytest.mark.parametrize("owned", [True, False])
def test_stored_master_lookup_is_project_scoped_with_old_asset_fallback(monkeypatch, owned):
    from app.agents import asset_recipe
    final_id, master_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    selected = {"id": final_id, "source": "ai", "metadata": {"colorExecution": {
        "version": 1, "masterAssetId": master_id, "masterSha256": "a" * 64}}}
    master = {"id": master_id, "source": "ai", "metadata": {"internalGarmentMaster": True, "sha256": "a" * 64}}
    lookup = AsyncMock(return_value=master if owned else None)
    monkeypatch.setattr(asset_recipe.repo, "get_project_asset_for_user", lookup)
    result = asyncio.run(asset_recipe.resolve_color_master_asset(object(), "user", "project", selected))
    assert result == (master if owned else selected)
    lookup.assert_awaited_once_with(lookup.call_args.args[0], "user", "project", master_id)
    old = {"id": final_id, "source": "upload", "metadata": {}}
    assert asyncio.run(asset_recipe.resolve_color_master_asset(object(), "user", "project", old)) == old


def test_editor_color_edit_uses_stored_original_master_and_keeps_target_record(monkeypatch):
    from app.workers import editor_image_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    final_id, master_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    import hashlib
    master_bytes = b"ORIGINAL-MASTER"
    selected = {"id": final_id, "source": "ai", "r2_key": "old-colored", "mime_type": "image/png",
        "metadata": {"cut_type": "styling", "colorExecution": {"version": 1, "masterAssetId": master_id,
            "masterSha256": hashlib.sha256(master_bytes).hexdigest()}}}
    master = {"id": master_id, "source": "ai", "r2_key": "master", "mime_type": "image/png",
        "metadata": {"internalGarmentMaster": True, "sha256": hashlib.sha256(master_bytes).hexdigest()}}
    target = {"id": "blue-front", "source": "upload", "r2_key": "target", "mime_type": "image/png"}
    async def asset(conn, uid, pid, aid):
        return {final_id: selected, master_id: master, "blue-front": target}.get(aid)
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", asset)
    monkeypatch.setattr(worker.asset_recipe, "resolve_asset_recipes", AsyncMock(return_value={final_id: {
        "cutType": "styling", "recipe": {"cutType": "styling", "colorId": "base"}}}))
    monkeypatch.setattr(worker.repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product()))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value={}))
    edited = AsyncMock(return_value=(b"NEW-BLUE", "image/png"))
    monkeypatch.setattr(worker.garment_edit, "generate", edited)
    finalized = AsyncMock(return_value={"id": "done"})
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", finalized)
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    app = fake_worker_app(make_settings())
    app.state.r2.get_bytes = lambda key: {"old-colored": b"PREVIOUS-COLOR", "master": master_bytes, "target": b"REAL-BLUE"}[key]
    asyncio.run(worker.run_editor_image_job(app, worker_job({"mode": "vary", "variationMode": "color", "colorId": "blue",
        "source": {"src": f"/v1/assets/{final_id}/file", "cutType": "styling"}, "changes": []})))
    assert edited.call_args.args[2].data == master_bytes
    result = finalized.call_args.kwargs
    assert result["group"] == "blue"
    assert result["image"]["metadata"]["colorExecution"]["masterAssetId"] == master_id
    assert result["image"]["metadata"]["generation"]["sourceAssetId"] == final_id


def test_extra_worn_color_requires_an_actual_front_reference():
    from app.agents import garment_color
    source = product()
    source["colors"][1]["images"] = [{"id": "blue-detail", "slot": "Detail"}]
    with pytest.raises(garment_color.GarmentColorError, match="color_reference_unavailable"):
        garment_color.plan_for(source, {"cutType": "styling", "colorId": "blue"})
    assert garment_color.plan_for(source, {"cutType": "product", "colorId": "blue"}).mode == "direct"


def test_color_application_rejects_changed_dimensions(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from app.agents import garment_color
    from app.agents.gemini_image import InlineImage
    def png(size):
        output = BytesIO()
        Image.new("RGB", size).save(output, format="PNG")
        return output.getvalue()
    monkeypatch.setattr(garment_color.garment_edit, "generate", AsyncMock(return_value=(png((16, 16)), "image/png")))
    with pytest.raises(garment_color.GarmentColorError, match="color_application_failed"):
        asyncio.run(garment_color.apply(None, None, InlineImage("image/png", png((12, 16))),
            InlineImage("image/png", png((12, 16))),
            garment_color.plan_for(product(), {"cutType": "styling", "colorId": "blue"}),
            cut_type="styling", clothing_type="top", front_closure={}))


@pytest.fixture
def editor_flow(monkeypatch):
    from types import SimpleNamespace
    from app.workers import editor_image_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    state = SimpleNamespace(calls=[], accepted=True, fail_upload=False, fail_stage=False)
    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product()))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value={}))
    async def asset(conn, uid, aid):
        return {"id": aid, "r2_key": aid, "mime_type": "image/png"}
    async def project_asset(conn, uid, pid, aid):
        return await asset(conn, uid, aid)
    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", project_asset)
    monkeypatch.setattr(worker.cut_generator, "generate", AsyncMock(return_value=(b"MASTER", "image/png")))
    async def color(*args, **kwargs):
        if state.fail_stage == "cancel":
            raise asyncio.CancelledError()
        if state.fail_stage:
            raise worker.garment_color.GarmentColorError("color_application_failed")
        return b"FINAL", "image/png"
    monkeypatch.setattr(worker.garment_color, "apply", color)
    async def success(conn, **kwargs):
        state.calls.append(("finalize", conn, kwargs))
        return {"id": "done"} if state.accepted else None
    async def create(conn, **kwargs):
        state.calls.append(("master_asset", conn, kwargs))
        return {"id": kwargs["asset_id"]}
    monkeypatch.setattr(worker.repo, "create_asset", create)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", success)
    state.failure = AsyncMock(return_value=True)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_failure", state.failure)
    state.emit = AsyncMock()
    monkeypatch.setattr(worker, "_emit", state.emit)
    state.app = fake_worker_app(make_settings())
    state.app.state.r2.get_bytes = lambda key: key.encode()
    def upload(key, data, *args, **kwargs):
        if data == b"FINAL" and state.fail_upload:
            raise RuntimeError("storage down")
        state.calls.append(("upload", key, data))
    state.app.state.r2.put_bytes = upload
    state.app.state.r2.delete = lambda key: state.calls.append(("delete", key))
    state.app.state.r2.head = lambda key: None
    state.job = worker_job({"mode": "new", "cutType": "styling", "shot": "full", "colorId": "blue"})
    state.run = lambda: asyncio.run(worker.run_editor_image_job(state.app, state.job))
    return state


def test_editor_lease_loss_never_persists_master_and_cleans_both_objects(editor_flow):
    flow = editor_flow
    flow.accepted = False
    flow.run()
    assert not any(call[0] == "master_asset" for call in flow.calls)
    uploaded = {call[1] for call in flow.calls if call[0] == "upload"}
    deleted = {call[1] for call in flow.calls if call[0] == "delete"}
    assert len(uploaded) == 2 and deleted == uploaded


def test_editor_storage_failure_cleans_internal_master_without_charging(editor_flow):
    flow = editor_flow
    flow.fail_upload = True
    flow.run()
    assert not any(call[0] in ("finalize", "master_asset") for call in flow.calls)
    assert [call[1] for call in flow.calls if call[0] == "delete"] == [call[1] for call in flow.calls if call[0] == "upload"]
    assert flow.failure.call_args.kwargs["reserved"] == flow.job["credits_reserved"]


@pytest.mark.parametrize("reason", [True, "cancel"])
def test_editor_color_failure_preserves_cancellation_and_publishes_no_master(editor_flow, reason):
    flow = editor_flow
    flow.fail_stage = reason
    if reason == "cancel":
        with pytest.raises(asyncio.CancelledError):
            flow.run()
    else:
        flow.run()
    assert not any(call[0] in ("upload", "finalize", "master_asset") for call in flow.calls)
    assert flow.failure.call_args.kwargs["reserved"] == flow.job["credits_reserved"]


@pytest.fixture
def detail_flow(monkeypatch):
    from types import SimpleNamespace
    from app.workers import detail_page_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    from storyboard_v4_cases import v4_blocks
    state = SimpleNamespace(calls=[], accepted=True, missing_target=False)
    state.board = v4_blocks([{"id": "cut", "source": "ai", "contentRole": "fit", "sectionRole": "fit",
                              "cutType": "horizon", "shot": "full", "colorId": "blue"}])
    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={"copywriting": False}))
    monkeypatch.setattr(worker.repo, "get_storyboard", AsyncMock(side_effect=lambda *_a: state.board))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product()))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value={}))
    async def asset(conn, uid, aid):
        return {"id": aid, "r2_key": aid, "mime_type": "image/png"}
    async def project_asset(conn, uid, pid, aid):
        return None if state.missing_target else await asset(conn, uid, aid)
    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", project_asset)
    async def generate(settings, provider, spec, prod, images, **kwargs):
        state.calls.append(("render", spec, images, kwargs))
        return b"MASTER", "image/png"
    async def recolor(settings, provider, master, target, plan, **kwargs):
        state.calls.append(("color", master, target, plan))
        return b"FINAL", "image/png"
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(worker.garment_color, "apply", recolor)
    async def success(conn, **kwargs):
        state.calls.append(("finalize", conn, kwargs))
        return {"data": []} if state.accepted else None
    async def create(conn, **kwargs):
        state.calls.append(("master_asset", conn, kwargs))
        return {"id": kwargs["asset_id"]}
    monkeypatch.setattr(worker.repo, "create_asset", create)
    monkeypatch.setattr(worker.repo, "finalize_detail_page_success", success)
    state.failure = AsyncMock(return_value=True)
    monkeypatch.setattr(worker.repo, "finalize_detail_page_failure", state.failure)
    monkeypatch.setattr(worker.page_assembler, "assemble", lambda *a, **kw: [])
    state.emit = AsyncMock()
    monkeypatch.setattr(worker, "_emit", state.emit)
    state.app = fake_worker_app(make_settings())
    state.app.state.r2.get_bytes = lambda key: key.encode()
    state.app.state.r2.put_bytes = lambda key, data, *a, **kw: state.calls.append(("upload", key, data, kw))
    state.app.state.r2.delete = lambda key: state.calls.append(("delete", key))
    state.app.state.r2.head = lambda key: None
    state.job = worker_job()
    state.run = lambda: asyncio.run(worker.run_detail_page_job(state.app, state.job))
    return state


@pytest.mark.parametrize("accepted", [True, False])
def test_detail_finalizer_persists_internal_master_only_after_lease_fence(detail_flow, accepted):
    flow = detail_flow
    flow.accepted = accepted
    flow.run()
    final = next(call for call in flow.calls if call[0] == "finalize")
    assert len(final[2]["cut_assets"]) == 1 and final[2]["charge"] == flow.job["credits_reserved"]
    assert final[2]["cut_assets"][0]["metadata"]["generation"]["recipe"]["colorId"] == "blue"
    masters = [call for call in flow.calls if call[0] == "master_asset"]
    if accepted:
        assert len(masters) == 1 and masters[0][1] is final[1]
        assert flow.calls.index(masters[0]) > flow.calls.index(final)
        assert not any(call[0] == "delete" for call in flow.calls)
    else:
        assert masters == []
        assert {call[1] for call in flow.calls if call[0] == "delete"} == {
            call[1] for call in flow.calls if call[0] == "upload"}


def test_detail_missing_target_front_fails_before_spending_on_master(detail_flow):
    flow = detail_flow
    flow.missing_target = True
    flow.run()
    assert not any(call[0] in ("render", "color", "upload", "finalize", "master_asset") for call in flow.calls)
    assert flow.failure.await_count == 1
    reasons = [call.args[3].get("reason") for call in flow.emit.call_args_list if len(call.args) > 3]
    assert "color_reference_unavailable" in reasons


def test_confirmed_preparation_sees_base_color_and_base_evidence_for_extra_color(detail_flow, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from app.workers import detail_page_job as worker
    flow = detail_flow
    flow.job["payload"] = {"modelId": "mB"}
    flow.board[0]["exampleId"] = None
    monkeypatch.setattr(worker.confirmed_gpt_runtime, "profile_requested", lambda spec: True)
    refs = tuple({"key": key, "mime": "image/png", "byteLength": len(key.encode()),
                  "sha256": hashlib.sha256(key.encode()).hexdigest()} for key in ("face-sheet", "body-sheet"))
    monkeypatch.setattr(worker.cut_generator, "resolve_confirmed_gpt_direction_sheets", lambda spec: refs)
    captured = {}
    def packet(spec, **kwargs):
        captured.update(spec=spec, **kwargs)
        return SimpleNamespace(prompt_input="FROZEN-CONFIRMED", images=tuple(image for _, image in kwargs["seller_images"]), manifest="BASE")
    monkeypatch.setattr(worker.confirmed_gpt_runtime, "build_packet", packet)
    flow.run()
    assert captured["uses_base_color"] is True and captured["spec"]["colorId"] == "base"
    assert [image.data for _, image in captured["seller_images"]] == [b"front", b"back", b"detail"]
    generated = next(call for call in flow.calls if call[0] == "render")
    assert generated[3]["confirmed_prompt_input"] == "FROZEN-CONFIRMED"
    final = next(call for call in flow.calls if call[0] == "finalize")
    assert final[2]["cut_assets"][0]["metadata"]["generation"]["recipe"]["colorId"] == "blue"


@pytest.mark.parametrize("size,expected_request", [((1024, 1536), "1024x1536"), ((1001, 1501), "992x1488")])
def test_color_stage_preserves_master_canvas_independent_of_configured_tier(size, expected_request):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from app.agents import garment_color
    from app.agents.gemini_image import InlineImage
    from conftest import make_settings
    def png(dimensions):
        out = BytesIO()
        Image.new("RGB", dimensions).save(out, format="PNG")
        return out.getvalue()
    calls = []
    class Provider:
        async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
            calls.append(kwargs["openai_size_override"])
            w, h = map(int, kwargs["openai_size_override"].split("x"))
            return SimpleNamespace(image=png((w, h)), mime="image/png")
    output, _ = asyncio.run(garment_color.apply(
        make_settings(model_image_high="gpt-image-2", detail_cut_image_size="2K"), Provider(),
        InlineImage("image/png", png(size)), InlineImage("image/png", png(size)),
        garment_color.plan_for(product(), {"cutType": "styling", "colorId": "blue"}),
        cut_type="styling", clothing_type="top", front_closure={}))
    assert calls == [expected_request]
    with Image.open(BytesIO(output)) as image:
        assert image.size == size


@pytest.mark.parametrize("status", ["failed", "unavailable", "indeterminate", "not_run", "passed"])
def test_page_color_check_uses_three_authorities_and_only_explicit_failure_blocks(detail_flow, monkeypatch, status):
    from app.agents import garment_color_qc
    flow = detail_flow
    checked = AsyncMock(return_value={"status": status, "method": "ai_visual"})
    monkeypatch.setattr(garment_color_qc, "check", checked)
    flow.run()
    args = checked.call_args.args
    assert [image.data for image in args[1:4]] == [b"MASTER", b"blue-front", b"FINAL"]
    final = [call for call in flow.calls if call[0] == "finalize"]
    if status == "failed":
        assert final == []
        failure = flow.failure.call_args.kwargs["metadata"]
        assert failure["failedCuts"][0]["colorCheck"]["status"] == "failed"
        assert not any(call[0] == "upload" for call in flow.calls)
        assert any(call.args[3].get("reason") == "color_check_failed" for call in flow.emit.call_args_list if len(call.args) > 3)
    else:
        assert final[0][2]["cut_assets"][0]["metadata"]["colorExecution"]["colorCheck"]["status"] == status
        warnings = final[0][2]["metadata"].get("warnings") or []
        if status in ("unavailable", "indeterminate"):
            assert any(warning["code"] == f"color_check_{status}" for warning in warnings)


@pytest.mark.parametrize("status", ["failed", "unavailable", "not_run", "passed"])
def test_editor_color_check_records_status_and_rejects_structural_drift(editor_flow, monkeypatch, status):
    from app.agents import garment_color_qc
    flow = editor_flow
    monkeypatch.setattr(garment_color_qc, "check", AsyncMock(return_value={"status": status, "method": "ai_visual"}))
    flow.run()
    finals = [call for call in flow.calls if call[0] == "finalize"]
    if status == "failed":
        assert finals == [] and flow.failure.await_count == 1
        assert flow.failure.call_args.kwargs["metadata"]["colorExecution"]["colorCheck"]["status"] == "failed"
        assert not any(call[0] == "upload" for call in flow.calls)
    else:
        assert finals[0][2]["image"]["metadata"]["colorExecution"]["colorCheck"]["status"] == status


def test_real_internal_master_has_private_storage_and_license_record_without_wardrobe(monkeypatch):
    from contextlib import asynccontextmanager
    from app.services import garment_color as storage
    from app.agents.gemini_image import InlineImage
    from app.r2 import PRIVATE_NO_STORE
    from conftest import fake_worker_app, make_settings, worker_job
    app, job = fake_worker_app(make_settings()), worker_job()
    uploads = []
    app.state.r2.put_bytes = lambda *a, **kw: uploads.append((a, kw))
    monkeypatch.setattr(storage.repo, "create_ai_output_cleanup_intent", AsyncMock(return_value="cleanup"))
    staged = []
    master = asyncio.run(storage.stage_master(app, job, InlineImage("image/png", b"REAL-MASTER"),
        cut_type="horizon", real_derived=True, stored_outputs=staged))
    assert uploads[0][1]["cache"] == PRIVATE_NO_STORE and staged == [master]
    created, cleared, recorded = AsyncMock(return_value={}), AsyncMock(), AsyncMock()
    monkeypatch.setattr(storage.repo, "create_asset", created)
    monkeypatch.setattr(storage.repo, "clear_ai_output_cleanup_intent", cleared)
    monkeypatch.setattr(storage.repo, "insert_output_records", recorded)
    class Connection:
        @asynccontextmanager
        async def cursor(self):
            yield object()
    conn = Connection()
    asyncio.run(storage.persist_masters(conn, job=job, outputs=[{
        "internal_masters": [master], "provenance": {"license_id": "license", "model_id": "model"}}]))
    assert created.call_args.args[0] is conn
    metadata = created.call_args.kwargs["metadata"]
    assert metadata["facemarket_real_derived"] is True and metadata["internalGarmentMaster"] is True
    assert recorded.call_args.kwargs["records"][0]["license_id"] == "license"
    assert recorded.call_args.kwargs["records"][0]["asset_id"] == master["asset_id"]
    assert cleared.call_args.args == (conn, "cleanup")


def test_master_cancellation_settles_put_before_cleanup_can_clear_its_intent(monkeypatch):
    import threading
    from app.services import garment_color as storage
    from app.agents.gemini_image import InlineImage
    from conftest import fake_worker_app, make_settings, worker_job
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    objects, intents, staged = {}, {}, []
    app = fake_worker_app(make_settings())
    async def intent(conn, *, job_id, r2_key):
        intents["intent"] = r2_key
        return "intent"
    monkeypatch.setattr(storage.repo, "create_ai_output_cleanup_intent", intent)
    def upload(key, data, *args, **kwargs):
        started.set()
        release.wait(5)
        objects[key] = data
        finished.set()
    app.state.r2.put_bytes = upload
    async def caller():
        try:
            await storage.stage_master(app, worker_job(), InlineImage("image/png", b"PRIVATE-REAL"),
                cut_type="horizon", real_derived=True, stored_outputs=staged)
        except asyncio.CancelledError:
            for candidate in staged:
                objects.pop(candidate["key"], None)
                intents.pop(candidate["cleanup_intent_id"], None)
            raise
    async def scenario():
        task = asyncio.create_task(caller())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(.02)
            waited_for_put = not task.done()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 2)
            assert waited_for_put, "Cleanup must not run while a canceled PUT can still recreate the object"
            assert objects == {} and intents == {}
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_planned_extra_color_similar_cut_varies_base_master_then_colors_its_own_pose(monkeypatch):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker
    from test_storyboard_similar_cuts import setup_generation, source, child, item
    from conftest import worker_job
    app, seen = setup_generation(monkeypatch)
    original, followup = source(colorId="blue"), child(colorId="blue")
    target = worker.InlineImage("image/png", b"ACTUAL-BLUE")
    color = {"plan": garment_color.plan_for(product(), original), "target_asset": {"id": "blue-front"}, "target_image": target}
    stages = []
    async def apply(settings, provider, master, photo, plan, **kwargs):
        stages.append((master.data, photo.data))
        return b"BLUE-" + master.data, "image/png"
    monkeypatch.setattr(garment_color, "apply", apply)
    result = asyncio.run(worker._gen_cuts(app, worker_job(), [item(original), item(followup)], product(), {},
        color_context={"original": color, "next": color}))
    assert seen["plans"][0][0] == seen["vary"][0][0] == b"GENERATED-ORIGINAL"
    assert stages == [(b"GENERATED-ORIGINAL", b"ACTUAL-BLUE"), (b"CHILD-moment_1", b"ACTUAL-BLUE")]
    source_asset, child_asset = result[1]
    assert child_asset["metadata"]["similar_cut"]["rootAssetId"] == source_asset["asset_id"]
    assert child_asset["metadata"]["generation"]["recipe"]["colorId"] == "blue"
    assert child_asset["metadata"]["colorExecution"]["masterAssetId"] != source_asset["metadata"]["colorExecution"]["masterAssetId"]
    import hashlib
    assert child_asset["internal_masters"][0]["sha256"] == hashlib.sha256(b"CHILD-moment_1").hexdigest()


@pytest.mark.parametrize("color_status", ["passed", "failed", "unavailable"])
@pytest.mark.parametrize("render_color,target_color", [("base", "blue"), ("blue", "green"), ("blue", "base")])
def test_editor_similar_uses_stored_base_master_then_persists_its_varied_master(monkeypatch, color_status, render_color, target_color):
    import hashlib
    from app.workers import editor_image_job as worker
    from conftest import fake_worker_app, make_settings, worker_job
    from test_similar_cut_editor import PLAN
    root_id, master_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    target_id = "front" if target_color == "base" else f"{target_color}-front"
    root = {"id": root_id, "source": "ai", "r2_key": "root-final", "mime_type": "image/png", "metadata": {
        "cut_type": "styling", "colorExecution": {"version": 1, "mode": "base_then_color", "targetColorId": target_color,
        "baseColorId": "base", "renderColorId": render_color, "masterAssetId": master_id,
        "masterSha256": hashlib.sha256(b"ORIGINAL-MASTER").hexdigest(),
        "targetEvidence": [{"assetId": target_id, "sha256": hashlib.sha256(b"BLUE-PHOTO").hexdigest()}]}}}
    master = {"id": master_id, "source": "ai", "r2_key": "master", "mime_type": "image/png", "metadata": {"internalGarmentMaster": True}}
    target = {"id": target_id, "source": "upload", "r2_key": "target", "mime_type": "image/png"}
    monkeypatch.setattr(worker.repo, "get_project_asset_for_user", AsyncMock(side_effect=lambda conn, uid, pid, aid:
        {root_id: root, master_id: master, target_id: target}.get(aid)))
    current_product = product()
    current_product["colors"].append({"id": "green", "images": [{"id": "green-front", "slot": "Front"}]})
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=current_product))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "list_wardrobe_images", AsyncMock(return_value=[{"asset_id": root_id, "color_id": target_color}]))
    monkeypatch.setattr(worker.asset_recipe, "resolve_asset_recipes", AsyncMock(return_value={root_id: {
        "cutType": "styling", "recipe": {"cutType": "styling", "colorId": target_color}}}))
    envelope = {"version": 1, "rootAssetId": root_id, "sourceAssetId": root_id}
    planned = AsyncMock(return_value=({**envelope, "plan": PLAN}, {}))
    varied = AsyncMock(return_value=(b"VARIED-BASE", "image/png"))
    colored = AsyncMock(return_value=(b"VARIED-BLUE", "image/png"))
    monkeypatch.setattr(worker, "_plan_similar_cut", planned)
    monkeypatch.setattr(worker.cut_variator, "generate", varied)
    monkeypatch.setattr(worker.garment_color, "apply", colored)
    monkeypatch.setattr(worker.garment_color_qc, "check", AsyncMock(return_value={"status": color_status}))
    failure = AsyncMock(return_value=True)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_failure", failure)
    finalize, create = AsyncMock(return_value={"id": "done"}), AsyncMock(return_value={})
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", finalize)
    monkeypatch.setattr(worker.repo, "create_asset", create)
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    app = fake_worker_app(make_settings())
    app.state.r2.get_bytes = lambda key: {"root-final": b"OLD-COLORED", "master": b"ORIGINAL-MASTER", "target": b"BLUE-PHOTO"}[key]
    asyncio.run(worker.run_editor_image_job(app, worker_job({"mode": "vary", "variationMode": "similar", "changes": [],
        "source": {"src": f"/v1/assets/{root_id}/file", "cutType": "styling"}, "_similarCut": envelope})))
    assert planned.call_args.args[2].data == varied.call_args.args[2].data == b"ORIGINAL-MASTER"
    assert colored.call_args.args[2].data == b"VARIED-BASE"
    if color_status == "failed":
        assert finalize.await_count == create.await_count == 0
        assert failure.call_args.kwargs["metadata"]["colorExecution"]["colorCheck"]["status"] == "failed"
        return
    result = finalize.call_args.kwargs
    assert result["image"]["metadata"]["colorExecution"]["colorCheck"]["status"] == color_status
    assert result["image"]["metadata"]["colorExecution"]["renderColorId"] == render_color
    assert result["image"]["metadata"]["colorExecution"]["mode"] == "base_then_color"
    assert result["group"] == target_color and result["image"]["metadata"]["similar_cut"]["rootAssetId"] == root_id
    assert result["image"]["metadata"]["generation"]["sourceAssetId"] == root_id
    assert result["image"]["internal_masters"][0]["sha256"] == hashlib.sha256(b"VARIED-BASE").hexdigest()


def test_extra_color_similar_checkpoint_reuses_target_only_with_same_evidence_and_master(monkeypatch):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker, cut_checkpoints
    from test_storyboard_similar_cuts import setup_generation, source, child, item
    from test_detail_cut_checkpoints import _IntentDB
    from conftest import worker_job
    app, seen = setup_generation(monkeypatch)
    db = _IntentDB()
    db.install(monkeypatch)
    original, followup = source(colorId="blue"), child(colorId="blue")
    applied = []
    async def apply(settings, provider, master, target, plan, **kwargs):
        applied.append((master.data, target.data))
        return target.data + b"-" + master.data, "image/png"
    monkeypatch.setattr(garment_color, "apply", apply)
    async def run(job_id, evidence):
        job = {**worker_job(), "id": job_id}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE")
        color = {"plan": garment_color.plan_for(product(), original), "target_asset": {"id": "blue-front"},
                 "target_image": worker.InlineImage("image/png", evidence)}
        result = await worker._gen_cuts(app, job, [item(original), item(followup)], product(), {},
            color_context={"original": color, "next": color}, cut_checkpoints=store)
        db.error_jobs.add(job_id)
        return result
    first = asyncio.run(run("first", b"BLUE-A"))
    second = asyncio.run(run("second", b"BLUE-A"))
    assert len(seen["vary"]) == 1 and len(applied) == 2
    assert second[1][1]["metadata"]["colorExecution"]["masterAssetId"]
    third = asyncio.run(run("third", b"BLUE-B"))
    assert len(seen["vary"]) == 2 and len(applied) == 4
    assert app.state.r2.get_bytes(third[1][1]["key"]).startswith(b"BLUE-B-")
    assert first[1][1]["metadata"]["generation"]["recipe"]["colorId"] == "blue"


def test_similar_child_color_check_failure_keeps_original_target_and_no_child_master(monkeypatch):
    from app.agents import garment_color, garment_color_qc
    from app.workers import detail_page_job as worker
    from test_storyboard_similar_cuts import setup_generation, source, child, item
    from conftest import worker_job
    app, seen = setup_generation(monkeypatch)
    original, followup = source(colorId="blue"), child(colorId="blue")
    async def apply(settings, provider, master, target, plan, **kwargs):
        return b"BLUE-" + master.data, "image/png"
    async def check(settings, master, target, output, **kwargs):
        return {"status": "failed" if master.data.startswith(b"CHILD") else "passed"}
    monkeypatch.setattr(garment_color, "apply", apply)
    monkeypatch.setattr(garment_color_qc, "check", check)
    color = {"plan": garment_color.plan_for(product(), original), "target_asset": {"id": "blue-front"},
             "target_image": worker.InlineImage("image/png", b"BLUE")}
    failed = {}
    result = asyncio.run(worker._gen_cuts(app, worker_job(), [item(original), item(followup)], product(), {},
        color_context={"original": color, "next": color}, failed_cuts=failed))
    assert len(result[0]) == len(result[1]) == 1
    assert result[0][0]["blockId"] == "original" and failed["next"]["reason"] == "color_check_failed"
    assert failed["next"]["colorCheck"]["status"] == "failed"
    assert len(result[1][0]["internal_masters"]) == 1
