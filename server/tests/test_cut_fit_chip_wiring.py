"""칩·운영 검사 결합과 단 한 번의 교정. 모든 이미지 호출과 판정은 가짜다."""

import asyncio
from copy import deepcopy

import pytest

from app.agents import cut_fit_chip_qc as chips
from app.agents.analysis_fit import FIT_CHECK_SCALES, FIT_VALUE_SENTENCES
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from conftest import make_settings
from test_cut_fit_chip_qc import profile, report
from test_cut_output_qc_wiring import _qc_result, _run_detail_cut, _run_editor
from test_cut_output_qc import _raw


def run_checks(monkeypatch, first=None, second=None, *, operating_before=None, operating_after=None,
               operating_mode="repair", chip_mode="repair", error=None, can_repair=True):
    events, calls = [], []
    first = first or report()
    second = second or report(observed="basic")
    before = operating_before or _qc_result()
    after = operating_after or _qc_result()
    async def fake_chip(settings, p, candidate, **kwargs):
        events.append("chips:" + candidate.data.decode())
        return deepcopy(first if candidate.data == b"stage1" else second)
    async def operating(candidate):
        events.append("operating:" + candidate.data.decode())
        if error == "operating":
            raise RuntimeError("fake unavailable")
        return before if candidate.data == b"stage1" else after
    async def make_stage2(route, instructions):
        events.append("make-stage2")
        calls.append((route, instructions))
        if error == "generation":
            raise RuntimeError("fake unavailable")
        return InlineImage("image/png", b"stage2")
    monkeypatch.setattr(chips, "verdict", fake_chip)
    output, result, warnings = asyncio.run(chips.check_and_repair(
        make_settings(cut_fit_chip_qc_mode=chip_mode), {}, InlineImage("image/png", b"stage1"),
        judge_operating=operating, make_stage2=make_stage2, operating_mode=operating_mode, can_repair=can_repair))
    return output, result, warnings, events, calls


@pytest.mark.parametrize("first,operating_before,route", [
    (report(), None, "EDIT_STAGE1"),
    (report(target="semi_long", observed="semi_crop", value="semi_long"), None, "REGENERATE_FROM_SCRATCH"),
    (report("sleeve", "fingers", "elbow", value="fingers"), None, "EDIT_STAGE1"),
    (report(), _qc_result("garmentConstruction"), "REGENERATE_FROM_SCRATCH"),
])
def test_one_attempt_combines_routes_and_requires_both_comparisons(monkeypatch, first, operating_before, route):
    second = deepcopy(first)
    for row in second["axes"]:
        row.update(observed=row["target"], distance=0, delta=0, status="PASS")
    output, result, warnings, events, calls = run_checks(monkeypatch, first, second, operating_before=operating_before)
    assert output.data == b"stage2"
    assert len(calls) == 1 and calls[0][0] == route
    assert all(len(line) <= 240 for line in calls[0][1]) and len(calls[0][1]) <= 5
    assert events == ["operating:stage1", "chips:stage1", "make-stage2", "operating:stage2", "chips:stage2"]
    assert result["repair"]["accepted"] is True
    assert result["fitChipQc"]["sellerCheck"] == {"required": False, "axes": []}
    assert warnings == []


def test_operating_hold_does_not_suppress_a_chip_failure(monkeypatch):
    hold = _qc_result("framingCrop")
    hold["gates"]["framingCrop"]["status"] = "UNJUDGEABLE"
    output, result, warnings, events, calls = run_checks(monkeypatch, operating_before=hold)
    assert len(calls) == 1 and calls[0][0] == "EDIT_STAGE1"
    assert output.data == b"stage2"


@pytest.mark.parametrize("operating_mode", ["off", "shadow"])
def test_chip_repair_can_supply_the_attempt_but_still_uses_operating_comparison(monkeypatch, operating_mode):
    output, result, warnings, events, calls = run_checks(monkeypatch, operating_mode=operating_mode)
    assert output.data == b"stage2" and len(calls) == 1
    assert events.count("operating:stage1") == events.count("operating:stage2") == 1


def test_recording_mode_never_adds_operating_or_image_calls(monkeypatch):
    first = report(level="shadow")
    output, result, warnings, events, calls = run_checks(monkeypatch, first, chip_mode="shadow", operating_mode="off")
    assert output.data == b"stage1" and calls == []
    assert events == ["chips:stage1"]
    assert "sellerCheck" not in result["fitChipQc"]


def test_shadow_observation_does_not_veto_an_existing_operating_repair(monkeypatch):
    output, result, warnings, events, calls = run_checks(monkeypatch,
        report(level="shadow"), report(observed="longer", level="shadow"),
        operating_before=_qc_result("framingCrop"), chip_mode="shadow")
    assert output.data == b"stage2" and len(calls) == 1
    assert result["repair"]["accepted"] is True
    assert "sellerCheck" not in result["fitChipQc"]


@pytest.mark.parametrize("regression", ["operating", "chip", "unjudgeable", "record_only_axis"])
def test_either_comparison_can_reject_stage2(monkeypatch, regression):
    first, second = report(), report(observed="basic")
    operating_after = _qc_result("garmentColor") if regression == "operating" else _qc_result()
    if regression == "chip":
        second = report(observed="longer")
    if regression == "unjudgeable":
        second = report(observed="not_visible")
    if regression == "record_only_axis":
        first["axes"].extend(report("fit", "over", "over", value="over", level="shadow")["axes"])
        second["axes"].extend(report("fit", "over", "slim", value="over", level="shadow")["axes"])
    output, result, warnings, events, calls = run_checks(monkeypatch, first, second, operating_after=operating_after)
    accepted = regression == "record_only_axis"
    assert len(calls) == 1 and output.data == (b"stage2" if accepted else b"stage1")
    assert result["repair"]["accepted"] is accepted
    assert result["fitChipQc"]["sellerCheck"] == {"required": not accepted, "axes": [] if accepted else ["top.length"]}


def test_second_failure_is_a_seller_signal_without_a_third_attempt(monkeypatch):
    output, result, warnings, events, calls = run_checks(monkeypatch, second=report())
    assert len(calls) == 1 and events.count("chips:stage2") == 1
    assert result["fitChipQc"]["sellerCheck"] == {"required": True, "axes": ["top.length"]}


@pytest.mark.parametrize("error", ["operating", "generation"])
def test_unavailable_check_or_attempt_keeps_stage1_without_repeating_calls(monkeypatch, error):
    output, result, warnings, events, calls = run_checks(monkeypatch, error=error)
    assert output.data == b"stage1" and len(calls) == (0 if error == "operating" else 1)
    assert events.count("operating:stage1") == 1
    assert result["repair"]["accepted"] is False
    assert warnings


def test_button_guard_prevents_an_extra_image_attempt(monkeypatch):
    output, result, warnings, events, calls = run_checks(monkeypatch, can_repair=False)
    assert output.data == b"stage1" and calls == []


def test_detail_worker_runs_chip_check_after_operating_and_persists_report(monkeypatch):
    events = []
    async def operating(*args, **kwargs):
        events.append("operating")
        return _qc_result()
    async def fake_chip(settings, p, candidate, **kwargs):
        events.append("chips")
        return report(level="shadow")
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", operating)
    monkeypatch.setattr(chips, "verdict", fake_chip)
    run = _run_detail_cut(monkeypatch, events=events, qc_mode="shadow",
        settings_overrides={"cut_fit_chip_qc_mode": "shadow"},
        spec={"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
        analysis={"fitProfile": profile(axes={"length": "basic"}, sources={"length": "seller"})})
    assert events == ["generate", "operating", "chips", "save"]
    assert run["result"][3][0]["fitChipQc"]["axes"][0]["axis"] == "length"
    assert run["r2"].saved == [b"INITIAL"]


@pytest.mark.parametrize("mode,manifest", [("off", "1. PRODUCT — front"),
    ("repair", "1. MANNEQUIN — selected")])
def test_detail_worker_keeps_existing_path_when_off_or_mannequin_attached(monkeypatch, mode, manifest):
    async def operating(*args, **kwargs):
        return _qc_result()
    async def forbidden(*args, **kwargs):
        raise AssertionError("chip path must not run")
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", operating)
    monkeypatch.setattr(chips, "check_and_repair", forbidden)
    run = _run_detail_cut(monkeypatch, manifest=manifest, qc_mode="repair",
        settings_overrides={"cut_fit_chip_qc_mode": mode},
        spec={"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
        analysis={"fitProfile": profile(axes={"length": "basic"}, sources={"length": "seller"})})
    assert "fitChipQc" not in run["result"][3][0]
    assert run["r2"].saved == [b"INITIAL"]


@pytest.mark.parametrize("route", ["EDIT_STAGE1", "REGENERATE_FROM_SCRATCH"])
def test_detail_worker_uses_one_real_callback_and_saves_only_adopted_image(monkeypatch, route):
    async def operating(*args, **kwargs):
        return _qc_result()
    async def fake_chip(settings, p, candidate, **kwargs):
        value = "basic" if route == "EDIT_STAGE1" else "semi_long"
        if candidate.data == b"INITIAL":
            return report(target=value, value=value, observed="long" if route == "EDIT_STAGE1" else "semi_crop")
        return report(target=value, value=value, observed=value)
    edits = []
    async def fake_repair(settings, gemini, b, product, source, *, qc_corrections, **kwargs):
        edits.append((source.data, qc_corrections))
        return b"FIXED", "image/png"
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", operating)
    monkeypatch.setattr(chips, "verdict", fake_chip)
    monkeypatch.setattr(dpj.cut_generator, "repair", fake_repair)
    value = "basic" if route == "EDIT_STAGE1" else "semi_long"
    run = _run_detail_cut(monkeypatch, qc_mode="shadow", generated_outputs=[b"INITIAL", b"FIXED"],
        settings_overrides={"cut_fit_chip_qc_mode": "repair"},
        spec={"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
        analysis={"fitProfile": profile(axes={"length": value}, sources={"length": "seller"})})
    assert run["r2"].saved == [b"FIXED"]
    assert run["result"][3][0]["repair"]["route"] == route
    assert len(edits) == (1 if route == "EDIT_STAGE1" else 0)
    assert len(run["generateKwargs"]) == (1 if route == "EDIT_STAGE1" else 2)


@pytest.mark.parametrize("chip_mode", ["off", "repair"])
@pytest.mark.parametrize("axis,value,observed,route", [
    ("sleeve", "fingers", "elbow", "EDIT_STAGE1"),
    ("length", "slight_crop", "semi_crop", "EDIT_STAGE1"),
    ("length", "basic", "long", "EDIT_STAGE1"),
    ("length", "semi_long", "semi_crop", "REGENERATE_FROM_SCRATCH"),
])
def test_detail_worker_uses_the_same_fit_repair_route_with_chips_on_or_off(monkeypatch, chip_mode, axis, value, observed, route):
    _, target = FIT_CHECK_SCALES["top", axis, value]
    gate = "fitClosureAllowedMutation"
    correction = (FIT_VALUE_SENTENCES["top", axis, value],)
    chip_inputs, edited = [], []

    async def fake_judge(settings, prompt, images, schema, **kwargs):
        stage1 = images[-1].data == b"INITIAL"
        raw = _raw()
        if stage1:
            next(row for row in raw["gates"] if row["gate"] == gate)["status"] = "FAIL"
        raw["declaredFitReview"] = {
            f"{axis}Gates": [11] if stage1 else [],
            "observed": {axis: observed if stage1 else target},
        }
        return raw, "fake"

    async def fake_chip(settings, p, candidate, **kwargs):
        chip_inputs.append(candidate.data)
        return report(axis, target, observed if candidate.data == b"INITIAL" else target, value=value)

    async def fake_repair(settings, gemini, b, product, source, *, qc_corrections, **kwargs):
        prompt = dpj.cut_generator.build_qc_repair_prompt(
            b, product, qc_corrections, has_mannequin=kwargs["has_mannequin"], qc_result=kwargs["qc_result"])
        expected = "Make the sleeves of the sold top longer" if axis == "sleeve" else "Make the sold top shorter"
        assert prompt.startswith(expected) and "Keep everything else exactly the same" in prompt
        edited.append((source.data, qc_corrections))
        return b"FIXED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "analyze_with_fallback", fake_judge)
    monkeypatch.setattr(chips, "verdict", fake_chip)
    monkeypatch.setattr(dpj.cut_generator, "repair", fake_repair)
    run = _run_detail_cut(monkeypatch, qc_mode="repair", generated_outputs=[b"INITIAL", b"FIXED"],
        settings_overrides={"cut_fit_chip_qc_mode": chip_mode},
        spec={"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
        analysis={"fitProfile": profile(axes={axis: value}, sources={axis: "seller"})})

    assert run["result"][3][0]["repair"]["route"] == route
    assert run["result"][3][0]["repair"]["accepted"] is True
    assert run["r2"].saved == [b"FIXED"]
    assert chip_inputs == ([b"INITIAL", b"FIXED"] if chip_mode == "repair" else [])
    if route == "EDIT_STAGE1":
        assert edited == [(b"INITIAL", correction)]
        assert len(run["generateKwargs"]) == 1
    else:
        assert edited == []
        assert run["generateKwargs"][1]["qc_corrections"] == correction


@pytest.mark.parametrize("operating_mode", ["off", "repair"])
def test_editor_chip_checks_are_record_only_even_when_global_chip_mode_is_repair(monkeypatch, operating_mode):
    seen = []
    async def fake_chip(settings, p, candidate, **kwargs):
        seen.append((p, candidate.data, kwargs))
        return {**report(level="shadow"), "mode": "shadow"}
    async def forbidden(*args, **kwargs):
        raise AssertionError("editor must not repair")
    monkeypatch.setattr(chips, "verdict", fake_chip)
    monkeypatch.setattr(chips, "check_and_repair", forbidden)
    captured, r2 = _run_editor(monkeypatch, qc_mode=operating_mode, cut_type="horizon",
                               settings_overrides={"cut_fit_chip_qc_mode": "repair"})
    assert len(seen) == 1 and seen[0][2]["observe_only"] is True
    assert r2.saved == [b"INITIAL"]
    assert captured["finalize"]["metadata"]["cutQc"]["fitChipQc"]["mode"] == "shadow"


@pytest.mark.parametrize("chip_mode", ["off", "shadow"])
def test_generic_worker_records_male_slim_fit_and_keeps_generation_sleeve_context(monkeypatch, chip_mode):
    async def fake_judge(*args, **kwargs):
        return _raw(), "fake"
    async def fake_chip(*args, **kwargs):
        return report("fit", "slim", "slim", value="slim", level="shadow")
    monkeypatch.setattr(dpj.cut_output_qc, "analyze_with_fallback", fake_judge)
    monkeypatch.setattr(chips, "verdict", fake_chip)
    p = profile(axes={"fit": "slim"}, sources={"fit": "seller"}, gender="men")
    run = _run_detail_cut(monkeypatch, qc_mode="shadow", settings_overrides={"cut_fit_chip_qc_mode": chip_mode},
        spec={"id": "worn", "cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
        analysis={"fitProfile": p, "fitRead": {"sleeveType": "long"}})
    contract = run["result"][3][0]["contract"]
    assert contract["declaredFitAxes"] == []
    assert contract["recordOnlyFitAxes"] == [{"axis": "fit", "value": "slim"}]
    assert run["generateKwargs"][0]["analysis"]["fitRead"]["sleeveType"] == "long"
