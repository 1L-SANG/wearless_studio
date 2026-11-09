from copy import deepcopy

import pytest

from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
from page_template_cases import case_input


@pytest.mark.parametrize("kind", ["ai", "mine"])
def test_showroom_reuse_slot_accepts_an_independent_photo_without_new_fields(kind):
    value = case_input("show-2-mono")
    board = value["storyboard"]
    first = board[0]
    original = next(b for b in board if b.get("pageSectionKind") == "color_product" and b.get("direction") == "front")
    fallback = {**original, "id": "opening-ghost", "source": kind, "pageSectionKey": first["pageSectionKey"],
                "pageSectionKind": "first", "pageDesignId": first["pageDesignId"], "sectionId": first["sectionId"],
                "slotKey": "first.ghost"}
    if kind == "mine":
        fallback.update(cutType=None, ownImages=["seller:ghost"], thumb="seller:ghost")
    updated = [first, fallback, *(b for b in board[1:] if b.get("pageSectionKind") != "color_product")]
    validate_storyboard_v4(updated)
    saved = canonicalize_storyboard(updated, for_storage=True)
    assert [b["id"] for b in saved] == [b["id"] for b in updated]
    assert next(b for b in saved if b["id"] == fallback["id"])["source"] == kind


@pytest.mark.parametrize("kind", ["ai", "mine"])
def test_sns_text_lead_accepts_only_its_existing_reuse_slot_photos(kind):
    value = case_input("emo-sns-message")
    board = value["storyboard"]
    lead = next(b for b in board if b.get("pageSectionKind") == "sns")
    original = next(b for b in board if b.get("pageSectionUsage") == "styling" and b.get("source") == "ai")
    fallback = {**original, "id": "sns-independent", "source": kind, "pageSectionKey": lead["pageSectionKey"],
                "pageSectionKind": "sns", "pageDesignId": lead["pageDesignId"], "sectionId": lead["sectionId"],
                "slotKey": "sns.sns1", "refScope": "all"}
    for key in ("pageSectionUsage", "spaceGroupId", "spaceVariation", "spaceSetMemberOrder", "setSelectionOrigin"):
        fallback.pop(key, None)
    if kind == "mine":
        fallback.update(cutType=None, ownImages=["seller:sns"], thumb="seller:sns")
    updated = []
    for block in board:
        if block.get("pageSectionUsage") == "styling":
            continue
        updated.append(block)
        if block is lead:
            updated.append(fallback)
    validate_storyboard_v4(updated)
    saved = canonicalize_storyboard(updated, for_storage=True)
    assert next(b for b in saved if b["id"] == fallback["id"])["source"] == kind
    invalid = [{**b, "slotKey": "sns.not-a-slot"} if b["id"] == fallback["id"] else b for b in updated]
    with pytest.raises(ValueError, match="invalid_storyboard_text_card"):
        validate_storyboard_v4(invalid)


@pytest.mark.parametrize("fixture_name", ["emo-2-mono", "show-2-mono", "common-2-mono"])
def test_arbitrary_section_run_order_is_preserved_during_validation_and_canonical_save(fixture_name):
    value = case_input(fixture_name)
    runs = []
    for block in value["storyboard"]:
        if not runs or runs[-1][0]["pageSectionKey"] != block["pageSectionKey"]:
            runs.append([])
        runs[-1].append(block)
    board = deepcopy(runs[0] + [b for run in reversed(runs[1:]) for b in run])
    validate_storyboard_v4(board)
    saved = canonicalize_storyboard(board, for_storage=True)
    assert [b["id"] for b in saved] == [b["id"] for b in board]
    assembled = assemble_v4(saved, value["cutResults"], value["copyResults"], value["product"], True, analysis=value.get("analysis"))
    expected = list(dict.fromkeys(b["pageSectionKey"] for b in board))
    actual = list(dict.fromkeys(b["pageSectionKey"] for b in assembled if b.get("pageSectionKey")))
    assert actual == expected
