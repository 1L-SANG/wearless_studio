from copy import deepcopy

import pytest

from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_templates import CATALOG, get_template, section_definition, validate_catalog
from page_template_cases import assemble_input, case_input, photos, section


FIRST_PAIRS = [("P01-D09", "P01-D09-G"), ("P01-SHOW-01", "P01-SHOW-01-G"), ("P01-D11", "P01-D01")]
FABRIC_PAIRS = [("T03-D01", "T03-D01-P"), ("T03-D06", "T03-D06-P")]


def _input(template_id="emo", kind="first", design_id="P01-D09-G"):
    value = case_input(f"{template_id}-2-mono")
    for block in value["storyboard"]:
        if block["pageSectionKind"] == "color_product":
            block["productPresentation"] = "ghost"
        if block["pageSectionKind"] == kind:
            block["pageDesignId"] = design_id
    return value


def _front(value):
    return next(block for block in value["storyboard"] if block["pageSectionKind"] == "color_product" and block.get("direction") == "front" and block.get("colorId") == "blue")


def _extra(value, kind="first"):
    key = value["storyboard"][0]["pageSectionKey"].split(".")[0] + "." + kind
    slot = kind + (".ghost" if kind == "first" else ".product")
    return next(photo for photo in photos(section(assemble_input(value), key)) if photo["slotKey"] == slot)


def test_hidden_pairs_keep_existing_base_designs_and_catalog_primitives():
    assert sorted(key for key, design in CATALOG["designs"].items() if design.get("pickerHidden")) == sorted(pair for _, pair in FIRST_PAIRS + FABRIC_PAIRS)
    assert len(CATALOG["designs"]) == 32
    for base, pair in FIRST_PAIRS[:2] + FABRIC_PAIRS:
        assert CATALOG["designs"][pair]["layout"][:-1] == CATALOG["designs"][base]["layout"]
        assert CATALOG["designs"][pair]["layout"][-1]["type"] == "panel"
    assert validate_catalog(CATALOG) is CATALOG


@pytest.mark.parametrize("value", [None, "true", 1, {}])
def test_picker_hidden_rejects_non_boolean_values(value):
    invalid = deepcopy(CATALOG)
    invalid["designs"]["P01-D09"]["pickerHidden"] = value
    with pytest.raises(ValueError, match="pickerHidden"):
        validate_catalog(invalid)


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("base,pair", FIRST_PAIRS + FABRIC_PAIRS)
def test_saved_pairs_are_valid_and_reuse_the_original_without_adding_generation(template_id, base, pair):
    kind = "fabric" if pair.endswith("-P") else "first"
    value = _input(template_id, kind, pair)
    before = deepcopy(value["storyboard"])
    definition = section_definition(get_template(template_id), template_id + "." + kind, kind, design_id=pair)
    slot = next(item for item in definition["photoSlots"] if item["slotKey"] == ("ghost" if kind == "first" else "product"))
    assert slot["fill"] == "reuse"
    assert slot["reuseFrom"] == {"kind": "color_product", "slot": "front", "color": "base"}
    validate_storyboard_v4(value["storyboard"])
    assert _extra(value, kind)["sourceBlockId"] == _front(value)["id"]
    assert value["storyboard"] == before


@pytest.mark.parametrize("mode", [None, "flat", "natural_light"])
def test_first_requires_uniform_explicit_mode_even_on_a_different_face(mode):
    value = _input()
    back = next(block for block in value["storyboard"] if block["pageSectionKind"] == "color_product" and block.get("direction") == "back")
    if mode is None:
        back.pop("productPresentation")
    else:
        back["productPresentation"] = mode
    assert "sourceBlockId" not in _extra(value)


def test_legacy_similar_product_still_participates_in_the_uniform_mode_check():
    value = _input()
    back = next(block for block in value["storyboard"] if block["pageSectionKind"] == "color_product" and block.get("direction") == "back")
    back.update(similarSourceBlockId=_front(value)["id"], productPresentation="flat")
    assert "sourceBlockId" not in _extra(value)


@pytest.mark.parametrize("kind,design_id", [("first", "P01-D09-G"), ("fabric", "T03-D01-P")])
def test_reuse_follows_selected_base_then_the_first_listed_color(kind, design_id):
    value = _input("emo", kind, design_id)
    value["product"]["colors"].reverse()
    assert _extra(value, kind)["sourceBlockId"] == "emo.color_product.front1"
    for color in value["product"]["colors"]:
        color.pop("isBase", None)
    assert _extra(value, kind)["sourceBlockId"] == "emo.color_product.front2"


def test_first_reuse_shares_failure_and_retry_metadata():
    value = _input()
    source_id = _front(value)["id"]
    next(cut for cut in value["cutResults"] if cut["blockId"] == source_id)["imageUrl"] = None
    value["failedCuts"] = {source_id: {"reason": "temporary_error", "retryable": True, "sourceBlockId": "retry-source"}}
    image = _extra(value)
    assert image["sourceBlockId"] == source_id
    assert image["src"] is None
    assert image["genFailed"] is True
    assert image["genFailReason"] == "temporary_error"
    assert image["genFailRetryable"] is True
    assert image["genFailSourceBlockId"] == "retry-source"


@pytest.mark.parametrize("patch", [
    {"pageSectionKey": "emo.other_product", "slotKey": "other_product.front1"},
    {"colorId": "ivory"}, {"direction": "back"}, {"source": "mine", "ownImages": ["seller:front"]},
    {"shot": "detail"}, {"cutType": "styling"}, {"similarSourceBlockId": "other"},
])
def test_first_does_not_borrow_incompatible_or_unrelated_photos(patch):
    value = _input()
    _front(value).update(patch)
    assert "sourceBlockId" not in _extra(value)


def test_dedicated_clean_ghost_survives_storage_and_wins_over_reuse():
    value = _input()
    first = value["storyboard"][0]
    ghost = {"id": "dedicated-first-ghost", "source": "ai", "taxonomyVersion": 4,
             "pageSectionKey": first["pageSectionKey"], "pageSectionKind": "first", "pageDesignId": first["pageDesignId"],
             "sectionId": first["sectionId"], "sectionLayout": "stack", "sectionCustom": True, "slotKey": "first.ghost",
             "cutType": "product", "shot": "ghost", "productPresentation": "ghost", "direction": "front", "colorId": "blue",
             "contentRole": "productOverview", "sectionRole": "product", "faceExposure": None, "matchIds": [], "refScope": "all"}
    value["storyboard"].insert(1, ghost)
    value["storyboard"] = canonicalize_storyboard(value["storyboard"], for_storage=True)
    stored = value["storyboard"][1]
    assert (stored["cutType"], stored["shot"], stored["productPresentation"], stored["contentRole"]) == ("product", "ghost", "ghost", "productOverview")
    assert stored["pageDesignId"] == "P01-D09-G"
    assert _extra(value)["sourceBlockId"] == ghost["id"]


@pytest.mark.parametrize("mode", [None, "flat", "natural_light", "ghost"])
def test_fabric_reuses_base_front_across_product_modes(mode):
    value = _input("emo", "fabric", "T03-D01-P")
    source = _front(value)
    source["productPresentation"] = mode
    assert _extra(value, "fabric")["sourceBlockId"] == source["id"]


def test_fabric_accepts_mine_front_without_a_generation_block_but_not_back():
    value = _input("show", "fabric", "T03-D06-P")
    source = _front(value)
    source.update(source="mine", ownImages=["seller:front"], cutType=None, shot=None)
    assert _extra(value, "fabric")["src"] == "seller:front"
    source["direction"] = "back"
    assert "sourceBlockId" not in _extra(value, "fabric")


@pytest.mark.parametrize("own_images", [[], [""], [None], [42], "seller:front"])
def test_fabric_mine_reuse_requires_a_nonempty_first_image_string(own_images):
    value = _input("emo", "fabric", "T03-D01-P")
    _front(value).update(source="mine", ownImages=own_images, cutType=None, shot=None)
    assert "sourceBlockId" not in _extra(value, "fabric")


@pytest.mark.parametrize("direction", [None, "front", "", "back", "side"])
def test_fabric_mine_front_direction_matches_js_including_legacy_empty_string(direction):
    value = _input("emo", "fabric", "T03-D01-P")
    source = _front(value)
    source.update(source="mine", ownImages=["seller:front"], cutType=None, shot=None, direction=direction)
    image = _extra(value, "fabric")
    assert image.get("sourceBlockId") == (source["id"] if direction in (None, "front") else None)
