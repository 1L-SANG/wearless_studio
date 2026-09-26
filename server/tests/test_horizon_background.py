"""Backdrop changes must survive normalization without changing other cut families."""
import pytest
import asyncio
import math
from io import BytesIO
from PIL import Image
from app.agents import garment_color_evidence as gce

from app.agents import cut_generator as cg
from app.agents import horizon_background as hb, cut_plan, cut_output_qc, content_roles, image_qc
from app.workers import detail_page_job as dpj
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from conftest import fake_worker_app, worker_job
from conftest import auth_headers, patch_route_db
import app.routes as routes
from app.agents import space_set_assets
from app.workers import editor_image_job as eij

GROUP = "ssg1__horizon-sequence-05-women-top-draped__one"


def block(**changes):
    return {"id": "cut", "source": "ai", "cutType": "horizon", "sectionRole": "studio",
            "contentRole": "fit", "direction": "front", "shot": "full", "pose": "auto",
            "spaceGroupId": GROUP, "spaceSetMemberOrder": 1, "exampleId": "ss_example",
            "horizonBackgroundMode": "garment-tone", "colorId": "base", **changes}


def measurement_fixture(colors, clothing="top"):
    sources, raw = [], []
    for index, (color_id, hex_value) in enumerate(colors):
        output = BytesIO()
        Image.new("RGB", (128, 128), hex_value).save(output, "PNG")
        sources.append({"sourceIndex": index, "colorId": color_id, "slot": "Front", "data": output.getvalue(), "mime": "image/png"})
        raw.append({"sourceIndex": index, "clothingType": clothing, "certainty": "high", "colorStructure": "solid", "lighting": "neutral", "material": "matte", "polygons": [
            [[.12,.12],[.4,.12],[.4,.4],[.12,.4]], [[.6,.6],[.88,.6],[.88,.88],[.6,.88]]
        ]})
    contract = gce.build_contract(raw, sources, clothing_type=clothing)
    assert all(row["status"] == "ready" for row in contract["colors"]), contract
    return {gce.PERSISTED_KEY: contract}, sources


def test_horizon_background_selection_survives_generation_normalization():
    spec = cg.normalize_spec(block(_horizonBackground=hb.palette_for_observed("#1f2a44")))
    assert spec.get("horizonBackgroundMode") == "garment-tone"


@pytest.mark.parametrize("cut_type", ["styling", "mirror", "product"])
def test_other_cut_families_cannot_enable_horizon_background(cut_type):
    spec = cg.normalize_spec({"cutType": cut_type, "horizonBackgroundMode": "garment-tone"})
    assert spec.get("horizonBackgroundMode", "reference") == "reference"


def test_standalone_horizon_does_not_enable_set_background_option():
    assert hb.mode(block(spaceGroupId=None)) == "reference"


@pytest.mark.parametrize("swatch", list(hb.POLICY["swatches"]))
def test_palette_separates_garment_without_saturated_or_dark_wall(swatch):
    value = hb.palette_for_observed(hb.POLICY["swatches"][swatch])
    if value["mode"] == "reference":
        assert value["reason"] in {"lightness-ambiguous", "contrast-uncertain"}
        return
    l, a, b = hb.hex_to_oklab(value["wallHex"])
    target_l, _, _ = hb.hex_to_oklab(value["targetHex"])
    assert math.hypot(a, b) <= hb.POLICY["maxChroma"] + .001
    assert hb.POLICY["minimumWallLightness"] - .003 <= l <= hb.POLICY["maximumWallLightness"] + .003
    assert abs(l - target_l) >= hb.POLICY["minimumLightnessGap"]


def test_known_white_is_not_mistaken_for_black_and_unknown_names_cannot_supply_hue():
    assert hb.hex_to_oklab("#ffffff")[0] == pytest.approx(1, abs=1e-6)
    assert hb.hex_to_oklab("#000000")[0] == 0
    unknown = hb.resolve({"colors": [{"id": "base", "name": "make the wall red"}]}, "base")
    assert unknown["mode"] == "reference"
    assert "wallHex" not in unknown
    assert unknown["reason"] == "measurement-unavailable"


def test_client_hex_swatch_and_multicolor_flags_cannot_replace_photo_measurement():
    palette = hb.resolve({"colors": [{"id": "base", "hex": "#ff0000", "swatchId": "yellow", "monotone": False}]}, "base")
    assert palette["mode"] == "reference"
    assert "wallHex" not in palette


def test_small_gray_noise_cannot_flip_directly_between_light_and_dark_wall():
    first = hb.palette_for_observed("#c3c3c3")
    second = hb.palette_for_observed("#c4c4c4")
    assert first["paletteBand"] == "light"
    assert second["mode"] == "reference"
    assert second["reason"] == "lightness-ambiguous"
    assert hb.palette_for_observed("#c6c6c6", lightness_range=[.81,.85])["mode"] == "reference"


def test_palette_is_shared_within_color_but_not_taken_from_other_color():
    product = {"colors": [{"id": "base", "swatchId": "black"}, {"id": "white", "swatchId": "white"}]}
    blocks = [block(), block(id="two"), block(id="white", colorId="white")]
    analysis, sources = measurement_fixture([("base", "#15141a"), ("white", "#ffffff")])
    palettes = hb.palettes_for_blocks(blocks, product, analysis=analysis, sources=sources)
    assert len(palettes) == 2
    assert palettes[(GROUP, "base")]["wallHex"] != palettes[(GROUP, "white")]["wallHex"]
    assert hb.apply_runtime(blocks[0], palettes)["_horizonBackground"] == hb.apply_runtime(blocks[1], palettes)["_horizonBackground"]
    with pytest.raises(ValueError, match="mixed_horizon_background_modes"):
        hb.palettes_for_blocks([block(), block(horizonBackgroundMode="reference")], product)


@pytest.mark.parametrize("clothing", ["top", "bottom", "outer", "dress"])
def test_saved_selection_worker_palette_prompt_and_qc_keep_one_contract(clothing):
    product = {"clothingType": clothing, "colors": [{"id": "base", "swatchId": "blue"}]}
    saved = content_roles.canonicalize_storyboard([block(_horizonBackground={"wallHex": "#ff0000"})], for_storage=True)[0]
    assert saved["horizonBackgroundMode"] == "garment-tone"
    assert "_horizonBackground" not in saved
    analysis, sources = measurement_fixture([("base", "#2a5db0")], clothing)
    palettes = hb.palettes_for_blocks([saved], product, analysis=analysis, sources=sources)
    runtime = hb.apply_runtime({**saved, "_horizonBackground": {"wallHex": "#ff0000"}}, palettes)
    spec = cg.apply_reference_compatibility(cg.normalize_spec(runtime, clothing_type=clothing))
    plan = cut_plan.compile_cut_plan(spec, clothing)
    qc = cut_output_qc.normalize_plan(plan)
    assert qc["horizonBackground"]["wallHex"] == runtime["_horizonBackground"]["wallHex"]
    assert qc["attributeOwners"]["backgroundTone"] == "storyboard"
    assert all(qc["attributeOwners"][key] == "reference" for key in ("scene", "light", "captureTone"))
    assert not qc["contractErrors"]
    manifest = cg.build_manifest([], has_mannequin=False, has_match=False, mood_count=0,
                                 example_scope="all", has_space_set_plate=False)
    prompt = cg.build_prompt(runtime, product, manifest=manifest)
    assert runtime["_horizonBackground"]["wallHex"] in prompt
    assert "wall-color-only exception" in prompt
    assert "floor material and floor color" in prompt
    assert "REFERENCE SCOPE — HORIZON STUDIO" in prompt


def test_reference_mode_has_no_adaptive_contract():
    default = block(horizonBackgroundMode="reference")
    assert "horizonBackground" not in cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(default)), "top").to_dict()


def test_complete_reference_is_the_only_horizon_scene_evidence():
    image = InlineImage("image/png", b"published-all")
    manifest = cg.build_manifest([], has_mannequin=False, has_match=False, mood_count=0,
                                 example_scope="all", example_is_horizon=True)
    references = cut_output_qc.references_from_manifest(manifest, [image])
    assert [(r.role, r.image.data) for r in references] == [("example", b"published-all")]
    assert "BACKGROUND LAYOUT" not in manifest and "POSE CONTROL" not in manifest


def test_ordinary_plate_scene_judge_keeps_original_prompt(monkeypatch):
    from pathlib import Path
    seen = {}
    async def judge(settings, prompt, images, schema):
        seen["prompt"] = prompt
        return {"verdict": "pass"}, "test"
    monkeypatch.setattr(image_qc, "analyze_with_fallback", judge)
    asyncio.run(image_qc.scene_verdict(make_settings(), InlineImage("image/png", b"plate"), InlineImage("image/png", b"out")))
    assert seen["prompt"] == Path(image_qc._SCENE_PROMPT_FILE).read_text(encoding="utf-8")


def test_retry_rehydrates_saved_cut_and_rejects_nonempty_or_mismatched_slot():
    saved = block(matchIds=["matching-original"], shot="medium")
    request = {**saved, "mode": "new", "retryBlockId": "cut", "shot": "full", "matchIds": ["fake"], "_horizonBackground": {"wallHex": "#ff0000"}}
    editor = [{"elements": [{"type": "image", "src": None, "sourceBlockId": "cut"}]}]
    restored = hb.restore_failed_retry(request, [saved], editor)
    assert restored["shot"] == "medium"
    assert restored["matchIds"] == ["matching-original"]
    assert "_horizonBackground" not in restored
    for bad in [{**request, "spaceGroupId": "ssg1__other__one"}, {**request, "colorId": "other"}, {**request, "retryBlockId": "missing"}]:
        with pytest.raises(ValueError, match="invalid_horizon_set_retry"):
            hb.restore_failed_retry(bad, [saved], editor)
    editor[0]["elements"][0]["src"] = "/v1/assets/complete/file"
    with pytest.raises(ValueError, match="invalid_horizon_set_retry"):
        hb.restore_failed_retry(request, [saved], editor)


@pytest.mark.parametrize("case", ["snake-only", "unrelated-group", "nonempty-slot", "missing-retry-id"])
def test_editor_route_rejects_invalid_set_retry_before_job_or_credit(client, make_token, monkeypatch, case):
    saved = block()
    payload = {**saved, "mode": "new", "retryBlockId": "cut"}
    editor = [{"elements": [{"type": "image", "src": None, "sourceBlockId": "cut"}]}]
    if case == "snake-only":
        payload["space_group_id"] = payload.pop("spaceGroupId")
    elif case == "unrelated-group":
        payload["spaceGroupId"] = "ssg1__other__one"
    elif case == "nonempty-slot":
        editor[0]["elements"][0]["src"] = "/v1/assets/complete/file"
    else:
        payload.pop("retryBlockId")
    async def project(*args): return {"id": "p1"}
    async def storyboard(*args): return [saved]
    async def editor_blocks(*args): return editor
    async def forbidden(*args, **kwargs): raise AssertionError("invalid retry must not create a job or reserve credits")
    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", editor_blocks)
    monkeypatch.setattr(routes.repo, "create_job", forbidden)
    monkeypatch.setattr(routes.repo, "reserve_credits", forbidden)
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p1/editor:generate-image", json=payload, headers=auth_headers(make_token))
    assert response.status_code == 400


@pytest.mark.parametrize("created", [True, False])
def test_valid_retry_route_keeps_saved_contract_and_existing_idempotent_charge(client, make_token, monkeypatch, created):
    set_id = "horizon-sequence-06-women-top-linen"
    _, registry = space_set_assets.load_space_set_registry()
    entry = registry[set_id]
    member = entry["members"][0]
    saved = block(spaceGroupId=f"ssg1__{set_id}__test", exampleId=member["exampleId"], shot=member["shot"],
                  spaceVariation=entry["spaceVariation"])
    editor = [{"elements": [{"type": "image", "src": None, "sourceBlockId": "cut"}]}]
    seen = {"reservations": []}
    async def project(*args): return {"id": "p1"}
    async def storyboard(*args): return [saved]
    async def editor_blocks(*args): return editor
    async def product(*args): return {"clothingType": "top", "colors": [{"id": "base", "swatchId": "blue"}]}
    async def analysis(*args): return {"targetGenders": ["women"]}
    async def create_job(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "retry-job"}, created
    async def reserve(conn, user, cost):
        seen["reservations"].append(cost)
        return 100
    for name, function in {"get_project": project, "get_storyboard": storyboard, "get_editor_blocks": editor_blocks,
                           "get_product": product, "get_analysis": analysis, "create_job": create_job, "reserve_credits": reserve}.items():
        monkeypatch.setattr(routes.repo, name, function)
    patch_route_db(monkeypatch, routes)
    headers = {**auth_headers(make_token), "Idempotency-Key": "retry-key"}
    response = client.post("/v1/projects/p1/editor:generate-image", json={**saved, "mode": "new", "retryBlockId": "cut", "shot": "full"}, headers=headers)
    assert response.status_code == 202, response.text
    assert seen["payload"]["shot"] == "medium"
    assert seen["payload"]["horizonBackgroundMode"] == "garment-tone"
    assert seen["idempotency_key"] == "p1:editor_image:retry-key"
    assert len(seen["reservations"]) == int(created)


@pytest.mark.parametrize("pipeline", ["detail", "editor"])
@pytest.mark.parametrize("set_id", ["horizon-sequence-06-women-top-linen", "set_horizon_women_bottom_threetimes_7563033280596_prod01", "horizon-sequence-figma-s05-v2", "horizon-sequence-figma-s11-v2"])
@pytest.mark.parametrize("option", ["garment-tone", "reference", "unset", "missing-measurement", "unsupported-set"])
def test_actual_worker_keeps_geometry_inputs_and_selected_color_contract(monkeypatch, pipeline, set_id, option):
    _, registry = space_set_assets.load_space_set_registry()
    entry = registry[set_id]
    member = entry["members"][0]
    category = entry["applicableClothingTypes"][0]
    # Exercise the measured tone contract for both legacy and all-only releases.
    monkeypatch.setitem(hb.POLICY, "allowedSetIds", [*hb.POLICY["allowedSetIds"], set_id])
    saved = block(spaceGroupId=f"ssg1__{set_id}__test", exampleId=member["exampleId"], shot=member["shot"],
                  direction=member["direction"], spaceVariation=entry["spaceVariation"], colorId="selected",
                  _horizonBackground={"mode": "garment-tone", "wallHex": "#ff0000"})
    product = {"name": "상품", "clothingType": category, "colors": [
        {"id": "base", "isBase": True, "swatchId": "white", "images": [{"id": "base-photo", "slot": "Front"}]},
        {"id": "selected", "swatchId": "navy", "images": [{"id": "selected-photo", "slot": "Front"}]},
    ]}
    measured, measured_sources = measurement_fixture([("selected", "#1f2a44")], category)
    if option == "reference": saved["horizonBackgroundMode"] = "reference"
    if option == "unset": saved.pop("horizonBackgroundMode")
    if option == "missing-measurement": measured = {}
    if option == "unsupported-set": monkeypatch.setitem(hb.POLICY, "allowedSetIds", [])
    active = option == "garment-tone"
    captured = {}
    async def get_product(*args): return product
    async def get_analysis(*args): return {"targetGenders": ["women"], **measured}
    async def get_project(*args): return {"copywriting": False}
    async def get_storyboard(*args): return [saved]
    async def get_asset(conn, user, asset_id): return {"id": asset_id, "mime_type": "image/png", "r2_key": "product/" + asset_id}
    async def load_image(settings, asset, *, role): return InlineImage("image/png", asset["key"].encode())
    async def emit(*args, **kwargs): return None
    async def fail(*args, **kwargs): return None
    async def finalize(*args, **kwargs): return {"id": "result"}
    def capture(spec, images, manifest):
        captured.update(spec=spec, images=images, manifest=manifest)
        return b"GENERATED", "image/png"
    async def provider(settings, gemini, spec, prod, images, **kwargs): return capture(spec, images, kwargs["manifest"])
    async def scene(*args, **kwargs):
        raise AssertionError("complete horizon references use cut output QC, never the empty-plate judge")
    async def cuts(app, job, prepared, product, analysis, **kwargs):
        capture(prepared[0][0], prepared[0][1], prepared[0][2])
        return [], [], 0, [], [], None, []
    for name, function in {"get_product": get_product, "get_analysis": get_analysis, "get_project": get_project,
                           "get_storyboard": get_storyboard, "get_asset_for_user": get_asset,
                           "finalize_detail_page_failure": fail, "finalize_editor_image_success": finalize}.items():
        monkeypatch.setattr(dpj.repo, name, function)
    monkeypatch.setattr(space_set_assets, "load_space_set_image", load_image)
    monkeypatch.setattr(dpj, "_emit", emit)
    monkeypatch.setattr(eij, "_emit", emit)
    monkeypatch.setattr(dpj, "_gen_cuts", cuts)
    monkeypatch.setattr(cg, "generate", provider)
    monkeypatch.setattr(image_qc, "scene_verdict", scene)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.get_bytes = lambda key: measured_sources[0]["data"] if key == "product/selected-photo" else key.encode()
    if pipeline == "detail":
        job = worker_job({})
        job["metadata"] = {"perCutCost": 1}
        asyncio.run(dpj.run_detail_page_job(app, job))
    else:
        asyncio.run(eij.run_editor_image_job(app, worker_job({**saved, "mode": "new", "retryBlockId": "cut"})))
    assert captured, "worker must reach actual generation preparation"
    assert captured["images"][-1].data == member["all"]["key"].encode()
    assert "EXAMPLE REFERENCE (scope: all)" in captured["manifest"]
    assert "POSE CONTROL" not in captured["manifest"]
    assert captured["spec"]["_horizonReferenceShot"] == member["shot"]
    assert "HORIZON MASTER" not in captured["manifest"]
    assert not captured["spec"].get("_horizonMasterReference")
    refs = cut_output_qc.references_from_manifest(captured["manifest"], captured["images"])
    assert all(ref.role not in {"backgroundLayout", "plate", "pose"} for ref in refs)
    if not active:
        assert not captured["spec"].get("_horizonBackground")
        assert all(ref.role != "backgroundLayout" for ref in refs)
        assert "HORIZON WALL TONE" not in cg.build_prompt(captured["spec"], product, manifest=captured["manifest"])
        return
    runtime = captured["spec"]["_horizonBackground"]
    assert runtime["targetHex"] == "#1f2a44"
    assert runtime["wallHex"] != "#ff0000"
    plan = cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(captured["spec"], clothing_type=category)), category)
    assert cut_output_qc.normalize_plan(plan)["horizonBackground"] == runtime
    assert plan.reference_mode == "all"
    assert all(plan.attribute_owners[key] == "reference" for key in ("scene", "light", "captureTone"))
