"""Versioned v2 visual observations with deterministic coverage and release checks.

Structural validation cannot prove visual truth. Only the pinned GPT transport
observes pixels; malformed or unavailable observations hold without raw errors.
"""
from __future__ import annotations

import asyncio
from copy import copy, deepcopy
from dataclasses import is_dataclass, replace
import math
import re

import httpx

from . import vision_llm
from .gemini_image import InlineImage
from .wearshot_contract import (VERSION, GARMENT_AXES, GLOBAL_AXES, ContractError,
                               RepairPlan, WearshotContract, image_sha256)
from .wearshot_prompt import authority_description, render_generation, render_repair

_STATUSES = ("PASS", "FAIL", "UNJUDGEABLE")


def review_settings(settings):
    """Copy the dedicated, validated v2 deadline into the shared transport slot."""
    timeout = getattr(settings, "wearshot_qc_timeout_seconds", 180.0)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("wearshot_v2_invalid_qc_timeout")
    if is_dataclass(settings):
        return replace(settings, analysis_timeout_seconds=timeout)
    configured = copy(settings)
    configured.analysis_timeout_seconds = timeout
    return configured


def _object(properties):
    return dict(type="object", additionalProperties=False, required=list(properties), properties=properties)


def _check_schema(na=False):
    return _object(dict(status=dict(type="string", enum=[*_STATUSES, *(["NOT_APPLICABLE"] if na else [])]),
                        evidence=dict(type="string")))


def _not_applicable(contract, axis):
    return (axis == "body" and contract.model_body_key is None or
            axis in {"identity", "expression", "hair"} and contract.frame_lock.face_visibility == "hidden")


def review_schema(contract: WearshotContract, repair_plan: RepairPlan | None = None) -> dict:
    if repair_plan is not None:
        repair_plan.validate(contract)
    string = dict(type="string")
    detail = _object(dict(code=string, evidenceKeys=dict(type="array", items=string),
                          **_check_schema(na=True)["properties"]))
    garment = _object(dict(garmentId=string, mannequinKey=string, sellerKeys=dict(type="array", items=string),
                           **({"lengthReferenceKey": string} if any(g.approved_length_key is not None for g in contract.garments) else {}),
                           checks=_object({axis: _check_schema(na=True) for axis in GARMENT_AXES}),
                           details=dict(type="array", items=detail)))
    globals_ = {}
    for axis in GLOBAL_AXES:
        globals_[axis] = _check_schema()
        if _not_applicable(contract, axis):
            globals_[axis]["properties"]["status"]["enum"] = ["NOT_APPLICABLE"]
    return _object(dict(contractVersion=dict(type="string", enum=[VERSION]),
                        contractFingerprint=dict(type="string", enum=[contract.fingerprint]),
                        garments=dict(type="array", items=garment), globalChecks=_object(globals_),
                        variation=_object(dict(poseChanged=dict(type="boolean"), backgroundChanged=dict(type="boolean"),
                                               gradingOnly=dict(type="boolean"), evidence=string)),
                        protectedChecks=_object({axis: _check_schema() for axis in repair_plan.approved_axes} if repair_plan else {})))


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("invalid coverage")


def _evidence(value):
    if type(value) is not str or not value.strip() or len(value) > 4000:
        raise ValueError("missing evidence")
    return value.strip()[:1200]


def _check(value, na=False):
    _exact(value, ("status", "evidence"))
    if value["status"] not in (("NOT_APPLICABLE",) if na else _STATUSES):
        raise ValueError("invalid status")
    return dict(status=value["status"], evidence=_evidence(value["evidence"]))


def _aggregate(checks):
    statuses = [v["status"] for v in checks]
    status = ("FAIL" if "FAIL" in statuses else "UNJUDGEABLE" if "UNJUDGEABLE" in statuses
              else "PASS" if "PASS" in statuses else "NOT_APPLICABLE")
    return dict(status=status, evidence="Derived from explicit v2 attribute checks.")


def _gates(contract, attributes, valid):
    def keys(*names):
        return [attributes[n] for n in names]
    def local(*axes, matching_only=False):
        return [value for key, value in attributes.items() if key.startswith("garment:") and
                any(key.endswith(":" + a) or a == "detail" and ":detail:" in key for a in axes)
                and (not matching_only or key.split(":")[1] in contract.expected_matching_ids)]
    groups = {
        "fileValidity": [dict(status="PASS", evidence="Decoded image verified.")],
        "recipeIntent": [dict(status="PASS" if valid else "UNJUDGEABLE", evidence="Versioned coverage validation.")],
        "framingCrop": keys("camera", "crop"),
        "framingDirectionFacePose": keys("crop", "pose", "expression"),
        "garmentConstruction": local("structure", "detail"),
        "garmentColor": local("color"), "materialTexture": local("material"),
        "patternHardware": local("detail"), "garmentTextLogo": local("detail"),
        "matchingGarmentIdentity": local(*GARMENT_AXES, "detail", matching_only=True),
        "fitClosureAllowedMutation": local("fit", "length"),
        "modelIdentity": keys("identity"), "modelBodyProportions": keys("body"),
        "anatomyPerspectiveAsymmetry": keys("anatomy"),
        "referenceScopeCaptureClass": keys("capture"),
        # Compatibility only: this v2 projection explicitly uses OR variation semantics.
        "relatedSceneDifferentPlace": keys("variation"),
        "lightingShadowReflectionDrape": keys("light"), "minimumVariation": keys("variation"),
    }
    projected = {name: _aggregate(checks) for name, checks in groups.items()}
    for check in projected.values():
        if check["status"] == "NOT_APPLICABLE":
            check["status"] = "NA"
    return projected


def _binding(contract, candidate_hash, repair_plan):
    return dict(contractVersion=VERSION, contractFingerprint=contract.fingerprint,
                referenceKeys=[r.key for r in contract.references], references=[r.to_dict() for r in contract.references],
                candidateSha256=candidate_hash,
                repairBaseSha256=repair_plan.base_sha256 if repair_plan else None,
                repairPlan=repair_plan.to_dict() if repair_plan else None)


def _unavailable(contract, candidate_hash, repair_plan=None):
    attrs = {key: dict(status="UNJUDGEABLE", evidence="V2 observation unavailable.") for key in contract.all_attribute_keys}
    for axis in contract.all_attribute_keys:
        if axis not in contract.attribute_keys:
            attrs[axis] = dict(status="NOT_APPLICABLE", evidence="Not applicable under bound caller scope.")
    return {**_binding(contract, candidate_hash, repair_plan), "status": "UNJUDGEABLE", "valid": False,
            "attributes": attrs, "passedAttributes": [], "failedAttributes": list(contract.attribute_keys),
            "garments": [], "globalChecks": {}, "variation": None, "protectedChecks": {},
            "gates": _gates(contract, attrs, False), "observations": None,
            "evidence": "V2 observation unavailable or malformed."}


def validate(raw: object, contract: WearshotContract, candidate: InlineImage, *,
             repair_plan: RepairPlan | None = None, base_image: InlineImage | None = None) -> dict:
    """Validate exact observation coverage. Invalid pixels/plans raise ContractError."""
    candidate_hash = image_sha256(candidate)
    if repair_plan is not None:
        repair_plan.validate(contract, base_image)
    elif base_image is not None:
        raise ContractError("wearshot_v2:base_without_plan")
    result = _unavailable(contract, candidate_hash, repair_plan)
    return _validate_observations(raw, contract, result, repair_plan.approved_axes if repair_plan else ())


def _validate_observations(raw, contract, result, protected_axes):
    """Pixel-independent normalization shared with previously bound QC receipts."""
    try:
        _exact(raw, review_schema(contract)["required"])
        if raw["contractVersion"] != VERSION or raw["contractFingerprint"] != contract.fingerprint:
            raise ValueError("wrong contract")
        if type(raw["garments"]) is not list or len(raw["garments"]) != len(contract.garments):
            raise ValueError("wrong garment coverage")
        attributes, garments = {}, []
        for observed, binding in zip(raw["garments"], contract.garments, strict=True):
            length_binding = ({"lengthReferenceKey": binding.approved_length_key or binding.mannequin_key}
                              if any(g.approved_length_key is not None for g in contract.garments) else {})
            _exact(observed, ("garmentId", "mannequinKey", "sellerKeys", "checks", "details", *length_binding))
            if (observed["garmentId"] != binding.garment_id or observed["mannequinKey"] != binding.mannequin_key
                    or observed["sellerKeys"] != list(binding.seller_keys)):
                raise ValueError("wrong garment bindings")
            if any(observed[key] != value for key, value in length_binding.items()):
                raise ValueError("wrong length binding")
            _exact(observed["checks"], GARMENT_AXES)
            checks = {axis: _check(observed["checks"][axis], axis in binding.out_of_frame_axes) for axis in GARMENT_AXES}
            for axis, check in checks.items():
                attributes[f"garment:{binding.garment_id}:{axis}"] = check
            if type(observed["details"]) is not list or len(observed["details"]) != len(binding.essentials):
                raise ValueError("wrong detail coverage")
            details = []
            for detail, essential in zip(observed["details"], binding.essentials, strict=True):
                _exact(detail, ("code", "evidenceKeys", "status", "evidence"))
                if detail["code"] != essential.code or detail["evidenceKeys"] != list(essential.evidence_keys):
                    raise ValueError("wrong detail evidence")
                check = _check({k: detail[k] for k in ("status", "evidence")}, not essential.visible)
                attributes[f"garment:{binding.garment_id}:detail:{essential.code}"] = check
                details.append(dict(code=essential.code, evidenceKeys=list(essential.evidence_keys), **check))
            garments.append(dict(garmentId=binding.garment_id, mannequinKey=binding.mannequin_key,
                                 sellerKeys=list(binding.seller_keys), checks=checks, details=details, **length_binding))
        _exact(raw["globalChecks"], GLOBAL_AXES)
        globals_ = {axis: _check(raw["globalChecks"][axis], _not_applicable(contract, axis)) for axis in GLOBAL_AXES}
        attributes.update(globals_)
        variation = raw["variation"]
        _exact(variation, ("poseChanged", "backgroundChanged", "gradingOnly", "evidence"))
        if any(type(variation[k]) is not bool for k in ("poseChanged", "backgroundChanged", "gradingOnly")):
            raise ValueError("invalid variation observation")
        variation = {**variation, "evidence": _evidence(variation["evidence"])}
        attributes["variation"] = dict(status="PASS" if (variation["poseChanged"] or variation["backgroundChanged"])
                                       and not variation["gradingOnly"] else "FAIL", evidence=variation["evidence"])
        _exact(raw["protectedChecks"], protected_axes)
        protected = {axis: _check(raw["protectedChecks"][axis]) for axis in protected_axes}
        # Protection requires BOTH final authority fidelity and before/after fidelity.
        for axis, check in protected.items():
            attributes[axis] = _aggregate([attributes[axis], check])
        normalized = dict(contractVersion=VERSION, contractFingerprint=contract.fingerprint, garments=garments,
                          globalChecks=globals_, variation=variation, protectedChecks=protected)
        result.update(status=_aggregate(list(attributes.values()))["status"], valid=True,
                      attributes=attributes, garments=garments, globalChecks=globals_, variation=variation,
                      protectedChecks=protected, observations=deepcopy(normalized),
                      passedAttributes=[a for a in contract.attribute_keys if attributes[a]["status"] == "PASS"],
                      failedAttributes=[a for a in contract.attribute_keys if attributes[a]["status"] != "PASS"],
                      gates=_gates(contract, attributes, True), evidence="Validated v2 observation coverage.")
    except (ValueError, TypeError, KeyError):
        pass
    return result


def _prior_receipt(result, contract):
    """Revalidate receipt metadata and combined observations without old base bytes.

The current repair plan binds this receipt's candidate pixels. An earlier base's
bytes are not needed to normalize its already recorded before/after observations;
its exact base hash and bounded plan metadata must still agree within the receipt.
"""
    if type(result) is not dict or result.get("valid") is not True:
        raise ValueError("invalid receipt")
    candidate_hash = result.get("candidateSha256")
    if type(candidate_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", candidate_hash) is None:
        raise ValueError("invalid candidate hash")
    expected = _binding(contract, candidate_hash, None)
    metadata = result.get("repairPlan")
    approved = ()
    if metadata is not None:
        _exact(metadata, ("contractFingerprint", "baseSha256", "failedAxes", "approvedAxes"))
        if metadata["contractFingerprint"] != contract.fingerprint or type(metadata["baseSha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", metadata["baseSha256"]) is None:
            raise ValueError("invalid prior repair binding")
        for key in ("failedAxes", "approvedAxes"):
            axes = metadata[key]
            if type(axes) is not list or any(type(axis) is not str for axis in axes) or len(set(axes)) != len(axes):
                raise ValueError("invalid prior repair axes")
        failed, approved = set(metadata["failedAxes"]), tuple(metadata["approvedAxes"])
        if (not failed or failed & set(approved) or not (failed | set(approved)) <= set(contract.attribute_keys)
                or ("variation" in failed and contract.variation_axis not in failed)):
            raise ValueError("invalid prior repair scope")
        expected.update(repairPlan=metadata, repairBaseSha256=metadata["baseSha256"])
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("prior receipt binding mismatch")
    normalized = _validate_observations(result.get("observations"), contract,
                                        {**_unavailable(contract, candidate_hash), **expected}, approved)
    if not normalized["valid"] or any(result.get(key) != normalized[key] for key in
                                      ("attributes", "protectedChecks", "status", "passedAttributes", "failedAttributes")):
        raise ValueError("prior receipt summary mismatch")
    return normalized


def _bound_result(result, contract, repair_plan=None):
    if type(result) is not dict or not result.get("valid") or result.get("contractVersion") != VERSION:
        return False
    expected = _binding(contract, result.get("candidateSha256"), repair_plan)
    return (all(result.get(k) == v for k, v in expected.items())
            and type(result.get("candidateSha256")) is str and re.fullmatch(r"[0-9a-f]{64}", result["candidateSha256"]) is not None
            and set(result.get("attributes", {})) == set(contract.all_attribute_keys))


def release_allowed(result: dict, contract: WearshotContract, candidate: InlineImage, *,
                    repair_plan: RepairPlan | None = None) -> bool:
    """Full v2 release only. Re-derive checks rather than trusting summary booleans."""
    try:
        if not _bound_result(result, contract, repair_plan) or result["candidateSha256"] != image_sha256(candidate):
            return False
        checked = validate(result.get("observations"), contract, candidate, repair_plan=repair_plan)
        return (checked["valid"] and checked["status"] == "PASS" and not checked["failedAttributes"]
                and all(result.get(k) == checked[k] for k in ("attributes", "protectedChecks", "status", "passedAttributes", "failedAttributes")))
    except (ValueError, TypeError, KeyError):
        return False


def compare_repair(contract: WearshotContract, repair_plan: RepairPlan, before: dict, after: dict) -> bool:
    """Accept scoped progress only with exact base and no protected regression.

This is not release authorization; call release_allowed on the final candidate.
Before may itself be a previous repair receipt, bound to the same authority.
"""
    try:
        repair_plan.validate(contract)
        if before.get("contractVersion") != VERSION or before.get("candidateSha256") != repair_plan.base_sha256:
            return False
        prior = _prior_receipt(before, contract)
        if not _bound_result(after, contract, repair_plan):
            return False
        # Observation validation is pixel-independent; base bytes here only satisfy
        # the decoder boundary. Final candidate bytes are checked by release_allowed.
        normalized_after = validate(after.get("observations"), contract, repair_plan.base_image, repair_plan=repair_plan)
        if not normalized_after["valid"] or any(after.get(k) != normalized_after[k] for k in
                                              ("attributes", "protectedChecks", "status", "passedAttributes", "failedAttributes")):
            return False
        if any(prior["attributes"][axis]["status"] != "PASS" for axis in repair_plan.approved_axes):
            return False
        required = (*repair_plan.failed_axes, *repair_plan.approved_axes)
        return (all(after["attributes"][axis]["status"] == "PASS" for axis in required)
                and set(after.get("protectedChecks", {})) == set(repair_plan.approved_axes)
                and all(after["protectedChecks"][axis]["status"] == "PASS" for axis in repair_plan.approved_axes))
    except (ValueError, TypeError, KeyError):
        return False


async def verdict(settings, contract: WearshotContract, candidate: InlineImage, *,
                  repair_plan: RepairPlan | None = None, base_image: InlineImage | None = None) -> dict:
    """One GPT-only call; preflight/provider failures produce a sanitized hold."""
    model = getattr(settings, "wearshot_qc_model", "gpt-6-astra") or "gpt-6-astra"
    result = _unavailable(contract, None, repair_plan)
    result.update(model=model, provider=None, timeoutSeconds=None, errorCategory=None)
    try:
        settings = review_settings(settings)
        timeout = settings.analysis_timeout_seconds
        result["timeoutSeconds"] = timeout
    except (ValueError, TypeError, OverflowError):
        result.update(errorCategory="invalid_timeout", evidence="V2 QC requires a finite positive deadline.")
        return result
    try:
        result["candidateSha256"] = image_sha256(candidate)
        if repair_plan is not None:
            repair_plan.validate(contract, base_image)
            rendered = render_repair(contract, repair_plan)
        else:
            if base_image is not None:
                raise ContractError("wearshot_v2:base_without_plan")
            rendered = render_generation(contract)
        if not getattr(settings, "openai_api_key", None):
            result["errorCategory"] = "provider_unavailable"
            return result
        if type(model) is not str or not model.startswith("gpt-"):
            result["errorCategory"] = "invalid_model"
            return result
        images = [*rendered.images, candidate]
        prompt = ("Judge the final CANDIDATE against this exact v2 contract. Do not generate or repair an image.\n"
                  + authority_description(contract) + "\nExact attachment order:\n"
                  + "\n".join(f"{i}. {key}" for i, key in enumerate(rendered.reference_keys, 1))
                  + f"\n{len(images)}. candidate — final output to judge\n"
                  "Return exact garment order, binding keys, all checks and every visible essential. "
                  "FAIL means visible mismatch; UNJUDGEABLE means insufficient visible evidence. "
                  "Only caller-declared nonapplicable checks use NOT_APPLICABLE. "
                  "Separate selected-model identity, natural expression, and hair; hair never proves identity. "
                  "Pose/background checks grade macro validity and naturalness, not whether both changed. "
                  "variation compares final to original example: poseChanged/backgroundChanged mean "
                  "genuine small natural changes, not artifacts, grading or garment substitution. "
                  "A repair already varied from source need not vary again. "
                  "protectedChecks compare every protected axis to the exact repair base, including same-light "
                  "color/WB/exposure preservation where protected; always also check final vs bound authorities. "
                  "Do not invent protected attributes or omit a detail/length. Return only the JSON schema.\n"
                  + ("Protected attributes: " + str(repair_plan.approved_axes) + "\n" if repair_plan else "")
                  +
                  "Expected contract fingerprint: " + contract.fingerprint)
        result["provider"] = "gpt"
        async with asyncio.timeout(timeout):
            raw = await vision_llm._call_gpt(settings, model, prompt, images, review_schema(contract, repair_plan), timeout)
        result.update(validate(raw, contract, candidate, repair_plan=repair_plan, base_image=base_image))
        if not result["valid"]:
            result["errorCategory"] = "malformed_observation"
    except (TimeoutError, httpx.TimeoutException):
        result.update(errorCategory="deadline_exceeded", evidence="V2 review deadline exceeded.")
    except Exception:
        # Never copy or log provider responses, URLs, credentials, or exceptions.
        result["evidence"] = "V2 review unavailable."
        result["errorCategory"] = "provider_unavailable" if result["provider"] else "invalid_request"
    return result
