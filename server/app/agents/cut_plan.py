"""Typed cut-plan compiler for prompt ownership and conflict resolution.

The image prompt is deliberately not assembled here.  This module turns an already
normalized cut spec into a small contract that a prompt renderer can inject without
re-deciding which input owns each visual attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping


RecipeFamily = Literal["styling", "horizon", "product"]
CaptureMode = Literal["lifestyle", "mirrorSelfie", "studio"]
ProductVariant = Literal["ghost", "detail"]
ReferenceMode = Literal["none", "all", "pose", "bg"]
AttributeOwner = Literal[
    "productTruth",
    "storyboard",
    "fitProfile",
    "modelFace",
    "modelFullBody",
    "reference",
    "spaceSet",
    "recipe",
]


class CutPlanError(ValueError):
    """Raised when a spec cannot produce an unambiguous cut plan."""


_CLOTHING_TYPES = frozenset({"top", "bottom", "outer", "dress"})
_WORN_SHOTS = frozenset({"full", "medium"})
_PRODUCT_SHOTS = frozenset({"ghost", "detail"})
_WORN_DIRECTIONS = frozenset({"front", "side", "back"})
_PRODUCT_DIRECTIONS = frozenset({"front", "back"})
_WORN_FACES = frozenset({"same", "show", "hide"})
_MIRROR_FACES = frozenset({"show", "hide"})
_CLOSURES = frozenset({"open", "partial", "closed"})

_PRODUCT_TRUTH_ATTRIBUTES = (
    "construction",
    "length",
    "material",
    "pattern",
    "hardware",
    "silhouette",
    "textLogo",
)
_STORYBOARD_ATTRIBUTES = (
    "color",
    "direction",
    "shot",
    "face",
    "model",
    "matching",
    "outerClosure",
)
_REFERENCE_ATTRIBUTES: dict[ReferenceMode, tuple[str, ...]] = {
    "none": (),
    "all": ("pose", "camera", "scene", "light", "captureTone"),
    "pose": ("pose",),
    "bg": ("scene", "light", "captureTone"),
}
_REFERENCE_CAPABLE_ATTRIBUTES = ("pose", "camera", "scene", "light", "captureTone")


@dataclass(frozen=True)
class CutPlan:
    """Validated prompt contract with one owner for every active attribute."""

    clothing_type: str
    recipe_family: RecipeFamily
    reference_mode: ReferenceMode
    attribute_owners: Mapping[str, AttributeOwner]
    reference_attributes: tuple[str, ...]
    storyboard_values: Mapping[str, Any]
    declared_fit_axes: tuple[str, ...] = ()
    capture_mode: CaptureMode | None = None
    product_variant: ProductVariant | None = None
    space_set_continuity: bool | None = None
    reference_direction_compatible: bool = True
    reference_face_visibility: Literal["hidden", "visible"] | None = None

    def uses_styling_all_location_recomposition(self) -> bool:
        """Whether ``all`` is a styling inspiration, not an exact location plate."""

        return (
            self.recipe_family == "styling"
            and self.capture_mode == "lifestyle"
            and self.reference_mode == "all"
            and self.space_set_continuity is None
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the compact, JSON-safe contract used by prompt renderers."""

        recipe: dict[str, Any] = {"family": self.recipe_family}
        if self.capture_mode is not None:
            recipe["captureMode"] = self.capture_mode
        if self.product_variant is not None:
            recipe["productVariant"] = self.product_variant

        out: dict[str, Any] = {
            "recipeFamily": self.recipe_family,
            "clothingType": self.clothing_type,
            "referenceMode": self.reference_mode,
            "recipe": recipe,
            "storyboard": dict(self.storyboard_values),
            "attributeOwners": dict(self.attribute_owners),
            "referenceAllowedAttributes": list(self.reference_attributes),
            "declaredFitAxes": list(self.declared_fit_axes),
            "conflictResolution": {
                "productTruthControlsOnly": list(_PRODUCT_TRUTH_ATTRIBUTES),
                "fitProfileControlsOnly": [f"fit.{axis}" for axis in self.declared_fit_axes],
                "storyboardDirectionOverridesReference": True,
                "explicitStoryboardPoseOverridesReference": True,
                "explicitStoryboardPoseApplied": (
                    self.attribute_owners["pose"] == "storyboard"
                ),
                "referenceCameraExcludesStoryboardDirectionAndShot": True,
                "referenceDirectionCompatible": self.reference_direction_compatible,
                "examplePersonFaceAndBodyAuthority": False,
                "sellerMetadataOverridesMannequinGeometry": False,
                "stylingAllLocationRecompositionApplied": (
                    self.uses_styling_all_location_recomposition()
                ),
                "spaceSetCameraAndPoseRemainCutSpecific": (
                    self.space_set_continuity is not None
                ),
            },
        }
        if self.capture_mode is not None:
            out["captureMode"] = self.capture_mode
        if self.product_variant is not None:
            out["productVariant"] = self.product_variant
        if self.space_set_continuity is not None:
            out["spaceSetContinuity"] = self.space_set_continuity
        if self.reference_face_visibility is not None:
            out["referenceFaceVisibility"] = self.reference_face_visibility
        return out

    def render_prompt_contract(self) -> str:
        """Render a compact deterministic block suitable for prompt injection.

        ``to_dict`` is the audit/persistence representation. The image model gets this
        shorter form so the ownership contract does not bury the photographic task.
        """

        grouped: dict[str, list[str]] = {}
        for attribute, owner in self.attribute_owners.items():
            grouped.setdefault(owner, []).append(attribute)
        recipe = self.recipe_family
        if self.capture_mode:
            recipe += f"/{self.capture_mode}"
        elif self.product_variant:
            recipe += f"/{self.product_variant}"
        lines = [
            "CUT PLAN AUTHORITY — one owner per visual attribute; never blend owners.",
            f"recipe={recipe}; clothing={self.clothing_type}; reference={self.reference_mode}",
        ]
        for owner in (
            "productTruth",
            "storyboard",
            "fitProfile",
            "modelFace",
            "modelFullBody",
            "reference",
            "spaceSet",
            "recipe",
        ):
            attributes = sorted(grouped.get(owner, []))
            if attributes:
                lines.append(f"{owner}: {', '.join(attributes)}")
        lines.extend((
            "Storyboard color/direction/shot/face/model/matching/closure and any explicit pose "
            "override the example; never recover an overridden attribute from its pixels.",
            "Selected-model inputs own face and body proportions; the example person owns neither.",
            "ProductTruth pixels own construction, length, material, pattern, hardware, "
            "silhouette and garment text/logo. Seller name/category/sales/legacy-fit text owns "
            "no geometry; only declared fitProfile axes may override mannequin pixels.",
            "Reference owns only listed attributes; ignore all other reference pixels.",
        ))
        if self.uses_styling_all_location_recomposition():
            lines.append(
                "Styling/all keeps scene type, ordinary ambience, palette, time, lighting logic "
                "and camera relation; use a coherent different location instance in the same "
                "visual family. Do not use a structural/prop change quota or force object changes "
                "to prove difference."
            )
        return "\n".join(lines)


def _normalized_clothing_type(value: str) -> str:
    clothing_type = str(value or "").strip().lower()
    if clothing_type not in _CLOTHING_TYPES:
        raise CutPlanError("unknown_clothing_type")
    return clothing_type


def _declared_fit_axes(profile: Mapping[str, Any] | None) -> tuple[str, ...]:
    if profile is None:
        return ()
    if not isinstance(profile, Mapping):
        raise CutPlanError("invalid_fit_profile")
    axes = profile.get("axes") or {}
    if not isinstance(axes, Mapping):
        raise CutPlanError("invalid_fit_axes")
    names: list[str] = []
    for raw_name, value in axes.items():
        if value is None:
            continue
        if not isinstance(raw_name, str) or not raw_name.strip() or "." in raw_name:
            raise CutPlanError("invalid_fit_axis")
        names.append(raw_name.strip())
    return tuple(sorted(set(names)))


def _reference_mode(spec: Mapping[str, Any], recipe_family: RecipeFamily) -> ReferenceMode:
    example_id = spec.get("exampleId")
    space_group_id = spec.get("spaceGroupId")
    raw_scope = spec.get("refScope", "all")
    if raw_scope not in {"none", "all", "pose", "bg"}:
        raise CutPlanError("unknown_reference_mode")
    if not example_id and not space_group_id:
        return "none"
    # Published space sets bind a per-cut pose control while the set contract owns
    # continuity.  Stored all/bg values must not compete with that binding.
    mode: ReferenceMode = "pose" if space_group_id else raw_scope
    if mode == "none":
        raise CutPlanError("reference_mode_none_with_reference")
    if recipe_family == "product" and mode != "all":
        raise CutPlanError("product_reference_mode_must_be_all")
    return mode


def _recipe(spec: Mapping[str, Any]) -> tuple[
    RecipeFamily, CaptureMode | None, ProductVariant | None
]:
    cut_type = spec.get("cutType")
    shot = spec.get("shot")
    direction = spec.get("direction")
    face = spec.get("faceExposure")

    if cut_type == "styling":
        if shot not in _WORN_SHOTS or direction not in _WORN_DIRECTIONS \
                or face not in _WORN_FACES:
            raise CutPlanError("invalid_styling_spec")
        return "styling", "lifestyle", None
    if cut_type == "mirror":
        if shot not in _WORN_SHOTS or direction is not None or face not in _MIRROR_FACES:
            raise CutPlanError("invalid_mirror_spec")
        return "styling", "mirrorSelfie", None
    if cut_type == "horizon":
        if shot not in _WORN_SHOTS or direction not in _WORN_DIRECTIONS \
                or face not in _WORN_FACES:
            raise CutPlanError("invalid_horizon_spec")
        return "horizon", "studio", None
    if cut_type == "product":
        if shot not in _PRODUCT_SHOTS or direction not in _PRODUCT_DIRECTIONS or face is not None:
            raise CutPlanError("invalid_product_spec")
        return "product", None, shot
    raise CutPlanError("unknown_cut_type")


def compile_cut_plan(
    spec: Mapping[str, Any],
    clothing_type: str,
    *,
    fit_profile: Mapping[str, Any] | None = None,
) -> CutPlan:
    """Compile a normalized cut spec into an unambiguous prompt contract.

    ``fit_profile`` may be passed separately from analysis.  For small callers and
    fixtures, a normalized ``fitProfile`` embedded in ``spec`` is also accepted.
    """

    if not isinstance(spec, Mapping):
        raise CutPlanError("invalid_cut_spec")
    normalized_clothing_type = _normalized_clothing_type(clothing_type)
    recipe_family, capture_mode, product_variant = _recipe(spec)
    reference_mode = _reference_mode(spec, recipe_family)
    reference_direction_compatible = spec.get("_referenceDirectionCompatible", True)
    if type(reference_direction_compatible) is not bool:
        raise CutPlanError("invalid_reference_direction_compatibility")
    reference_face_visibility = spec.get("_referenceFaceVisibility")
    if reference_face_visibility not in {None, "hidden", "visible"}:
        raise CutPlanError("invalid_reference_face_visibility")

    raw_pose = spec.get("pose", "auto")
    if not isinstance(raw_pose, str) or not raw_pose.strip():
        raise CutPlanError("invalid_pose")
    pose = raw_pose.strip()
    explicit_pose = pose != "auto"

    raw_matches = spec.get("matchIds") or []
    if not isinstance(raw_matches, (list, tuple)):
        raise CutPlanError("invalid_matching")
    outer_closure = spec.get("outerClosureState")
    if outer_closure is not None and outer_closure not in _CLOSURES:
        raise CutPlanError("invalid_outer_closure")

    space_group_id = spec.get("spaceGroupId")
    space_set_continuity: bool | None = None
    if space_group_id:
        raw_continuity = spec.get(
            "_spaceSetContinuity", spec.get("spaceSetContinuity", True)
        )
        if type(raw_continuity) is not bool:
            raise CutPlanError("invalid_space_set_continuity")
        space_set_continuity = raw_continuity

    effective_fit_profile = fit_profile
    if effective_fit_profile is None and spec.get("fitProfile") is not None:
        effective_fit_profile = spec.get("fitProfile")
    declared_fit_axes = _declared_fit_axes(effective_fit_profile)

    reference_attributes = list(_REFERENCE_ATTRIBUTES[reference_mode])
    if reference_mode == "all" and not reference_direction_compatible:
        # 방향이 달라지면 예시 포즈의 좌우·근원근과 카메라가 새 방향과 결합할 수 없다.
        # 장면·광원·촬영 톤만 남기고 방향 결합 항목은 계열 기본값으로 되돌린다.
        for attribute in ("pose", "camera"):
            if attribute in reference_attributes:
                reference_attributes.remove(attribute)
    if recipe_family == "product" and "pose" in reference_attributes:
        reference_attributes.remove("pose")
    if explicit_pose and "pose" in reference_attributes:
        reference_attributes.remove("pose")

    owners: dict[str, AttributeOwner] = {
        attribute: "productTruth" for attribute in _PRODUCT_TRUTH_ATTRIBUTES
    }
    owners.update({attribute: "storyboard" for attribute in _STORYBOARD_ATTRIBUTES})
    owners.update({attribute: "recipe" for attribute in _REFERENCE_CAPABLE_ATTRIBUTES})
    owners.update({attribute: "reference" for attribute in reference_attributes})
    if spec.get("modelId"):
        owners["faceIdentity"] = "modelFace"
        owners["bodyProportions"] = "modelFullBody"
    else:
        owners["faceIdentity"] = "recipe"
        owners["bodyProportions"] = "recipe"
    if explicit_pose:
        owners["pose"] = "storyboard"
    for axis in declared_fit_axes:
        owners[f"fit.{axis}"] = "fitProfile"
    if space_set_continuity is not None:
        owners["sceneContinuity"] = "spaceSet"

    storyboard_values = {
        "color": spec.get("colorId"),
        "direction": spec.get("direction"),
        "shot": spec.get("shot"),
        "face": spec.get("faceExposure"),
        "pose": pose,
        "model": spec.get("modelId"),
        "matching": [str(value) for value in raw_matches],
        "outerClosure": outer_closure,
    }

    return CutPlan(
        clothing_type=normalized_clothing_type,
        recipe_family=recipe_family,
        capture_mode=capture_mode,
        product_variant=product_variant,
        reference_mode=reference_mode,
        space_set_continuity=space_set_continuity,
        reference_direction_compatible=reference_direction_compatible,
        reference_face_visibility=reference_face_visibility,
        declared_fit_axes=declared_fit_axes,
        reference_attributes=tuple(reference_attributes),
        attribute_owners=MappingProxyType(owners),
        storyboard_values=MappingProxyType(storyboard_values),
    )


def render_prompt_contract(plan: CutPlan) -> str:
    """Convenience wrapper for callers that keep rendering functions module-level."""

    if not isinstance(plan, CutPlan):
        raise CutPlanError("invalid_cut_plan")
    return plan.render_prompt_contract()
