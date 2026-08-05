"""Protected-component contract for Product Truth backed hybrid compositing.

This module is intentionally pure: it only interprets approved Product Truth and
validated geometry inventories. A single component box is never promoted into
existence truth because boxes can be hallucinated by the geometry model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ProtectedComponentStatus(str, Enum):
    PASS = "PASS"
    MISSING = "MISSING"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class ProtectedComponentReason:
    component: str
    reason: str


@dataclass(frozen=True)
class ProtectedComponentResult:
    status: ProtectedComponentStatus
    required_components: tuple[str, ...]
    available_components: tuple[str, ...]
    missing: tuple[ProtectedComponentReason, ...] = ()
    review_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "requiredComponents": list(self.required_components),
            "availableComponents": list(self.available_components),
            "missing": [
                {"component": item.component, "reason": item.reason}
                for item in self.missing
            ],
            "reviewReasons": list(self.review_reasons),
        }


_ABSENT_VALUES = {
    "0",
    "absent",
    "collarless",
    "false",
    "none",
    "no",
    "no_collar",
    "without",
}
_UNKNOWN_VALUES = {"", "auto", "null", "unknown", "unspecified"}

_BOX_KEYS = {
    "collar": ("collar_box",),
    "placket": ("placket_box", "button_box"),
    "cuffs": ("cuff_box", "cuffs_box", "cuff_l_box", "cuff_r_box"),
    "logo": ("logo_box",),
    "print": ("print_box", "text_print_box", "graphic_print_box"),
    "embroidery": ("embroidery_box",),
}


def _truth_section(truth: Mapping | None, *names: str) -> Mapping:
    if not isinstance(truth, Mapping):
        return {}
    for name in names:
        value = truth.get(name)
        if isinstance(value, Mapping):
            return value
    return {}


def _known_absent(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return text in _ABSENT_VALUES


def _known_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return text not in _ABSENT_VALUES and text not in _UNKNOWN_VALUES


def _int_or_none(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _flags(garment: Mapping) -> set[str]:
    return {
        str(value or "").strip().upper()
        for value in garment.get("structureFlags") or garment.get("structure_flags") or []
        if str(value or "").strip()
    }


def _component_boxes(inventory: Mapping | None) -> Mapping:
    if not isinstance(inventory, Mapping):
        return {}
    boxes = inventory.get("component_boxes") or inventory.get("componentBoxes") or {}
    return boxes if isinstance(boxes, Mapping) else {}


def _has_box(inventory: Mapping | None, component: str) -> bool:
    boxes = _component_boxes(inventory)
    return any(key in boxes for key in _BOX_KEYS.get(component, (f"{component}_box",)))


def _inventory_positive(inventory: Mapping | None, component: str) -> bool:
    if not isinstance(inventory, Mapping):
        return False
    if component == "placket":
        buttons = _int_or_none(inventory.get("visible_buttons") or inventory.get("visibleButtons"))
        return bool(inventory.get("placket")) or bool(buttons and buttons > 0)
    return bool(inventory.get(component))


def _both_inventories_positive(
    source_inventory: Mapping | None,
    carrier_inventory: Mapping | None,
    component: str,
) -> bool:
    return (
        _inventory_positive(source_inventory, component)
        and _inventory_positive(carrier_inventory, component)
    )


def _both_boxes_present(
    source_inventory: Mapping | None,
    carrier_inventory: Mapping | None,
    component: str,
) -> bool:
    return _has_box(source_inventory, component) and _has_box(carrier_inventory, component)


def _add(parts: list[str], component: str) -> None:
    if component not in parts:
        parts.append(component)


def derive_required_protected_components(
    truth: Mapping | None,
    *,
    source_inventory: Mapping | None = None,
    carrier_inventory: Mapping | None = None,
) -> tuple[str, ...]:
    """Return protected components required by approved truth plus corroborated inventory.

    Product Truth is authoritative. Validated source+carrier inventory can add a
    requirement when both sides agree positively, but a lone component box cannot.
    """

    garment = _truth_section(truth, "garmentSpec", "garment_spec")
    protected = _truth_section(
        truth, "protectedDetails", "protected_details", "protectedAssets", "protected_assets")
    flags = _flags(garment)
    required: list[str] = []

    collar_type = garment.get("collarType", garment.get("collar_type"))
    if "COLLAR" in flags or _known_present(collar_type):
        _add(required, "collar")

    button_count = _int_or_none(garment.get("buttonCount", garment.get("button_count")))
    if (
        button_count is not None and button_count > 0
    ) or protected.get("buttonCount") or protected.get("button_count") or "BUTTONS" in flags:
        _add(required, "placket")

    cuff_type = garment.get("cuffType", garment.get("cuff_type"))
    if "CUFFS" in flags or _known_present(cuff_type):
        _add(required, "cuffs")

    if protected.get("logo") or protected.get("logos"):
        _add(required, "logo")
    if protected.get("textPrint") or protected.get("graphicPrint") or protected.get("print") or protected.get("prints"):
        _add(required, "print")
    if protected.get("embroidery"):
        _add(required, "embroidery")

    for component in ("collar", "placket", "cuffs", "logo", "print", "embroidery"):
        if (
            _both_inventories_positive(source_inventory, carrier_inventory, component)
            or _both_boxes_present(source_inventory, carrier_inventory, component)
        ):
            _add(required, component)

    if _known_absent(collar_type) and "COLLAR" not in flags and not _both_inventories_positive(
        source_inventory, carrier_inventory, "collar"
    ):
        required = [part for part in required if part != "collar"]
    if button_count == 0 and "BUTTONS" not in flags and not _both_inventories_positive(
        source_inventory, carrier_inventory, "placket"
    ):
        required = [part for part in required if part != "placket"]

    return tuple(required)


def _review_reasons(
    truth: Mapping | None,
    *,
    source_inventory: Mapping | None,
    carrier_inventory: Mapping | None,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(truth, Mapping) or truth.get("status") != "approved":
        return ("approved_truth_required",)

    garment = _truth_section(truth, "garmentSpec", "garment_spec")
    flags = _flags(garment)
    reasons: list[str] = []

    collar_type = garment.get("collarType", garment.get("collar_type"))
    collar_established = (
        "collar" in required
        or _known_absent(collar_type)
        or "COLLAR" in flags
        or _both_inventories_positive(source_inventory, carrier_inventory, "collar")
    )
    if not collar_established:
        reasons.append("insufficient_truth:collar")

    button_count = _int_or_none(garment.get("buttonCount", garment.get("button_count")))
    placket_established = (
        "placket" in required
        or button_count == 0
        or "BUTTONS" in flags
        or _both_inventories_positive(source_inventory, carrier_inventory, "placket")
    )
    if not placket_established:
        reasons.append("insufficient_truth:placket")

    return tuple(reasons)


def evaluate_protected_components(
    truth: Mapping | None,
    *,
    source_inventory: Mapping | None = None,
    carrier_inventory: Mapping | None = None,
) -> ProtectedComponentResult:
    """Compare required protected parts against source/carrier component geometry."""

    required = derive_required_protected_components(
        truth, source_inventory=source_inventory, carrier_inventory=carrier_inventory)
    missing: list[ProtectedComponentReason] = []
    available: list[str] = []

    for component in required:
        source_has = _has_box(source_inventory, component)
        carrier_has = _has_box(carrier_inventory, component)
        if source_has and carrier_has:
            available.append(component)
        elif not source_has and not carrier_has:
            missing.append(ProtectedComponentReason(component, "source_and_carrier_geometry_missing"))
        elif not source_has:
            missing.append(ProtectedComponentReason(component, "source_geometry_missing"))
        else:
            missing.append(ProtectedComponentReason(component, "carrier_geometry_missing"))

    review_reasons = _review_reasons(
        truth,
        source_inventory=source_inventory,
        carrier_inventory=carrier_inventory,
        required=required,
    )
    if missing:
        status = ProtectedComponentStatus.MISSING
    elif review_reasons:
        status = ProtectedComponentStatus.REVIEW
    else:
        status = ProtectedComponentStatus.PASS
    return ProtectedComponentResult(
        status=status,
        required_components=required,
        available_components=tuple(available),
        missing=tuple(missing),
        review_reasons=review_reasons,
    )
