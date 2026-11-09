from itertools import groupby

import pytest

from app.agents.page_template_assembler import estimate_text_height
from app.agents.page_templates import CATALOG
from page_template_cases import MATRIX, assemble_input, case_input, elements, photos, section, text


@pytest.mark.parametrize("case", MATRIX, ids=lambda case: case["name"])
def test_template_color_matrix_geometry_and_card_coverage(case):
    value = case["input"]
    blocks = assemble_input(value)
    tid, count, _ = case["name"].split("-")
    expected_counts = {"emo": [17, 18, 21], "show": [15, 16, 19]}
    generated = [card for card in value["storyboard"] if card["source"] == "ai"]
    assert len(generated) == expected_counts[tid][int(count) - 1]
    assert [key for key, _ in groupby(b["pageSectionKey"] for b in blocks if b.get("pageSectionKey"))] == [key for key, _ in groupby(c["pageSectionKey"] for c in value["storyboard"])]
    sources = {photo.get("sourceBlockId") for photo in photos(blocks)}
    assert all(card["id"] in sources for card in generated)
    for block in blocks:
        assert "templateId" not in block
        if block.get("pageSectionKey"):
            assert block["h"] <= CATALOG["textMetrics"]["maxBlockHeight"]
        for element in block["elements"]:
            assert element["x"] >= 0 and element["y"] >= 0
            assert element["x"] + element["w"] <= 1000
            assert element["w"] > 0 and element["h"] > 0
            if block.get("h"):
                assert element["y"] + element["h"] <= block["h"]
            if block.get("pageSectionKey") and element["type"] == "image":
                assert element["sourceBlockId"] and element["slotKey"] and element["roleHint"]
            if block.get("pageSectionKey") and element.get("copyRole"):
                assert element["sourceBlockId"] == next(card["id"] for card in value["storyboard"] if card["pageSectionKey"] == block["pageSectionKey"])
    assert [block["kind"] for block in blocks[-4:]] == ["size", "care", "ai-notice", "common-notice"]
    assert blocks[-1]["elements"][0]["text"] == "모니터와 기기 설정에 따라 실제 색상과 다르게 보일 수 있어요."
    names = [el for el in elements(section(blocks, tid + ".wear_styling")) if el.get("copyRole", "").startswith("colorName.")]
    assert len(names) == (int(count) - 1) * 2
    assert all(el["text"] != "라이트블루" for el in names)


@pytest.mark.parametrize("measured", ["#111111", "#ffffff", "invalid", None])
def test_first_text_group_is_centered_and_uses_group_contrast(measured):
    value = case_input("emo-2-point")
    value["cutResults"][0]["overlayTextColors"] = {"emo.first:first.hero:center": measured}
    first = section(assemble_input(value), "emo.first")
    image = photos(first)[0]
    copy = [el for el in elements(first) if el.get("copyRole")]
    assert len(copy) == 4
    top, bottom = min(el["y"] for el in copy), max(el["y"] + el["h"] for el in copy)
    assert (top + bottom) / 2 == image["y"] + image["h"] / 2
    for element in copy:
        assert element["style"]["color"] == (measured if measured in ("#111111", "#ffffff") else "#ffffff")


def test_copy_pending_off_and_fact_sources():
    pending = assemble_input(case_input("emo-copy-pending"))
    assert text(section(pending, "emo.first"), "name")[0]["text"] == "데일리 코튼 셔츠"
    assert text(section(pending, "emo.first"), "tagline")[0]["text"] == ""
    assert text(section(pending, "emo.first"), "tagline")[0]["placeholder"]
    assert text(section(pending, "emo.intro"), "body")[0]["text"] == ""
    off_value = case_input("emo-copy-off-seller")
    off = assemble_input(off_value)
    assert text(section(off, "emo.intro"), "body")[0]["text"] == off_value["analysis"]["mdComment"]
    assert text(section(off, "emo.for_you"), "title")[0]["text"] == "이런 분께 추천해요"
    assert all(el["text"] == "" and el["placeholder"] for el in elements(section(off, "emo.review")) if el.get("copyRole", "").startswith("items."))
    value = case_input("emo-2-mono")
    value["copyResults"][0]["texts"].append({"role": "name", "text": "지어낸 상품명"})
    blocks = assemble_input(value)
    assert text(section(blocks, "emo.first"), "name")[0]["text"] == value["product"]["name"]
    assert text(section(blocks, "emo.points"), "items.0.desc")[0]["text"] == value["analysis"]["featureCopy"][0]["desc"]


def test_text_fallbacks_and_unavailable_facts():
    blocks = assemble_input(case_input("emo-text-fallbacks"))
    for key, design_id in [("emo.fabric", "T03-TEXT-01"), ("emo.points", "PT-EMO-TEXT")]:
        group = section(blocks, key)
        assert len(group) == 1
        assert group[0]["pageDesignId"] == design_id
        assert not photos(group)
    assert text(section(blocks, "emo.intro"), "body")[0]["aiDraft"]
    assert not text(blocks, "notice")


def test_reuse_failure_and_deleted_source():
    blocks = assemble_input(case_input("show-reuse-failed"))
    ghost = next(el for el in photos(section(blocks, "show.first")) if el["slotKey"] == "first.ghost")
    original = next(el for el in photos(section(blocks, "show.color_product")) if el.get("sourceBlockId") == ghost["sourceBlockId"])
    for element in (ghost, original):
        assert element["src"] is None
        assert element["genFailed"]
        assert element["genFailReason"] == "temporary_error"
        assert element["genFailRetryable"]
        assert element["genFailSourceBlockId"] == "retry-source"
    orphan = next(el for el in photos(section(assemble_input(case_input("show-reuse-orphan")), "show.first")) if el["slotKey"] == "first.ghost")
    assert "sourceBlockId" not in orphan
    assert orphan["src"] is None
    assert orphan["roleHint"] == "productOverview"


@pytest.mark.parametrize("name", ["emo-sns-message", "emo-sns-story"])
def test_sns_reuses_styling_sources_with_distinct_target_slots(name):
    value = case_input(name)
    blocks = assemble_input(value)
    styling = [card for card in value["storyboard"] if card["pageSectionKey"] == "emo.wear_styling"]
    images = photos(section(blocks, "emo.sns"))
    assert images[0]["sourceBlockId"] == styling[0]["id"]
    assert images[0]["slotKey"] == "sns.sns1"
    if name.endswith("story"):
        assert images[1]["sourceBlockId"] == styling[1]["id"]
        assert images[1]["slotKey"] == "sns.sns2"
        assert len(text(section(blocks, "emo.sns"), "lines.0")) == 1
        assert len(text(section(blocks, "emo.sns"), "lines.1")) == 1


def test_overflow_preserves_each_extra_card_in_its_original_section():
    blocks = assemble_input(case_input("emo-overflow"))
    for source in ("overflow-hero", "gallery-extra"):
        assert sum(el.get("sourceBlockId") == source for el in photos(blocks)) == 1
    first = section(blocks, "emo.first")
    assert len(first) == 2
    assert photos([first[1]])[0]["sourceBlockId"] == "overflow-hero"
    assert not any(el.get("copyRole") for el in first[1]["elements"])


def test_inversion_and_point_tokens_and_english_case():
    mono = assemble_input(case_input("show-2-mono"))
    point = assemble_input(case_input("show-2-point"))
    for key in ("show.intro", "show.fabric", "show.points"):
        assert section(point, key) == section(mono, key)
    common = assemble_input(case_input("common-2-point"))
    assert any(el.get("fill") == CATALOG["pointPalette"]["blue"]["accentBg"] for el in elements(section(common, "emo.intro")))
    assert text(section(mono, "show.color_product"), "colors.0.en") == []
    assert text(section(mono, "show.color_product"), "label")[0]["text"] == "COLOR"


def test_korean_word_and_unicode_text_height_matches_shared_metrics():
    assert estimate_text_height("가나다 라마바", 100, 25, 36) == 72
    assert estimate_text_height("가나다\n라마바", 100, 25, 36) == 72
    assert estimate_text_height("🙂🙂", 100, 25, 36) == 36


def test_each_fabric_group_has_its_own_contrast_and_bubbles_keep_fixed_colors():
    value = case_input("show-2-point")
    cut = next(cut for cut in value["cutResults"] if cut["blockId"] == "show.fabric.fabric")
    cut["overlayTextColors"] = {"show.fabric:fabric.fabric:heading": "#111111", "show.fabric:fabric.fabric:description": "#ffffff"}
    fabric = section(assemble_input(value), "show.fabric")
    assert all(text(fabric, role)[0]["style"]["color"] == "#111111" for role in ("label", "title"))
    assert all(text(fabric, role)[0]["style"]["color"] == "#ffffff" for role in ("mix", "body"))
    value = case_input("emo-sns-story")
    for cut in value["cutResults"]:
        cut["overlayTextColors"] = {key: "#111111" for key in cut.get("overlayTextColors", {})}
    blocks = assemble_input(value)
    assert text(section(blocks, "emo.review"), "label")[0]["style"]["color"] == "#ffffff"
    assert text(section(blocks, "emo.review"), "items.0.text")[0]["style"]["color"] == CATALOG["templates"][0]["colors"]["ink"]
    assert text(section(blocks, "emo.faq"), "items.0.q")[0]["style"]["color"] == CATALOG["templates"][0]["colors"]["ink"]
    assert all(el["style"]["color"] == "#111111" for el in text(section(blocks, "emo.sns"), "handle"))
    assert not any("_overlayGroupKey" in el for el in elements(blocks))


def test_fit_album_saved_crop_removes_a_synthetic_head_covering_upper_23_percent():
    from PIL import Image, ImageChops

    value = case_input("common-3-point")
    album = photos(section(assemble_input(value), "emo.points_fit"))
    assert len(album) == 3
    for photo in album:
        crop = photo["crop"]
        assert set(crop) == {"iw", "ih", "ox", "oy"}
        assert crop["oy"] / crop["ih"] >= .249
        assert abs(crop["ox"] - (crop["iw"] - photo["w"]) / 2) <= .5
        assert crop["oy"] + photo["h"] <= crop["ih"]
        source = next(cut for cut in value["cutResults"] if cut["blockId"] == photo["sourceBlockId"])
        with Image.new("RGB", (source["width"], source["height"]), "white") as image:
            image.paste("red", (0, 0, source["width"], int(source["height"] * .23)))
            with image.resize((crop["iw"], crop["ih"])) as scaled:
                with scaled.crop((crop["ox"], crop["oy"], crop["ox"] + photo["w"], crop["oy"] + photo["h"])) as shown:
                    assert ImageChops.subtract(shown.getchannel("R"), shown.getchannel("G")).getbbox() is None


def test_summary_rule_is_centered_only_for_showroom():
    show = section(assemble_input(case_input("show-2-mono")), "show.intro")
    rule = next(el for el in elements(show) if el["type"] == "shape" and el["h"] == 2)
    assert rule["x"] + rule["w"] / 2 == 500
    common = section(assemble_input(case_input("common-2-mono")), "emo.intro")
    assert [el["x"] for el in elements(common) if el["type"] == "shape" and el["h"] == 2] == [56]
