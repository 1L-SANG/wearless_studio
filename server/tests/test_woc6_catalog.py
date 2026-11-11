from copy import deepcopy

import pytest

from app.agents.page_templates import CATALOG, get_template, resolve_page_tokens, section_definition, validate_catalog, wear_addition_definition


def test_photo_flow_contract_accepts_valid_grouping():
    for pattern in ("twoColumn", "heroPair"):
        catalog = deepcopy(CATALOG)
        catalog["designs"]["P02-D01"]["layout"] = [{"type": "photoFlow", "slot": "*", "pattern": pattern, "groupBy": "spaceGroup", "aspect": "4/5", "gap": 24, "captions": []}]
        assert validate_catalog(catalog) is catalog


@pytest.mark.parametrize("prop,value", [("pattern", "masonry"), ("groupBy", "color"), ("slot", ""), ("aspect", "source"), ("captions", {})])
def test_photo_flow_rejects_unsupported_policies(prop, value):
    catalog = deepcopy(CATALOG)
    catalog["designs"]["P02-D01"]["layout"] = [{"type": "photoFlow", "slot": "*", "pattern": "twoColumn", "groupBy": "spaceGroup", "aspect": "4/5", "gap": 24, "captions": [], prop: value}]
    with pytest.raises(ValueError, match="photoFlow"):
        validate_catalog(catalog)


@pytest.mark.parametrize("value", [None, {}, {"layout": {"id": "column", "name": ""}, "decoration": {"id": "basic", "name": "기본"}}, {"layout": {"id": "masonry", "name": "기본"}, "decoration": {"id": "basic", "name": "기본"}}])
def test_choice_axes_reject_malformed_values(value):
    catalog = deepcopy(CATALOG)
    catalog["designs"]["P02-D01"]["choiceAxes"] = value
    with pytest.raises(ValueError, match="choice axes"):
        validate_catalog(catalog)


@pytest.mark.parametrize("prop", ["minPhotos", "minColors"])
@pytest.mark.parametrize("value", [None, 0, -1, 1.5, True, "2"])
def test_design_minima_are_positive_integers(prop, value):
    catalog = deepcopy(CATALOG)
    catalog["designs"]["P02-D01"][prop] = value
    with pytest.raises(ValueError, match="design minimum"):
        validate_catalog(catalog)


@pytest.mark.parametrize("patch", [{"fit": "stretch"}, {"fallbackAspect": "0/3"}, {"fallbackAspect": "source"}, {"cropTop": .1}])
def test_natural_photo_rejects_a_second_crop_and_invalid_aspects(patch):
    catalog = deepcopy(CATALOG)
    catalog["designs"]["P02-D01"]["layout"] = [{"type": "photo", "slot": "*", "fit": "contain", "aspect": "source", "fallbackAspect": "2/3"}]
    assert validate_catalog(catalog) is catalog
    catalog["designs"]["P02-D01"]["layout"][0].update(patch)
    with pytest.raises(ValueError, match="photo fit|fallback aspect|contain crop"):
        validate_catalog(catalog)


@pytest.mark.parametrize("template_id", ["emo", "show"])
def test_section_lookup_uses_new_defaults_and_preserves_saved_reuse(template_id):
    template = get_template(template_id)
    assert wear_addition_definition(template, "fit")["designId"] == "WEAR-FIT-COLUMN-01"
    assert wear_addition_definition(template, "styling")["designId"] == "P02-D01"
    assert section_definition(template, f"{template_id}.points", "points")["designId"] == "PT-COLUMN-01"
    assert section_definition(template, f"{template_id}.color_product", "color_product")["designId"] == "CP-COLUMN-01"
    paired = section_definition(template, f"{template_id}.first", "first", design_id="P01-D09-G")
    assert next(slot for slot in paired["photoSlots"] if slot["slotKey"] == "ghost")["fill"] == "reuse"


@pytest.mark.parametrize("template_id", ["emo", "show"])
def test_plain_gothic_preserves_body_and_saved_catalog_version(template_id):
    tokens = resolve_page_tokens({"templateId": template_id, "fontStyle": "plainGothic"})
    assert tokens["fontStyle"] == "plainGothic"
    assert tuple(tokens["fonts"]["labelEn"][key] for key in ("font", "weight", "italic", "uppercase", "trackingEm")) == ("Pretendard", 500, False, True, .12)
    assert tuple(tokens["fonts"]["titleKo"][key] for key in ("font", "weight", "italic", "trackingEm")) == ("Pretendard", 600, False, -.02)
    assert tokens["fonts"]["body"] == get_template(template_id)["fonts"]["body"]
    assert CATALOG["catalogVersion"] == "page-templates-2026-10-28.2"
