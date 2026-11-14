import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.agents import cut_output_qc as qc
from app.agents.vision_llm import qc_system_prompt
from conftest import make_settings
from test_cut_output_qc import _img, _model_refs, _plan, _raw


_PRODUCT_AUTHORITY = {
    "garmentLocalColor": True,
    "analysisFitAndProductEvidence": True,
    "productPermanentConstructionStillFinal": True,
}


def _request(*, selected_mannequin=False, sold_product_fit="regular fit"):
    return SimpleNamespace(
        scope=SimpleNamespace(selected_mannequin=selected_mannequin),
        sold_product_fit=sold_product_fit,
        outfit=SimpleNamespace(selected_roles=()),
    )


def _framing(_request):
    return {
        "shot": "full",
        "requestedFraming": "complete head through both feet",
        "faceExposure": "visible",
        "roughFraming": "vertical full-body",
    }


def _context(prompt):
    return json.loads(prompt.split("\nVALIDATED_QC_CONTEXT\n", 1)[1])


def test_confirmed_product_qc_uses_product_color_and_analysis_fit(monkeypatch):
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    source = _plan(declaredFitAxes=["length", "sleeve", "fit"])
    source["attributeOwners"].update(length="productTruth", silhouette="productTruth",
                                     **{"fit.top": "fitProfile"})
    contract = qc._confirmed_authority_contract(qc.normalize_plan(source), _request())
    refs = [qc.LabeledReference("product", _img(b"PRODUCT"))]

    prompt = qc.build_prompt(contract, refs, authority_profile="confirmed_gpt_v1")

    assert contract["attributeOwners"]["color"] == "productTruth"
    assert contract["attributeOwners"]["declaredFit"] == "fitProfile"
    assert contract["attributeOwners"]["silhouette"] == "productTruth"
    assert contract["attributeOwners"]["length"] == "productTruth"
    assert contract["soldProductFit"] == "regular fit"
    assert contract["declaredFitAxisCount"] == 1
    assert contract["confirmedGptProductAuthority"] == _PRODUCT_AUTHORITY
    assert "confirmedGptMannequinAuthority" not in contract
    assert "PRODUCT images alone own" in prompt
    assert "garment-local color" in prompt
    assert "soldProductFit" in prompt
    assert "MANNEQUIN" not in prompt


def test_standalone_confirmed_product_qc_detects_actual_reference_roles():
    contract = qc.normalize_plan(_plan())
    prompt = qc.build_prompt(contract, [qc.LabeledReference("product", _img())],
                             authority_profile="confirmed_gpt_v1")
    assert "selected PRODUCT garment-local color" in prompt
    assert "MANNEQUIN" not in prompt


def test_existing_confirmed_qc_and_privileged_context_remain_byte_identical():
    refs = [qc.LabeledReference("mannequin", _img(b"m")),
            qc.LabeledReference("product", _img(b"p"))]
    prompt = qc.build_prompt(qc.normalize_plan(_plan()), refs,
                             authority_profile="confirmed_gpt_v1")
    assert hashlib.sha256(prompt.encode()).hexdigest() == "28cdf858e017a6f9c3a1ef17a9c68a2a8799fdb35f9d3e8a1541a698a29a6a6d"
    policy = qc_system_prompt("cut", {
        "authorityProfile": "confirmed_gpt_v1",
        "attributeOwners": {"color": "mannequin", "declaredFit": "mannequin",
                            "construction": "productTruth"},
        "referenceAllowedAttributes": [],
    })
    assert hashlib.sha256(policy.encode()).hexdigest() == "fa29c7d2ad93595fc18675a484c810f84036b2535898affe6cad2570a4402a86"


@pytest.mark.parametrize("profile,marker,accepted", [
    ("confirmed_gpt_v1", _PRODUCT_AUTHORITY, True),
    ("generic_v1", _PRODUCT_AUTHORITY, False),
    ("confirmed_gpt_v1", None, False),
    ("confirmed_gpt_v1", {**_PRODUCT_AUTHORITY, "garmentLocalColor": False}, False),
    ("confirmed_gpt_v1", {**_PRODUCT_AUTHORITY, "garmentLocalColor": 1}, False),
    ("confirmed_gpt_v1", {**_PRODUCT_AUTHORITY, "extra": "SELLER_ATTACK"}, False),
])
def test_product_color_privilege_requires_exact_confirmed_marker(profile, marker, accepted):
    metadata = {
        "authorityProfile": profile,
        "attributeOwners": {"color": "productTruth", "declaredFit": "fitProfile",
                            "construction": "productTruth"},
        "referenceAllowedAttributes": [],
    }
    if marker is not None:
        metadata["confirmedGptProductAuthority"] = marker
    prompt = qc_system_prompt("cut", metadata)
    context = _context(prompt)
    assert context["scope"] == ("explicit_fields_only" if accepted else "incomplete_no_additional_authority")
    assert "SELLER_ATTACK" not in prompt
    if accepted:
        assert context["attributeOwners"]["color"] == "productTruth"
        assert context["confirmedGptProductAuthority"] == _PRODUCT_AUTHORITY


@pytest.mark.parametrize("profile,mirror_flag", [("generic_v1", True), ("confirmed_gpt_v1", "true")])
def test_qc_does_not_grant_mirror_authority_to_other_profiles_or_nonboolean_values(profile, mirror_flag):
    context = {
        "authorityProfile": profile,
        "attributeOwners": {"color": "storyboard" if profile == "generic_v1" else "productTruth"},
        "referenceAllowedAttributes": [],
        "sourceMirrored": mirror_flag,
    }
    if profile == "confirmed_gpt_v1":
        context["confirmedGptProductAuthority"] = _PRODUCT_AUTHORITY

    policy = qc_system_prompt("cut", context)
    assert "sourceMirrored" not in _context(policy)
    assert "Judge only PRODUCT as photographed in a mirror" not in policy


def test_product_authority_marker_cannot_mix_with_mannequin_owner():
    context = _context(qc_system_prompt("cut", {
        "authorityProfile": "confirmed_gpt_v1",
        "attributeOwners": {"color": "productTruth", "declaredFit": "mannequin"},
        "confirmedGptProductAuthority": _PRODUCT_AUTHORITY,
        "referenceAllowedAttributes": [],
    }))
    assert context["scope"] == "incomplete_no_additional_authority"


@pytest.mark.parametrize("source_mirrored", [True, False, "true"])
def test_confirmed_product_qc_judges_true_orientation_of_mirror_source_only(monkeypatch, source_mirrored):
    from test_confirmed_gpt_no_mannequin import _packet

    packet = _packet(monkeypatch, analysis={"sourceMirrored": source_mirrored})
    captured = {}

    async def analyze(settings, prompt, images, schema, *, system_prompt, **kwargs):
        captured["policy"] = system_prompt
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    plan = _plan()
    plan["storyboard"]["shot"] = "medium"
    result = asyncio.run(qc.verdict(
        make_settings(), plan, qc.references_from_manifest(packet.manifest, packet.images), _img(b"OUTPUT"),
        authority_profile="confirmed_gpt_v1", confirmed_prompt_input=packet.prompt_input))

    context = _context(captured["policy"])
    assert context["scope"] == "explicit_fields_only"
    if source_mirrored is True:
        assert result["contract"]["sourceMirrored"] is True
        assert context["sourceMirrored"] is True
        assert "readable orientation" in captured["policy"]
        assert "asymmetric" in captured["policy"]
        assert "only PRODUCT" in captured["policy"]
    else:
        assert "sourceMirrored" not in result["contract"]
        assert "sourceMirrored" not in context


@pytest.mark.parametrize("fit", [None, "regular fit"])
def test_product_qc_verdict_repairs_from_product_with_existing_route(monkeypatch, fit):
    monkeypatch.setattr(qc, "confirmed_gpt_framing_contract", _framing)
    captured = {}

    async def analyze(settings, prompt, images, schema, **kwargs):
        captured.update(prompt=prompt, images=images, policy=kwargs["system_prompt"])
        raw = _raw()
        for row in raw["gates"]:
            if row["gate"] in {"garmentConstruction", "garmentColor", "materialTexture",
                                "fitClosureAllowedMutation"}:
                row["status"] = "FAIL"
        return raw, "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    refs = [qc.LabeledReference("product", _img(b"PRODUCT")), *_model_refs(),
            qc.LabeledReference("example", _img(b"EXAMPLE"))]
    plan = _plan(declaredFitAxes=["length", "sleeve", "matchingFit"])
    plan["attributeOwners"].update({"fit.length": "fitProfile", "fit.sleeve": "fitProfile"})
    result = asyncio.run(qc.verdict(
        make_settings(), plan, refs, _img(b"OUTPUT"),
        authority_profile="confirmed_gpt_v1", confirmed_prompt_input=_request(sold_product_fit=fit)))

    assert result["authorityProfile"] == "confirmed_gpt_v1"
    assert result["contract"]["soldProductFit"] == fit
    assert result["contract"]["declaredFitAxisCount"] == (1 if fit else 0)
    assert ("declaredFit" in result["contract"]["attributeOwners"]) is bool(fit)
    assert result["gates"]["recipeIntent"]["status"] == "PASS"
    assert qc.repair_route(result) == "REGENERATE_FROM_SCRATCH"
    instructions = qc.repair_instructions(result)
    assert len(instructions) == 4
    assert all("MANNEQUIN" not in instruction for instruction in instructions)
    assert all("PRODUCT" in instruction for instruction in instructions)
    assert "analysis fit" in instructions[-1]
    assert "MANNEQUIN" not in captured["prompt"]
    assert [image.data for image in captured["images"]] == [
        b"PRODUCT", b"MODEL-FACE", b"MODEL-BODY", b"EXAMPLE", b"OUTPUT"]
    privileged = _context(captured["policy"])
    assert privileged["attributeOwners"]["color"] == "productTruth"
    assert privileged["confirmedGptProductAuthority"] == _PRODUCT_AUTHORITY


@pytest.mark.parametrize("category,fit", [("top", "over"), ("outer", "slim")])
def test_actual_no_mannequin_packet_passes_qc_with_the_same_framing_and_fit(monkeypatch, category, fit):
    from test_confirmed_gpt_no_mannequin import _packet

    packet = _packet(monkeypatch, clothing_type=category,
                     analysis={"fitProfile": {"category": category, "gender": "women",
                               "version": 2, "axes": {"fit": fit, "length": "crop", "sleeve": "cap"}}})

    async def analyze(*args, **kwargs):
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    plan = _plan()
    plan["storyboard"]["shot"] = "medium"
    result = asyncio.run(qc.verdict(
        make_settings(), plan, qc.references_from_manifest(packet.manifest, packet.images), _img(b"OUTPUT"),
        authority_profile="confirmed_gpt_v1", confirmed_prompt_input=packet.prompt_input))

    assert result["passed"] is True
    assert result["contract"]["soldProductFit"] == packet.prompt_input.sold_product_fit
    assert ("oversized volume" if fit == "over" else "a slim version") in result["contract"]["soldProductFit"]
    assert "caps the shoulder" not in result["contract"]["soldProductFit"]
    if category == "outer":
        assert len(result["contract"]["soldProductFit"]) > 200
    assert result["contract"]["declaredFitAxisCount"] == 1
    assert result["contract"]["confirmedExampleFraming"]["shot"] == "medium"
    assert result["contract"]["confirmedGptProductAuthority"] == _PRODUCT_AUTHORITY
