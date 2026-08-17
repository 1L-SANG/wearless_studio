"""Independent hard-gate QC for one generated storyboard cut.

This module is wired after AG-06 in optional shadow or repair mode. It accepts the compiled
cut-plan authority contract, labelled reference pixels and one candidate, then asks
the shared vision tier for an auditable gate-by-gate verdict.  The producer prompt and
producer verdict are not inputs: the judge sees only the contract and the evidence it needs.

Correction operations are selected from fixed templates.  Vision evidence is retained
for audit, but is never interpolated into a later generation prompt; this prevents visible
seller text (or model-produced instructions) from becoming a prompt-injection channel.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback


GATES = (
    "fileValidity",
    "recipeIntent",
    "framingDirectionFacePose",
    "garmentConstruction",
    "garmentColor",
    "materialTexture",
    "patternHardware",
    "garmentTextLogo",
    "matchingGarmentIdentity",
    "fitClosureAllowedMutation",
    "modelIdentity",
    "modelBodyProportions",
    "anatomyPerspectiveAsymmetry",
    "referenceScopeCaptureClass",
    "relatedSceneDifferentPlace",
    "lightingShadowReflectionDrape",
)
STATUSES = ("PASS", "FAIL", "NA", "UNJUDGEABLE")
REFERENCE_ROLES = (
    "product", "mannequin", "modelFace", "modelBody", "matching", "example", "plate",
)
_REFERENCE_ROLE_LABELS = {
    "product": "PRODUCT",
    "mannequin": "MANNEQUIN (coarse worn-geometry prior only)",
    "modelFace": "MODEL FACE",
    "modelBody": "MODEL FULL BODY",
    "matching": "MATCHING",
    "example": "EXAMPLE",
    "plate": "PLATE",
}

MAX_EVIDENCE_LENGTH = 240
MAX_CORRECTION_OPERATIONS = 5
MAX_CORRECTION_LENGTH = 240

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "cut_output_qc_v1.txt")

_RECIPES = frozenset({"styling", "horizon", "product"})
_CLOTHING_TYPES = frozenset({"top", "bottom", "outer", "dress"})
_CAPTURE_MODES = frozenset({"lifestyle", "mirrorSelfie", "studio"})
_PRODUCT_VARIANTS = frozenset({"ghost", "detail"})
_REFERENCE_MODES = frozenset({"none", "all", "pose", "bg"})
_SHOTS = frozenset({"full", "medium", "ghost", "detail"})
_DIRECTIONS = frozenset({"front", "side", "back"})
_FACES = frozenset({"same", "show", "hide"})
_CLOSURES = frozenset({"open", "partial", "closed"})
_OWNERS = frozenset({
    "productTruth", "storyboard", "fitProfile", "reference", "spaceSet", "recipe",
    "modelFace", "modelFullBody",
})
_OWNER_ATTRIBUTES = frozenset({
    "construction", "material", "pattern", "hardware", "textLogo", "color",
    "direction", "shot", "face", "model", "matching", "outerClosure", "pose",
    "camera", "scene", "light", "captureTone", "sceneContinuity", "faceIdentity",
    "bodyProportions",
})
_REFERENCE_ATTRIBUTES = frozenset({"pose", "camera", "scene", "light", "captureTone"})

_GARMENT_GATES = (
    "garmentConstruction",
    "garmentColor",
    "materialTexture",
    "patternHardware",
    "garmentTextLogo",
    "fitClosureAllowedMutation",
)

# These strings, rather than provider-written evidence, are the only values eligible for
# retry prompt wiring.  Keep them generic: PRODUCT/MODEL/etc. are image roles, not seller text.
_CORRECTIONS = MappingProxyType({
    "fileValidity": "Regenerate one complete, decodable image with no blank, truncated, or corrupt region.",
    "recipeIntent": "Regenerate using only the compiled recipe and product subtype or capture mode.",
    "framingDirectionFacePose": (
        "Restore the storyboard shot, direction, face handling, and effective pose; storyboard "
        "controls override example pixels."
    ),
    "garmentConstruction": (
        "Restore the PRODUCT garment's silhouette, panels, neckline, sleeves, seams, "
        "lengths, and construction."
    ),
    "garmentColor": "Restore the target PRODUCT color without borrowing color from an example or scene.",
    "materialTexture": (
        "Restore PRODUCT-evidenced crinkle, weave or holes, edge thickness, layering, roughness, "
        "gloss, translucency, weight, and drape; do not leave a flat 2-D surface."
    ),
    "patternHardware": (
        "Restore PRODUCT pattern scale and placement plus the exact count, type, and placement of hardware."
    ),
    "garmentTextLogo": (
        "Restore PRODUCT garment text and logos exactly from the product references in true "
        "readable orientation; add no caption, overlay, or watermark."
    ),
    "matchingGarmentIdentity": (
        "Restore the selected MATCHING garment's construction, color, material, pattern, hardware, "
        "fit, text, graphics, embroidery, and logos exactly from the MATCHING reference."
    ),
    "fitClosureAllowedMutation": (
        "Apply only compiled fit mutations and the storyboard closure state; preserve every "
        "undeclared garment axis."
    ),
    "modelIdentity": "Restore visible identity only from the selected MODEL FACE evidence.",
    "modelBodyProportions": (
        "Restore stature and visible head-to-body, shoulder, torso, waist, hip, arm, and leg "
        "proportions only from selected MODEL FULL BODY evidence; never borrow another role's body."
    ),
    "anatomyPerspectiveAsymmetry": (
        "Repair anatomy, contacts, perspective, and intended left-right asymmetry without "
        "changing the cut plan."
    ),
    "referenceScopeCaptureClass": (
        "Use each reference only for its compiled attributes and restore the requested capture "
        "class without leakage."
    ),
    "relatedSceneDifferentPlace": (
        "Keep the EXAMPLE scene relationship but make a coherent different place in the same "
        "visual family. Remove near-copying or unrelated drift. Do not use change quotas or force "
        "added, moved, duplicated, or awkwardly staged objects."
    ),
    "lightingShadowReflectionDrape": (
        "Restore pose-driven tension, compression and asymmetric folds plus coherent self, "
        "reflection, contact, and cast shadows under the owned scene light."
    ),
})

# 이 세 축만 1차 이미지를 직접 편집한다. 의류·모델 정체성·장소·레시피처럼 원본
# 근거를 다시 봐야 하는 실패는 scratch 재생성으로 보낸다.
_EDIT_STAGE1_GATES = frozenset({
    "framingDirectionFacePose",
    "anatomyPerspectiveAsymmetry",
    "lightingShadowReflectionDrape",
})


@dataclass(frozen=True)
class LabeledReference:
    """One reference image and its authority role, kept in caller-supplied order."""

    role: str
    image: InlineImage


def references_from_manifest(
    manifest: str, images: Sequence[InlineImage],
) -> list[LabeledReference]:
    """Convert the generation manifest to QC roles without forwarding its prose.

    ``build_manifest`` and the image list are a positional contract, so a missing,
    duplicated or unknown line must not silently shift authority to a different image.
    MOOD is intentionally dropped: it is not garment/model/example/plate truth for this
    independent candidate judge.
    """

    if not isinstance(manifest, str) or not manifest.strip():
        raise VisionError("cut_output_qc: empty image manifest")
    if not isinstance(images, Sequence):
        raise VisionError("cut_output_qc: images must be an ordered sequence")
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    if len(lines) != len(images):
        raise VisionError(
            f"cut_output_qc: manifest/image count mismatch ({len(lines)} != {len(images)})"
        )

    references: list[LabeledReference] = []
    for expected_number, (line, image) in enumerate(zip(lines, images), start=1):
        match = re.fullmatch(r"(\d+)\.\s+(.+)", line)
        if not match or int(match.group(1)) != expected_number:
            raise VisionError("cut_output_qc: invalid image manifest numbering")
        if not _valid_image(image):
            raise VisionError(f"cut_output_qc: invalid manifest image {expected_number}")
        label = match.group(2)
        role: str | None
        if label.startswith(("PRODUCT ", "PRODUCT —")):
            role = "product"
        elif label.startswith(("MANNEQUIN ", "MANNEQUIN —")):
            role = "mannequin"
        elif label.startswith(("MODEL FULL BODY ", "MODEL FULL BODY —")):
            role = "modelBody"
        elif label.startswith(("MODEL FACE ", "MODEL FACE —", "MODEL SHEET ",
                               "MODEL SHEET —", "MODEL ", "MODEL —")):
            role = "modelFace"
        elif label.startswith(("MATCHING ", "MATCHING —")):
            role = "matching"
        elif label.startswith("POSE CONTROL"):
            role = "example"
        elif label.startswith(("EXAMPLE REFERENCE (scope: bg)", "EXAMPLE (scope: bg)")):
            role = "plate"
        elif label.startswith(("EXAMPLE REFERENCE (scope: all)", "EXAMPLE (scope: all)")):
            role = "example"
        elif label.startswith("SPACE SET PLATE"):
            role = "plate"
        elif label.startswith(("MOOD ", "MOOD —")):
            role = None
        else:
            raise VisionError(f"cut_output_qc: unknown manifest label at image {expected_number}")
        if role is not None:
            references.append(LabeledReference(role, image))
    return references


def qc_schema() -> dict:
    """Strict structured-output schema; exact gate coverage is enforced again in ``validate``."""

    gate_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "gate": {"type": "string", "enum": list(GATES)},
            "status": {"type": "string", "enum": list(STATUSES)},
            "evidence": {"type": "string"},
        },
        "required": ["gate", "status", "evidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "gates": {
                "type": "array",
                "items": gate_item,
                "minItems": len(GATES),
                "maxItems": len(GATES),
            },
        },
        "required": ["gates"],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enum(value: Any, allowed: frozenset[str], *, default: str | None = None) -> str | None:
    return value if isinstance(value, str) and value in allowed else default


def normalize_plan(plan: Any) -> dict:
    """Reduce a ``CutPlan``/dict to an allowlisted, prompt-safe QC contract.

    IDs, product names, color names, pose prose, matching IDs and all other free text are
    intentionally omitted.  The judge receives their pixels plus categorical ownership, which
    is sufficient for comparison and cannot smuggle seller text into the prompt.
    """

    if hasattr(plan, "to_dict") and callable(plan.to_dict):
        plan = plan.to_dict()
    source = _mapping(plan)
    recipe_block = _mapping(source.get("recipe"))
    storyboard = _mapping(source.get("storyboard"))
    errors: list[str] = []

    recipe = source.get("recipeFamily") or recipe_block.get("family") or source.get("recipe")
    # Tolerate the current pre-compiler cutType shape for isolated callers.
    legacy_cut = source.get("cutType")
    if recipe not in _RECIPES and legacy_cut in {"styling", "horizon", "product", "mirror"}:
        recipe = "styling" if legacy_cut == "mirror" else legacy_cut
    recipe = _enum(recipe, _RECIPES)
    if recipe is None:
        errors.append("unknown_recipe")
    clothing_type = _enum(source.get("clothingType"), _CLOTHING_TYPES)
    if clothing_type is None:
        errors.append("unknown_clothing_type")

    capture = source.get("captureMode") or recipe_block.get("captureMode")
    if legacy_cut == "mirror" and capture is None:
        capture = "mirrorSelfie"
    capture = _enum(capture, _CAPTURE_MODES)
    variant = source.get("productVariant") or recipe_block.get("productVariant")
    variant = _enum(variant, _PRODUCT_VARIANTS)

    reference_mode = source.get("referenceMode", source.get("refScope", "none"))
    reference_mode = _enum(reference_mode, _REFERENCE_MODES)
    if reference_mode is None:
        reference_mode = "none"
        errors.append("unknown_reference_mode")

    shot_raw = storyboard.get("shot", source.get("shot"))
    shot = _enum(shot_raw, _SHOTS)
    direction_raw = storyboard.get("direction", source.get("direction"))
    direction = _enum(direction_raw, _DIRECTIONS)
    face_raw = storyboard.get("face", source.get("faceExposure"))
    face = _enum(face_raw, _FACES)
    reference_face_visibility = source.get("referenceFaceVisibility")
    if reference_face_visibility not in {None, "hidden", "visible"}:
        reference_face_visibility = None
        errors.append("invalid_reference_face_visibility")
    effective_face = face
    if face == "same" and reference_face_visibility == "hidden":
        effective_face = "hide"
    elif face == "same" and reference_face_visibility == "visible":
        effective_face = "show"
    closure_raw = storyboard.get("outerClosure", source.get("outerClosureState"))
    closure = _enum(closure_raw, _CLOSURES)
    if closure_raw is not None and closure is None:
        errors.append("invalid_closure")

    pose_raw = storyboard.get("pose", source.get("pose", "auto"))
    pose_mode = "auto" if pose_raw == "auto" else "explicit"
    model_selected = bool(storyboard.get("model", source.get("modelId")))
    matching_raw = storyboard.get("matching", source.get("matchIds", []))
    matching_count = len(matching_raw) if isinstance(matching_raw, (list, tuple)) else 0
    color_raw = storyboard.get("color", source.get("colorId"))

    if recipe in {"styling", "horizon"}:
        if shot not in {"full", "medium"}:
            errors.append("invalid_worn_shot")
        if capture == "mirrorSelfie":
            if direction is not None or face not in {"show", "hide"}:
                errors.append("invalid_mirror_controls")
        elif direction not in _DIRECTIONS or face not in _FACES:
            errors.append("invalid_worn_controls")
        if recipe == "styling" and capture not in {"lifestyle", "mirrorSelfie"}:
            errors.append("invalid_styling_capture")
        if recipe == "horizon" and capture != "studio":
            errors.append("invalid_horizon_capture")
    elif recipe == "product":
        if variant not in _PRODUCT_VARIANTS or shot not in _PRODUCT_VARIANTS:
            errors.append("invalid_product_variant")
        if direction not in {"front", "back"} or face is not None:
            errors.append("invalid_product_controls")

    raw_owners = source.get("attributeOwners", source.get("authorities"))
    owners_source = _mapping(raw_owners)
    owners: dict[str, str] = {}
    if not owners_source:
        errors.append("missing_authority_contract")
    for attribute, owner in owners_source.items():
        if attribute in _OWNER_ATTRIBUTES and owner in _OWNERS:
            owners[str(attribute)] = str(owner)
        elif isinstance(attribute, str) and attribute.startswith("fit.") and owner == "fitProfile":
            # Axis names are deliberately collapsed; arbitrary axis strings are not prompt input.
            owners["declaredFit"] = "fitProfile"
        elif attribute in _OWNER_ATTRIBUTES:
            errors.append(f"invalid_owner_{attribute}")

    allowed_reference = source.get("referenceAllowedAttributes") or []
    if not isinstance(allowed_reference, (list, tuple)):
        allowed_reference = []
        errors.append("invalid_reference_attributes")
    reference_attributes = sorted({
        value for value in allowed_reference if value in _REFERENCE_ATTRIBUTES
    })

    precedence = _mapping(source.get("conflictResolution"))
    precedence_contract = {
        "storyboardDirectionOverridesReference": (
            precedence.get("storyboardDirectionOverridesReference") is True
        ),
        "explicitStoryboardPoseOverridesReference": (
            precedence.get("explicitStoryboardPoseOverridesReference") is True
        ),
        "referenceCameraExcludesStoryboardDirectionAndShot": (
            precedence.get("referenceCameraExcludesStoryboardDirectionAndShot") is True
        ),
    }
    if owners_source and not all(precedence_contract.values()):
        errors.append("storyboard_precedence_not_enforced")

    continuity_raw = source.get("spaceSetContinuity")
    continuity = continuity_raw if type(continuity_raw) is bool else None
    if continuity_raw is not None and continuity is None:
        errors.append("invalid_space_set_continuity")

    repeat_raw = source.get("exampleRepeatIndex", 0)
    example_repeat_index = repeat_raw if type(repeat_raw) is int and repeat_raw >= 0 else 0
    if type(repeat_raw) is not int or repeat_raw < 0:
        errors.append("invalid_example_repeat_index")

    declared_axes = source.get("declaredFitAxes") or []
    declared_axis_count = len(declared_axes) if isinstance(declared_axes, (list, tuple)) else 0
    return {
        "contractVersion": 1,
        "recipeFamily": recipe or "unknown",
        "clothingType": clothing_type or "unknown",
        "captureMode": capture or "none",
        "productVariant": variant or "none",
        "referenceMode": reference_mode,
        "storyboard": {
            "shot": shot or "unknown",
            "direction": direction or "none",
            "face": effective_face or "none",
            "requestedFace": face or "none",
            "poseMode": pose_mode,
            "colorSelection": "selected" if color_raw not in (None, "") else "base",
            "modelSelected": model_selected,
            "matchingCount": min(2, max(0, matching_count)),
            "outerClosure": closure or "none",
        },
        "attributeOwners": dict(sorted(owners.items())),
        "referenceAllowedAttributes": reference_attributes,
        "declaredFitAxisCount": declared_axis_count,
        "exampleRepeatIndex": example_repeat_index,
        "spaceSetContinuity": continuity,
        "precedence": precedence_contract,
        "contractErrors": sorted(set(errors)),
    }


def gate_applicability(contract: Mapping[str, Any]) -> dict[str, bool]:
    """Derive deterministic NA gates; the provider cannot redefine applicability."""

    recipe = contract.get("recipeFamily")
    storyboard = _mapping(contract.get("storyboard"))
    worn = recipe in {"styling", "horizon"}
    selected_model = worn and storyboard.get("modelSelected") is True
    face_can_identify = (
        selected_model
        and storyboard.get("face") != "hide"
        and storyboard.get("direction") != "back"
        and not (
            contract.get("clothingType") == "bottom"
            and storyboard.get("shot") == "medium"
        )
    )
    applicable = {gate: True for gate in GATES}
    applicable["matchingGarmentIdentity"] = (
        worn and storyboard.get("matchingCount", 0) > 0
    )
    applicable["modelIdentity"] = face_can_identify
    applicable["modelBodyProportions"] = selected_model
    applicable["anatomyPerspectiveAsymmetry"] = worn
    applicable["relatedSceneDifferentPlace"] = (
        recipe == "styling"
        and contract.get("captureMode") in {"lifestyle", "mirrorSelfie"}
        and contract.get("referenceMode") == "all"
        and contract.get("spaceSetContinuity") is None
    )
    return applicable


def _reference_manifest(references: Sequence[LabeledReference]) -> str:
    counts = {role: 0 for role in REFERENCE_ROLES}
    lines = []
    for index, reference in enumerate(references, start=1):
        counts[reference.role] += 1
        lines.append(
            f"{index}. {_REFERENCE_ROLE_LABELS[reference.role]} {counts[reference.role]}"
        )
    lines.append(f"{len(references) + 1}. GENERATED OUTPUT")
    return "\n".join(lines)


def build_prompt(contract: Mapping[str, Any], references: Sequence[LabeledReference]) -> str:
    """Render only canonical enum/boolean/count data; never serialize the source plan."""

    with open(_PROMPT_FILE, encoding="utf-8") as handle:
        template = handle.read()
    applicability = gate_applicability(contract)
    prompt = (
        template.replace("${referenceManifest}", _reference_manifest(references))
        .replace(
            "${planContract}",
            json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        .replace(
            "${applicability}",
            json.dumps(applicability, sort_keys=True, separators=(",", ":")),
        )
    )
    if "${" in prompt:
        raise VisionError("cut_output_qc: unresolved prompt token")
    return prompt


def _clean_evidence(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return clean_text(value, MAX_EVIDENCE_LENGTH)


def _correction_patch(gates: Mapping[str, Mapping[str, str]]) -> dict | None:
    blocking = [
        gate for gate in GATES
        if gates[gate]["status"] in {"FAIL", "UNJUDGEABLE"}
    ]
    if not blocking:
        return None
    operations = []
    for gate in blocking[:MAX_CORRECTION_OPERATIONS]:
        status = gates[gate]["status"]
        operations.append({
            "gate": gate,
            "action": "regenerate" if status == "FAIL" else "rejudge",
            "instruction": _CORRECTIONS[gate],
        })
    return {
        "version": 1,
        "blockingGates": blocking,
        "operations": operations,
        "truncated": len(blocking) > len(operations),
    }


def repair_route(result: Mapping[str, Any]) -> str:
    """QC 결과를 KEEP/EDIT_STAGE1/REGENERATE_FROM_SCRATCH/HOLD로 결정한다.

    UNJUDGEABLE은 결함 사실이 아니므로 이미지 호출을 추가하지 않는다. 편집 가능한 국소
    실패만 1차 이미지를 입력으로 쓰고, 나머지는 원래 authority 입력에서 다시 생성한다.
    """

    gates = _mapping(result.get("gates"))
    if result.get("passed") is True:
        return "KEEP_STAGE1"
    blocking = {
        gate for gate in GATES
        if _mapping(gates.get(gate)).get("status") in {"FAIL", "UNJUDGEABLE"}
    }
    if not blocking or any(
        _mapping(gates.get(gate)).get("status") == "UNJUDGEABLE"
        for gate in blocking
    ):
        return "HOLD_STAGE1"
    if blocking <= _EDIT_STAGE1_GATES:
        return "EDIT_STAGE1"
    return "REGENERATE_FROM_SCRATCH"


def repair_instructions(result: Mapping[str, Any]) -> tuple[str, ...]:
    """검증된 고정 문구만 2차 생성 프롬프트에 전달한다.

    provider evidence나 판매자 텍스트는 절대 전달하지 않는다. patch가 변조되거나 구조가
    어긋나면 2차 생성을 시작하지 않도록 fail-closed한다.
    """

    patch = result.get("correctionPatch")
    if not isinstance(patch, Mapping) or set(patch) != {
        "version", "blockingGates", "operations", "truncated",
    } or patch.get("version") != 1:
        raise VisionError("cut_output_qc: invalid correction patch")
    operations = patch.get("operations")
    if not isinstance(operations, list) or not operations:
        raise VisionError("cut_output_qc: empty correction operations")
    instructions = []
    for operation in operations:
        if not isinstance(operation, Mapping) or set(operation) != {
            "gate", "action", "instruction",
        }:
            raise VisionError("cut_output_qc: invalid correction operation")
        gate = operation.get("gate")
        expected_action = "rejudge" if (
            _mapping(_mapping(result.get("gates")).get(gate)).get("status")
            == "UNJUDGEABLE"
        ) else "regenerate"
        if (
            gate not in _CORRECTIONS
            or operation.get("action") != expected_action
            or operation.get("instruction") != _CORRECTIONS[gate]
        ):
            raise VisionError("cut_output_qc: untrusted correction operation")
        instructions.append(_CORRECTIONS[gate])
    if len(instructions) > MAX_CORRECTION_OPERATIONS:
        raise VisionError("cut_output_qc: too many correction operations")
    return tuple(instructions)


def compare_repair(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict:
    """2차 결과가 실제로 개선됐는지 결정한다.

    PASS면 채택한다. 아직 FAIL이어도 blocking gate가 줄고, 1차 PASS/NA 축을 새로 망가뜨리지
    않았을 때만 채택한다. 그 외에는 1차를 보존한다.
    """

    before_gates = _mapping(before.get("gates"))
    after_gates = _mapping(after.get("gates"))
    if set(before_gates) != set(GATES) or set(after_gates) != set(GATES):
        raise VisionError("cut_output_qc: incomplete repair comparison")

    def blocking(gates: Mapping[str, Any]) -> set[str]:
        return {
            gate for gate in GATES
            if _mapping(gates.get(gate)).get("status") in {"FAIL", "UNJUDGEABLE"}
        }

    before_blocking = blocking(before_gates)
    after_blocking = blocking(after_gates)
    regressions = [
        gate for gate in GATES
        if _mapping(before_gates.get(gate)).get("status") in {"PASS", "NA"}
        and _mapping(after_gates.get(gate)).get("status") in {"FAIL", "UNJUDGEABLE"}
    ]
    accepted = after.get("passed") is True or (
        len(after_blocking) < len(before_blocking) and not regressions
    )
    return {
        "accepted": accepted,
        "beforeBlockingCount": len(before_blocking),
        "afterBlockingCount": len(after_blocking),
        "regressions": regressions,
    }


def validate(
    raw: Any,
    *,
    applicable: Mapping[str, bool] | None = None,
    forced: Mapping[str, Mapping[str, str]] | None = None,
) -> dict:
    """Validate exact coverage and fail closed on malformed or unjudgeable output.

    ``NA`` is accepted only for a gate deterministically marked inapplicable.  If the
    provider returns ``NA`` for an applicable hard gate, it becomes ``UNJUDGEABLE``.
    """

    applicable_map = {
        gate: bool((applicable or {}).get(gate, True)) for gate in GATES
    }
    malformed = not isinstance(raw, Mapping) or set(raw) != {"gates"}
    rows = raw.get("gates") if isinstance(raw, Mapping) else None
    if not isinstance(rows, list):
        rows = []
        malformed = True

    by_gate: dict[str, list[Mapping[str, Any]]] = {gate: [] for gate in GATES}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"gate", "status", "evidence"}:
            malformed = True
            continue
        gate = row.get("gate")
        if gate not in by_gate:
            malformed = True
            continue
        by_gate[gate].append(row)
    if len(rows) != len(GATES):
        malformed = True

    out: dict[str, dict[str, str]] = {}
    for gate in GATES:
        if not applicable_map[gate]:
            out[gate] = {
                "status": "NA",
                "evidence": "Not applicable under the compiled cut plan.",
            }
            continue
        entries = by_gate[gate]
        if malformed or len(entries) != 1:
            out[gate] = {
                "status": "UNJUDGEABLE",
                "evidence": "Judge response did not provide one valid result for every hard gate.",
            }
            continue
        row = entries[0]
        status = row.get("status")
        evidence = _clean_evidence(row.get("evidence"))
        if status not in STATUSES or status == "NA" or not evidence:
            status = "UNJUDGEABLE"
            evidence = evidence or "Judge evidence was missing or invalid for an applicable gate."
        out[gate] = {"status": status, "evidence": evidence}

    for gate, override in (forced or {}).items():
        if gate not in out:
            continue
        status = override.get("status") if isinstance(override, Mapping) else None
        evidence = _clean_evidence(
            override.get("evidence") if isinstance(override, Mapping) else None
        )
        if status not in {"FAIL", "UNJUDGEABLE"}:
            status = "UNJUDGEABLE"
        out[gate] = {
            "status": status,
            "evidence": evidence or "Deterministic QC preflight could not validate this gate.",
        }

    passed = all(result["status"] in {"PASS", "NA"} for result in out.values())
    return {
        "verdict": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": out,
        "correctionPatch": _correction_patch(out),
    }


def _valid_image(image: Any) -> bool:
    return (
        isinstance(image, InlineImage)
        and isinstance(image.data, bytes)
        and bool(image.data)
        and isinstance(image.mime, str)
        and image.mime.startswith("image/")
    )


def _forced_preflight(
    contract: Mapping[str, Any], references: Sequence[LabeledReference], generated: InlineImage,
) -> dict[str, dict[str, str]]:
    forced: dict[str, dict[str, str]] = {}
    roles = {reference.role for reference in references}
    if not _valid_image(generated):
        forced["fileValidity"] = {
            "status": "FAIL",
            "evidence": "Generated output is empty or is not a supported image payload.",
        }
        for gate in GATES[1:]:
            forced[gate] = {
                "status": "UNJUDGEABLE",
                "evidence": "Generated output is unavailable for visual judgment.",
            }
        return forced

    if contract.get("contractErrors"):
        forced["recipeIntent"] = {
            "status": "UNJUDGEABLE",
            "evidence": "Compiled plan failed the canonical authority-contract preflight.",
        }
    if "product" not in roles:
        for gate in _GARMENT_GATES:
            forced[gate] = {
                "status": "UNJUDGEABLE",
                "evidence": "No PRODUCT reference was supplied for garment comparison.",
            }
    storyboard = _mapping(contract.get("storyboard"))
    applicability = gate_applicability(contract)
    if applicability["modelIdentity"] and "modelFace" not in roles:
        forced["modelIdentity"] = {
            "status": "UNJUDGEABLE",
            "evidence": "No MODEL FACE reference was supplied for visible identity comparison.",
        }
    if applicability["modelBodyProportions"] and "modelBody" not in roles:
        forced["modelBodyProportions"] = {
            "status": "UNJUDGEABLE",
            "evidence": "No MODEL FULL BODY reference was supplied for body-proportion comparison.",
        }
    expected_matching_count = storyboard.get("matchingCount", 0)
    actual_matching_count = sum(
        reference.role == "matching" for reference in references
    )
    if actual_matching_count != expected_matching_count:
        evidence = (
            "MATCHING reference count does not match the compiled plan "
            f"({actual_matching_count} supplied; {expected_matching_count} required)."
        )
        forced["matchingGarmentIdentity"] = {
            "status": "UNJUDGEABLE",
            "evidence": evidence,
        }
        forced["fitClosureAllowedMutation"] = {
            "status": "UNJUDGEABLE",
            "evidence": evidence,
        }

    reference_mode = contract.get("referenceMode")
    reference_attributes = set(contract.get("referenceAllowedAttributes") or ())
    if reference_mode in {"all", "pose"} and "example" not in roles:
        forced["referenceScopeCaptureClass"] = {
            "status": "UNJUDGEABLE",
            "evidence": "The reference contract requires an EXAMPLE image, but none was supplied.",
        }
        if applicability["relatedSceneDifferentPlace"]:
            forced["relatedSceneDifferentPlace"] = {
                "status": "UNJUDGEABLE",
                "evidence": "Related-but-different scene judgment requires an EXAMPLE image.",
            }
        if reference_attributes & {"pose", "camera"}:
            forced["framingDirectionFacePose"] = {
                "status": "UNJUDGEABLE",
                "evidence": "Example-controlled pose or camera evidence is unavailable.",
            }
    plate_required = reference_mode == "bg" or contract.get("spaceSetContinuity") is True
    if plate_required and "plate" not in roles:
        forced["referenceScopeCaptureClass"] = {
            "status": "UNJUDGEABLE",
            "evidence": "The scene contract requires a PLATE image, but none was supplied.",
        }
        forced["lightingShadowReflectionDrape"] = {
            "status": "UNJUDGEABLE",
            "evidence": "Plate-owned scene lighting cannot be judged without the PLATE image.",
        }
    return forced


def _validate_references(references: Sequence[LabeledReference]) -> list[LabeledReference]:
    out = []
    for reference in references:
        if not isinstance(reference, LabeledReference):
            raise VisionError("cut_output_qc: reference must be LabeledReference")
        if reference.role not in REFERENCE_ROLES:
            raise VisionError(f"cut_output_qc: unknown reference role {reference.role!r}")
        if not _valid_image(reference.image):
            raise VisionError(f"cut_output_qc: invalid {reference.role} reference image")
        out.append(reference)
    return out


async def verdict(
    settings: Settings,
    plan: Any,
    references: Sequence[LabeledReference],
    generated_image: InlineImage,
) -> dict:
    """Judge one candidate; reference order is preserved and output is always attached last.

    Vision-provider errors propagate as ``VisionError`` for the future caller to handle.  A
    locally invalid generated payload is returned as a deterministic FAIL without making a
    provider call.
    """

    checked_references = _validate_references(references)
    contract = normalize_plan(plan)
    applicable = gate_applicability(contract)
    forced = _forced_preflight(contract, checked_references, generated_image)
    if not _valid_image(generated_image):
        result = validate({}, applicable=applicable, forced=forced)
        result.update({"provider": None, "contract": contract})
        return result

    prompt = build_prompt(contract, checked_references)
    images = [*(reference.image for reference in checked_references), generated_image]
    raw, provider = await analyze_with_fallback(settings, prompt, images, qc_schema())
    result = validate(raw, applicable=applicable, forced=forced)
    result.update({"provider": provider, "contract": contract})
    return result
