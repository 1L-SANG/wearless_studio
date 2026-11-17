"""선언 칩을 운영 검사에 전달하되 마네킹 계약과 항목 순서는 유지한다."""

import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.agents import cut_output_qc as qc, cut_plan
from app.agents.analysis_fit import build_analysis_fit_block, select_applied_fit_axes
from conftest import make_settings
from test_cut_output_qc import _img, _plan, _raw
from test_confirmed_gpt_product_qc import _framing


def profile(axes=None, sources=None, category="top", gender="women"):
    return {"category": category, "gender": gender, "axes": axes or {}, "axisSources": sources or {}}


@pytest.mark.parametrize("source", ["seller", "measure", "ai", "unsure", None])
def test_generic_contract_only_contains_fixed_targets_from_the_applied_policy(monkeypatch, source):
    p = profile({"fit": "over", "length": "slight_crop", "sleeve": "back_of_hand"},
                {axis: source for axis in ("fit", "length", "sleeve")} if source else {})
    observed = {}
    async def fake_judge(settings, prompt, images, schema, **kwargs):
        observed["prompt"] = prompt
        return _raw(), "fake"
    monkeypatch.setattr(qc, "analyze_with_fallback", fake_judge)
    result = asyncio.run(qc.verdict(make_settings(), _plan(), [qc.LabeledReference("product", _img())],
                                   _img(), fit_profile=p))
    declared = result["contract"]["declaredFitAxes"]
    assert [(d["axis"], d["value"]) for d in declared] == [(axis, value) for axis, value, source in select_applied_fit_axes(p)]
    assert result["contract"]["declaredFitAxisCount"] == len(declared)
    assert all(set(d) == {"axis", "value", "target"} and isinstance(d["target"], str) for d in declared)
    assert "Declared dimensions in declaredFitAxes follow their fixed targets" in observed["prompt"]
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
    assert [d["axis"] for d in contract["declaredFitAxes"]] == ["fit", "length", "sleeve"]
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


def test_confirmed_male_slim_long_sleeve_target_matches_the_actual_generation_paragraph(monkeypatch):
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    p = profile({"fit": "slim"}, {"fit": "seller"}, gender="men")
    request = SimpleNamespace(scope=SimpleNamespace(selected_mannequin=False), analysis_fit_profile=p,
                              fit_read={"sleeveType": "long"}, outfit=SimpleNamespace(selected_roles=()))
    contract = qc._confirmed_authority_contract(qc.normalize_plan(_plan()), request)
    expected = build_analysis_fit_block("top", "men", [("fit", "slim", "seller")], sleeve_type="long")
    assert contract["declaredFitAxes"][0]["target"] == expected
    assert "the short sleeves grip the biceps" not in expected


@pytest.mark.parametrize("authority,expected", [
    ("generic_v1", "4f5fbe5a179bb3d649b8be84fc2ddfdaf6e655dfc50509b2fc94503684033316"),
    ("confirmed_gpt_v1", "28cdf858e017a6f9c3a1ef17a9c68a2a8799fdb35f9d3e8a1541a698a29a6a6d"),
])
def test_mannequin_operating_prompt_is_byte_identical(authority, expected):
    refs = [qc.LabeledReference("mannequin", _img(b"m")), qc.LabeledReference("product", _img(b"p"))]
    rendered = qc.build_prompt(qc.normalize_plan(_plan()), refs, authority_profile=authority)
    assert sha256(rendered.encode()).hexdigest() == expected
    assert "Treat a contact re-performed" not in rendered
