"""Full v2 coverage, natural variation and repair regression are release gates."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import importlib
from types import SimpleNamespace

import pytest

from app.agents import vision_llm
from test_wearshot_contract_v2 import core, packet, png


def qc():
    assert importlib.util.find_spec("app.agents.wearshot_qc"), "v2 QC missing"
    return importlib.import_module("app.agents.wearshot_qc")


def mark(status="PASS"):
    return dict(status=status, evidence="Observed the bound visible region against its owning reference.")


def observed(contract):
    return dict(contractVersion="approved_mannequin_v2", contractFingerprint=contract.fingerprint,
                garments=[dict(garmentId=g.garment_id, mannequinKey=g.mannequin_key, sellerKeys=list(g.seller_keys),
                               checks={axis: mark() for axis in ("color", "fit", "length", "structure", "material")},
                               details=[dict(code=d.code, evidenceKeys=list(d.evidence_keys), **mark()) for d in g.essentials])
                          for g in contract.garments],
                globalChecks={axis: mark("NOT_APPLICABLE" if (axis == "body" and contract.model_body_key is None)
                                        or (axis in {"identity", "expression", "hair"} and contract.frame_lock.face_visibility == "hidden") else "PASS")
                              for axis in ("camera", "crop", "identity", "expression", "hair", "body", "anatomy", "capture", "light", "pose", "background")},
                variation=dict(poseChanged=True, backgroundChanged=False, gradingOnly=False,
                               evidence="A natural arm position changed against the original example."),
                protectedChecks={})


def test_pose_only_is_enough_and_full_coverage_is_releasable():
    q = qc()
    contract, candidate = packet(), png("green")
    result = q.validate(observed(contract), contract, candidate)
    assert q.release_allowed(result, contract, candidate)
    assert result["gates"]["minimumVariation"]["status"] == "PASS"
    assert result["attributes"]["body"]["status"] == "NOT_APPLICABLE"
    assert "body" not in result["passedAttributes"]
    assert result["contractFingerprint"] == contract.fingerprint
    assert result["referenceKeys"] == ["t-anchor", "t-seller", "m-anchor", "m-seller", "face", "example"]
    assert not q.release_allowed(result, contract, png("black"))


@pytest.mark.parametrize("breakage", ["missing_garment", "duplicate_garment", "wrong_anchor", "wrong_seller", "missing_detail", "detail_evidence", "missing_length", "missing_camera", "invented_field", "provider_na", "camera_fail", "crop_fail", "length_fail", "material_fail", "both_unchanged", "grading_only", "fingerprint"])
def test_missing_or_wrong_bound_coverage_and_visual_failures_block_release(breakage):
    q = qc()
    contract, candidate = packet(), png()
    raw = observed(contract)
    if breakage == "missing_garment": raw["garments"].pop()
    if breakage == "duplicate_garment": raw["garments"].append(deepcopy(raw["garments"][0]))
    if breakage == "wrong_anchor": raw["garments"][1]["mannequinKey"] = "t-anchor"
    if breakage == "wrong_seller": raw["garments"][1]["sellerKeys"] = ["t-seller"]
    if breakage == "missing_detail": raw["garments"][0]["details"] = []
    if breakage == "detail_evidence": raw["garments"][0]["details"][0]["evidenceKeys"] = ["m-seller"]
    if breakage == "missing_length": del raw["garments"][0]["checks"]["length"]
    if breakage == "missing_camera": del raw["globalChecks"]["camera"]
    if breakage == "invented_field": raw["trusted"] = True
    if breakage == "provider_na": raw["globalChecks"]["identity"] = mark("NOT_APPLICABLE")
    if breakage == "camera_fail": raw["globalChecks"]["camera"] = mark("FAIL")
    if breakage == "crop_fail": raw["globalChecks"]["crop"] = mark("FAIL")
    if breakage == "length_fail": raw["garments"][1]["checks"]["length"] = mark("FAIL")
    if breakage == "material_fail": raw["garments"][1]["checks"]["material"] = mark("FAIL")
    if breakage == "both_unchanged": raw["variation"]["poseChanged"] = False
    if breakage == "grading_only": raw["variation"]["gradingOnly"] = True
    if breakage == "fingerprint": raw["contractFingerprint"] = "wrong"
    result = q.validate(raw, contract, candidate)
    assert not q.release_allowed(result, contract, candidate)
    assert result["status"] in {"FAIL", "UNJUDGEABLE"}


def test_provider_cannot_certify_body_without_body_reference_or_outpaint_hidden_face():
    q, c = qc(), core()
    contract = packet(frame_lock=c.FrameLock("medium", "hidden", "Face outside crop"))
    candidate = png()
    assert q.release_allowed(q.validate(observed(contract), contract, candidate), contract, candidate)
    raw = observed(contract)
    raw["globalChecks"]["body"] = mark()
    assert not q.release_allowed(q.validate(raw, contract, candidate), contract, candidate)


def test_repair_requires_exact_base_and_protected_checks_even_if_failures_decrease():
    q, c = qc(), core()
    contract, base, candidate = packet(), png("orange"), png("green")
    plan = c.make_repair_plan(contract, base, ("garment:trousers:length",), ("identity", "expression", "garment:top:color", "pose", "background"))
    before_raw = observed(contract)
    before_raw["garments"][1]["checks"]["length"] = mark("FAIL")
    before = q.validate(before_raw, contract, base)
    after_raw = observed(contract)
    after_raw["protectedChecks"] = {axis: mark() for axis in plan.approved_axes}
    after = q.validate(after_raw, contract, candidate, repair_plan=plan, base_image=base)
    assert q.compare_repair(contract, plan, before, after)
    assert q.release_allowed(after, contract, candidate, repair_plan=plan)
    after_raw["protectedChecks"]["expression"] = mark("FAIL")
    regression = q.validate(after_raw, contract, candidate, repair_plan=plan, base_image=base)
    assert not q.compare_repair(contract, plan, before, regression)
    assert not q.release_allowed(regression, contract, candidate, repair_plan=plan)
    with pytest.raises(ValueError): q.validate(after_raw, contract, candidate, repair_plan=plan, base_image=png("blue"))
    assert not q.compare_repair(contract, plan, {**before, "candidateSha256": "wrong"}, after)
    assert not q.compare_repair(contract, plan, {**before, "contractVersion": "generic_v1"}, after)


def test_gpt_transport_uses_exact_packet_order_and_configurable_safe_model(monkeypatch):
    q = qc()
    contract, candidate = packet(), png("green")
    async def transport(settings, model, prompt, images, schema, timeout):
        assert model == "gpt-6-astra"
        assert images == [*(r.image for r in contract.references), candidate]
        assert "7. candidate" in prompt and "partial" in prompt
        assert schema["additionalProperties"] is False
        return observed(contract)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(q.verdict(SimpleNamespace(openai_api_key="test", analysis_timeout_seconds=5), contract, candidate))
    assert q.release_allowed(result, contract, candidate)
    assert result["model"] == "gpt-6-astra" and result["provider"] == "gpt"


@pytest.mark.parametrize("failure", ["provider", "missing_key", "wrong_model", "bad_base"])
def test_provider_errors_are_sanitized_and_preflight_never_calls_provider(monkeypatch, caplog, failure):
    q, c = qc(), core()
    contract, candidate = packet(), png()
    async def transport(*args, **kwargs):
        assert failure == "provider", "preflight must avoid external call"
        raise RuntimeError("secret provider response")
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    settings = SimpleNamespace(openai_api_key=None if failure == "missing_key" else "test", analysis_timeout_seconds=5,
                               wearshot_qc_model="gemini-test" if failure == "wrong_model" else "gpt-6-astra")
    extras = {}
    if failure == "bad_base":
        extras = dict(repair_plan=c.make_repair_plan(contract, png("orange"), ("capture",), ()), base_image=candidate)
    result = asyncio.run(q.verdict(settings, contract, candidate, **extras))
    assert result["status"] == "UNJUDGEABLE"
    assert not q.release_allowed(result, contract, candidate)
    assert "secret provider response" not in str(result) + caplog.text


def test_cropped_trouser_length_is_na_while_visible_top_length_remains_required():
    q, c = qc(), core()
    contract = packet(matching=(c.GarmentBinding("trousers", "m-anchor", ("m-seller",),
                            (c.EssentialDetail("hem", "cuffed", ("m-seller",), visible=False),),
                            out_of_frame_axes=("length",)),))
    candidate = png()
    raw = observed(contract)
    raw["garments"][1]["checks"]["length"] = mark("NOT_APPLICABLE")
    raw["garments"][1]["details"][0]["status"] = "NOT_APPLICABLE"
    result = q.validate(raw, contract, candidate)
    assert q.release_allowed(result, contract, candidate)
    assert "garment:trousers:length" not in result["passedAttributes"]
    assert result["attributes"]["garment:trousers:length"]["status"] == "NOT_APPLICABLE"
    raw["garments"][0]["checks"]["length"] = mark("NOT_APPLICABLE")
    assert not q.release_allowed(q.validate(raw, contract, candidate), contract, candidate)


def test_repair_summary_tampering_cannot_override_failed_observations():
    q, c = qc(), core()
    contract, base, candidate = packet(), png("orange"), png("green")
    plan = c.make_repair_plan(contract, base, ("capture",), ("identity",))
    before_raw = observed(contract)
    before_raw["globalChecks"]["capture"] = mark("FAIL")
    before = q.validate(before_raw, contract, base)
    after_raw = observed(contract)
    after_raw["globalChecks"]["capture"] = mark("FAIL")
    after_raw["protectedChecks"] = {"identity": mark()}
    after = q.validate(after_raw, contract, candidate, repair_plan=plan)
    after["attributes"]["capture"] = mark()
    assert not q.compare_repair(contract, plan, before, after)


def test_repair_transport_binds_base_before_authorities_and_does_not_redemand_variation(monkeypatch):
    q, c = qc(), core()
    contract, base, candidate = packet(), png("orange"), png("green")
    plan = c.make_repair_plan(contract, base, ("capture",), ("pose", "background", "garment:top:color"))
    async def transport(settings, model, prompt, images, schema, timeout):
        assert model == "gpt-selected-qc"
        assert images == [base, *(r.image for r in contract.references), candidate]
        assert "1. repairBase" in prompt and "8. candidate" in prompt
        assert "same-light" in prompt and "need not vary again" in prompt
        assert set(schema["properties"]["protectedChecks"]["required"]) == {"pose", "background", "garment:top:color"}
        raw = observed(contract)
        raw["protectedChecks"] = {axis: mark() for axis in plan.approved_axes}
        return raw
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(q.verdict(SimpleNamespace(openai_api_key="test", wearshot_qc_model="gpt-selected-qc", analysis_timeout_seconds=5),
                                  contract, candidate, repair_plan=plan, base_image=base))
    assert q.release_allowed(result, contract, candidate, repair_plan=plan)
    assert result["repairBaseSha256"] == plan.base_sha256
    assert result["gates"]["modelBodyProportions"]["status"] == "NA"


@pytest.mark.parametrize("raw", [None, [], {}, {"contractVersion": "approved_mannequin_v2"}])
def test_malformed_provider_output_is_an_unjudgeable_hold(raw):
    q = qc()
    contract, candidate = packet(), png()
    result = q.validate(raw, contract, candidate)
    assert result["status"] == "UNJUDGEABLE" and not result["valid"]
    assert not q.release_allowed(result, contract, candidate)


def test_optional_body_is_certified_only_when_exact_body_reference_is_bound():
    q, c = qc(), core()
    original = packet()
    body = c.BoundReference("body", "modelBody", png("purple"), asset_id="owned-body-asset")
    contract = packet(references=(*original.references, body), model_body_key="body")
    candidate = png()
    result = q.validate(observed(contract), contract, candidate)
    assert q.release_allowed(result, contract, candidate)
    assert "body" in result["passedAttributes"]


@pytest.mark.parametrize("prior_state,allowed", [("protected_failure", False), ("forged_summary", False),
                                                 ("wrong_base_binding", False), ("success", True)])
def test_new_approval_retains_prior_repair_protection_outcome_and_binding(prior_state, allowed):
    q, c = qc(), core()
    contract = packet()
    original_base, previous_candidate, candidate = png("orange"), png("green"), png("blue")
    previous_plan = c.make_repair_plan(contract, original_base, ("capture",), ("garment:top:color",))
    previous_raw = observed(contract)
    previous_raw["protectedChecks"] = {"garment:top:color": mark("FAIL" if prior_state in {"protected_failure", "forged_summary"} else "PASS")}
    previous = q.validate(previous_raw, contract, previous_candidate, repair_plan=previous_plan)
    if prior_state == "forged_summary":
        previous["attributes"]["garment:top:color"] = mark()
        previous["passedAttributes"].append("garment:top:color")
        previous["failedAttributes"] = []
        previous["status"] = "PASS"
    if prior_state == "wrong_base_binding":
        previous["repairBaseSha256"] = "0" * 64
    next_plan = c.make_repair_plan(contract, previous_candidate, ("light",), ("garment:top:color",))
    next_raw = observed(contract)
    next_raw["protectedChecks"] = {"garment:top:color": mark()}
    after = q.validate(next_raw, contract, candidate, repair_plan=next_plan)
    assert q.compare_repair(contract, next_plan, previous, after) is allowed


@pytest.mark.parametrize("profile,criterion", [("clean", "natural clean handheld mobile rendering"),
                                              ("soft", "subtly softer microcontrast and restrained sharpening")])
def test_qc_receives_selected_capture_criteria_and_failed_capture_blocks_release(monkeypatch, profile, criterion):
    q = qc()
    contract, candidate = packet(capture_profile=profile), png()
    async def transport(settings, model, prompt, images, schema, timeout):
        assert criterion in prompt
        assert "real face/hair focus and visible garment structure" in prompt
        assert "Unconditional blur, invented fabric texture, and color grading are not authorized" in prompt
        assert ("older phone" in prompt) is (profile == "soft")
        raw = observed(contract)
        raw["globalChecks"]["capture"] = mark("FAIL")
        return raw
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    result = asyncio.run(q.verdict(SimpleNamespace(openai_api_key="test", analysis_timeout_seconds=5), contract, candidate))
    assert result["valid"] and result["attributes"]["capture"]["status"] == "FAIL"
    assert not q.release_allowed(result, contract, candidate)
