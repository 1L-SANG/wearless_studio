from copy import deepcopy

import pytest
from PIL import Image

from app.agents.page_template_contrast import overlay_text_metadata
from app.agents.page_templates import CATALOG, validate_catalog
from page_template_cases import assemble_input, case_input, collect_input_regions, elements


IDS = {"review": ("I09-D06", "I09-SCRIM-01"), "faq": ("T08-D07", "T08-SCRIM-01")}


def overlay_input(kind, design_id=None):
    value = case_input("emo-2-point")
    value["storyboard"] = [card for card in value["storyboard"] if card["pageSectionKind"] == kind]
    for card in value["storyboard"]:
        card["pageDesignId"] = design_id or IDS[kind][0]
    source = next(card for card in value["storyboard"] if card["source"] != "text")
    next(cut for cut in value["cutResults"] if cut["blockId"] == source["id"])["overlayTextColors"] = {f"emo.{kind}:{kind}.background:heading": "#111111"}
    return value


def section_elements(value):
    return elements([block for block in assemble_input(value) if block.get("pageSectionKey")])


@pytest.mark.parametrize("kind", ["review", "faq"])
def test_base_one_photo_full_scrim_and_bottom_alternating_white_bubbles(kind):
    value = overlay_input(kind)
    content = section_elements(value)
    photos = [el for el in content if el["type"] == "image"]
    assert len(photos) == 1
    assert (photos[0]["w"], photos[0]["h"]) == (1000, 1250)
    scrim = next((el for el in content if el.get("fill") == "#000000"), None)
    assert scrim is not None
    assert [scrim[key] for key in ("x", "y", "w", "h", "opacity")] == [0, 0, 1000, 1250, .3]
    bubbles = [el for el in content if el.get("opacity") == .96]
    assert len(bubbles) == 3
    for index, bubble in enumerate(bubbles):
        assert bubble["fill"] == "#ffffff" and bubble["w"] <= 731
        assert bubble["x"] + bubble["w"] == 930 if index % 2 else bubble["x"] == 70
        assert 625 < bubble["y"] and bubble["y"] + bubble["h"] <= 1162.5
        assert bubble["boxShadow"] == "0px 3px 12px rgba(0, 0, 0, 0.18)"
    assert bubbles[-1]["y"] + bubbles[-1]["h"] == 1162.5
    assert next(el for el in content if el.get("copyRole") == "label")["style"]["color"] == "#ffffff"
    assert collect_input_regions(value) == []


@pytest.mark.parametrize("kind", ["review", "faq"])
def test_gradient_alternative_uses_the_same_one_photo_with_white_copy(kind):
    design_id = IDS[kind][1]
    assert design_id in CATALOG["designs"]
    value = overlay_input(kind, design_id)
    content = section_elements(value)
    assert len([el for el in content if el["type"] == "image"]) == 1
    [scrim] = [el for el in content if el["type"] == "shape"]
    assert [scrim[key] for key in ("x", "y", "w", "h")] == [0, 525, 1000, 725]
    assert scrim["fill"] == "linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.72) 100%)"
    assert all(el["style"]["color"] == "#ffffff" for el in content if el["type"] == "text")
    assert collect_input_regions(value) == []


def test_generic_catalog_scrim_controls_geometry_and_fixed_white_suppresses_sampling(monkeypatch):
    design = deepcopy(CATALOG["designs"]["I09-D06"])
    design["id"] = "GENERIC-OVERLAY"
    design["layout"][0].update(aspect="4/5", scrim={"color": "#123456", "opacity": .4, "heightRatio": .5, "gradient": True})
    for group in design["layout"][0]["groups"]:
        group["textColor"] = "#ffffff"
    monkeypatch.setitem(CATALOG["designs"], design["id"], design)
    value = overlay_input("review", design["id"])
    content = section_elements(value)
    scrim = next((el for el in content if el.get("fill", "").startswith("linear-gradient(")), None)
    assert scrim is not None
    assert [scrim["y"], scrim["h"]] == [625, 625]
    assert scrim["fill"] == "linear-gradient(180deg, rgba(18, 52, 86, 0) 0%, rgba(18, 52, 86, 0.4) 100%)"
    assert collect_input_regions(value) == []
    with Image.new("RGB", (40, 50), "white") as image:
        assert overlay_text_metadata(value["storyboard"][0], image, product=value["product"], analysis=value["analysis"],
                                     storyboard=value["storyboard"], copy_results=value["copyResults"]) == {}


@pytest.mark.parametrize("prop,value,pattern", [
    ("scrim", {"color": "black", "opacity": .3}, "overlay scrim"),
    ("scrim", {"color": "#000000", "opacity": 1.1}, "overlay scrim"),
    ("scrim", {"color": "#000000", "opacity": .3, "heightRatio": 0}, "overlay scrim"),
    ("scrim", {"color": "#000000", "opacity": .3, "gradient": "yes"}, "overlay scrim"),
    ("fillOpacity", -.1, "fill opacity"),
    ("itemGap", -1, "list item gap"),
    ("shadow", {"x": 0, "y": 3, "blur": -1, "color": "#000000", "opacity": .18}, "shape shadow"),
])
def test_generic_paint_validation_rejects_invalid_data(prop, value, pattern):
    catalog = deepcopy(CATALOG)
    overlay = catalog["designs"]["I09-D06"]["layout"][0]
    target = overlay if prop == "scrim" else next(node for node in overlay["groups"][0]["nodes"] if node["type"] == "list")
    target[prop] = value
    with pytest.raises(ValueError, match=pattern):
        validate_catalog(catalog)


def test_generic_group_fixed_text_color_validation():
    catalog = deepcopy(CATALOG)
    catalog["designs"]["I09-D06"]["layout"][0]["groups"][0]["textColor"] = "white"
    with pytest.raises(ValueError, match="overlay text color"):
        validate_catalog(catalog)
