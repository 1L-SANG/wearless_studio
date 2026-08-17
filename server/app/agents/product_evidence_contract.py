"""Strict seller-product evidence contract produced inside AG-01.

The vision model describes what is visible in the already-attached AG-01 images.  It
does not get to declare which bytes it saw: the server creates and later verifies the
ordered SHA-256/byte-length binding for both the seller originals and the resized
analysis inputs.  This keeps the confirmed GPT cut path fail-closed when product
images are replaced or reordered after analysis.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any


PERSISTED_KEY = "confirmedGptProductEvidence"
INTERNAL_BINDING_KEY = "__confirmedGptProductEvidenceInput"
SCHEMA_VERSION = 1

_SLOT_MAP = {
    "Front": "FRONT",
    "Back": "BACK",
    "Detail": "FRONT_DETAIL",
    "BackDetail": "BACK_DETAIL",
}
_JUDGEABILITY = frozenset({"usable", "uncertain"})
_JUDGEABILITY_REASONS = frozenset(
    {
        "clear_enough",
        "blur",
        "occlusion",
        "hanger_distortion",
        "fold_distortion",
        "mixed_light",
        "background_interference",
        "partial_crop",
    }
)
_CODE_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


class ProductEvidenceContractError(ValueError):
    """AG-01 output cannot safely feed the confirmed GPT product-evidence path."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sequence_sha(rows: list[dict[str, Any]]) -> str:
    return sha256(_canonical_bytes(rows)).hexdigest()


def _byte_record(data: bytes, mime: str) -> dict[str, Any]:
    if not isinstance(data, bytes) or not data:
        raise ProductEvidenceContractError("product_evidence_image_bytes_required")
    if not isinstance(mime, str) or not mime.startswith("image/"):
        raise ProductEvidenceContractError("product_evidence_image_mime_required")
    return {
        "mime": mime.lower(),
        "sha256": sha256(data).hexdigest(),
        "byteLength": len(data),
    }


def build_input_binding(
    source_images: list[tuple[bytes, str]],
    analysis_images: list[tuple[bytes, str]],
    slots: list[str],
) -> dict[str, Any]:
    """Seal the exact ordered originals and actual bytes attached to the AG-01 call."""

    if not (
        isinstance(source_images, list)
        and isinstance(analysis_images, list)
        and isinstance(slots, list)
        and 1 <= len(source_images) <= 4
        and len(source_images) == len(analysis_images) == len(slots)
    ):
        raise ProductEvidenceContractError("product_evidence_input_count_mismatch")

    images: list[dict[str, Any]] = []
    for ordinal, (source, analyzed, slot) in enumerate(
        zip(source_images, analysis_images, slots, strict=True), 1
    ):
        if slot not in _SLOT_MAP:
            raise ProductEvidenceContractError(
                f"product_evidence_unknown_slot:{slot}"
            )
        if not (
            isinstance(source, tuple)
            and len(source) == 2
            and isinstance(analyzed, tuple)
            and len(analyzed) == 2
        ):
            raise ProductEvidenceContractError("product_evidence_image_tuple_required")
        source_data, source_mime = source
        analysis_data, analysis_mime = analyzed
        images.append(
            {
                "ordinal": ordinal,
                "slot": _SLOT_MAP[slot],
                "source": _byte_record(source_data, source_mime),
                "analysis": _byte_record(analysis_data, analysis_mime),
            }
        )
    if not any(row["slot"] == "FRONT" for row in images):
        raise ProductEvidenceContractError("product_evidence_front_image_required")

    source_sequence = [
        {
            "ordinal": row["ordinal"],
            "slot": row["slot"],
            **row["source"],
        }
        for row in images
    ]
    analysis_sequence = [
        {
            "ordinal": row["ordinal"],
            "slot": row["slot"],
            **row["analysis"],
        }
        for row in images
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "hashAlgorithm": "sha256",
        "orderedSourceInputSha256": _sequence_sha(source_sequence),
        "orderedAnalysisInputSha256": _sequence_sha(analysis_sequence),
        "images": images,
    }


def evidence_schema() -> dict[str, Any]:
    """Strict-compatible JSON Schema fragment for the one existing AG-01 call."""

    panel = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidenceOrdinal": {"type": "integer"},
            "detail": {"type": "string"},
            "judgeability": {
                "type": "string",
                "enum": sorted(_JUDGEABILITY),
            },
            "judgeabilityReasons": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": sorted(_JUDGEABILITY_REASONS),
                },
            },
        },
        "required": [
            "evidenceOrdinal",
            "detail",
            "judgeability",
            "judgeabilityReasons",
        ],
    }
    hard_fact = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {"type": "string"},
            "value": {"type": "string"},
            "evidenceOrdinals": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["code", "value", "evidenceOrdinals"],
    }
    uncertainty = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {"type": "string"},
            "value": {"type": "string"},
            "reason": {"type": "string"},
            "evidenceOrdinals": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["code", "value", "reason", "evidenceOrdinals"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "panels": {"type": "array", "items": panel},
            "hardFacts": {"type": "array", "items": hard_fact},
            "uncertainties": {"type": "array", "items": uncertainty},
            "visibleSurfacePlan": {"type": "string"},
        },
        "required": [
            "panels",
            "hardFacts",
            "uncertainties",
            "visibleSurfacePlan",
        ],
    }


def _exact_keys(value: object, expected: set[str], error: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductEvidenceContractError(error)
    return value


def _line(value: object, field: str, *, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or _CONTROL_RE.search(value)
        or "${" in value
        or "[[" in value
        or "]]" in value
    ):
        raise ProductEvidenceContractError(
            f"product_evidence_invalid_single_line:{field}"
        )
    return value


def _validated_binding(value: object) -> dict[str, Any]:
    binding = _exact_keys(
        value,
        {
            "schemaVersion",
            "hashAlgorithm",
            "orderedSourceInputSha256",
            "orderedAnalysisInputSha256",
            "images",
        },
        "product_evidence_input_binding_field_set_mismatch",
    )
    if binding["schemaVersion"] != SCHEMA_VERSION or binding["hashAlgorithm"] != "sha256":
        raise ProductEvidenceContractError("product_evidence_input_binding_version_mismatch")
    images = binding["images"]
    if not isinstance(images, list) or not 1 <= len(images) <= 4:
        raise ProductEvidenceContractError("product_evidence_input_binding_images_invalid")
    normalized: list[dict[str, Any]] = []
    for expected_ordinal, row_value in enumerate(images, 1):
        row = _exact_keys(
            row_value,
            {"ordinal", "slot", "source", "analysis"},
            "product_evidence_input_image_field_set_mismatch",
        )
        if row["ordinal"] != expected_ordinal or row["slot"] not in set(_SLOT_MAP.values()):
            raise ProductEvidenceContractError("product_evidence_input_image_order_mismatch")
        copies: dict[str, Any] = {"ordinal": expected_ordinal, "slot": row["slot"]}
        for kind in ("source", "analysis"):
            record = _exact_keys(
                row[kind],
                {"mime", "sha256", "byteLength"},
                "product_evidence_input_byte_record_field_set_mismatch",
            )
            if (
                not isinstance(record["mime"], str)
                or not record["mime"].startswith("image/")
                or not isinstance(record["sha256"], str)
                or not _SHA_RE.fullmatch(record["sha256"])
                or type(record["byteLength"]) is not int
                or record["byteLength"] <= 0
            ):
                raise ProductEvidenceContractError("product_evidence_input_byte_record_invalid")
            copies[kind] = dict(record)
        normalized.append(copies)
    if not any(row["slot"] == "FRONT" for row in normalized):
        raise ProductEvidenceContractError("product_evidence_front_image_required")

    source_sequence = [
        {"ordinal": row["ordinal"], "slot": row["slot"], **row["source"]}
        for row in normalized
    ]
    analysis_sequence = [
        {"ordinal": row["ordinal"], "slot": row["slot"], **row["analysis"]}
        for row in normalized
    ]
    if binding["orderedSourceInputSha256"] != _sequence_sha(source_sequence):
        raise ProductEvidenceContractError("product_evidence_source_sequence_hash_mismatch")
    if binding["orderedAnalysisInputSha256"] != _sequence_sha(analysis_sequence):
        raise ProductEvidenceContractError("product_evidence_analysis_sequence_hash_mismatch")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "hashAlgorithm": "sha256",
        "orderedSourceInputSha256": binding["orderedSourceInputSha256"],
        "orderedAnalysisInputSha256": binding["orderedAnalysisInputSha256"],
        "images": normalized,
    }


def render_prompt_block(binding_value: object) -> str:
    """Render trusted image order plus the bounded evidence-extraction instructions."""

    binding = _validated_binding(binding_value)
    rows = "\n".join(
        "- evidenceOrdinal {ordinal}: slot {slot}; seller-original "
        "sha256={source_sha}, bytes={source_bytes}, mime={source_mime}; attached-analysis "
        "sha256={analysis_sha}, bytes={analysis_bytes}, mime={analysis_mime}".format(
            ordinal=row["ordinal"],
            slot=row["slot"],
            source_sha=row["source"]["sha256"],
            source_bytes=row["source"]["byteLength"],
            source_mime=row["source"]["mime"],
            analysis_sha=row["analysis"]["sha256"],
            analysis_bytes=row["analysis"]["byteLength"],
            analysis_mime=row["analysis"]["mime"],
        )
        for row in binding["images"]
    )
    return f"""

CONFIRMED GPT PRODUCT-EVIDENCE CONTRACT (server-owned, required):
The following evidenceOrdinal order is the exact attached image order. The server, not
you, owns these hashes and byte counts. Refer to evidence only by these ordinals; never
invent, omit, reorder or duplicate an ordinal.
{rows}

Return `confirmedGptProductEvidence` in English with exactly these four fields:
- panels: exactly one item per evidenceOrdinal, in order. `detail` is a short literal
  description of the garment area actually judgeable in that image. `judgeability` is
  usable or uncertain. `judgeabilityReasons` is a non-empty unique list from:
  {', '.join(sorted(_JUDGEABILITY_REASONS))}. Use clear_enough only when no listed
  limitation applies. A supplied but weak image is uncertain, never missing.
- hardFacts: 1-12 visibly proven product-identity/construction/seam/closure/pattern/
  permanent-detail facts. Each item is {{code, value, evidenceOrdinals}}. Codes are
  lowercase snake_case. Every ordinal must directly support the fact. Do not promote
  inferred material, exact RGB, hand feel or unsupported worn fit to a hard fact.
- uncertainties: 1-12 facts that seller pixels cannot prove exactly. Each item is
  {{code, value, reason, evidenceOrdinals}}. Include the relevant source ordinals and a
  concrete visual limitation; do not use generic model uncertainty.
- visibleSurfacePlan: one single-line FRONT-direction plan. FRONT/FRONT_DETAIL surfaces
  are dominant, BACK/BACK_DETAIL is context only for physically revealed slivers and
  transitions. Name supported seams/pattern/structure that must remain on the correct
  surface; do not invent hidden construction.

Hard and uncertain fact codes must be unique across both arrays. Ordinal arrays must be
non-empty, unique, ascending, and within the attached evidence order. This contract is
additional output from this same AG-01 call; do not weaken or replace the ordinary
product-analysis fields above.
""".rstrip()


def _ordinals(value: object, *, count: int, field: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int for item in value)
        or value != sorted(set(value))
        or value[0] < 1
        or value[-1] > count
    ):
        raise ProductEvidenceContractError(f"product_evidence_invalid_ordinals:{field}")
    return list(value)


def _validate_facts(
    raw_facts: object,
    *,
    uncertain: bool,
    panel_status: dict[int, str],
    codes: set[str],
) -> list[dict[str, Any]]:
    label = "uncertainty" if uncertain else "hard_fact"
    if not isinstance(raw_facts, list) or not 1 <= len(raw_facts) <= 12:
        raise ProductEvidenceContractError(f"product_evidence_{label}s_required")
    expected = {"code", "value", "evidenceOrdinals"}
    if uncertain:
        expected.add("reason")
    out: list[dict[str, Any]] = []
    for index, raw_value in enumerate(raw_facts, 1):
        row = _exact_keys(
            raw_value,
            expected,
            f"product_evidence_{label}_field_set_mismatch",
        )
        code = row["code"]
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code) or code in codes:
            raise ProductEvidenceContractError(f"product_evidence_invalid_{label}_code")
        codes.add(code)
        ordinals = _ordinals(
            row["evidenceOrdinals"], count=len(panel_status), field=f"{label}_{index}"
        )
        if not uncertain and not any(panel_status[ordinal] == "usable" for ordinal in ordinals):
            raise ProductEvidenceContractError(
                "product_evidence_hard_fact_requires_usable_panel"
            )
        item: dict[str, Any] = {
            "code": code,
            "value": _line(row["value"], f"{label}_{code}", max_length=400),
            "evidenceOrdinals": ordinals,
        }
        if uncertain:
            item["reason"] = _line(
                row["reason"], f"uncertainty_reason_{code}", max_length=400
            )
        out.append(item)
    return out


def validate_and_bind(raw_value: object, binding_value: object) -> dict[str, Any]:
    """Strictly validate model prose, then attach only the server-trusted byte binding."""

    binding = _validated_binding(binding_value)
    raw = _exact_keys(
        raw_value,
        {"panels", "hardFacts", "uncertainties", "visibleSurfacePlan"},
        "product_evidence_contract_field_set_mismatch",
    )
    raw_panels = raw["panels"]
    if not isinstance(raw_panels, list) or len(raw_panels) != len(binding["images"]):
        raise ProductEvidenceContractError("product_evidence_panel_count_mismatch")

    panels: list[dict[str, Any]] = []
    panel_status: dict[int, str] = {}
    for image, panel_value in zip(binding["images"], raw_panels, strict=True):
        panel = _exact_keys(
            panel_value,
            {"evidenceOrdinal", "detail", "judgeability", "judgeabilityReasons"},
            "product_evidence_panel_field_set_mismatch",
        )
        ordinal = image["ordinal"]
        if panel["evidenceOrdinal"] != ordinal:
            raise ProductEvidenceContractError("product_evidence_panel_order_mismatch")
        status = panel["judgeability"]
        reasons = panel["judgeabilityReasons"]
        if status not in _JUDGEABILITY:
            raise ProductEvidenceContractError("product_evidence_invalid_judgeability")
        if (
            not isinstance(reasons, list)
            or not reasons
            or reasons != list(dict.fromkeys(reasons))
            or any(reason not in _JUDGEABILITY_REASONS for reason in reasons)
            or ("clear_enough" in reasons and len(reasons) != 1)
        ):
            raise ProductEvidenceContractError("product_evidence_invalid_judgeability_reasons")
        slot = image["slot"]
        panels.append(
            {
                "evidenceOrdinal": ordinal,
                "slot": slot,
                "detail": _line(
                    panel["detail"], f"panel_{ordinal}_detail", max_length=180
                ),
                "surfaceAuthority": (
                    "DOMINANT" if slot in {"FRONT", "FRONT_DETAIL"} else "CONTEXT"
                ),
                "judgeability": status,
                "judgeabilityReasons": list(reasons),
                "provided": True,
            }
        )
        panel_status[ordinal] = status

    codes: set[str] = set()
    hard_facts = _validate_facts(
        raw["hardFacts"], uncertain=False, panel_status=panel_status, codes=codes
    )
    uncertainties = _validate_facts(
        raw["uncertainties"], uncertain=True, panel_status=panel_status, codes=codes
    )
    surface_plan = _line(
        raw["visibleSurfacePlan"], "visible_surface_plan", max_length=1000
    )
    folded_plan = surface_plan.casefold()
    if "front" not in folded_plan or "dominant" not in folded_plan:
        raise ProductEvidenceContractError("product_evidence_front_surface_plan_required")

    contract: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "direction": "front",
        "inputBinding": binding,
        "panels": panels,
        "hardFacts": hard_facts,
        "uncertainties": uncertainties,
        "visibleSurfacePlan": surface_plan,
    }
    contract["contractSha256"] = sha256(_canonical_bytes(contract)).hexdigest()
    return contract


def validate_persisted(value: object) -> dict[str, Any]:
    """Verify an analysis-payload contract before any generation adapter consumes it."""

    stored = _exact_keys(
        value,
        {
            "schemaVersion",
            "direction",
            "inputBinding",
            "panels",
            "hardFacts",
            "uncertainties",
            "visibleSurfacePlan",
            "contractSha256",
        },
        "product_evidence_persisted_field_set_mismatch",
    )
    if stored["schemaVersion"] != SCHEMA_VERSION or stored["direction"] != "front":
        raise ProductEvidenceContractError("product_evidence_persisted_version_mismatch")
    binding = _validated_binding(stored["inputBinding"])
    panels = stored["panels"]
    if not isinstance(panels, list) or len(panels) != len(binding["images"]):
        raise ProductEvidenceContractError("product_evidence_panel_count_mismatch")
    raw_panels: list[dict[str, Any]] = []
    for image, panel_value in zip(binding["images"], panels, strict=True):
        panel = _exact_keys(
            panel_value,
            {
                "evidenceOrdinal",
                "slot",
                "detail",
                "surfaceAuthority",
                "judgeability",
                "judgeabilityReasons",
                "provided",
            },
            "product_evidence_persisted_panel_field_set_mismatch",
        )
        expected_authority = (
            "DOMINANT" if image["slot"] in {"FRONT", "FRONT_DETAIL"} else "CONTEXT"
        )
        if (
            panel["evidenceOrdinal"] != image["ordinal"]
            or panel["slot"] != image["slot"]
            or panel["surfaceAuthority"] != expected_authority
            or panel["provided"] is not True
        ):
            raise ProductEvidenceContractError("product_evidence_persisted_panel_binding_mismatch")
        raw_panels.append(
            {
                "evidenceOrdinal": panel["evidenceOrdinal"],
                "detail": panel["detail"],
                "judgeability": panel["judgeability"],
                "judgeabilityReasons": panel["judgeabilityReasons"],
            }
        )
    rebuilt = validate_and_bind(
        {
            "panels": raw_panels,
            "hardFacts": stored["hardFacts"],
            "uncertainties": stored["uncertainties"],
            "visibleSurfacePlan": stored["visibleSurfacePlan"],
        },
        binding,
    )
    if stored["contractSha256"] != rebuilt["contractSha256"]:
        raise ProductEvidenceContractError("product_evidence_contract_hash_mismatch")
    return rebuilt


def source_binding_matches(
    contract_value: object,
    source_images: list[tuple[bytes, str]],
    slots: list[str],
) -> bool:
    """Return whether current seller bytes/order still match the sealed AG-01 originals."""

    contract = validate_persisted(contract_value)
    try:
        current = build_input_binding(source_images, source_images, slots)
    except ProductEvidenceContractError:
        return False
    sealed = contract["inputBinding"]
    return (
        current["orderedSourceInputSha256"]
        == sealed["orderedSourceInputSha256"]
        and [row["source"] for row in current["images"]]
        == [row["source"] for row in sealed["images"]]
        and [row["slot"] for row in current["images"]]
        == [row["slot"] for row in sealed["images"]]
    )
