"""Actual template assembly remains retryable through editor slot classification."""
from copy import deepcopy
import uuid

import pytest

from app.agents import context_fill, horizon_background
from page_template_cases import assemble_input, case_input


def assembled_photo(template, kind, *, failed=False, blank=False):
    value = case_input(f"{template}-1-mono")
    source = next(card for card in value["storyboard"] if card["source"] == "ai" and card["pageSectionKind"] == kind)
    source["matchIds"] = [] if kind == "points" else ["matching-retained"]
    if failed or blank:
        value["cutResults"] = [cut for cut in value["cutResults"] if cut["blockId"] != source["id"]]
    if failed:
        value["failedCuts"] = {source["id"]: {"reason": "temporary_error", "retryable": True}}
    blocks = assemble_input(value)
    block = next(block for block in blocks if any(el.get("sourceBlockId") == source["id"] and el["type"] == "image" for el in block["elements"]))
    element = next(el for el in block["elements"] if el.get("sourceBlockId") == source["id"] and el["type"] == "image")
    assert block["kind"] == "info"
    return value, source, blocks, block, element


@pytest.mark.parametrize("template,kind", [("emo", "points"), ("emo", "review"), ("emo", "faq"), ("show", "points")])
def test_actual_template_info_photo_classification_and_saved_retry_recipe(template, kind):
    value, source, blocks, block, element = assembled_photo(template, kind, failed=True)
    by_id = {card["id"]: card for card in value["storyboard"]}
    assert element["genFailed"] is True
    assert context_fill.slot_kind(block, element, by_id) == "planned"
    assert context_fill.slot_kind(block, {**element, "genPending": True}, by_id) == "pending"
    assert context_fill.slot_kind(block, {**element, "src": "/filled.png", "genPending": True}, by_id) == "filled"
    blank_input, _, _, blank_block, blank = assembled_photo(template, kind, blank=True)
    assert not blank.get("genFailed")
    assert context_fill.slot_kind(blank_block, blank, by_id) == "planned"
    assert context_fill.slot_kind(blank_block, {**blank, "sourceBlockId": None}, by_id) == "unplanned"
    assert context_fill.slot_kind(blank_block, blank, {source["id"]: {**source, "source": "mine"}}) == "unplanned"
    request = {"mode": "new", "retryBlockId": element["sourceBlockId"], "cutType": "horizon", "colorId": "wrong-color"}
    restored = horizon_background.restore_failed_retry(request, value["storyboard"], blocks)
    assert restored["retryBlockId"] == source["id"]
    assert restored["cutType"] == ("product" if kind == "points" else "styling")
    assert restored["shot"] == ("detail" if kind == "points" else "full")
    assert restored["colorId"] == "blue"
    assert restored["direction"] == "front"
    assert restored["matchIds"] == ([] if kind == "points" else ["matching-retained"])
    if kind == "points":
        assert restored["detailTargetId"] == "collar"


def test_auto_legacy_hidden_and_non_image_template_elements_remain_unsupported():
    value, _, blocks, block, element = assembled_photo("emo", "review", failed=True)
    by_id = {card["id"]: card for card in value["storyboard"]}
    for kind in ("size", "care", "ai-notice", "common-notice"):
        auto = next(block for block in blocks if block["kind"] == kind)
        assert context_fill.slot_kind({**auto, "pageSectionKey": "emo.notice"}, element, by_id) == "unsupported"
        assert context_fill.slot_kind({**auto, "kind": "info", "pageSectionKey": "emo.notice"}, element, by_id) == "unsupported"
        assert context_fill.slot_kind({**block, "kind": kind, "auto": False}, element, by_id) == "unsupported"
    for key in (None, "", "   ", 42):
        assert context_fill.slot_kind({**block, "pageSectionKey": key}, element, by_id) == "unsupported"
    assert context_fill.slot_kind({**block, "hidden": True}, element, by_id) == "unsupported"
    assert context_fill.slot_kind(block, {**element, "hidden": True}, by_id) == "unsupported"
    assert context_fill.slot_kind(block, {**element, "type": "text"}, by_id) == "unsupported"


def test_template_info_photos_participate_in_neighbors_and_first_photo_detection():
    value, source, blocks, review, review_image = assembled_photo("emo", "review")
    _, _, _, faq, faq_image = assembled_photo("emo", "faq", blank=True)
    asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "woc17-review"))
    review_image["src"] = f"/v1/assets/{asset_id}/file"
    faq_image.update(sourceBlockId=None, roleHint="hero")
    auto = deepcopy(next(block for block in blocks if block["kind"] == "common-notice"))
    auto.update(kind="info", pageSectionKey="emo.notice", elements=[{**review_image, "id": "auto-photo"}])
    legacy = {"id": "legacy", "kind": "info", "elements": [{**review_image, "id": "legacy-photo"}]}
    context = context_fill.build_context(project_id="p", slot={"blockId": faq["id"], "elementId": faq_image["id"]},
        blocks=[auto, legacy, review, faq], storyboard=value["storyboard"], product=value["product"], analysis=value["analysis"],
        asset_recipes={asset_id: {"recipe": {"cutType": "styling", "shot": "full", "sectionRole": "styling"}}},
        examples={}, previous_recipes=[], attempt=0)
    assert context["kind"] == "unplanned"
    assert [photo["element"]["id"] for photo in context["page"]] == [review_image["id"]]
    assert context["nearestWorn"]["assetId"] == asset_id
    assert context["purpose"] == "benefit"
