"""칩 판독은 가짜 응답만 사용한다. 상품 사진과 목표 값은 판독자에게 보내지 않는다."""

import asyncio
from io import BytesIO
from itertools import product

from PIL import Image
import pytest

from app.agents import cut_fit_chip_qc as qc
from app.agents.analysis_fit import FIT_CHECK_SCALES, FIT_VALUE_SENTENCES, select_applied_fit_axes
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings


def image():
    picture = Image.new("RGB", (120, 400), "blue")
    picture.paste("red", (0, 0, 120, 100))
    out = BytesIO()
    picture.save(out, format="PNG")
    return InlineImage("image/png", out.getvalue())


def profile(category="top", axes=None, sources=None, gender="women"):
    return {"category": category, "gender": gender, "axes": axes or {}, "axisSources": sources or {}}


@pytest.mark.parametrize("scale,target,observed,distance,status", [
    ("top_hem", "basic", "basic", 0, "PASS"), ("top_hem", "basic", "semi_long", 1, "PASS"),
    ("top_hem", "basic", "long", 2, "FAIL"), ("top_hem", "crop", "semi_crop", 1, "FAIL"),
    ("top_hem", "semi_crop", "crop", 1, "FAIL"), ("sleeve_end", "elbow", "short", 1, "PASS"),
    ("sleeve_end", "fingers", "wrist", 2, "FAIL"), ("top_fit", "slim", "over", 3, "FAIL"),
    ("pants_hem", "ankle", "above_ankle", 1, "PASS"),
    ("top_hem", "crop", "not_visible", None, "UNJUDGEABLE"),
    ("bottom_hem", "floor", "not_applicable", None, "UNJUDGEABLE"),
    ("top_hem", "basic", "tucked", None, "UNJUDGEABLE"),
])
def test_distance_and_crop_boundary(scale, target, observed, distance, status):
    result = qc.judge_distance(scale, target, observed)
    assert result["distance"] == distance
    assert result["status"] == status


def test_all_applied_values_have_a_scale_and_grouped_values_share_a_point():
    assert set(qc.SCALES) == {"top_hem", "sleeve_end", "bottom_hem", "pants_hem", "top_fit"}
    for scale in qc.SCALES.values():
        assert {"not_visible", "not_applicable"} <= set(scale["values"])
    for category, axis, value in FIT_CHECK_SCALES:
        scale, target = FIT_CHECK_SCALES[category, axis, value]
        assert target in qc.SCALES[scale]["values"]
    for category, axis, values in (("top", "length", ("crop", "slight_crop")),
            ("top", "sleeve", ("cap_short", "short", "above_elbow")),
            ("top", "sleeve", ("above_wrist", "wrist")),
            ("pants", "length", ("ankle_cover", "below_ankle"))):
        assert len({FIT_CHECK_SCALES[category, axis, value] for value in values}) == 1


def test_settings_default_off_and_owner_models(monkeypatch):
    for key in ("CUT_FIT_CHIP_QC_MODE", "MODEL_CUT_FIT_CHIP_QC_TOP_LENGTH", "MODEL_CUT_FIT_CHIP_QC_OTHER"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert settings.cut_fit_chip_qc_mode == "off"
    assert settings.model_cut_fit_chip_qc_top_length == "gemini-3.1-pro-preview"
    assert settings.model_cut_fit_chip_qc_other == "gemini-3.8-flash"
    monkeypatch.setenv("CUT_FIT_CHIP_QC_MODE", "repair")
    assert load_settings().cut_fit_chip_qc_mode == "repair"
    monkeypatch.setenv("CUT_FIT_CHIP_QC_MODE", "invalid")
    assert load_settings().cut_fit_chip_qc_mode == "off"


@pytest.mark.parametrize("mode,mannequin,cut_type,p", [
    ("off", False, "horizon", profile(axes={"length": "crop"}, sources={"length": "seller"})),
    ("repair", True, "horizon", profile(axes={"length": "crop"}, sources={"length": "seller"})),
    ("shadow", False, "product", profile(axes={"length": "crop"}, sources={"length": "seller"})),
    ("repair", False, "horizon", profile(axes={"length": "crop"}, sources={"length": "unsure"})),
    ("shadow", False, "horizon", profile("outer", {"length": "long"}, {"length": "seller"})),
    ("shadow", False, "horizon", profile(axes={"length": "basic"}, sources={"length": "seller"}, gender="men")),
    ("shadow", False, "horizon", profile(axes={"fit": "regular"}, sources={"fit": "ai"})),
])
def test_inactive_checks_make_zero_calls(monkeypatch, mode, mannequin, cut_type, p):
    async def forbidden(*args, **kwargs):
        raise AssertionError("unexpected paid call")
    monkeypatch.setattr(qc, "analyze_with_fallback", forbidden)
    result = asyncio.run(qc.verdict(make_settings(cut_fit_chip_qc_mode=mode), p, image(),
                                    has_mannequin=mannequin, cut_type=cut_type))
    assert result is None


def test_two_model_groups_only_see_full_image_and_enlarged_lower_three_quarters(monkeypatch):
    calls = []
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        calls.append((prompt, images, schema, kwargs))
        names = schema["properties"]["axes"]["properties"]
        return {"reasoning": "visible body landmarks", "axes": {name: {"top_hem": "basic", "sleeve_end": "wrist", "top_fit": "over"}[name] for name in names}}, "gemini"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    p = profile(axes={"length": "semi_long", "sleeve": "fingers", "fit": "over"},
                sources={axis: "seller" for axis in ("length", "sleeve", "fit")})
    p["sellerNote"] = "SELLER ATTACK"
    candidate = image()
    result = asyncio.run(qc.verdict(make_settings(cut_fit_chip_qc_mode="repair"), p, candidate))
    assert len(calls) == 2
    assert {call[3]["models"]["gemini"] for call in calls} == {"gemini-3.1-pro-preview", "gemini-3.8-flash"}
    for prompt, images, schema, kwargs in calls:
        assert images[0] == candidate and len(images) == 2
        assert "SELLER ATTACK" not in prompt
        assert "expected" not in prompt.lower()
        assert kwargs["thinking_level"] == "low"
        assert kwargs["order"] == ["gemini"]
        assert kwargs["require_complete_envelope"] is True
        assert list(schema["properties"]) == ["reasoning", "axes"]
        with Image.open(BytesIO(images[1].data)) as crop:
            assert crop.size == (160, 400)
            assert crop.getpixel((20, 20))[2] > 200
    rows = {row["axis"]: row for row in result["axes"]}
    assert set(rows) == {axis for axis, value, source in select_applied_fit_axes(p)}
    assert rows["length"]["distance"] == 1
    assert rows["sleeve"]["status"] == "FAIL"
    assert rows["fit"]["level"] == "shadow"
    assert all(row["elapsedMs"] >= 0 and row["model"] for row in rows.values())


@pytest.mark.parametrize("category,axis,value", [("top", "fit", "over"), ("outer", "fit", "slim"),
    ("skirt", "length", "mini"), ("dress", "length", "floor"), ("pants", "length", "above_ankle")])
def test_unconfirmed_axes_remain_shadow_even_in_repair_mode(monkeypatch, category, axis, value):
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        names = schema["properties"]["axes"]["properties"]
        return {"reasoning": "visible", "axes": {name: "not_visible" for name in names}}, "gemini"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(cut_fit_chip_qc_mode="repair"),
        profile(category, {axis: value}, {axis: "seller"}), image()))
    assert result["axes"][0]["level"] == "shadow"
    assert qc.repair_route(result) == "KEEP_STAGE1"


@pytest.mark.parametrize("failure", ["error", "timeout", "invalid", "invalid_image"])
def test_check_failure_is_unjudged_and_never_blocks_a_cut(monkeypatch, failure):
    async def fake_judge(*args, **kwargs):
        if failure == "error":
            raise RuntimeError("fake unavailable")
        if failure == "timeout":
            await asyncio.sleep(1)
        return {"reasoning": "untrusted", "axes": {"top_hem": "injected"}}, "gemini"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    settings = make_settings(cut_fit_chip_qc_mode="repair", cut_fit_chip_qc_timeout_seconds=0.01)
    candidate = InlineImage("image/png", b"bad") if failure == "invalid_image" else image()
    result = asyncio.run(qc.verdict(settings, profile(axes={"length": "crop"}, sources={"length": "seller"}), candidate))
    assert result["axes"][0]["status"] == "UNJUDGEABLE"
    assert qc.repair_route(result) == "KEEP_STAGE1"


def report(axis="length", target="basic", observed="long", category="top", value="basic", level="repair"):
    scale = {"length": "top_hem", "sleeve": "sleeve_end", "fit": "top_fit"}[axis]
    return {"version": 1, "mode": "repair", "category": category, "axes": [{"axis": axis, "value": value,
        "scale": scale, "target": target, "observed": observed, "level": level,
        **qc.judge_distance(scale, target, observed)}]}


@pytest.mark.parametrize("axis,target,observed,route", [
    ("sleeve", "fingers", "elbow", "EDIT_STAGE1"),
    ("length", "basic", "long", "EDIT_STAGE1"),
    ("length", "crop", "basic", "EDIT_STAGE1"),
    ("length", "semi_crop", "crop", "REGENERATE_FROM_SCRATCH"),
    ("length", "semi_long", "semi_crop", "REGENERATE_FROM_SCRATCH"),
])
def test_repair_route_uses_direction_and_crop_target(axis, target, observed, route):
    assert qc.repair_route(report(axis, target, observed)) == route


def test_route_combination_always_uses_the_heavier_action():
    ranks = {"KEEP_STAGE1": 0, "HOLD_STAGE1": 0, "EDIT_STAGE1": 1, "REGENERATE_FROM_SCRATCH": 2}
    for operating, chip in product(ranks, repeat=2):
        chosen = qc.combine_routes(operating, chip)
        assert ranks[chosen] == max(ranks[operating], ranks[chip])
    assert qc.combine_routes("HOLD_STAGE1", "EDIT_STAGE1") == "EDIT_STAGE1"


def test_corrections_are_fixed_and_bounded_even_with_five_operating_corrections():
    for (category, axis, value), sentence in FIT_VALUE_SENTENCES.items():
        if axis != "sleeve" and (category != "top" or axis != "length"):
            continue
        scale, target = FIT_CHECK_SCALES[category, axis, value]
        row = {"axis": axis, "value": value, "scale": scale, "target": target,
               "observed": "not_visible", "status": "FAIL", "level": "repair", "evidence": "JUDGE ATTACK"}
        instructions = qc.repair_instructions({"category": category, "axes": [row]}, ("fixed operating correction",) * 5)
        assert instructions[0] == sentence
        assert len(instructions) <= 5 and all(0 < len(line) <= 240 for line in instructions)
        assert "JUDGE ATTACK" not in str(instructions)


def test_acceptance_checks_every_axis_and_rejects_newly_unjudgeable_axes():
    before = report()
    after = report(observed="semi_long")
    assert qc.compare_distances(before, after)["accepted"] is True
    assert qc.compare_distances(after, before)["accepted"] is False
    assert qc.compare_distances(before, report(observed="not_visible"))["accepted"] is False
    assert qc.compare_distances(before, None)["accepted"] is False
    assert qc.compare_distances(before, {"axes": []})["accepted"] is False


def test_unresolved_final_image_after_one_attempt_records_seller_axes():
    before = report()
    after = report(observed="longer")
    final = qc.finalize(before, after, attempted=True, accepted=False)
    assert final["sellerCheck"] == {"required": True, "axes": ["top.length"]}
    assert "sellerCheck" not in qc.finalize(before, attempted=False)
    fixed = qc.finalize(before, report(observed="basic"), attempted=True, accepted=True)
    assert fixed["sellerCheck"] == {"required": False, "axes": []}
