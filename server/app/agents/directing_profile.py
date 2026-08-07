"""Server-owned art-direction profiles for AG-06 cut generation.

The client never sends free-form directing prose.  A trusted server resolver may
attach one of these small, enum-only mappings to an approved generation example.
Rendering stays deliberately short: the profile reinforces observable directing
properties without competing with the cut contract or the seller's garment truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_KNOWN_FIELDS = {
    "directionMode",
    "poseDynamics",
    "camera",
    "framing",
    "capture",
    "scene",
    "light",
}

_ALLOWED_VALUES = {
    "directionMode": {"exact", "retarget"},
    "poseDynamics": {
        "reference_kinematics",
        "natural_asymmetry",
        "controlled_stillness",
        "natural_motion",
    },
    "camera": {
        "reference_geometry",
        "handheld_oblique",
        "handheld_eye_level",
        "tripod_centered",
        "mirror_phone",
        "product_camera",
    },
    "framing": {
        "reference_crop",
        "casual_off_center",
        "centered_catalog",
        "product_close",
    },
    "capture": {
        "phone_snapshot",
        "casual_digital",
        "editorial",
        "studio_catalog",
        "mirror_selfie",
        "product_catalog",
    },
    "scene": {
        "reference_location",
        "lifestyle_location",
        "horizon_studio",
        "mirror_room",
        "product_studio",
    },
    "light": {
        "reference_integrated",
        "natural_soft",
        "natural_hard",
        "mixed_available",
        "studio_soft",
        "product_diffused",
    },
}

# Do not push every cut family through irrelevant concepts.  In particular,
# product-only work has no human pose and no direction-retarget semantics.
_FIELDS_BY_CUT = {
    "styling": _KNOWN_FIELDS,
    "mirror": _KNOWN_FIELDS,
    "horizon": _KNOWN_FIELDS,
    "product": {"camera", "framing", "capture", "scene", "light"},
}

_LINES = {
    "poseDynamics": {
        "reference_kinematics": (
            "Kinematics: preserve the reference's visible balance, joint relationships, "
            "counter-rotation, unequal limb timing and near/far depth; do not neutralize it "
            "into a bilaterally symmetric mannequin stance."
        ),
        "natural_asymmetry": (
            "Kinematics: keep believable human asymmetry and counterbalance across torso, "
            "pelvis, limbs, head and hair; avoid mirrored shoulders, hands, legs and folds."
        ),
        "controlled_stillness": (
            "Kinematics: use a calm controlled stance while retaining small human imbalance, "
            "unequal joint angles and physically plausible fabric response."
        ),
        "natural_motion": (
            "Kinematics: preserve the reference's motion rhythm, counterbalance and fabric "
            "response rather than freezing the body into a centered catalog stance."
        ),
    },
    "camera": {
        "reference_geometry": (
            "Camera: preserve the reference's camera height, lens perspective, subject scale "
            "and near/far relationship where compatible with the current CUT SPEC."
        ),
        "handheld_oblique": (
            "Camera: retain a plausible handheld, slightly non-orthogonal viewpoint with "
            "natural perspective and no artificial front-on flattening."
        ),
        "handheld_eye_level": (
            "Camera: use an ordinary eye-level handheld viewpoint with credible perspective, "
            "not a perfectly leveled campaign camera."
        ),
        "tripod_centered": (
            "Camera: use a stable centered catalog viewpoint and coherent lens perspective; "
            "do not distort the body or garment to force symmetry."
        ),
        "mirror_phone": (
            "Camera: preserve physically plausible phone-in-mirror geometry, reflection, "
            "camera height and subject distance."
        ),
        "product_camera": (
            "Camera: use clean product-photography geometry with undistorted garment "
            "proportions and a deliberate product viewing angle."
        ),
    },
    "framing": {
        "reference_crop": (
            "Framing: preserve the reference crop boundary, subject scale, headroom and "
            "negative-space rhythm only where compatible with the requested shot."
        ),
        "casual_off_center": (
            "Framing: keep a casually observed, slightly off-center composition with natural "
            "negative space; the requested full/medium shot boundary still wins."
        ),
        "centered_catalog": (
            "Framing: use a clear centered catalog composition while preserving natural body "
            "depth; the requested full/medium shot boundary still wins."
        ),
        "product_close": (
            "Framing: make the product the sole visual subject at the requested product-shot "
            "scale, without unrelated props or body parts."
        ),
    },
    "capture": {
        "phone_snapshot": (
            "Capture character: look like an ordinary phone snapshot with modest dynamic "
            "range, consumer-camera sharpness and natural white balance; never upgrade it into "
            "a luxury campaign or polished editorial."
        ),
        "casual_digital": (
            "Capture character: retain a candid everyday digital-camera finish with restrained "
            "retouching and no advertising-campaign polish."
        ),
        "editorial": (
            "Capture character: use a deliberate fashion-editorial finish, but keep anatomy, "
            "fabric and location physically photographic rather than synthetic."
        ),
        "studio_catalog": (
            "Capture character: use restrained e-commerce studio photography with consistent "
            "exposure and no glossy campaign treatment."
        ),
        "mirror_selfie": (
            "Capture character: retain a credible casual mirror selfie, including ordinary "
            "phone-camera rendering rather than a staged editorial photograph."
        ),
        "product_catalog": (
            "Capture character: use clean factual e-commerce product photography without "
            "decorative editorial styling."
        ),
    },
    "scene": {
        "reference_location": (
            "Scene: preserve the reference location's type, spatial depth and ambient character "
            "only within the active reference scope; do not copy branding or readable text."
        ),
        "lifestyle_location": (
            "Scene: keep a believable occupied everyday location with natural depth and "
            "imperfection, not a pristine synthetic set."
        ),
        "horizon_studio": (
            "Scene: keep a seamless neutral horizon studio with coherent floor contact and "
            "background tone."
        ),
        "mirror_room": (
            "Scene: keep a physically coherent room or fitting-room reflection with believable "
            "depth and no duplicated objects."
        ),
        "product_studio": (
            "Scene: keep a minimal product studio that supports silhouette and material truth "
            "without competing props."
        ),
    },
    "light": {
        "reference_integrated": (
            "Light integration: subject, garment and scene must share the reference's light "
            "direction, softness, exposure and white balance; add matching contact/cast shadows, "
            "fabric self-shadow, reflections and fold shading."
        ),
        "natural_soft": (
            "Light integration: use soft available light with consistent direction, white "
            "balance, contact shadow and fabric self-shadow across person and scene."
        ),
        "natural_hard": (
            "Light integration: use one coherent hard-light direction; cast shadows, skin, hair, "
            "garment folds and background highlights must agree."
        ),
        "mixed_available": (
            "Light integration: preserve plausible mixed available light and white balance while "
            "keeping person, garment and scene in the same exposure and shadow system."
        ),
        "studio_soft": (
            "Light integration: use one coherent soft studio setup with grounded contact shadow "
            "and material-appropriate highlights and folds."
        ),
        "product_diffused": (
            "Light integration: use diffused product light that reveals true material, seams, "
            "hardware and folds without clipping highlights or flattening texture."
        ),
    },
}


def normalize_directing_profile(
    raw: Mapping[str, Any] | None,
    *,
    cut_type: str,
) -> dict[str, str] | None:
    """Validate a trusted profile and return a stable enum-only mapping.

    Unknown fields, free-form values, and fields irrelevant to the current cut are
    rejected rather than silently leaking prose into the model prompt.
    """

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_directing_profile")
    if cut_type not in _FIELDS_BY_CUT:
        raise ValueError("invalid_directing_profile_cut_type")
    unknown = set(raw) - _KNOWN_FIELDS
    if unknown:
        raise ValueError(f"unknown_directing_profile_field:{sorted(unknown)[0]}")
    irrelevant = set(raw) - _FIELDS_BY_CUT[cut_type]
    if irrelevant:
        raise ValueError(f"directing_profile_field_not_applicable:{sorted(irrelevant)[0]}")

    clean: dict[str, str] = {}
    for field, value in raw.items():
        if not isinstance(value, str) or value not in _ALLOWED_VALUES[field]:
            raise ValueError(f"invalid_directing_profile_value:{field}")
        clean[field] = value
    return clean or None


def render_directing_profile(
    raw: Mapping[str, Any] | None,
    *,
    cut_type: str,
    requested_direction: str | None,
    explicit_pose: bool = False,
    reference_direction_compatible: bool | None = None,
) -> str:
    """Render a concise, lower-authority profile block for the current cut.

    ``reference_direction_compatible=False`` is computed by the server registry
    resolver and always forces retarget semantics.  A user-selected direction and
    named pose therefore cannot be undone by stale example metadata.
    """

    profile = normalize_directing_profile(raw, cut_type=cut_type)
    if not profile:
        return ""

    lines = [
        "SERVER DIRECTING PROFILE (supporting guidance; lower authority than CUT SPEC and product truth)",
        "- Authority: seller PRODUCT/MANNEQUIN references are the sole truth for garment "
        "identity, structure, fit, color, pattern, logo and text. The explicit CUT SPEC "
        "direction, shot, color and pose override this profile.",
    ]

    if cut_type != "product":
        direction_mode = profile.get("directionMode", "exact")
        if reference_direction_compatible is False:
            direction_mode = "retarget"
        if direction_mode == "retarget":
            lines.append(
                f"- Direction relationship: RETARGET to the requested {requested_direction or 'current'} "
                "view. Preserve only the reference's motion energy, balance and asymmetry; rebuild "
                "joint geometry, foreshortening and visible garment surfaces for the CUT SPEC. Never "
                "copy literal limb coordinates or turn the model back toward the reference view."
            )
        else:
            lines.append(
                f"- Direction relationship: EXACT for the requested {requested_direction or 'current'} "
                "view. Preserve visible kinematic and perspective relationships where they do not "
                "conflict with the CUT SPEC."
            )

    # A named user pose has already won in the main cut contract.  Suppress profile
    # kinematics instead of giving the image model a second pose to reconcile.
    for field in ("poseDynamics", "camera", "framing", "capture", "scene", "light"):
        if field == "poseDynamics" and explicit_pose:
            continue
        value = profile.get(field)
        if value:
            lines.append(f"- {_LINES[field][value]}")
    return "\n".join(lines)
