import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.agents.gemini_image import InlineImage
from app.agents import asset_recipe, cut_generator
from app.services import outfit_generation
from app.workers import mannequin_job


def snapshot(**items):
    return {"version": 1, "slots": {
        role: {"itemId": identifier, "origin": "user"}
        for role, identifier in items.items()
    }}


@pytest.fixture
def app(monkeypatch):
    @asynccontextmanager
    async def connection():
        yield object()

    rows = [
        {"id": role, "clothing_type": kind, "is_active": True,
         "image_asset_id": "asset-" + role, "image_key": "key-" + role,
         "is_custom": False}
        for role, kind in (("inner", "inner"), ("bottom", "bottom"),
                           ("footwear", "shoes"), ("bag", "bag"), ("accessory", "accessory"))
    ]

    async def catalog(conn, user_id, project_id):
        assert (user_id, project_id) == ("user", "project")
        return rows

    monkeypatch.setattr(outfit_generation.repo, "list_active_matching_items", catalog)
    async def asset(conn, user_id, asset_id):
        return {"id": asset_id, "source": "seed", "r2_key": asset_id.replace("asset-", "key-"),
                "mime_type": "image/png"}
    monkeypatch.setattr(outfit_generation.repo, "get_asset_for_user", asset)
    return SimpleNamespace(state=SimpleNamespace(
        pool=SimpleNamespace(connection=connection),
        r2=SimpleNamespace(get_bytes=lambda key: key.encode()),
    )), rows


def test_resolver_uses_authoritative_catalog_assets_and_role_order(app):
    application, rows = app
    selected = snapshot(accessory="accessory", bottom="bottom", bag="bag",
                        footwear="footwear", inner="inner")
    selected["slots"]["inner"]["imageId"] = "attacker-image"
    refs = asyncio.run(outfit_generation.resolve_images(
        application, snapshot=selected, clothing_type="outer", user_id="user", project_id="project"))
    assert [role for role, image in refs] == ["inner", "footwear", "bag", "accessory"]
    assert [image.data for role, image in refs] == [b"key-inner", b"key-footwear", b"key-bag", b"key-accessory"]


def test_mannequin_resolves_inner_only_and_keeps_other_slots_in_snapshot(app):
    application, rows = app
    selected = snapshot(inner="inner", bottom="bottom", footwear="footwear", bag="bag")
    refs = asyncio.run(outfit_generation.resolve_images(
        application, snapshot=selected, clothing_type="outer", user_id="user", project_id="project",
        mannequin_only=True))
    assert [role for role, image in refs] == ["inner"]
    assert selected["slots"]["bag"]["itemId"] == "bag"


@pytest.mark.parametrize("change", ["missing", "inactive", "wrong_role", "custom"])
def test_selected_extra_cannot_silently_use_a_different_item(app, change):
    application, rows = app
    row = next(row for row in rows if row["id"] == "footwear")
    if change == "missing":
        rows.remove(row)
    elif change == "inactive":
        row["is_active"] = False
    elif change == "wrong_role":
        row["clothing_type"] = "bag"
    else:
        row["is_custom"] = True
    with pytest.raises(outfit_generation.OutfitReferenceError, match="footwear"):
        asyncio.run(outfit_generation.resolve_images(
            application, snapshot=snapshot(footwear="footwear"), clothing_type="top",
            user_id="user", project_id="project"))


def test_complement_override_changes_only_the_existing_one_garment_slot():
    selected = snapshot(inner="inner", bottom="bottom", footwear="footwear")
    actual = outfit_generation.with_complement(selected, "outer", ["other-bottom"])
    assert actual["slots"]["bottom"]["itemId"] == "other-bottom"
    assert actual["slots"]["inner"] == selected["slots"]["inner"]
    assert actual["slots"]["footwear"] == selected["slots"]["footwear"]
    assert selected["slots"]["bottom"]["itemId"] == "bottom"
    assert outfit_generation.complement_ids(snapshot(bottom=None), "top", ["legacy"]) == []


def test_source_recipe_and_similar_cut_keep_frozen_outfit():
    selected = snapshot(inner="inner", footwear="footwear")
    recipe = asset_recipe.project_recipe({"cutType": "styling", "outfitSelectionsSnapshot": selected})
    assert recipe["outfitSelectionsSnapshot"] == selected
    recipe["outfitSelectionsSnapshot"]["slots"]["inner"]["itemId"] = "changed"
    assert selected["slots"]["inner"]["itemId"] == "inner"


def test_extra_refs_have_roles_before_and_after_legacy_matching():
    manifest = cut_generator.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=True, mood_count=0,
        outfit_roles=("inner", "footwear", "bag", "accessory"))
    assert manifest.index("OUTFIT INNER") < manifest.index("MATCHING") < manifest.index("OUTFIT FOOTWEAR")
    assert manifest.index("OUTFIT FOOTWEAR") < manifest.index("OUTFIT BAG") < manifest.index("OUTFIT ACCESSORY")


def test_mannequin_manifest_and_edit_include_selected_inner_and_barefoot():
    manifest = mannequin_job._build_manifest(
        [{"slot": "Front"}], True, "outer", outfit_roles=("inner",))
    assert manifest.index("OUTFIT INNER") < manifest.index("matching BOTTOM")
    assert "barefoot" in manifest


def test_parent_and_cached_draft_are_invalidated_when_inner_changes():
    old = snapshot(inner="one", bottom="bottom")
    new = snapshot(inner="two", bottom="bottom")
    profile = {"category": "outer", "gender": "women"}
    parent = {"generation_metadata": {
        "profileCategory": "outer", "profileGender": "women", "matchItemId": "bottom",
        "editDepth": 1, "outfitSelectionsSnapshot": old}}
    assert mannequin_job._compatible_parent_metadata(parent, profile, "bottom", outfit_snapshot=new) is None
    args = dict(clothing_type="outer", base_asset_id="base", product_assets=[("Front", "front")],
                match_asset_id="match", match_item_id="bottom", fit_profile=profile,
                source_mirrored=False, prompt_version="v1")
    assert mannequin_job.mannequin_draft_fingerprint(**args, outfit_snapshot=old) != mannequin_job.mannequin_draft_fingerprint(**args, outfit_snapshot=new)


def test_runtime_catalog_replacement_cannot_change_a_previous_recipe(app):
    application, rows = app
    provenance = []
    refs = asyncio.run(outfit_generation.resolve_images(
        application, snapshot=snapshot(footwear="footwear"), clothing_type="top",
        user_id="user", project_id="project", resolved_assets=provenance))
    assert provenance[0]["assetId"] == "asset-footwear"
    assert provenance[0]["imageSha256"]
    next(row for row in rows if row["id"] == "footwear")["image_asset_id"] = "asset-new"
    with pytest.raises(outfit_generation.OutfitReferenceError):
        asyncio.run(outfit_generation.resolve_images(
            application, snapshot=snapshot(footwear="footwear"), clothing_type="top",
            user_id="user", project_id="project", reference_assets=provenance))


def test_actual_inner_replaces_mannequin_default_white_inner():
    from app.agents import mannequin, prompts
    template = prompts.load_prompt_template(SimpleNamespace(mannequin_prompt_file=""))
    ctx = mannequin.prompt_context(clothing_type="outer", product_count=2, base_gender="women",
                                  image_manifest="1. base\n2. OUTFIT INNER")
    prompt = prompts.render_mannequin_prompt(template, ctx, {}, {})
    assert "SELECTED INNER" in prompt
    assert "DEFAULT TO WHITE" not in prompt


def test_worn_prompt_uses_actual_refs_and_product_has_no_outfit_roles():
    from app.agents.cut_plan import compile_cut_plan
    manifest = cut_generator.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=0,
        outfit_roles=("inner", "footwear", "bag"))
    spec = {"cutType": "horizon", "shot": "full", "direction": "front", "_outfitRoles": ("inner", "footwear", "bag")}
    prompt = cut_generator.build_prompt(spec, {"clothing_type": "outer"}, manifest=manifest)
    assert "SELECTED ACTUAL OUTFIT AUTHORITY" in prompt
    assert "OUTERWEAR INNER — MANNEQUIN ANCHOR" not in prompt
    assert compile_cut_plan(cut_generator.apply_reference_compatibility(cut_generator.normalize_spec(spec)), "outer").to_dict()["outfitRoles"] == ["inner", "footwear", "bag"]
    spec.update(cutType="product", shot="ghost")
    assert "outfitRoles" not in compile_cut_plan(cut_generator.apply_reference_compatibility(cut_generator.normalize_spec(spec)), "outer").to_dict()


def test_closure_prompt_gives_selected_inner_authority_without_default_white():
    from app.agents import garment_edit
    prompt = garment_edit.prompt_for("closure", clothing_type="outer", closure_state="open",
                                     front_closure={"kind": "zipper"}, has_selected_inner=True)
    assert "selected actual inner garment" in prompt
    assert "plain white crew-neck" not in prompt


def test_old_page_asset_uses_original_job_outfit_over_edited_storyboard():
    original = snapshot(footwear="old-shoes")
    result = asset_recipe.recipe_for_asset(
        {"id": "asset", "metadata": {}}, page_map={"asset": "block"},
        storyboard_by_id={"block": {"id": "block", "source": "ai", "cutType": "horizon",
                                    "outfitSelectionsSnapshot": snapshot(footwear="new-shoes")}},
        producing_job={"kind": "detail_page", "payload": {"outfitSelectionsSnapshot": original}})
    assert result["recipe"]["outfitSelectionsSnapshot"] == original


def test_complement_role_is_revalidated_at_generation_even_without_extra_refs(app):
    application, rows = app
    next(row for row in rows if row["id"] == "bottom")["clothing_type"] = "bag"
    with pytest.raises(outfit_generation.OutfitReferenceError, match="bottom"):
        asyncio.run(outfit_generation.resolve_images(
            application, snapshot=snapshot(bottom="bottom"), clothing_type="top",
            user_id="user", project_id="project"))
