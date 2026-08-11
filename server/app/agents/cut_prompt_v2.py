"""Experiment-only structured prompt candidate for styling/all cut A/B tests.

This module deliberately has no production caller.  It consumes the same normalized
spec and :mod:`cut_plan` authority compiler as the live renderer, then renders a
short XML prompt for controlled experiments.  The candidate rejects unsupported
families/scopes instead of silently weakening their contracts.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping
from xml.sax.saxutils import escape

from .cut_plan import CutPlan, compile_cut_plan, render_prompt_contract
from .directing_profile import render_directing_profile
from .fit_axes import build_fit_profile_block, normalize_fit_profile


_TEMPLATE = Path(__file__).resolve().parents[2] / "prompts" / "cut_generate_candidate_v2.txt"
_TOKEN_RE = re.compile(r"\$\{([a-z_]+)\}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NUMBERED_MANIFEST_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")

_DIRECTION_RULES = {
    "front": "Use a front-family view. A natural body turn up to about 30 degrees is allowed, but the front of the garment remains readable.",
    "side": "Use a clear side view. Rebuild limb geometry and visible garment surfaces for that side; never turn back toward the example view.",
    "back": "Use a rear view. The back of the garment is the subject; never rotate toward the example view to reveal a face.",
}
_SHOT_RULES = {
    ("top", "full"): "Full shot: include head, complete body, garment hem, both complete feet, footwear and visible floor margin.",
    ("bottom", "full"): "Full shot: include head, complete body, entire bottom garment, both complete feet, footwear and visible floor margin.",
    ("outer", "full"): "Full shot: include head, complete body, outerwear hem, both complete feet, footwear and visible floor margin.",
    ("dress", "full"): "Full shot: include head, complete body, complete dress hem, both complete feet, footwear and visible floor margin.",
    ("top", "medium"): "Medium shot: use a purpose-shot closer camera from head through hip, with the entire top and its hem readable; do not crop a full-shot rendering.",
    ("outer", "medium"): "Medium shot: use a purpose-shot closer camera from head through hip, with the entire outerwear upper silhouette readable; do not crop a full-shot rendering.",
    ("dress", "medium"): "Medium shot: use a purpose-shot closer camera from head through hip while keeping the dress bodice and waist relationship readable; do not crop a full-shot rendering.",
    ("bottom", "medium"): "Medium shot: frame from waist through feet and keep the entire bottom garment, both hems and both complete feet visible.",
}
_FACE_RULES = {
    "hide": "Hide the face completely and naturally. Show no identifiable eyes, nose or mouth.",
    "show": "Keep the selected model's face visible and recognizable with a calm natural expression.",
    "same": "Keep the selected model identity consistent and the face visually unobtrusive; do not borrow the example person's identity.",
}
_OUTER_RULES = {
    "open": "Keep the inner garment shown by MANNEQUIN exact. Outer opening: fully open using only real closure hardware proven by PRODUCT. Keep every closure unfastened and show that inner layer naturally.",
    "partial": "Keep the inner garment shown by MANNEQUIN exact. Outer opening: partially open using only real closure hardware proven by PRODUCT. Fasten only a physically plausible subset.",
    "closed": "Keep the inner garment shown by MANNEQUIN exact. Outer opening: fully closed using only real closure hardware proven by PRODUCT. Do not invent a closure for an open-front garment.",
}

# Prefixes come only from cut_generator.build_manifest.  Descriptions are rebuilt
# from constants below, so arbitrary prose in a supplied manifest never reaches the
# model.  Longest/more-specific prefixes must appear first.
_MANIFEST_ROLES = (
    ("PRODUCT — the garment worn on a mannequin", "PRODUCT_MANNEQUIN", "sold garment color, fit, length and drape evidence"),
    ("PRODUCT — front view", "PRODUCT_FRONT", "sold garment front evidence"),
    ("PRODUCT — back view", "PRODUCT_BACK", "sold garment back evidence"),
    ("PRODUCT — front-side detail close-up", "PRODUCT_DETAIL", "sold garment front-side material, stitching, print and hardware evidence"),
    ("PRODUCT — back-side detail close-up", "PRODUCT_BACK_DETAIL", "sold garment back-only material, stitching and hardware evidence; never place it on the front"),
    ("PRODUCT — view of the garment", "PRODUCT_VIEW", "sold garment identity evidence"),
    ("MODEL FULL BODY —", "MODEL_FULL_BODY", "selected model height and body-proportion evidence only; zero face authority"),
    ("MODEL SHEET —", "MODEL_SHEET", "face-angle identity sheet only; not full-body evidence"),
    ("MODEL FACE —", "MODEL_FACE", "selected model facial identity evidence only; zero body authority"),
    ("MODEL —", "MODEL_LEGACY_FACE", "legacy selected model face evidence; not full-body evidence"),
    ("MATCHING —", "MATCHING", "seller-selected coordinating garment evidence"),
    ("MOOD —", "MOOD", "lighting, color and ambience evidence only"),
    ("EXAMPLE REFERENCE (scope: all) —", "EXAMPLE_ALL", "permitted pose, camera, scene, light and capture-tone evidence only"),
)


class CandidatePromptError(ValueError):
    """Raised when an experiment would violate or weaken the typed cut contract."""


@lru_cache(maxsize=1)
def load_candidate_template() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _xml_text(value: Any) -> str:
    """Escape dynamic data and make template-looking text inert inside XML."""

    clean = _CONTROL_RE.sub("", str(value or ""))
    clean = "".join(character for character in clean if _is_xml_character(character))
    clean = escape(clean, {'"': "&quot;", "'": "&apos;"})
    return clean.replace("${", "&#36;{")


def _is_xml_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        codepoint in (0x9, 0xA, 0xD)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _canonical_manifest(raw: str) -> tuple[str, Counter[str]]:
    if not isinstance(raw, str) or not raw.strip():
        raise CandidatePromptError("input_manifest_required")
    entries: list[tuple[int, str, str]] = []
    seen_indices: set[int] = set()
    roles: Counter[str] = Counter()
    for raw_line in raw.splitlines():
        if not raw_line.strip():
            continue
        match = _NUMBERED_MANIFEST_RE.fullmatch(raw_line)
        if not match:
            raise CandidatePromptError("invalid_manifest_line")
        index = int(match.group(1))
        description = match.group(2)
        if index in seen_indices:
            raise CandidatePromptError("duplicate_manifest_index")
        role_entry = next(
            (entry for entry in _MANIFEST_ROLES if description.startswith(entry[0])),
            None,
        )
        if role_entry is None:
            raise CandidatePromptError("unknown_manifest_role")
        _prefix, role, safe_description = role_entry
        seen_indices.add(index)
        roles[role] += 1
        entries.append((index, role, safe_description))
    if [index for index, _role, _description in entries] != list(range(1, len(entries) + 1)):
        raise CandidatePromptError("non_contiguous_manifest")
    if not any(role.startswith("PRODUCT_") for role in roles):
        raise CandidatePromptError("product_reference_required")
    if roles["EXAMPLE_ALL"] != 1:
        raise CandidatePromptError("exactly_one_all_scope_example_required")
    if roles["MATCHING"] > 1:
        raise CandidatePromptError("duplicate_matching_reference")
    if roles["MOOD"]:
        raise CandidatePromptError("mood_not_allowed_with_all_scope_example")
    if any(
        roles[role] > 1
        for role in ("MODEL_FACE", "MODEL_FULL_BODY", "MODEL_SHEET", "MODEL_LEGACY_FACE")
    ):
        raise CandidatePromptError("duplicate_model_identity_reference")
    has_any_model_reference = any(
        roles[role]
        for role in ("MODEL_FACE", "MODEL_FULL_BODY", "MODEL_SHEET", "MODEL_LEGACY_FACE")
    )
    has_exact_model_pair = roles["MODEL_FACE"] == roles["MODEL_FULL_BODY"] == 1
    if has_any_model_reference and (
        not has_exact_model_pair or roles["MODEL_SHEET"] or roles["MODEL_LEGACY_FACE"]
    ):
        raise CandidatePromptError("model_face_and_full_body_pair_required")
    rendered = "\n".join(
        f'    <input index="{index}" role="{role}">{safe_description}</input>'
        for index, role, safe_description in entries
    )
    return rendered, roles


def _assert_candidate_scope(plan: CutPlan, spec: Mapping[str, Any]) -> None:
    if spec.get("cutType") != "styling" or plan.recipe_family != "styling" \
            or plan.capture_mode != "lifestyle":
        raise CandidatePromptError("candidate_supports_styling_only")
    if plan.reference_mode != "all" or not spec.get("exampleId"):
        raise CandidatePromptError("candidate_requires_all_scope_example")
    if spec.get("spaceGroupId"):
        raise CandidatePromptError("candidate_excludes_space_sets")


def _storyboard_block(spec: Mapping[str, Any], clothing_type: str) -> str:
    direction = spec.get("direction")
    shot = spec.get("shot")
    face = spec.get("faceExposure")
    if direction not in _DIRECTION_RULES or (clothing_type, shot) not in _SHOT_RULES \
            or face not in _FACE_RULES:
        raise CandidatePromptError("invalid_storyboard_catalog_value")

    if (
        spec.get("_referenceFaceVisibility") == "hidden"
        and face != "show"
        and spec.get("_referenceDirectionCompatible") is not False
    ):
        face_rule = (
            "Reference face hiding is exact: preserve its broad crop, head turn or physical "
            "occlusion mechanism. Show no eyes, nose or mouth, and do not reveal a lower face."
        )
        if shot == "full":
            face_rule += (
                " Full-shot framing wins over a head crop: if the reference hid the face by "
                "cropping it out, translate that hiding into a head turn, hair or a physically "
                "plausible generic occluder while keeping the complete head-to-feet frame."
            )
    else:
        face_rule = _FACE_RULES[face]

    pose = spec.get("pose") or "auto"
    if pose == "auto":
        pose_rule = "Pose: use the reference pose only when the authority contract grants pose; otherwise choose a natural asymmetric pose."
    else:
        # normalize_spec already limits this field, but escape again because this
        # renderer is intentionally safe when called directly in experiments.
        pose_rule = f"Explicit storyboard pose: {_xml_text(str(pose)[:40])}. Ignore the example pose."

    lines = (
        _DIRECTION_RULES[direction],
        _SHOT_RULES[(clothing_type, shot)],
        face_rule,
        pose_rule,
        "Selected model identity replaces the example person. Selected MATCHING replaces every example coordinating garment.",
    )
    return "\n".join(f"    {index}. {line}" for index, line in enumerate(lines, 1))


def _reference_block(plan: CutPlan, spec: Mapping[str, Any]) -> str:
    allowed = tuple(plan.reference_attributes)
    if not allowed:
        raise CandidatePromptError("all_scope_without_allowed_attributes")
    allowed_text = ", ".join(allowed)
    denied = [
        item for item in ("pose", "camera", "scene", "light", "captureTone")
        if item not in allowed
    ]
    lines = [f"Allowed attributes from EXAMPLE REFERENCE: {allowed_text}."]
    if denied:
        lines.append(
            "Explicitly denied example attributes: " + ", ".join(denied) + ". Rebuild them from STORYBOARD or the styling recipe."
        )
    if spec.get("_referenceDirectionCompatible") is False:
        lines.append(
            "Direction changed: retain scene, light, capture tone and only SCENE RELATION's "
            "qualitative camera relationship, with no example camera authority. Rebuild pose, "
            "gaze, camera geometry, crop, subject scale and placement for the current direction "
            "and shot."
        )
    elif spec.get("pose") == "auto" and "pose" in allowed:
        lines.append(
            "Preserve visible pose kinematics: torso and pelvis yaw, weight-bearing leg, unequal shoulder and hip lines, left/right limb positions, hand heights, stance, head angle, gaze, hair flow and near/far foreshortening. Do not symmetrize them."
        )
    return "\n".join(f"    {index}. {line}" for index, line in enumerate(lines, 1))


def _scene_relation(plan: CutPlan) -> str:
    shared = (
        "    Preserve scene type, ordinary ambience, palette, time of day, lighting "
        "principle and the qualitative camera-to-subject/environment relationship. Use a "
        "different concrete location instance. Change at least one spatial structure "
        "(wall, opening, path or building geometry) and at least two furniture, signage or "
        "prop placements."
    )
    if "camera" in plan.reference_attributes:
        return (
            shared
            + " Preserve the relational camera logic, not the reference location's exact "
            "coordinates or layout."
        )
    return (
        shared
        + " This qualitative relationship does not restore camera authority: rebuild camera "
        "geometry, crop, subject scale, placement and composition for the current STORYBOARD."
    )


def _identity_rule(roles: Mapping[str, int]) -> str:
    if roles.get("MODEL_FACE") and roles.get("MODEL_FULL_BODY"):
        return (
            "    MODEL_FACE and MODEL_FULL_BODY are one atomic pair for the same selected "
            "model. MODEL_FACE controls facial identity only and has zero authority over height, "
            "body shape, proportions or pose. MODEL_FULL_BODY controls only height, "
            "head-to-body ratio, shoulders, torso, waist, pelvis, arm proportions and leg "
            "proportions; it has zero face authority. Preserve those full-body proportions even "
            "when the face is hidden or the view is rear-facing. The EXAMPLE person has zero "
            "authority over both face and body build."
        )
    return (
        "    Use one consistent neutral house model. The EXAMPLE person has zero authority "
        "over both face and body build. Face handling remains authoritative."
    )


def _metadata_claim(value: Any) -> str:
    """Collapse seller text into a bounded data value; XML escaping happens at render."""

    return re.sub(r"\s+", " ", str(value or "")).strip()[:200]


def _generation_safe_product_context(
    product: Mapping[str, Any], analysis: Mapping[str, Any]
) -> str:
    """Render seller context without granting it garment-geometry authority."""

    claims: list[str] = []
    clothing_type = _metadata_claim(
        product.get("clothing_type") or product.get("clothingType")
    )
    if clothing_type:
        claims.append(f"- Broad clothing type: {clothing_type}")

    material_claims: list[str] = []
    for material in analysis.get("materials") or []:
        if isinstance(material, Mapping):
            material_name = _metadata_claim(material.get("name"))
        else:
            material_name = _metadata_claim(material)
        if material_name:
            material_claims.append(material_name)
    if material_claims:
        claims.append(f"- Material claims: {', '.join(material_claims)}")

    header = (
        "GENERATION-SAFE CONTEXT. Name, subcategory, sales/AI copy and legacy fit are omitted. "
        "Broad type/material claims own no geometry. PRODUCT/MANNEQUIN pixels win; only declared "
        "FIT PROFILE axes may override them."
    )
    return "\n".join((header, *claims))


def _render(template: str, values: Mapping[str, str]) -> str:
    expected = set(_TOKEN_RE.findall(template))
    if expected != set(values):
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise CandidatePromptError(f"candidate_template_contract:missing={missing}:extra={extra}")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    leftovers = _TOKEN_RE.findall(rendered)
    if leftovers:
        raise CandidatePromptError(f"unresolved_candidate_tokens:{sorted(set(leftovers))}")
    return rendered


def render_candidate_prompt(
    spec: Mapping[str, Any],
    product: Mapping[str, Any],
    analysis: Mapping[str, Any] | None,
    *,
    image_manifest: str,
    directing_profile: Mapping[str, Any] | None = None,
    template: str | None = None,
) -> str:
    """Render the experiment candidate from an already normalized styling spec."""

    analysis = analysis if isinstance(analysis, Mapping) else {}
    clothing_type = str(
        product.get("clothing_type") or product.get("clothingType") or ""
    ).strip().lower()
    manifest_block, roles = _canonical_manifest(image_manifest)
    fit_profile = normalize_fit_profile(analysis.get("fitProfile"))
    if fit_profile and not roles["PRODUCT_MANNEQUIN"] and not roles["MATCHING"]:
        fit_profile = {
            key: value
            for key, value in fit_profile.items()
            if key not in ("matchCut", "matchingFit")
        }
    plan = compile_cut_plan(spec, clothing_type, fit_profile=fit_profile)
    _assert_candidate_scope(plan, spec)

    has_matching = bool(spec.get("matchIds"))
    if has_matching != (roles["MATCHING"] == 1):
        raise CandidatePromptError("matching_manifest_mismatch")
    has_identity = bool(roles["MODEL_FACE"] and roles["MODEL_FULL_BODY"])
    if bool(spec.get("modelId")) != has_identity:
        raise CandidatePromptError("model_manifest_mismatch")

    matching_rule = (
        "    MATCHING is attached and required. Preserve its exact color, material, pattern, construction, hardware, text/logo, silhouette, rise, length, width and hem."
        if has_matching
        else "    No MATCHING is attached. Complete uncovered outfit areas only with plain neutral unbranded basics; never copy the example outfit."
    )
    outer_rule = ""
    if clothing_type == "outer":
        closure = spec.get("outerClosureState")
        if closure not in _OUTER_RULES:
            raise CandidatePromptError("outer_closure_required")
        outer_rule = f"    {_OUTER_RULES[closure]}"

    fit_block = build_fit_profile_block(fit_profile)
    product_block = _generation_safe_product_context(product, analysis)
    effective_directing_profile = dict(directing_profile or {})
    if "camera" not in plan.reference_attributes:
        if effective_directing_profile.get("camera") == "reference_geometry":
            effective_directing_profile.pop("camera")
        if effective_directing_profile.get("framing") == "reference_crop":
            effective_directing_profile.pop("framing")
    directing_block = render_directing_profile(
        effective_directing_profile or None,
        cut_type="styling",
        requested_direction=spec.get("direction"),
        explicit_pose=spec.get("pose") != "auto",
        reference_direction_compatible=spec.get("_referenceDirectionCompatible"),
    )

    values = {
        "authority_contract": _xml_text(render_prompt_contract(plan)),
        "storyboard_constraints": _storyboard_block(spec, clothing_type),
        "matching_rule": matching_rule,
        "outer_rule": outer_rule,
        "reference_scope": _reference_block(plan, spec),
        "scene_relation": _scene_relation(plan),
        "identity_rule": _identity_rule(roles),
        "directing_profile": _xml_text(directing_block or "No optional directing profile."),
        "fit_profile": _xml_text(fit_block or "No declared fit-axis override; follow PRODUCT and MANNEQUIN evidence."),
        "product_context": _xml_text(product_block or "No seller text metadata; use image evidence only."),
        "input_manifest": manifest_block,
    }
    return _render(template or load_candidate_template(), values)


def build_candidate_prompt(
    cut_spec: Mapping[str, Any],
    product: Mapping[str, Any],
    *,
    analysis: Mapping[str, Any] | None = None,
    manifest: str,
    directing_profile: Mapping[str, Any] | None = None,
) -> str:
    """Experiment-harness entry point using the production normalizer/resolver.

    Importing the live helpers locally keeps this candidate out of the production
    dependency graph while guaranteeing A/B inputs receive the same normalization.
    """

    from .cut_generator import apply_reference_compatibility, normalize_spec

    raw_scope = cut_spec.get("refScope")
    if raw_scope is None:
        raw_scope = cut_spec.get("ref_scope")
    if raw_scope not in (None, "all"):
        raise CandidatePromptError("candidate_requires_raw_all_scope")
    if cut_spec.get("spaceGroupId") or cut_spec.get("space_group_id"):
        raise CandidatePromptError("candidate_excludes_space_sets")
    clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
    spec = normalize_spec(dict(cut_spec), clothing_type=str(clothing_type))
    spec = apply_reference_compatibility(spec)
    return render_candidate_prompt(
        spec,
        product,
        analysis,
        image_manifest=manifest,
        directing_profile=directing_profile,
    )
