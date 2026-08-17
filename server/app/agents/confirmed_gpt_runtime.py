"""Runtime adapter for the owner-confirmed GPT styling profile.

Every structurally eligible front styling cut requests this path. Its selected
service example must then have manually verified directing metadata. Once selected,
every required authority input is atomic and fail-closed: the caller must not
silently substitute the generic detail-cut packet.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .confirmed_gpt_directing import (
    ConfirmedGptDirectingError,
    bind_confirmed_gpt_directing,
    confirmed_gpt_explicitly_excluded,
)
from .confirmed_gpt_prompt import (
    ConfirmedGptPromptInput,
    ConfirmedGptScope,
    InputRole,
    SellerEvidencePanel,
    SellerFact,
    SellerUncertainty,
)
from .gemini_image import InlineImage
from . import product_evidence_contract
from ..services import seller_evidence_grid


class ConfirmedGptRuntimeError(ValueError):
    """The selected confirmed-profile cut cannot be bound exactly."""


@dataclass(frozen=True)
class ConfirmedGptRuntimePacket:
    prompt_input: ConfirmedGptPromptInput
    images: tuple[InlineImage, ...]
    manifest: str
    evidence_grid_sha256: str


def profile_requested(spec: dict) -> bool:
    """Return whether a normalized cut requires the confirmed-profile contract.

    This predicate intentionally depends only on the requested cut contract—not on
    whether its current catalog entry happens to be complete. Missing metadata or
    runtime bytes are errors later, never a reason to fall back to the generic packet.
    """

    example_id = spec.get("exampleId") if isinstance(spec, dict) else None
    if not example_id:
        return False
    structurally_eligible = bool(
        spec.get("cutType") == "styling"
        and spec.get("direction") == "front"
        and spec.get("shot") in {"full", "medium"}
        and spec.get("refScope") == "all"
        and spec.get("pose") == "auto"
        and not spec.get("spaceGroupId")
        and spec.get("_referenceDirectionCompatible") is not False
    )
    if not structurally_eligible:
        return False
    try:
        return not confirmed_gpt_explicitly_excluded(str(example_id))
    except ConfirmedGptDirectingError as exc:
        raise ConfirmedGptRuntimeError(str(exc)) from exc


def _image(value: object, field: str) -> InlineImage:
    if (
        not isinstance(value, InlineImage)
        or not isinstance(value.mime, str)
        or not value.mime.startswith("image/")
        or not isinstance(value.data, bytes)
        or not value.data
    ):
        raise ConfirmedGptRuntimeError(f"confirmed_gpt_{field}_image_required")
    return value


def _manifest(*, matching_attached: bool) -> str:
    labels = [
        "MANNEQUIN — selected garment-local color and fit authority",
        "MODEL FACE — direction sheet",
        "MODEL FULL BODY — direction sheet",
        "PRODUCT — sold-product labelled evidence grid",
    ]
    if matching_attached:
        labels.append("MATCHING — directly selected garment evidence")
    labels.append("EXAMPLE REFERENCE (scope: all) — service reference")
    return "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))


def build_packet(
    spec: dict,
    *,
    clothing_type: str,
    identity_source: str,
    selected_model_id: str | None,
    effective_model_id: str | None,
    uses_base_color: bool,
    mannequin_image: InlineImage,
    face_direction_sheet: InlineImage,
    full_body_direction_sheet: InlineImage,
    seller_images: tuple[tuple[str, InlineImage], ...],
    matching_images: tuple[InlineImage, ...],
    example_image: InlineImage,
    evidence_contract: object,
) -> ConfirmedGptRuntimePacket:
    """Build the exact ordered provider packet or reject the cut without fallback."""

    if not profile_requested(spec):
        raise ConfirmedGptRuntimeError("confirmed_gpt_profile_not_requested")
    if identity_source != "VIRTUAL":
        raise ConfirmedGptRuntimeError("confirmed_gpt_requires_virtual_model")
    if not selected_model_id or selected_model_id != effective_model_id:
        raise ConfirmedGptRuntimeError("confirmed_gpt_forbids_model_substitution")
    if uses_base_color is not True:
        raise ConfirmedGptRuntimeError("confirmed_gpt_requires_base_color")
    if not isinstance(seller_images, tuple) or not 1 <= len(seller_images) <= 4:
        raise ConfirmedGptRuntimeError("confirmed_gpt_requires_one_to_four_seller_images")
    if not isinstance(matching_images, tuple) or len(matching_images) > 1:
        raise ConfirmedGptRuntimeError("confirmed_gpt_supports_at_most_one_matching_garment")

    mannequin = _image(mannequin_image, "mannequin")
    face_sheet = _image(face_direction_sheet, "face_direction_sheet")
    body_sheet = _image(full_body_direction_sheet, "full_body_direction_sheet")
    example = _image(example_image, "service_example")
    matches = tuple(_image(image, "matching") for image in matching_images)

    slots: list[str] = []
    current_sources: list[tuple[bytes, str]] = []
    for item in seller_images:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise ConfirmedGptRuntimeError("confirmed_gpt_invalid_seller_image_record")
        image = _image(item[1], "seller")
        slots.append(item[0])
        current_sources.append((image.data, image.mime))

    try:
        contract = product_evidence_contract.validate_persisted(evidence_contract)
        if not product_evidence_contract.source_binding_matches(
            contract, current_sources, slots
        ):
            raise ConfirmedGptRuntimeError("confirmed_gpt_seller_source_binding_drift")

        panels_by_ordinal = {
            panel["evidenceOrdinal"]: panel for panel in contract["panels"]
        }
        grid_panels = []
        prompt_panels = []
        for ordinal, ((_, image), binding_row) in enumerate(
            zip(seller_images, contract["inputBinding"]["images"], strict=True), 1
        ):
            panel = panels_by_ordinal.get(ordinal)
            if panel is None or panel["slot"] != binding_row["slot"]:
                raise ConfirmedGptRuntimeError(
                    "confirmed_gpt_evidence_panel_binding_mismatch"
                )
            grid_panels.append(
                {
                    "slot": panel["slot"],
                    "detail": panel["detail"],
                    "surfaceAuthority": panel["surfaceAuthority"],
                    "judgeability": panel["judgeability"],
                    "judgeabilityReasons": list(panel["judgeabilityReasons"]),
                    "data": image.data,
                }
            )
            prompt_panels.append(
                SellerEvidencePanel(
                    panel=ordinal,
                    slot=panel["slot"],
                    detail=panel["detail"],
                    surface_authority=panel["surfaceAuthority"],
                    judgeability=panel["judgeability"].upper(),
                    limits=tuple(panel["judgeabilityReasons"]),
                    provided=True,
                )
            )

        evidence_id = "confirmed_" + contract["contractSha256"][:16]
        grid_bytes, records, preflight, _prompt_map = (
            seller_evidence_grid.compose_labelled_grid(
                grid_panels, direction="front", evidence_id=evidence_id
            )
        )
        if len(records) != len(prompt_panels) or preflight.get("passed") is not True:
            raise ConfirmedGptRuntimeError("confirmed_gpt_evidence_grid_preflight_failed")

        directing = bind_confirmed_gpt_directing(
            str(spec["exampleId"]),
            example.data,
            shot=str(spec["shot"]),
            direction=str(spec["direction"]),
            clothing_type=clothing_type,
        )
    except ConfirmedGptRuntimeError:
        raise
    except Exception as exc:
        raise ConfirmedGptRuntimeError(str(exc)) from exc

    matching_attached = bool(matches)
    roles = (
        InputRole.SELECTED_MANNEQUIN_CUT,
        InputRole.MODEL_FACE_DIRECTION_SHEET,
        InputRole.MODEL_FULL_BODY_DIRECTION_SHEET,
        InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID,
        *((InputRole.MATCHING_GARMENT_EVIDENCE,) if matching_attached else ()),
        InputRole.SERVICE_EXAMPLE_REFERENCE,
    )
    prompt_input = ConfirmedGptPromptInput(
        scope=ConfirmedGptScope(
            family_mode="styling/direct",
            reference_scope="all",
            pose="auto",
            reference_direction_compatible=True,
            space_group_id=None,
            selected_mannequin=True,
            example_source="service",
        ),
        ordered_roles=roles,
        seller_evidence=tuple(prompt_panels),
        cut_lock=directing.cut_lock(),
        visible_surface_plan=contract["visibleSurfacePlan"],
        hard_facts=tuple(
            SellerFact(code=fact["code"], value=fact["value"])
            for fact in contract["hardFacts"]
        ),
        uncertainties=tuple(
            SellerUncertainty(
                code=fact["code"], value=fact["value"], reason=fact["reason"]
            )
            for fact in contract["uncertainties"]
        ),
        outfit=directing.outfit_lock(matching_attached=matching_attached),
        pose_semantics=directing.pose_semantics,
    )
    grid = InlineImage("image/png", grid_bytes)
    images = (mannequin, face_sheet, body_sheet, grid, *matches, example)
    return ConfirmedGptRuntimePacket(
        prompt_input=prompt_input,
        images=images,
        manifest=_manifest(matching_attached=matching_attached),
        evidence_grid_sha256=sha256(grid_bytes).hexdigest(),
    )
