"""WOC2 round 3 keeps native pixel caps and accepts the owner's narrower regions."""
from copy import deepcopy

import pytest

from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
from test_woc2_point_sources import board


GEOMETRY = [
    ({"width": 96, "height": 144}, {"iw": 96, "ih": 144, "ox": 0, "oy": 0}),
    ({"width": 40, "height": 1000}, {"iw": 40, "ih": 1000, "ox": 0, "oy": 66}),
    ({"width": 1000, "height": 40}, {"iw": 1000, "ih": 40, "ox": 376, "oy": 0}),
    ({"width": 1024, "height": 1536}, {"iw": 1024, "ih": 1536, "ox": 390, "oy": 216}),
    ({"width": 1024, "height": 1365, "dimensionsKnown": True}, {"iw": 1024, "ih": 1365, "ox": 390, "oy": 168}),
    ({"width": 1536, "height": 2048}, {"iw": 1427, "ih": 1902, "ox": 628, "oy": 319}),
    ({"width": 3072, "height": 4608}, {"iw": 1427, "ih": 2140, "ox": 628, "oy": 385}),
    ({}, {"iw": 1427, "ih": 2140, "ox": 628, "oy": 385}),
    ({"width": 1024, "height": 1365, "dimensionsKnown": False}, {"iw": 1427, "ih": 1902, "ox": 628, "oy": 319}),
    ({"width": -4, "height": 0}, {"iw": 1427, "ih": 2140, "ox": 628, "oy": 385}),
]


def narrow_board(region=None):
    blocks = board()
    blocks[1]["pointItems"] = [blocks[1]["pointItems"][0]]
    blocks[1]["pointItems"][0]["cropRegion"] = region if region is not None else [.44, .20, .30, .16]
    return blocks


@pytest.mark.parametrize("meta,expected", GEOMETRY)
def test_worn_crop_uses_30_percent_width_and_never_upscales_measured_pixels(meta, expected):
    blocks = narrow_board()
    before = deepcopy(blocks)
    page = assemble_v4(blocks, [{"blockId": "wear-photo", "imageUrl": "/worn.png", **meta}], [],
                       {"clothingType": "top", "colors": [{"id": "base", "isBase": True}]}, True)
    image = next(element for run in page for element in run["elements"] if element.get("pointItemKind") == "wornCrop")
    assert (image["w"], image["h"]) == (428, 428)
    assert image["crop"] == expected
    assert image["w"] / image["crop"]["iw"] >= .3 - .0001
    if meta.get("width", 0) > 0 and meta.get("height", 0) > 0 and meta.get("dimensionsKnown") is not False:
        assert image["crop"]["iw"] <= meta["width"]
        assert image["crop"]["ih"] <= meta["height"]
    assert blocks == before


@pytest.mark.parametrize("region", [[.36, .20, .28, .17], [.1, .1, .01, .01]])
def test_positive_narrow_regions_validate_without_rewriting_saved_values(region):
    blocks = narrow_board(region)
    before = deepcopy(blocks)
    validate_storyboard_v4(blocks)
    assert canonicalize_storyboard(blocks)[1]["pointItems"][0]["cropRegion"] == region
    assert blocks == before


@pytest.mark.parametrize("region", [
    [0, 0, 0, .1], [0, 0, .3, 0], [.8, 0, .28, .2], [0, .9, .28, .2],
    [-.1, .2, .28, .2], [0, 0, float("inf"), .2], [0, 0, float("nan"), .2], [0, 0, "0.28", .2],
])
def test_narrow_region_validation_retains_finite_positive_size_and_bounds(region):
    with pytest.raises(ValueError, match="invalid_storyboard_point_items"):
        validate_storyboard_v4(narrow_board(region))


def test_narrow_region_validation_retains_body_part_and_worn_source_checks():
    invalid_part = narrow_board()
    invalid_part[1]["pointItems"][0]["bodyPart"] = "face"
    with pytest.raises(ValueError, match="invalid_storyboard_point_items"):
        validate_storyboard_v4(invalid_part)
    invalid_source = narrow_board()
    invalid_source[0].update(cutType="product", shot="ghost")
    with pytest.raises(ValueError, match="invalid_storyboard_point_items"):
        validate_storyboard_v4(invalid_source)
