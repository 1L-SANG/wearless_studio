import pytest

from app.agents import page_copy
from app.agents.page_template_assembler import assemble_v4
from app.agents.page_templates import CATALOG, resolve_page_tokens, text_slot_for_role
from page_template_cases import assemble_input, case_input, elements, section, text


def intro_input(template="emo", seller="셀러 원문", body="다듬은 첫 문장\n다듬은 둘째 문장"):
    return {
        "storyboard": [{"id": "intro", "taxonomyVersion": 4, "source": "text", "sectionId": "intro",
                        "pageSectionKey": f"{template}.intro", "pageSectionKind": "intro",
                        "pageDesignId": "T06-D01" if template == "show" else "T01-D07", "slotKey": None}],
        "product": {"name": "셔츠", "colors": []}, "analysis": {"mdComment": seller},
        "copywriting": True, "copyResults": [{"blockId": "intro", "texts": [{"role": "body", "text": body}]}],
    }


@pytest.mark.parametrize("template", ["emo", "show"])
def test_polished_seller_body_has_priority_without_becoming_a_draft(template):
    value = intro_input(template)
    body = text(assemble_input(value), "body")
    assert [el["text"] for el in body] == [value["copyResults"][0]["texts"][0]["text"]]
    assert all(not el.get("aiDraft") for el in body)
    value["copyResults"] = []
    assert text(assemble_input(value), "body")[0]["text"] == "셀러 원문"
    value["analysis"]["mdComment"] = ""
    value["copyResults"] = intro_input(template)["copyResults"]
    assert all(el.get("aiDraft") for el in text(assemble_input(value), "body"))


def test_empty_md_hides_avatar_and_name_and_recomputes_height():
    empty = intro_input(seller=" ", body="")
    blocks = section(assemble_input(empty), "emo.intro")
    assert all(el.get("hidden") for el in text(blocks, "who"))
    assert not any(el.get("text") == "MD" and not el.get("hidden") for el in elements(blocks))
    # Keep the editable body placeholder so copy-slot discovery can still request a draft.
    assert text(blocks, "body")[0]["aiDraft"]
    assert (text(blocks, "body")[0]["x"], text(blocks, "body")[0]["w"]) == (82, 836)
    panel = next(el for el in elements(blocks) if el["type"] == "shape" and not el.get("hidden"))
    assert (panel["x"], panel["w"]) == (56, 888)
    # Supply both lines to isolate avatar height. B3b folds a missing title,
    # so a short body alone may correctly be shorter than the empty input UI.
    filled = intro_input(seller="", body="초안 문장")
    filled["copyResults"][0]["texts"].append({"role": "title", "text": "상품 소개"})
    populated = section(assemble_input(filled), "emo.intro")
    assert blocks[0]["h"] < populated[0]["h"]


@pytest.mark.parametrize("template", ["emo", "show"])
@pytest.mark.parametrize("design", ["I09-D06", "I09-SCRIM-01"])
def test_review_stars_are_fixed_editable_background_appropriate_text(template, design):
    value = case_input("emo-2-point")
    value["storyboard"] = [{**card, "pageSectionKey": f"{template}.review", "pageDesignId": design}
                           for card in value["storyboard"] if card["pageSectionKind"] == "review"]
    value["pageStyle"] = {**CATALOG["defaults"], "templateId": template, "colorMode": "point"}
    value["copywriting"] = False
    block = section(assemble_input(value), f"{template}.review")
    stars = [el for el in elements(block) if el.get("copyRole", "").endswith(".stars")]
    assert len(stars) == 3
    for i, star in enumerate(stars):
        assert star["text"] == "★★★★★"
        assert star["sourceBlockId"] == value["storyboard"][0]["id"]
        assert not star.get("aiDraft")
        assert star["style"]["color"] == ("#ffffff" if design == "I09-SCRIM-01" else
                                           resolve_page_tokens(value["pageStyle"], "blue")["colors"]["accentInk"])
        assert star["y"] + star["h"] <= text(block, f"items.{i}.text")[0]["y"]


@pytest.mark.parametrize("seller,ai", [([], []), (["카라"], []), (["카라"], ["카라"])])
def test_detail_label_uses_candidate_origin_even_when_seller_feature_has_same_name(seller, ai):
    value = case_input("emo-2-mono")
    value["analysis"].update(sellingPoints=seller, aiSuggestedPoints=ai,
                            partDescriptionCopy=[{"point": "카라", "label": "쉬운 이름", "desc": "부위 설명"}])
    block = section(assemble_input(value), "emo.points")
    assert text(block, "items.0.name")[0]["text"] == "쉬운 이름"
    assert text(block, "items.0.desc")[0]["text"] == "부위 설명"
    value["analysis"]["partDescriptionCopy"][0].pop("label")
    assert text(section(assemble_input(value), "emo.points"), "items.0.name")[0]["text"] == "카라"


def test_saved_page_copy_is_used_only_when_no_explicit_result_was_supplied():
    value = intro_input(seller="", body="저장된 본문")
    value["analysis"]["pageCopy"] = {"copyResults": value["copyResults"], "model": "fake", "promptVersion": "v2", "createdAt": "now"}
    args = (value["storyboard"], [], None, value["product"], True)
    assert text(assemble_v4(*args, analysis=value["analysis"]), "body")[0]["text"] == "저장된 본문"
    assert text(assemble_v4(value["storyboard"], [], [], value["product"], True, analysis=value["analysis"]), "body")[0]["text"] == ""


def test_feature_label_fallback_and_unknown_worn_crop_names():
    value = case_input("emo-2-mono")
    value["analysis"].update(sellingPoints=[], aiSuggestedPoints=["카라"], partDescriptionCopy=[],
                            featureCopy=[{"point": "카라", "label": "쉬운 특징", "desc": "특징 설명"}])
    block = section(assemble_input(value), "emo.points")
    assert text(block, "items.0.name")[0]["text"] == "쉬운 특징"
    assert text(block, "items.0.desc")[0]["text"] == "특징 설명"
    value["analysis"].update(sellingPoints=["카라"], aiSuggestedPoints=[])
    assert text(section(assemble_input(value), "emo.points"), "items.0.name")[0]["text"] == "카라"
    value = case_input("woc11-emo-PT-PHOTO-CARD-01-new-worn")
    lead = next(card for card in value["storyboard"] if card.get("pointItems"))
    original = lead["pointItems"][0]["label"]
    value["analysis"].update(sellingPoints=[original], aiSuggestedPoints=[original],
                            partDescriptionCopy=[{"point": original, "label": "대체 이름", "desc": "부위 설명"}],
                            featureCopy=[{"point": original, "label": "대체 특징", "desc": "특징 설명"}])
    assert text(section(assemble_input(value), lead["pageSectionKey"]), "items.0.name")[0]["text"] == original


@pytest.mark.parametrize("template", ["emo", "show"])
@pytest.mark.parametrize("kind,role,target", [("fabric", "title", "section"), ("fabric", "body", "section"),
    ("intro", "body", "section"), ("points", "fit.tip", "section"), ("points", "items.0.desc", "part"),
    ("review", "items.0.text", "section"), ("review", "items.0.who", "section"), ("faq", "items.0.a", "section")])
def test_catalog_limits_match_copy_limits_for_both_templates(template, kind, role, target):
    for seller in ("", "가" * 180, "가" * 350):
        slot = text_slot_for_role(CATALOG["kinds"][kind], role, template_id=template, seller_comment=seller)
        assert slot["maxChars"] == page_copy._limits({"kind": kind, "role": role, "target": target, "template": template}, {"sellerComment": seller})[0]


@pytest.mark.parametrize("kind,role,source", [("faq", "items.0.a", "aiDraft"), ("fabric", "title", "aiDraft"),
    ("fabric", "body", "aiDraft"), ("points", "fit.tip", "aiDraft"), ("for_you", "items.0", "ai")])
def test_catalog_draft_sources(kind, role, source):
    assert text_slot_for_role(CATALOG["kinds"][kind], role)["source"] == source
