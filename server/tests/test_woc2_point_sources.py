"""WOC2 crop sources use existing worn images, including seller-owned images."""
from copy import deepcopy

import pytest

from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4, point_photos_for_run


def board(**changes):
    source = {"id": "wear-photo", "taxonomyVersion": 4, "source": "ai", "pageSectionKey": "emo.wear_life",
              "pageSectionKind": "wear", "pageSectionUsage": "styling", "pageDesignId": "P02-D01", "sectionId": "wear",
              "sectionLayout": "stack", "slotKey": None, "contentRole": "coordination", "cutType": "styling",
              "shot": "full", "direction": "front", "colorId": "base", "faceExposure": "same", **changes}
    lead = {"id": "points", "taxonomyVersion": 4, "source": "text", "pageSectionKey": "emo.points",
            "pageSectionKind": "points", "pageDesignId": "PT-EMO-01", "sectionId": "points", "sectionLayout": "stack",
            "slotKey": None, "cutType": None, "contentRole": None, "sectionRole": None,
            "pointItems": [{"kind": "wornCrop", "sourceBlockId": source["id"], "bodyPart": part, "label": label,
                            "cropRegion": region} for part, label, region in [
                                ("shoulder", "어깨선", [.15, .16, .7, .35]),
                                ("neckline", "목선", [.2, .15, .6, .3]),
                                ("length", "입었을 때 기장", [.1, .38, .8, .45])]]}
    return [source, lead]


@pytest.mark.parametrize("changes", [
    {}, {"direction": "side"}, {"direction": "back"},
    {"cutType": "horizon", "direction": "side"},
    {"source": "mine", "cutType": None, "ownImages": ["/seller-worn.png"]},
])
def test_worn_sources_validate_and_assemble_three_crops_without_a_new_generation(changes):
    blocks = board(**changes)
    snapshot = deepcopy(blocks)
    canonical = canonicalize_storyboard(blocks)
    selected = point_photos_for_run([canonical[1]], canonical)
    assert len(selected) == 3
    assert {photo["id"] for photo in selected} == {"wear-photo"}
    cuts = [{"blockId": "wear-photo", "imageUrl": "/generated-worn.png", "width": 1024, "height": 1365}]
    assembled = assemble_v4(canonical, cuts, [], {"clothingType": "top", "colors": [{"id": "base", "isBase": True}]}, True)
    images = [element for run in assembled if run.get("pageSectionKey") == "emo.points"
              for element in run["elements"] if element["type"] == "image"]
    assert len(images) == 3
    assert {image["src"] for image in images} == {"/seller-worn.png" if changes.get("source") == "mine" else "/generated-worn.png"}
    assert all(image["pointItemKind"] == "wornCrop" and image["sourceBlockId"] == "wear-photo" for image in images)
    assert blocks == snapshot


@pytest.mark.parametrize("changes", [
    {"cutType": "product", "shot": "ghost"},
    {"source": "mine", "cutType": None, "ownImages": []},
    {"source": "mine", "cutType": None, "ownImages": [None]},
    {"source": "mine", "cutType": None, "ownImages": "/unlisted-worn.png"},
])
def test_non_worn_or_unavailable_image_cannot_be_a_crop_source(changes):
    with pytest.raises(ValueError, match="invalid_storyboard_point_items"):
        validate_storyboard_v4(board(**changes))
