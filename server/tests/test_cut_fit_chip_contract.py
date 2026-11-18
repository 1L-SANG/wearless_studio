"""선언 칩을 운영 검사에 전달하되 마네킹 계약과 항목 순서는 유지한다."""

import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.agents import analysis_fit, cut_fit_chip_qc as chip_qc, cut_output_qc as qc, cut_plan
from app.agents.analysis_fit import FIT_CHECK_SCALES, FIT_VALUE_SENTENCES, select_applied_fit_axes
from app.agents.vision_llm import _to_gemini_schema
from conftest import make_settings
from test_cut_output_qc import _img, _model_refs, _plan, _raw
from test_confirmed_gpt_product_qc import _framing


def profile(axes=None, sources=None, category="top", gender="women"):
    return {"category": category, "gender": gender, "axes": axes or {}, "axisSources": sources or {}}


@pytest.mark.parametrize("source", ["seller", "measure", "ai", "unsure", None])
def test_generic_contract_only_contains_chips_from_the_applied_policy(monkeypatch, source):
    p = profile({"fit": "over", "length": "slight_crop", "sleeve": "back_of_hand"},
                {axis: source for axis in ("fit", "length", "sleeve")} if source else {})
    observed = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed["prompt"] = prompt
        return _raw(), "fake"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(), _plan(), [qc.LabeledReference("product", _img())],
                                   _img(), fit_profile=p))
    contract = result["contract"]
    declared = contract["declaredFitAxes"] + contract.get("recordOnlyFitAxes", [])
    assert {(d["axis"], d["value"]) for d in declared} == {(axis, value) for axis, value, _ in select_applied_fit_axes(p)}
    assert contract["declaredFitAxisCount"] == len(declared)
    assert "Treat a contact re-performed on a real part of the current outfit" in observed["prompt"]
    assert list(result["gates"]) == list(qc.GATES)


def test_confirmed_contract_uses_all_applied_axes_and_preserves_visible_size_authority(monkeypatch):
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    p = profile({"fit": "over", "length": "crop", "sleeve": "wrist"},
                {axis: "seller" for axis in ("fit", "length", "sleeve")})
    request = SimpleNamespace(scope=SimpleNamespace(selected_mannequin=False), analysis_fit_profile=p,
                              outfit=SimpleNamespace(selected_roles=()), sold_product_fit="UNTRUSTED LEGACY TEXT")
    contract = qc._confirmed_authority_contract(qc.normalize_plan(_plan()), request)
    rendered = qc.build_prompt(contract, [qc.LabeledReference("product", _img())], authority_profile="confirmed_gpt_v1")
    assert contract["declaredFitAxisCount"] == 3
    assert [d["axis"] for d in contract["declaredFitAxes"]] == ["length", "sleeve"]
    assert contract["recordOnlyFitAxes"] == [{"axis": "fit", "value": "over"}]
    assert contract["attributeOwners"]["length"] == "fitProfile"
    assert contract["attributeOwners"]["declaredFit"] == "fitProfile"
    assert "clearly visible garment size" in rendered
    assert "soldProductFit" not in rendered
    assert "UNTRUSTED" not in rendered
    assert "Treat a contact re-performed on a real part of the current outfit" in rendered


def test_invalid_values_cannot_supply_a_target_or_an_owner(monkeypatch):
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    request = SimpleNamespace(scope=SimpleNamespace(selected_mannequin=False), outfit=SimpleNamespace(selected_roles=()),
                              analysis_fit_profile=profile({"length": "SELLER ATTACK"}, {"length": "seller"}))
    contract = qc._confirmed_authority_contract(qc.normalize_plan(_plan()), request)
    assert contract["declaredFitAxes"] == []
    assert contract["declaredFitAxisCount"] == 0
    assert contract["attributeOwners"].get("length") != "fitProfile"
    assert "SELLER ATTACK" not in str(contract)


def test_compiled_generic_plan_carries_values_to_qc_without_serializing_raw_profile(monkeypatch):
    p = profile({"length": "crop"}, {"length": "seller"})
    p["sellerNote"] = "SELLER ATTACK"
    plan = cut_plan.compile_cut_plan({"cutType": "horizon", "shot": "full", "direction": "front", "faceExposure": "show"},
                                     "top", fit_profile=p, has_mannequin=False)
    observed = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed.update(kwargs)
        return _raw(), "fake"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(), plan, [qc.LabeledReference("product", _img())], _img()))
    assert [d["value"] for d in result["contract"]["declaredFitAxes"]] == ["crop"]
    assert "SELLER ATTACK" not in str(plan.to_dict()) + str(result["contract"])
    policy = json.loads(observed["system_prompt"].split("\nVALIDATED_QC_CONTEXT\n", 1)[1])
    assert policy["scope"] == "explicit_fields_only"
    assert policy["attributeOwners"]["length"] == "fitProfile"


_FIT_CHIP_CASES = [
    ("pants", {"length": "ankle"}, ()),
    ("skirt", {"length": "midi"}, ()),
    ("dress", {"length": "midi", "sleeve": "wrist"}, ("sleeve",)),
    ("top", {"fit": "over"}, ()),
    ("top", {"fit": "slim", "length": "slight_crop", "sleeve": "above_wrist"}, ("length", "sleeve")),
    ("outer", {"fit": "semi_over", "sleeve": "cap_short"}, ("sleeve",)),
]


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
@pytest.mark.parametrize("category,axes,enforced", _FIT_CHIP_CASES)
def test_operating_contract_only_sets_enforced_chip_targets(monkeypatch, authority, category, axes, enforced):
    p = profile(axes, dict.fromkeys(axes, "seller"), category=category)
    contract, prompt, schema, _ = _operating_verdict(monkeypatch, p, _raw(), authority)

    assert [row["axis"] for row in contract["declaredFitAxes"]] == list(enforced)
    assert contract["recordOnlyFitAxes"] == [{"axis": axis, "value": value}
                                           for axis, value in axes.items() if axis not in enforced]
    assert contract["declaredFitAxisCount"] == len(axes)
    assert "cannot be a reason to fail any gate" in prompt
    assert "undeclared dimensions" in prompt
    assert "fixed targets" not in prompt
    for number, gate in enumerate(qc.GATES, 1):
        assert f"\n{number}. {gate} " in prompt
    for axis, value in axes.items():
        if axis not in enforced:
            assert FIT_VALUE_SENTENCES[category, axis, value] not in prompt
    review = schema["properties"]["declaredFitReview"]
    for axis in axes:
        assert review["properties"][f"{axis}Gates"] == {"type": "array", "items": {"type": "integer"}}
        assert f"{axis}Gates" in prompt
    if enforced:
        assert "two or more positions" in prompt
        assert "exactly one of the target and observed positions is crop" in prompt
        for row in contract["declaredFitAxes"]:
            axis, value = row["axis"], row["value"]
            scale, target = FIT_CHECK_SCALES[category, axis, value]
            assert (row["scale"], row["scaleTarget"], row["target"]) == (
                scale, target, FIT_VALUE_SENTENCES[category, axis, value])
            assert review["properties"]["observed"]["properties"][axis]["enum"] == list(chip_qc.SCALES[scale]["values"])
            positions = [prompt.index(f"{name} = {chip_qc.SCALES[scale]['values'][name]}")
                         for name in chip_qc.ORDERS[scale]]
            assert positions == sorted(positions)
    else:
        assert "observed" not in review["properties"]


def _schema_nodes(node):
    yield node
    for child in node.get("properties", {}).values():
        yield from _schema_nodes(child)
    if "items" in node:
        yield from _schema_nodes(node["items"])


@pytest.mark.parametrize("category,axes", [(category, axes) for category, axes, _ in _FIT_CHIP_CASES])
def test_declared_fit_schema_adds_at_most_seven_fields_twenty_enum_values_and_only_integer_arrays(category, axes):
    p = profile(axes, dict.fromkeys(axes, "seller"), category=category)
    contract = qc._analysis_fit_contract({"attributeOwners": {}}, p)
    schema = qc.qc_schema(repair_parts=True, fit_contract=contract)
    baseline = qc.qc_schema(repair_parts=True)
    review = schema["properties"].pop("declaredFitReview")
    schema["required"].remove("declaredFitReview")
    # 칩이 있으면 gates 배열의 길이 제한을 뺀다(Gemini가 세 축을 400으로 거절해서, 2026-11-18 실측).
    assert "minItems" not in schema["properties"]["gates"] and "maxItems" not in schema["properties"]["gates"]
    del baseline["properties"]["gates"]["minItems"], baseline["properties"]["gates"]["maxItems"]
    assert schema == baseline
    for shape, integer_type in ((review, "integer"), (_to_gemini_schema(review), "INTEGER")):
        nodes = list(_schema_nodes(shape))
        assert all(node["items"] == {"type": integer_type}
                   and "minItems" not in node and "maxItems" not in node
                   for node in nodes if node["type"].lower() == "array")
        assert 1 + sum(len(node.get("properties", {})) for node in nodes) <= 7
        assert sum(len(node.get("enum", [])) for node in nodes) <= 20


def _operating_verdict(monkeypatch, p, raw, authority="generic_v1", *, plan=None):
    captured = {}

    async def fake_judge(settings, prompt, images, schema, **kwargs):
        captured.update(prompt=prompt, schema=schema)
        return raw, "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    kwargs = {"fit_profile": p}
    if authority == "confirmed_gpt_v1":
        monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
        kwargs["confirmed_prompt_input"] = SimpleNamespace(
            scope=SimpleNamespace(selected_mannequin=False), analysis_fit_profile=p,
            outfit=SimpleNamespace(selected_roles=()))
    refs = [qc.LabeledReference("product", _img()), *_model_refs(), qc.LabeledReference("example", _img())]
    result = asyncio.run(qc.verdict(make_settings(), plan or _plan(), refs, _img(), authority_profile=authority, **kwargs))
    return result["contract"], captured["prompt"], captured["schema"], result


def test_both_detectors_use_the_same_enforcement_policy(monkeypatch):
    monkeypatch.setattr(analysis_fit, "is_enforced_fit_axis", lambda category, axis: axis == "fit")
    p = profile({"fit": "over", "sleeve": "wrist"}, {"fit": "seller", "sleeve": "seller"})
    report = asyncio.run(chip_qc.verdict(make_settings(cut_fit_chip_qc_mode="repair"), p, _img()))
    assert {row["axis"]: row["level"] for row in report["axes"]} == {"fit": "repair", "sleeve": "shadow"}
    contract, _, _, _ = _operating_verdict(monkeypatch, p, _raw())
    assert [row["axis"] for row in contract["declaredFitAxes"]] == ["fit"]


def _failed_gate(raw, gate):
    row = next(row for row in raw["gates"] if row["gate"] == gate)
    row.update(status="FAIL", evidence="The pants hem covers too much of the shoe; the logo is also wrong.")


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
@pytest.mark.parametrize("gate,review,expected", [
    ("fitClosureAllowedMutation", {"lengthGates": [11]}, "KEEP_STAGE1"),
    ("garmentConstruction", {"lengthGates": [5]}, "KEEP_STAGE1"),
    ("garmentTextLogo", {"lengthGates": [9]}, "KEEP_STAGE1"),
    ("modelBodyProportions", {"lengthGates": [13]}, "KEEP_STAGE1"),
    ("garmentTextLogo", {"lengthGates": []}, "REGENERATE_FROM_SCRATCH"),
    ("garmentTextLogo", None, "REGENERATE_FROM_SCRATCH"),
    ("fitClosureAllowedMutation", {"lengthGates": [11], "sleeveGates": [11]}, "REGENERATE_FROM_SCRATCH"),
])
def test_typed_fit_causes_do_not_block_for_record_only_axes(monkeypatch, authority, gate, review, expected):
    p = profile({"length": "ankle"}, {"length": "seller"}, category="pants")
    raw = _raw()
    _failed_gate(raw, gate)
    raw["gates"].reverse()  # Ordinals come from the prompt, independent of response order.
    if review is not None:
        raw["declaredFitReview"] = review
    _, _, _, result = _operating_verdict(monkeypatch, p, raw, authority)
    assert qc.repair_route(result) == expected
    assert list(result["gates"]) == list(qc.GATES)
    if expected == "KEEP_STAGE1":
        assert result["passed"] is True
        assert result["correctionPatch"] is None


@pytest.mark.parametrize("status,evidence,missing_matching,expected", [
    ("UNJUDGEABLE", "The trouser hem is hidden.", False, "KEEP_STAGE1"),
    ("FAIL", "", False, "HOLD_STAGE1"),
    ("FAIL", "The hem does not match.", True, "HOLD_STAGE1"),
])
def test_fit_review_only_overrides_a_valid_judge_attribution(monkeypatch, status, evidence, missing_matching, expected):
    p = profile({"length": "ankle"}, {"length": "seller"}, category="pants")
    raw = _raw()
    gate = "fitClosureAllowedMutation"
    next(row for row in raw["gates"] if row["gate"] == gate).update(status=status, evidence=evidence)
    raw["declaredFitReview"] = {"lengthGates": [11]}
    plan = _plan()
    if missing_matching:
        plan["storyboard"]["matching"] = ["missing-match"]
    _, _, _, result = _operating_verdict(monkeypatch, p, raw, plan=plan)
    assert qc.repair_route(result) == expected
    if missing_matching:
        assert result["gates"][gate]["status"] == "UNJUDGEABLE"


@pytest.mark.parametrize("target,observed,judge_fail,expected", [
    ("basic", "semi_long", True, "KEEP_STAGE1"),
    ("basic", "long", True, "EDIT_STAGE1"),
    ("basic", "long", False, "EDIT_STAGE1"),
    ("slight_crop", "semi_crop", True, "EDIT_STAGE1"),
    ("semi_crop", "crop", True, "REGENERATE_FROM_SCRATCH"),
    ("crop", "crop", True, "KEEP_STAGE1"),
    ("crop", "not_visible", True, "KEEP_STAGE1"),
    ("crop", None, True, "REGENERATE_FROM_SCRATCH"),
])
def test_server_scores_enforced_observations_with_chip_distance(monkeypatch, target, observed, judge_fail, expected):
    p = profile({"length": target}, {"length": "seller"})
    raw = _raw()
    gate = "fitClosureAllowedMutation"
    if judge_fail:
        _failed_gate(raw, gate)
    raw["declaredFitReview"] = {
        "lengthGates": [11] if judge_fail else [],
        "observed": {} if observed is None else {"length": observed},
    }
    _, _, _, result = _operating_verdict(monkeypatch, p, raw)
    assert qc.repair_route(result) == expected


def test_record_only_fit_does_not_change_the_sleeve_edit_route(monkeypatch):
    p = profile({"fit": "over", "sleeve": "wrist"}, {"fit": "seller", "sleeve": "seller"})
    raw = _raw()
    _failed_gate(raw, "fitClosureAllowedMutation")
    raw["declaredFitReview"] = {"fitGates": [11], "sleeveGates": [11], "observed": {"sleeve": "elbow"}}
    _, _, _, result = _operating_verdict(monkeypatch, p, raw)
    assert qc.repair_route(result) == "EDIT_STAGE1"
    assert qc.repair_instructions(result) == (FIT_VALUE_SENTENCES["top", "sleeve", "wrist"],)


@pytest.mark.parametrize("cause", ["other_gate", "other_failure_in_gate", "missing", "unconfirmed_axis", "duplicate",
                                  "out_of_range", "non_integer", "missing_axis_field"])
def test_enforced_fit_failure_keeps_existing_route_when_any_cause_is_unverified(monkeypatch, cause):
    p = profile({"length": "basic", "sleeve": "wrist"}, {"length": "seller", "sleeve": "seller"})
    raw = _raw()
    gate = "fitClosureAllowedMutation"
    _failed_gate(raw, gate)
    review = {"lengthGates": [11], "sleeveGates": [], "observed": {"length": "long", "sleeve": "wrist"}}
    if cause == "other_gate":
        _failed_gate(raw, "garmentTextLogo")
    if cause == "other_failure_in_gate":
        review["lengthGates"] = []
    if cause == "unconfirmed_axis":
        review["sleeveGates"] = [11]
    if cause == "duplicate":
        review["lengthGates"] = [11, 11]
    if cause == "out_of_range":
        review["lengthGates"] = [11, 18]
    if cause == "non_integer":
        review["lengthGates"] = [11, True]
    if cause == "missing_axis_field":
        del review["sleeveGates"]
    if cause != "missing":
        raw["declaredFitReview"] = review
    _, _, _, result = _operating_verdict(monkeypatch, p, raw)
    assert qc.repair_route(result) == "REGENERATE_FROM_SCRATCH"
    assert FIT_VALUE_SENTENCES["top", "length", "basic"] not in qc.repair_instructions(result)


@pytest.mark.parametrize("length_observed,expected", [
    ("semi_crop", "REGENERATE_FROM_SCRATCH"), ("longer", "EDIT_STAGE1"),
])
def test_verified_fit_axes_choose_one_route_and_each_fixed_sentence(monkeypatch, length_observed, expected):
    axes = {"length": "semi_long", "sleeve": "fingers"}
    raw = _raw()
    _failed_gate(raw, "garmentConstruction")
    _failed_gate(raw, "fitClosureAllowedMutation")
    raw["declaredFitReview"] = {
        "lengthGates": [5, 11],
        "sleeveGates": [11],
        "observed": {"length": length_observed, "sleeve": "elbow"},
    }
    _, _, _, result = _operating_verdict(monkeypatch, profile(axes, dict.fromkeys(axes, "seller")), raw)
    assert qc.repair_route(result) == expected
    assert qc.repair_instructions(result) == tuple(FIT_VALUE_SENTENCES["top", axis, value] for axis, value in axes.items())


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
def test_non_chip_repairs_do_not_target_the_record_only_dimension(monkeypatch, authority):
    p = profile({"length": "ankle"}, {"length": "seller"}, category="pants")
    raw = _raw()
    for gate in ("garmentConstruction", "fitClosureAllowedMutation"):
        _failed_gate(raw, gate)
    raw["declaredFitReview"] = {"lengthGates": []}
    _, _, _, result = _operating_verdict(monkeypatch, p, raw, authority)
    instructions = qc.repair_instructions(result)
    assert len(instructions) == 2
    assert all("undeclared dimensions" in text for text in instructions)
    assert all("ankle" not in text and "fixed" not in text and "lengths" not in text for text in instructions)
    assert "storyboard closure" in instructions[1]


@pytest.mark.parametrize("authority,expected", [
    ("generic_v1", "4f5fbe5a179bb3d649b8be84fc2ddfdaf6e655dfc50509b2fc94503684033316"),
    ("confirmed_gpt_v1", "28cdf858e017a6f9c3a1ef17a9c68a2a8799fdb35f9d3e8a1541a698a29a6a6d"),
])
def test_mannequin_operating_prompt_is_byte_identical(authority, expected):
    refs = [qc.LabeledReference("mannequin", _img(b"m")), qc.LabeledReference("product", _img(b"p"))]
    rendered = qc.build_prompt(qc.normalize_plan(_plan()), refs, authority_profile=authority)
    assert sha256(rendered.encode()).hexdigest() == expected
    assert "Treat a contact re-performed" not in rendered


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
def test_mannequin_verdict_ignores_analysis_chips(monkeypatch, authority):
    calls = []

    async def fake_judge(settings, prompt, images, schema, *, system_prompt, **kwargs):
        calls.append((prompt, schema, system_prompt))
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    refs = [qc.LabeledReference("mannequin", _img()), qc.LabeledReference("product", _img()),
            *_model_refs(), qc.LabeledReference("example", _img())]
    for p in (None, profile({"length": "crop", "fit": "over"}, {"length": "seller", "fit": "seller"})):
        request = (SimpleNamespace(scope=SimpleNamespace(selected_mannequin=True), analysis_fit_profile=p,
                                   outfit=SimpleNamespace(selected_roles=()))
                   if authority == "confirmed_gpt_v1" else None)
        result = asyncio.run(qc.verdict(make_settings(), _plan(), refs, _img(), authority_profile=authority,
                                       confirmed_prompt_input=request, fit_profile=p))
        assert result["passed"] is True
    assert calls[0] == calls[1]
    assert calls[0][1] == qc.qc_schema()
