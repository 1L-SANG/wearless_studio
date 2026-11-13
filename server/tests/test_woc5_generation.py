"""WOC5 owned first ghosts use the existing product generation contract, offline."""
import asyncio
from copy import deepcopy

import pytest

from app.agents import content_roles, cut_generator, product_button_qc
from app.agents.cut_plan import compile_cut_plan
from app.workers.detail_page_job import _duplicate_source_indexes
from conftest import make_settings
from page_template_cases import case_input


FIRST_PAIRS = ("P01-D01", "P01-D09-G", "P01-SHOW-01-G")


def dedicated_input(template="emo", design="P01-D09-G"):
    value = case_input(f"woc5-{template}-first-flat-dedicated")
    for block in value["storyboard"]:
        if block["pageSectionKind"] == "first":
            block["pageDesignId"] = design
    return value


def ghost_of(value):
    return next(block for block in value["storyboard"] if block.get("slotKey") == "first.ghost")


@pytest.mark.parametrize("template", ["emo", "show"])
@pytest.mark.parametrize("design", FIRST_PAIRS)
def test_owned_first_ghost_stores_and_normalizes_as_guarded_product(template, design):
    value = dedicated_input(template, design)
    before = deepcopy(value["storyboard"])
    ghost = ghost_of(value)
    base = next(color for color in value["product"]["colors"] if color.get("isBase"))
    expected = {
        "id": ghost["id"], "source": "ai", "taxonomyVersion": 4,
        "pageSectionKey": f"{template}.first", "pageSectionKind": "first",
        "pageDesignId": design, "slotKey": "first.ghost", "cutType": "product",
        "shot": "ghost", "productPresentation": "ghost", "direction": "front",
        "colorId": base["id"], "contentRole": "productOverview", "sectionRole": "product",
        "matchIds": [], "refImages": [], "refAssetIds": [], "faceExposure": None,
        "angle": "same", "refScope": "all",
    }
    assert {key: ghost.get(key) for key in expected} == expected
    forbidden = {
        "pose", "poseLabel", "poseThumb", "exampleId", "exampleChoice",
        "exampleSelectionOrigin", "spaceGroupId", "spaceVariation", "spaceSetMemberOrder",
        "setSelectionOrigin", "horizonBackgroundMode", "similarSourceBlockId", "ownImages",
    }
    assert forbidden.isdisjoint(ghost)
    canonical = content_roles.canonicalize_storyboard(value["storyboard"], for_storage=True)
    stored = next(block for block in canonical if block["id"] == ghost["id"])
    assert {key: stored.get(key) for key in expected} == expected
    assert value["storyboard"] == before
    runtime = cut_generator.apply_reference_compatibility(
        cut_generator.normalize_spec(stored, clothing_type="top"))
    assert runtime["productPresentation"] == "ghost"
    assert runtime["direction"] == "front"
    assert runtime["colorId"] == base["id"]
    assert runtime["exampleId"] is None and runtime["spaceGroupId"] is None
    assert runtime["refAssetIds"] == runtime["matchIds"] == []
    assert runtime["faceExposure"] is None
    assert product_button_qc.eligible(runtime)
    plan = compile_cut_plan(runtime, "top").to_dict()
    assert plan["productPresentation"] == "ghost"
    assert plan["referenceMode"] == "none"
    assert plan["referenceAllowedAttributes"] == []


def test_first_ghost_dispatches_through_product_button_guard(monkeypatch):
    value = dedicated_input()
    ghost = ghost_of(value)
    seen = []

    class NoDirectProvider:
        async def generate_content_image(self, *args, **kwargs):
            pytest.fail("first ghosts must not bypass the product button guard")

    async def guarded(settings, provider, request, images, manifest, **kwargs):
        seen.append(request.spec)
        return b"guarded-test-output", "image/png"

    monkeypatch.setattr(product_button_qc, "generate_guarded", guarded)
    result = asyncio.run(cut_generator.generate(
        make_settings(), NoDirectProvider(), ghost, value["product"], [],
        manifest="1. PRODUCT — front view of the garment"))
    assert result == (b"guarded-test-output", "image/png")
    assert len(seen) == 1 and seen[0]["productPresentation"] == "ghost"


@pytest.mark.parametrize("source_slots", [[], ["Back"], ["Detail", "BackDetail"]])
def test_first_ghost_requires_selected_base_front_before_any_provider(monkeypatch, source_slots):
    value = dedicated_input()
    ghost = ghost_of(value)
    product = {"clothingType": "top", "colors": [
        {"id": ghost["colorId"], "isBase": True,
         "images": [{"id": f"base-{slot}", "slot": slot} for slot in source_slots]},
        {"id": "other-color", "images": [{"id": "other-front", "slot": "Front"}]},
    ]}

    class NoProvider:
        async def generate_content_image(self, *args, **kwargs):
            pytest.fail("a missing selected Front must stop before image generation")

    async def no_guard(*args, **kwargs):
        pytest.fail("a missing selected Front must stop before product-button inspection")

    monkeypatch.setattr(product_button_qc, "generate_guarded", no_guard)
    with pytest.raises(ValueError, match="product_source_photo_required"):
        asyncio.run(cut_generator.generate(make_settings(), NoProvider(), ghost, product, []))


@pytest.mark.parametrize("mode,expected", [
    ("ghost", [None, 0]), ("flat", [None, None]), ("natural_light", [None, None]),
])
def test_dedicated_ghost_uses_existing_unique_generation_identity_across_runs(mode, expected):
    value = dedicated_input()
    ghost = ghost_of(value)
    source = deepcopy(next(block for block in value["storyboard"]
        if block["pageSectionKind"] == "color_product"
        and block.get("direction") == "front" and block.get("colorId") == ghost["colorId"]))
    # The original run is gone; a surviving alternate run cannot be borrowed by
    # the first slot, but identical recipes still share one generated image.
    source.update(id="other-front", pageSectionKey="emo.color_product_other",
                  sectionId="other-product", slotKey="color_product_other.front",
                  productPresentation=mode, angle="same")
    board = [block for block in value["storyboard"] if block["pageSectionKind"] != "color_product"] + [source]
    canonical = content_roles.canonicalize_storyboard(board, for_storage=True)
    selected = [next(block for block in canonical if block["id"] == block_id)
                for block_id in (ghost["id"], source["id"])]
    assert _duplicate_source_indexes(selected, "top") == expected
