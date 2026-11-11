import json
from copy import deepcopy

import pytest

from app.agents import page_templates
from app.agents.page_templates import CATALOG, base_color_of, get_template, resolve_page_tokens, validate_catalog, walk_layout


def test_saved_catalog_designs_and_woc6_defaults_without_sns_by_default():
    assert validate_catalog(CATALOG) is CATALOG
    assert len(CATALOG["kinds"]) == 10
    assert (CATALOG["firstTitleBand"]["top"] + CATALOG["firstTitleBand"]["bottom"]) / 2 == .5
    first = CATALOG["designs"]["P01-D09"]["layout"][0]["groups"][0]
    assert first["anchor"] == "center" and first["band"] == "firstTitleBand"
    assert all((slot.get("recipe") or {}).get("examplePrefer") != "topClear" for template in CATALOG["templates"] for definition in [*template["sections"], *template["addPlacements"].values()] for slot in definition["photoSlots"])
    assert CATALOG["photoBudgets"] == {"detail": 4, "maxExtraColors": 3}
    assert get_template("guide") is None
    assert all(slot["count"]["max"] == 3 for template in CATALOG["templates"] for section in template["sections"] for slot in section["photoSlots"] if (slot.get("recipe") or {}).get("detailPool") == "points")
    assert [group["name"] for group in CATALOG["menuGroups"]] == ["사진", "옷 자세히", "상품 설명", "후기와 소통"]
    assert [len([s for s in t["sections"] if s["presence"] == "default"]) for t in CATALOG["templates"]] == [10, 7]
    assert [s["instanceKey"] for s in get_template("show")["sections"] if s["kind"] == "points"] == ["points"]
    assert len(CATALOG["designs"]) == 41
    assert sum(bool(design.get("pickerHidden")) for design in CATALOG["designs"].values()) == 20
    for template in CATALOG["templates"]:
        for section in template["sections"]:
            if section["kind"] == "wear":
                assert section["designId"] == ("WEAR-FIT-COLUMN-01" if section["usage"] == "fit" else "P02-D01")
            if section["kind"] == "color_product":
                assert section["designId"] == "CP-COLUMN-01"
            if section["kind"] == "points":
                assert section["designId"] == "PT-COLUMN-01"
            if section["kind"] == "sns":
                assert section["presence"] == "optional"
            if section["presence"] == "default":
                assert not CATALOG["designs"][section["designId"]].get("pickerHidden")
        assert template["addPlacements"]["wear"]["designId"] == "P02-D01"
        assert template["addPlacements"]["color_product"]["designId"] == "CP-COLUMN-01"
        assert template["addPlacements"]["points"]["designId"] == "PT-COLUMN-01"
        assert template["addPlacements"]["sns"]["designIds"] == ["I09-D14", "P07-D08"]
        assert all(CATALOG["designs"][design_id].get("pickerHidden") for design_id in template["addPlacements"]["sns"]["designIds"])


def _set_path(catalog, path, value):
    keys = path.split("/")
    target = catalog
    for key in keys[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    key = int(keys[-1]) if isinstance(target, list) else keys[-1]
    target[key] = value


INVALID_VALUES = [
    ("designs/P04-D01/usage", None, "design usage"),
    ("designs/P02-D01/usage", "unknown", "design usage"),
    ("designs/P01-D09/usage", "styling", "design usage"),
    ("templates/0/sections/6/designId", "P04-D01", "design kind"),
    ("designs/P01-D09/displayName", None, "design displayName"),
    ("designs/P01-D09/displayName", "", "design displayName"),
    ("designs/P01-D09/displayName", "  ", "design displayName"),
    ("designs/P01-D09/displayName", 12, "design displayName"),
    ("designs/P01-D09/displayName", "x" * 25, "design displayName"),
    ("templates/0/sections/0/photoSlots/0/recipe/examplePrefer", "topClear", "examplePrefer"),
    ("designs/P01-D09/layout/0/groups/0/id", "", "overlay group"),
    ("designs/P01-D09/layout/0/groups/0/anchor", "unknown", "overlay anchor"),
    ("schemaVersion", 2, "schemaVersion"),
    ("kinds/intro/repeatable", True, "repeatable"),
    ("templates/0/sections/6/instanceKey", "first", "instanceKey"),
    ("templates/0/sections/2/usage", "fit", "section usage"),
    ("templates/0/sections/6/usage", "unknown", "section usage"),
    ("designs/T03-D01/layout/0/copyRole", "fit.body", "copyRole"),
    ("templates/0/sections/0/designId", "T03-D01", "design kind"),
    ("templates/0/sections/0/photoSlots/0/recipe/cutType", "product", "cutType"),
    ("templates/0/sections/0/photoSlots/0/recipe/allowedCutTypes", ["styling", "product"], "cutType"),
    ("templates/0/sections/0/photoSlots/0/recipe/shot", "detail", "shot"),
    ("templates/0/sections/0/photoSlots/0/recipe/allowedShots", ["full", "ghost"], "shot"),
    ("templates/0/sections/0/photoSlots/0/recipe/direction", "above", "direction"),
    ("templates/1/sections/0/photoSlots/1/reuseFrom/color", "extra", "reuse"),
    ("kinds/first/textSlots/0/maxChars", None, "maxChars"),
    ("kinds/first/textSlots/1/factPath", "analysis.transparency", "factPath"),
    ("templates/0/fonts/titleKo/font", "Unknown", "font"),
    ("designs/T03-D01/layout/0/color", "red", "color token"),
    ("designs/T03-D01/layout/0/type", "html", "layout"),
    ("templates/0/sections/2/block/invert", True, "invert block"),
    ("templates/0/addPlacements/faq/after", "missing", "placement target"),
    ("textMetrics/lineHeight", 0, "text metric"),
    ("textMetrics/uppercaseAscii", None, "text metric"),
    ("firstTitleBand/top", .9, "first title band"),
    ("templates/0/fonts/body", None, "required fonts"),
    ("templates/0/colors/ink", None, "required colors"),
    ("templates/0/sections/0/photoSlots/0/count", 0, "photo count"),
    ("designs/P01-D09/layout/0/aspect", "0/5", "aspect"),
    ("designs/T03-D01/layout/0/w", -1, "geometry"),
    ("defaults/colorMode", "unknown", "default page style"),
    ("templates/0/addPlacements/sns/designIds", ["P01-D09"], "add design kind"),
]


@pytest.mark.parametrize("path,value,pattern", INVALID_VALUES, ids=[case[0] for case in INVALID_VALUES])
def test_invalid_catalog_values_are_rejected(path, value, pattern):
    catalog = deepcopy(CATALOG)
    _set_path(catalog, path, value)
    with pytest.raises(ValueError, match=pattern):
        validate_catalog(catalog)


@pytest.mark.parametrize("mutation", ["missing_kind", "duplicate_intro", "first_position", "missing_first", "color_name", "front", "back", "review_reuse", "palette"])
def test_invalid_catalog_structure_is_rejected(mutation):
    catalog = deepcopy(CATALOG)
    if mutation == "missing_kind":
        del catalog["kinds"]["sns"]
    elif mutation == "duplicate_intro":
        catalog["templates"][0]["sections"].append({**catalog["templates"][0]["sections"][2], "instanceKey": "intro_2"})
    elif mutation == "first_position":
        catalog["templates"][0]["sections"].reverse()
    elif mutation == "missing_first":
        catalog["templates"][0]["sections"].pop(0)
    elif mutation in ("color_name", "front", "back"):
        for node in walk_layout(catalog["designs"]["CP-EMO-01"]["layout"]):
            if mutation == "color_name" and node.get("copyRole") == "colors.n.name":
                node["copyRole"] = "colors.n.en"
            if node.get("slot") == mutation:
                node["slot"] = "removed"
    elif mutation == "review_reuse":
        catalog["templates"][0]["sections"][1]["photoSlots"][0].update(fill="reuse", recipe=None, reuseFrom={"kind": "wear", "usage": "styling", "slot": "g", "index": 0})
    else:
        del catalog["pointPalette"]["blue"]
    with pytest.raises(ValueError):
        validate_catalog(catalog)


def _luminance(value):
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * coefficient for v, coefficient in zip(channels, (.2126, .7152, .0722)))


def test_point_palette_equals_contract_and_meets_contrast():
    expected = {
        "null": ("#5c5c5c", "#f4f4f4", "#e2e2e2"), "ivory": ("#5b544c", "#f6f3ee", "#ded8d1"), "beige": ("#75603b", "#fbf2e3", "#e5d7c0"),
        "brown": ("#815939", "#fef1e6", "#ebd4c3"), "purple": ("#6c5984", "#f6f0ff", "#ded4ed"),
        "red": ("#87544b", "#fef0ed", "#f0d1cc"), "yellow": ("#716232", "#f8f3e3", "#e1d9c0"),
        "green": ("#446f4f", "#e9f8ec", "#c9e0ce"), "blue": ("#4b648b", "#edf4ff", "#ccdaf1"),
        "navy": ("#536386", "#eef4ff", "#ced9f1"), "pink": ("#845261", "#ffeff3", "#eed0d8"),
    }
    for key, palette in CATALOG["pointPalette"].items():
        assert tuple(palette.values()) == expected.get(key, expected["null"])
        assert 1.05 / (_luminance(palette["accentInk"]) + .05) >= 4.5
        assert (_luminance(palette["accentBg"]) + .05) / (_luminance(palette["accentInk"]) + .05) >= 4.5


def test_token_resolution_uses_base_swatch_and_keeps_body_font():
    before = deepcopy(CATALOG)
    product = {"colors": [{"swatchId": "red"}, {"swatchId": "blue", "isBase": True}]}
    tokens = resolve_page_tokens({"templateId": "emo", "fontStyle": "boldGothic", "colorMode": "point"}, base_color_of(product)["swatchId"])
    assert tokens["colors"]["accentInk"] == CATALOG["pointPalette"]["blue"]["accentInk"]
    assert tokens["fonts"]["titleKo"]["weight"] == 800
    assert tokens["fonts"]["labelEn"]["font"] == "Cal Sans"
    assert tokens["fonts"]["body"]["font"] == "Pretendard"
    assert tokens["fonts"]["body"]["weight"] == 400
    assert resolve_page_tokens({"colorMode": "point"}, "unknown")["colors"]["accentInk"] == CATALOG["pointPalette"]["null"]["accentInk"]
    assert CATALOG == before


def test_invalid_catalog_fails_app_startup_before_connections(tmp_path, monkeypatch):
    from app import main

    invalid = deepcopy(CATALOG)
    del invalid["kinds"]["sns"]
    path = tmp_path / "invalid_templates.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    monkeypatch.setattr(page_templates, "CATALOG_PATH", path)
    monkeypatch.setattr(main, "create_pool", lambda *_: pytest.fail("startup opened a connection before catalog validation"))
    with pytest.raises(ValueError, match="ten section kinds"):
        main.create_app()
