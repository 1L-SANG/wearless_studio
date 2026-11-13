from io import BytesIO

import pytest
from PIL import Image

from app.agents.page_template_contrast import sample_overlay_color
from app.agents.page_templates import CATALOG
from app.workers.detail_page_job import _image_metrics, _overlay_text_metadata, _page_template_assembly_options
from page_template_cases import case_input, collect_input_regions


def _options(value):
    return {"product": value["product"], "analysis": value["analysis"], "template_context": {
        "storyboard": value["storyboard"], "copy_results": value["copyResults"],
        "copywriting": value["copywriting"], "profile": value.get("profile")}}


def _card(value, kind):
    return next(card for card in value["storyboard"] if card["pageSectionKind"] == kind and card["source"] == "ai")


def _metadata(value, colors, position='center', plate=False):
    plans = [plan for plan in collect_input_regions(value) if plan['key'] in colors]
    positions = {plan['key']: position for plan in plans if plan.get('positionCandidates')}
    return {"overlayTextColors": colors, "overlayTextLines": {
        line["key"]: {"color": colors[plan["key"]], "shade": False}
        for plan in plans for line in plan["lines"]},
        **({'overlayTextPositions': positions} if positions else {}),
        **({'overlayTextPlates': {key: True for key in positions}} if plate else {})}


@pytest.mark.parametrize("outside,center,expected", [("white", "black", "#ffffff"), ("black", "white", "#ffffff")])
def test_first_page_compares_visible_center_and_edges(outside, center, expected):
    value = case_input("emo-2-point")
    hero = _card(value, "first")
    with Image.new("RGB", (80, 100), outside) as image:
        image.paste(center, (0, 40, 80, 60))
        expected_meta = _metadata(value, {"emo.first:first.hero:center": expected}, position="center" if center == "black" else "bottom")
        assert _overlay_text_metadata(hero, image, **_options(value)) == expected_meta
        data = BytesIO()
        image.save(data, format="PNG")
    assert _image_metrics(data.getvalue(), hero, **_options(value)) == (80, 100, expected_meta)


@pytest.mark.parametrize("heading,description,expected", [
    ("white", "black", ("#111111", "#ffffff")),
    ("black", "white", ("#ffffff", "#111111")),
    ("white", "white", ("#111111", "#111111")),
    ("black", "black", ("#ffffff", "#ffffff")),
])
def test_fabric_heading_and_description_are_measured_independently(heading, description, expected):
    value = case_input("show-2-point")
    with Image.new("RGB", (80, 100), heading) as image:
        image.paste(description, (0, 50, 80, 100))
        colors = _overlay_text_metadata(_card(value, "fabric"), image, **_options(value))["overlayTextColors"]
    assert colors == {"show.fabric:fabric.fabric:heading": expected[0], "show.fabric:fabric.fabric:description": expected[1]}


@pytest.mark.parametrize("kind,design_id", [
    ("review", "I09-D06"), ("faq", "T08-D07"),
    ("review", "I09-SCRIM-01"), ("faq", "T08-SCRIM-01"),
])
def test_fixed_review_and_faq_designs_do_not_request_bare_image_measurement(kind, design_id):
    value = case_input("emo-2-point")
    for card in value["storyboard"]:
        if card["pageSectionKind"] == kind:
            card["pageDesignId"] = design_id
    with Image.new("RGB", (80, 100), "white") as image:
        assert _overlay_text_metadata(_card(value, kind), image, **_options(value)) == {}


def test_reused_sns_source_is_measured_at_its_target_group():
    value = case_input("emo-sns-story")
    source = next(card for card in value["storyboard"] if card["id"] == "emo.wear_styling.g1")
    with Image.new("RGB", (80, 120), "white") as image:
        assert _overlay_text_metadata(source, image, **_options(value)) == _metadata(value, {"emo.sns:sns.sns1:account": "#111111"})


def test_opaque_sns_bubbles_and_fit_caption_panels_do_not_request_measurement():
    value = case_input("emo-sns-message")
    source = next(card for card in value["storyboard"] if card["id"] == "emo.wear_styling.g1")
    with Image.new("RGB", (80, 120), "white") as image:
        assert _overlay_text_metadata(source, image, **_options(value)) == {}
        fit = next(card for card in value["storyboard"] if card.get("pageSectionUsage") == "fit")
        assert _overlay_text_metadata(fit, image, **_options(value)) == {}


@pytest.mark.parametrize("outside,center,expected", [("white", "black", "#ffffff"), ("black", "white", "#ffffff")])
def test_center_measurement_accounts_for_visible_cover_crop(outside, center, expected):
    value = case_input("emo-2-point")
    with Image.new("RGB", (100, 400), outside) as image:
        # Cover shows source rows 137.5..262.5. The actual centered text covers
        # rows ~190..210, rather than 40%-60% of the uncropped 400px source.
        image.paste(center, (0, 188, 100, 212))
        assert _overlay_text_metadata(_card(value, "first"), image, **_options(value)) == _metadata(value, {"emo.first:first.hero:center": expected}, position="center" if center == "black" else "bottom")


def test_sampling_supports_the_editors_explicit_crop_coordinates():
    plan = {"frame": {"w": 80, "h": 100, "crop": {"iw": 100, "ih": 200, "ox": 10, "oy": 50}},
            "region": {"left": 0, "top": 0, "right": 1, "bottom": .1}}
    with Image.new("RGB", (100, 200), "white") as image:
        image.paste("black", (10, 50, 90, 65))
        assert sample_overlay_color(image, plan) == "#ffffff"


@pytest.mark.parametrize("image", [None, b"invalid image", object()])
def test_measurement_failure_defaults_each_fabric_group_to_white(image):
    value = case_input("show-2-point")
    assert _overlay_text_metadata(_card(value, "fabric"), image, **_options(value)) == _metadata(value, {
        "show.fabric:fabric.fabric:heading": "#ffffff", "show.fabric:fabric.fabric:description": "#ffffff"})


def test_invalid_image_bytes_keep_white_group_metadata_and_do_not_block_assembly():
    value = case_input("emo-2-point")
    assert _image_metrics(b"invalid", _card(value, "first"), **_options(value)) == (
        None, None, _metadata(value, {"emo.first:first.hero:center": "#ffffff"}, position="center", plate=True))


def test_other_versions_and_images_without_bare_overlay_text_are_ignored():
    assert _overlay_text_metadata({"id": "legacy", "taxonomyVersion": 3}, None) == {}
    value = case_input("common-2-point")
    assert _overlay_text_metadata(_card(value, "first"), None, **_options(value)) == {}


def test_measurement_regions_follow_the_copy_and_exclude_opaque_and_fixed_paint_groups():
    value = case_input("emo-2-point")
    plans = collect_input_regions(value)
    assert [plan["key"] for plan in plans] == ["emo.first:first.hero:center"]
    for plan in plans:
        if plan["key"].endswith(":center"):
            assert (plan["region"]["top"] + plan["region"]["bottom"]) / 2 == .5
        assert plan["key"].endswith((":center", ":heading"))


def test_worker_passes_selected_page_style_and_base_swatch_without_changing_legacy_arguments():
    hero = _card(case_input("emo-2-point"), "first")
    style = {"templateId": "show", "fontStyle": "boldGothic", "colorMode": "point"}
    analysis = {"pageStyle": style}
    product = {"colors": [{"swatchId": "red"}, {"swatchId": "blue", "isBase": True}]}
    assert _page_template_assembly_options([hero], analysis, product) == {"analysis": analysis, "page_style": style, "base_swatch_id": "blue"}
    assert _page_template_assembly_options([hero], {}, {}) == {"analysis": {}, "page_style": CATALOG["defaults"], "base_swatch_id": None}
    assert _page_template_assembly_options([{"taxonomyVersion": 3}], analysis, product) == {}


def test_folded_duplicate_cut_keeps_contrast_for_both_reused_sns_positions(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from app.workers import detail_page_job as dpj
    from conftest import fake_worker_app, make_settings, worker_job

    value = case_input("emo-sns-story")
    cards = [card for card in value["storyboard"] if card["pageSectionKey"] == "emo.wear_styling"][:2]
    assert dpj._duplicate_source_indexes(cards, "top", analysis=value["analysis"]) == [None, 0]
    data = BytesIO()
    with Image.new("RGB", (80, 120), "white") as image:
        image.save(data, format="PNG")
    output = data.getvalue()
    generated = []

    async def generate(*args, **kwargs):
        generated.append(True)
        return output, "image/png"

    monkeypatch.setattr(dpj.cut_generator, "generate", generate)
    monkeypatch.setattr(dpj, "_emit", AsyncMock())
    monkeypatch.setattr(dpj, "_tag_preview_block", AsyncMock())
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", AsyncMock(return_value="intent"))
    app = fake_worker_app(make_settings(gemini_api_key="fake", r2_bucket="b",
                                        cut_output_qc_mode="off", page_output_qc_mode="off"))
    images = [dpj.InlineImage("image/png", output)]
    prepared = [(card, images, "1. PRODUCT — front", False, images) for card in cards]
    results, assets, *_ = asyncio.run(dpj._gen_cuts(app, worker_job(), prepared, value["product"], value["analysis"],
        template_context={"storyboard": value["storyboard"], "copy_results": value["copyResults"], "copywriting": True}))
    assert len(generated) == 1
    assert len(assets) == 1
    assert len(results) == 2
    for result in results:
        assert result["overlayTextColors"] == {"emo.sns:sns.sns1:account": "#111111", "emo.sns:sns.sns2:account": "#111111"}
