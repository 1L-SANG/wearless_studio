"""Strict seller-product evidence contract produced inside AG-01.

The vision model describes what is visible in the already-attached AG-01 images.  It
does not get to declare which bytes it saw: the server creates and later verifies the
ordered SHA-256/byte-length binding for both the seller originals and the resized
analysis inputs.  This keeps the confirmed GPT cut path fail-closed when product
images are replaced or reordered after analysis.
"""

from __future__ import annotations

from hashlib import sha256
import hmac
import json
import logging
import re
import time
from typing import Any


log = logging.getLogger("wearless.product_evidence_contract")

PERSISTED_KEY = "confirmedGptProductEvidence"
INTERNAL_BINDING_KEY = "__confirmedGptProductEvidenceInput"
HANDOFF_KEY = "confirmedGptProductEvidenceHandoff"
SCHEMA_VERSION = 1
HANDOFF_SCHEMA_VERSION = 1
OBSERVATIONS_VERSION = 2
HANDOFF_TTL_SECONDS = 24 * 60 * 60

FIXED_OBSERVATION_FIELDS = (
    "hem_shape",
    "cuff",
    "button_count_visible",
    "pattern_structure",
    "surface_texture",
    "seam_lines",
)

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
_FRONT_SURFACE_POLICY = (
    "FRONT/FRONT_DETAIL surfaces are DOMINANT; BACK/BACK_DETAIL surfaces are CONTEXT "
    "only for physically revealed slivers and transitions."
)
FRONT_SURFACE_POLICY = _FRONT_SURFACE_POLICY
_BACK_DOMINANT_RE = re.compile(
    r"\b(?:back|back_detail)(?:\s+surface)?\s+(?:is|are|as|=)?\s*dominant\b",
    re.IGNORECASE,
)


def _handoff_secret(secret: str | None) -> bytes:
    if not isinstance(secret, str) or len(secret) < 16:
        raise ProductEvidenceContractError("product_evidence_handoff_secret_unavailable")
    return secret.encode("utf-8")


def issue_handoff(
    contract_value: object,
    secret: str | None,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Sign a short-lived public-analysis artifact for authenticated draft promotion."""

    contract = validate_persisted(contract_value)
    issued_at = int(time.time()) if now is None else int(now)
    payload = {
        "schemaVersion": HANDOFF_SCHEMA_VERSION,
        "issuedAt": issued_at,
        "expiresAt": issued_at + HANDOFF_TTL_SECONDS,
        "contract": contract,
    }
    payload["signature"] = hmac.new(
        _handoff_secret(secret), _canonical_bytes(payload), "sha256"
    ).hexdigest()
    return payload


def verify_handoff(
    value: object,
    secret: str | None,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    handoff = _exact_keys(
        value,
        {"schemaVersion", "issuedAt", "expiresAt", "contract", "signature"},
        "product_evidence_handoff_field_set_mismatch",
    )
    current = int(time.time()) if now is None else int(now)
    issued_at = handoff["issuedAt"]
    expires_at = handoff["expiresAt"]
    if (
        handoff["schemaVersion"] != HANDOFF_SCHEMA_VERSION
        or type(issued_at) is not int
        or type(expires_at) is not int
        or expires_at - issued_at != HANDOFF_TTL_SECONDS
        or issued_at > current + 60
        or current > expires_at
    ):
        raise ProductEvidenceContractError("product_evidence_handoff_expired_or_invalid")
    signature = handoff["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ProductEvidenceContractError("product_evidence_handoff_signature_invalid")
    unsigned = {key: handoff[key] for key in handoff if key != "signature"}
    expected = hmac.new(
        _handoff_secret(secret), _canonical_bytes(unsigned), "sha256"
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ProductEvidenceContractError("product_evidence_handoff_signature_invalid")
    return validate_persisted(handoff["contract"])
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
    observation = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string"},
            "evidenceOrdinals": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["value", "evidenceOrdinals"],
    }
    button_count = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": ["integer", "null"]},
            "evidenceOrdinals": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["value", "evidenceOrdinals"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "panels": {"type": "array", "items": panel},
            "hardFacts": {"type": "array", "items": hard_fact},
            "uncertainties": {"type": "array", "items": uncertainty},
            "hem_shape": observation,
            "cuff": observation,
            "button_count_visible": button_count,
            "pattern_structure": observation,
            "surface_texture": observation,
            "seam_lines": observation,
        },
        "required": [
            "panels",
            "hardFacts",
            "uncertainties",
            *FIXED_OBSERVATION_FIELDS,
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

Return `confirmedGptProductEvidence` in English with exactly these nine fields:
- panels: exactly one item per evidenceOrdinal, in order. `detail` is a short literal
  description of the garment area actually judgeable in that image. `judgeability` is
  usable or uncertain. `judgeabilityReasons` is a non-empty unique list from:
  {', '.join(sorted(_JUDGEABILITY_REASONS))}. Use clear_enough only when no listed
  limitation applies. A supplied but weak image is uncertain, never missing.
- hardFacts: 1-12 visibly proven product-identity/construction/seam/closure/pattern/
  permanent-detail facts. Each item is {{code, value, evidenceOrdinals}}. Codes are
  lowercase snake_case. Every ordinal must directly support the fact. Do not promote
  inferred material, exact RGB, hand feel or unsupported worn fit to a hard fact.
  Within this limit, prioritize visible distinguishing construction over generic
  garment labels. When clearly supported, record closure count separately from closure
  function: uncertainty about whether a closed placket opens must not discard a clearly
  visible button count.

  For distinctive seams or panel boundaries, state the visible count and surface,
  shape, start and end landmarks, and which boundaries actually join. A paired
  construction can be one concise fact. Keep boundary count distinct from parallel
  stitch rows, knit ribs, folds and shadows. Record a connection only if it can be
  traced in the supplied pixels; do not bridge an occlusion by symmetry or expectation.

  Use product-specific facts, not a fixed checklist of positive features. Plain
  garments do not require invented seam facts. Do not infer that a feature is absent
  merely because it is hidden, low contrast or outside the image. Preserve clearly
  supported parts as hardFacts and place only the unresolved count, endpoint or
  connection in uncertainties, with its concrete image limitation and relevant
  evidenceOrdinals.

  Avoid duplicating the same seam in several facts. Do not import examples' button
  numbers, seam counts or garment categories into another product. Each evidenceOrdinal
  must directly support the specific statement; a back-only detail must remain back-only.

  For shoulder and sleeve identity, report visible construction rather than a category
  inferred from the product name or upper-arm coverage. Broad continuous shoulder fabric
  may belong to a sleeveless garment. Distinguish its bound arm-opening edge from a
  separately attached sleeve seam. If the construction cannot be resolved, put that
  specific ambiguity in uncertainties instead of declaring a cap sleeve as a hard fact.
  This does not mean every broad shoulder is sleeveless; preserve genuine sleeves when
  evidenced.
  Do not duplicate any of the six fixed observations below in hardFacts.
- uncertainties: 1-12 facts that seller pixels cannot prove exactly. Each item is
  {{code, value, reason, evidenceOrdinals}}. Include the relevant source ordinals and a
  concrete visual limitation; do not use generic model uncertainty.
  Include unresolved identity-critical counts, seam endpoints, connections or sleeve
  construction when present, not only generic fiber composition. Do not force those
  observations into hardFacts merely because the full photo is marked usable.

  Do not convert a flat garment's proportions into a proven worn hem position or an
  exact physical length. Unsupported worn fit remains an uncertainty under this
  contract.
- hem_shape: {{value, evidenceOrdinals}}. Value is straight, curved, shirt-tail, or
  unknown. Use a shape only when the hem is visible; a hidden or cropped hem is unknown.
- cuff: {{value, evidenceOrdinals}}. Describe only visible cuff height and opening ease
  relative to the photographed sleeve, or use none when a visible applicable sleeve has
  no cuff, or unknown. Never guess centimetres.
- button_count_visible: {{value, evidenceOrdinals}}. Value is the non-negative integer
  count actually visible. Use JSON null for unknown; the server stores it as unknown.
  Do not infer hidden buttons or closure function.
- pattern_structure: {{value, evidenceOrdinals}}. Concisely record visible paired lines,
  auxiliary lines, repeat spacing and figure/base-color relationships, or none when a
  clearly visible applicable surface is plain, or unknown.
- surface_texture: {{value, evidenceOrdinals}}. Record only visible weave, rib, pile or
  sheen evidence, or unknown. Never infer fiber composition or match a material library.
- seam_lines: {{value, evidenceOrdinals}}. Concisely record visible seam counts, relative
  starting landmarks and traced connections, or none when the applicable visible surface
  clearly has none, or unknown. Never bridge an occlusion.

Each fixed observation is independent. Use unknown rather than guessing an unseen region.
Except for unknown, evidenceOrdinals must be non-empty and include a usable panel that
directly supports the value. `none` is an evidenced absence, not a fallback for missing
pixels. Do not return visibleSurfacePlan or any other free surface-design prose. Front/back
routing policy is server-owned and is not a second source of garment design facts.

Hard and uncertain fact codes must be unique across both arrays. Ordinal arrays must be
non-empty, unique, ascending, and within the attached evidence order. This contract is
additional output from this same AG-01 call; do not weaken or replace the ordinary
product-analysis fields above.
""".rstrip()


def _ordinals(
    value: object, *, count: int, field: str, allow_empty: bool = False
) -> list[int]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(type(item) is not int for item in value)
        or value != sorted(set(value))
        or (value and value[0] < 1)
        or (value and value[-1] > count)
    ):
        raise ProductEvidenceContractError(f"product_evidence_invalid_ordinals:{field}")
    return list(value)


def _validate_facts(
    raw_facts: object,
    *,
    uncertain: bool,
    panel_status: dict[int, str],
    codes: set[str],
    max_length: int = 400,
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
            "value": _line(row["value"], f"{label}_{code}", max_length=max_length),
            "evidenceOrdinals": ordinals,
        }
        if uncertain:
            item["reason"] = _line(
                row["reason"], f"uncertainty_reason_{code}", max_length=400
            )
        out.append(item)
    return out


def _validate_fixed_observation(
    field: str,
    raw_value: object,
    *,
    panel_status: dict[int, str],
) -> dict[str, Any]:
    row = _exact_keys(
        raw_value,
        {"value", "evidenceOrdinals"},
        f"product_evidence_{field}_field_set_mismatch",
    )
    value = row["value"]
    if field == "button_count_visible":
        if value is None:
            value = "unknown"
        if value != "unknown" and (type(value) is not int or value < 0):
            raise ProductEvidenceContractError(
                "product_evidence_invalid_button_count_visible"
            )
    else:
        value = _line(value, field, max_length=240)
        if field == "hem_shape" and value not in {
            "straight", "curved", "shirt-tail", "unknown"
        }:
            raise ProductEvidenceContractError("product_evidence_invalid_hem_shape")
        if field in {"hem_shape", "surface_texture"} and value == "none":
            raise ProductEvidenceContractError(f"product_evidence_invalid_{field}")
        if field == "cuff" and re.search(r"\b\d+(?:\.\d+)?\s*cm\b", value, re.I):
            raise ProductEvidenceContractError("product_evidence_invalid_cuff_measurement")
    unknown = value == "unknown"
    ordinals = _ordinals(
        row["evidenceOrdinals"],
        count=len(panel_status),
        field=field,
        allow_empty=unknown,
    )
    if not unknown and not any(
        panel_status[ordinal] == "usable" for ordinal in ordinals
    ):
        raise ProductEvidenceContractError(
            "product_evidence_fixed_observation_requires_usable_panel"
        )
    return {"value": value, "evidenceOrdinals": ordinals}


def _normalized_judgeability_reasons(raw: object, *, ordinal: int) -> list[str]:
    """Normalize a panel's judgeability reasons; reject only real contract breaks.

    The vision model sometimes writes ``clear_enough`` next to an actual limitation, or
    repeats a reason.  Neither is a different judgement — the panel already carries its
    own usable/uncertain verdict, and this list only says what limits it.  Rejecting the
    self-contradiction turned one sloppy panel into a failed analysis for the whole
    product (2026-09-16 production 502s), so resolve it instead: a contradiction reads
    toward "there is a limitation", never away from one, and duplicates collapse in
    place.  An unlisted or non-string reason still fails closed — that is vocabulary the
    server never defined, so we cannot know what the model meant by it.
    """

    if (
        not isinstance(raw, list)
        or not raw
        or any(
            not isinstance(reason, str) or reason not in _JUDGEABILITY_REASONS
            for reason in raw
        )
    ):
        raise ProductEvidenceContractError("product_evidence_invalid_judgeability_reasons")
    reasons = list(dict.fromkeys(raw))
    if len(reasons) > 1:
        reasons = [reason for reason in reasons if reason != "clear_enough"]
    if reasons != raw:
        log.info(
            "judgeability_reasons_normalized ordinal=%d from=%s to=%s",
            ordinal, raw, reasons,
        )
    return reasons


def _validate_and_bind_version(
    raw_value: object,
    binding_value: object,
    *,
    observations_version: int | None,
) -> dict[str, Any]:
    binding = _validated_binding(binding_value)
    new_contract = observations_version == OBSERVATIONS_VERSION
    raw_fields = {"panels", "hardFacts", "uncertainties"}
    raw_fields.update(FIXED_OBSERVATION_FIELDS if new_contract else {"visibleSurfacePlan"})
    raw = _exact_keys(
        raw_value,
        raw_fields,
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
        if status not in _JUDGEABILITY:
            raise ProductEvidenceContractError("product_evidence_invalid_judgeability")
        reasons = _normalized_judgeability_reasons(
            panel["judgeabilityReasons"], ordinal=ordinal
        )
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
        raw["hardFacts"], uncertain=False, panel_status=panel_status, codes=codes,
        max_length=240 if new_contract else 400,
    )
    if new_contract and codes.intersection(FIXED_OBSERVATION_FIELDS):
        raise ProductEvidenceContractError("product_evidence_duplicate_fixed_observation")
    uncertainties = _validate_facts(
        raw["uncertainties"], uncertain=True, panel_status=panel_status, codes=codes,
        max_length=240 if new_contract else 400,
    )
    fixed = {}
    if new_contract:
        fixed = {
            field: _validate_fixed_observation(
                field, raw[field], panel_status=panel_status
            )
            for field in FIXED_OBSERVATION_FIELDS
        }
        surface_plan = _FRONT_SURFACE_POLICY
    else:
        raw_surface_plan = raw["visibleSurfacePlan"]
        already_normalized = (
            isinstance(raw_surface_plan, str)
            and raw_surface_plan.startswith(_FRONT_SURFACE_POLICY)
        )
        model_surface_plan = _line(
            raw_surface_plan,
            "visible_surface_plan",
            max_length=1000 if already_normalized else 800,
        )
        if _BACK_DOMINANT_RE.search(model_surface_plan):
            raise ProductEvidenceContractError("product_evidence_front_surface_plan_required")
        surface_plan = (
            model_surface_plan
            if already_normalized
            else f"{_FRONT_SURFACE_POLICY} Observed visible-surface details: {model_surface_plan}"
        )
        _line(surface_plan, "visible_surface_plan", max_length=1000)

    contract: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "direction": "front",
        "inputBinding": binding,
        "panels": panels,
        "hardFacts": hard_facts,
        "uncertainties": uncertainties,
        "visibleSurfacePlan": surface_plan,
    }
    if new_contract:
        contract["observationsVersion"] = OBSERVATIONS_VERSION
        contract.update(fixed)
    contract["contractSha256"] = sha256(_canonical_bytes(contract)).hexdigest()
    return contract


def validate_and_bind(raw_value: object, binding_value: object) -> dict[str, Any]:
    """Validate fresh AG-01 output and attach only server-trusted binding/routing fields."""

    return _validate_and_bind_version(
        raw_value, binding_value, observations_version=OBSERVATIONS_VERSION
    )


def validate_persisted(value: object) -> dict[str, Any]:
    """Verify an analysis-payload contract before any generation adapter consumes it."""

    legacy_fields = {
        "schemaVersion", "direction", "inputBinding", "panels", "hardFacts",
        "uncertainties", "visibleSurfacePlan", "contractSha256",
    }
    new_fields = legacy_fields | {"observationsVersion", *FIXED_OBSERVATION_FIELDS}
    valid_field_sets = {frozenset(legacy_fields), frozenset(new_fields)}
    if not isinstance(value, dict) or set(value) not in valid_field_sets:
        raise ProductEvidenceContractError(
            "product_evidence_persisted_field_set_mismatch"
        )
    stored = value
    new_contract = "observationsVersion" in stored
    if stored["schemaVersion"] != SCHEMA_VERSION or stored["direction"] != "front":
        raise ProductEvidenceContractError("product_evidence_persisted_version_mismatch")
    if new_contract and stored["observationsVersion"] != OBSERVATIONS_VERSION:
        raise ProductEvidenceContractError("product_evidence_observations_version_mismatch")
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
    raw_contract = {
        "panels": raw_panels,
        "hardFacts": stored["hardFacts"],
        "uncertainties": stored["uncertainties"],
    }
    if new_contract:
        if stored["visibleSurfacePlan"] != _FRONT_SURFACE_POLICY:
            raise ProductEvidenceContractError(
                "product_evidence_front_surface_plan_required"
            )
        raw_contract.update(
            {field: stored[field] for field in FIXED_OBSERVATION_FIELDS}
        )
    else:
        raw_contract["visibleSurfacePlan"] = stored["visibleSurfacePlan"]
    rebuilt = _validate_and_bind_version(
        raw_contract,
        binding,
        observations_version=(OBSERVATIONS_VERSION if new_contract else None),
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
