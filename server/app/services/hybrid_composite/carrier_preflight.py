"""Carrier quality preflight before deterministic texture projection.

This module is intentionally pure: no provider calls, no worker state, and no
mutation.  It consumes already-collected evidence and returns a deterministic
policy decision so obviously bad carrier artifacts do not reach projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

PREFLIGHT_POLICY_VERSION = "hybrid_carrier_preflight_v1"

PASS = "PASS"
RETRY = "RETRY"
REJECT = "REJECT"
REVIEW = "REVIEW"

DECISIONS = frozenset({PASS, RETRY, REJECT, REVIEW})

CARRIER_PREFLIGHT_REASONS = frozenset({
    "carrier_silhouette_cape",
    "carrier_silhouette_slab_torso",
    "expected_lower_missing",
    "matching_garment_missing",
    "hem_mismatch_gross",
    "sleeve_mismatch_gross",
    "frame_mismatch_gross",
    "garment_category_mismatch",
    "geometry_unmeasurable",
    "inventory_unmeasurable",
    "vision_unmeasurable",
    "image_unreadable",
})

LOWER_CATEGORIES = frozenset({"bottom", "bottoms", "pants", "trousers", "jeans", "skirt", "shorts"})

MIN_GEOMETRY_CONFIDENCE = 0.62
MAX_CAPE_HEM_TO_SHOULDER = 1.35
MAX_SLAB_EDGE_RATIO = 0.08
MAX_HEM_Y_ABS_DIFF = 0.18
MAX_SLEEVE_REL_ERR = 0.40
MIN_FRAME_IOU = 0.45


@dataclass(frozen=True)
class CarrierPreflightReason:
    code: str
    detail: str = ""
    severity: str = REJECT
    metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.code not in CARRIER_PREFLIGHT_REASONS:
            raise ValueError(f"unknown carrier preflight reason: {self.code!r}")
        if self.severity not in DECISIONS - {PASS}:
            raise ValueError(f"invalid carrier preflight severity: {self.severity!r}")

    def summary(self) -> dict:
        out = {"code": self.code, "severity": self.severity}
        if self.detail:
            out["detail"] = self.detail
        if self.metrics:
            out["metrics"] = self.metrics
        return out


@dataclass(frozen=True)
class CarrierPreflightResult:
    decision: str
    reasons: tuple[CarrierPreflightReason, ...] = ()
    metrics: dict = field(default_factory=dict)
    policy_version: str = PREFLIGHT_POLICY_VERSION

    def __post_init__(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"invalid carrier preflight decision: {self.decision!r}")

    @property
    def passed(self) -> bool:
        return self.decision == PASS

    def summary(self) -> dict:
        return {
            "decision": self.decision,
            "reasons": [r.summary() for r in self.reasons],
            "metrics": self.metrics,
            "policyVersion": self.policy_version,
        }


def preflight_carrier_quality(
    *,
    carrier_evidence: Mapping | None = None,
    canonical_evidence: Mapping | None = None,
    matching_evidence: Mapping | None = None,
    landmarks: Mapping | None = None,
    carrier_inventory: Mapping | None = None,
    canonical_inventory: Mapping | None = None,
    vision_observations: Mapping | None = None,
    require_vision: bool = False,
    matching_expected: bool = False,
) -> CarrierPreflightResult:
    """Return PASS/RETRY/REJECT/REVIEW before projection.

    Inputs are normalized dictionaries from earlier deterministic or provider
    stages.  Vision observations are optional and treated as evidence only when
    already normalized by the caller; this function never calls a provider.
    """

    carrier = dict(carrier_evidence or {})
    canonical = dict(canonical_evidence or {})
    matching = dict(matching_evidence or {})
    lm = dict(landmarks or {})
    inv = dict(carrier_inventory or {})
    canon_inv = dict(canonical_inventory or {})
    vision = dict(vision_observations or {})

    reasons: list[CarrierPreflightReason] = []
    metrics: dict = {"policyInputs": _input_presence(carrier, canonical, matching, lm, inv, canon_inv, vision)}

    image_metrics, image_reason = _image_metrics(carrier)
    if image_metrics:
        metrics["image"] = image_metrics
    if image_reason:
        reasons.append(image_reason)

    geometry_metrics, geometry_reason = _geometry_metrics(lm)
    metrics["geometry"] = geometry_metrics
    if geometry_reason:
        reasons.append(geometry_reason)

    inventory_reason = _inventory_reason(inv)
    if inventory_reason:
        reasons.append(inventory_reason)

    vision_reason = _vision_reason(vision, required=require_vision)
    if vision_reason:
        reasons.append(vision_reason)

    reasons.extend(_silhouette_reasons(carrier, vision, geometry_metrics, inv))
    reasons.extend(_vision_failure_reasons(vision, matching_expected=matching_expected))
    reasons.extend(_missing_garment_reasons(carrier, canonical, matching, inv, canon_inv, vision))
    reasons.extend(_mismatch_reasons(canonical, matching, geometry_metrics, inv, canon_inv))

    if reasons:
        return CarrierPreflightResult(
            decision=_decision_for(reasons),
            reasons=tuple(_dedupe_reasons(reasons)),
            metrics=metrics,
        )
    return CarrierPreflightResult(decision=PASS, metrics=metrics)


def _input_presence(*maps: Mapping) -> dict:
    names = (
        "carrierEvidence",
        "canonicalEvidence",
        "matchingEvidence",
        "landmarks",
        "carrierInventory",
        "canonicalInventory",
        "visionObservations",
    )
    return {name: bool(value) for name, value in zip(names, maps)}


def _image_metrics(carrier: Mapping) -> tuple[dict, CarrierPreflightReason | None]:
    path = carrier.get("carrier_image_path") or carrier.get("image_path")
    if not path:
        return {}, None
    try:
        from PIL import Image

        with Image.open(Path(path)) as img:
            width, height = img.size
    except Exception as exc:  # pragma: no cover - exact decoder errors vary
        return {}, CarrierPreflightReason(
            "image_unreadable",
            "carrier image could not be decoded",
            severity=RETRY,
            metrics={"error": type(exc).__name__},
        )
    aspect_hw = round(height / max(width, 1), 4)
    return {"width": width, "height": height, "aspectHW": aspect_hw}, None


def _geometry_metrics(landmarks: Mapping) -> tuple[dict, CarrierPreflightReason | None]:
    required = ("shoulder_l", "shoulder_r", "hem_l", "hem_r")
    missing = [name for name in required if _point(landmarks.get(name)) is None]
    if missing:
        return {"missing": missing}, CarrierPreflightReason(
            "geometry_unmeasurable",
            "required carrier landmarks are missing",
            severity=RETRY,
            metrics={"missing": missing},
        )

    sl, sr, hl, hr = (_point(landmarks[name]) for name in required)
    assert sl and sr and hl and hr
    shoulder_width = sr[0] - sl[0]
    hem_width = hr[0] - hl[0]
    torso_height = ((hl[1] + hr[1]) - (sl[1] + sr[1])) / 2.0
    if shoulder_width <= 0.02 or hem_width <= 0.02 or torso_height <= 0.02:
        return {
            "shoulderWidth": round(shoulder_width, 4),
            "hemWidth": round(hem_width, 4),
            "torsoHeight": round(torso_height, 4),
        }, CarrierPreflightReason(
            "geometry_unmeasurable",
            "carrier torso geometry is degenerate",
            severity=RETRY,
        )

    edge_delta = abs((hl[0] - sl[0]) - (sr[0] - hr[0]))
    metrics = {
        "shoulderWidth": round(shoulder_width, 4),
        "hemWidth": round(hem_width, 4),
        "hemToShoulderRatio": round(hem_width / shoulder_width, 4),
        "torsoHeight": round(torso_height, 4),
        "hemY": round((hl[1] + hr[1]) / 2.0, 4),
        "sideEdgeDelta": round(edge_delta, 4),
    }
    conf = landmarks.get("confidence")
    if isinstance(conf, (int, float)):
        metrics["confidence"] = round(float(conf), 3)
        if float(conf) < MIN_GEOMETRY_CONFIDENCE:
            return metrics, CarrierPreflightReason(
                "geometry_unmeasurable",
                f"carrier geometry confidence {float(conf):.2f} < {MIN_GEOMETRY_CONFIDENCE}",
                severity=RETRY,
                metrics={"confidence": round(float(conf), 3)},
            )
    return metrics, None


def _inventory_reason(inventory: Mapping) -> CarrierPreflightReason | None:
    if not inventory:
        return CarrierPreflightReason(
            "inventory_unmeasurable",
            "carrier construction inventory is missing",
            severity=RETRY,
        )
    measurable = any(key in inventory for key in (
        "collar",
        "placket",
        "cuffs",
        "visible_buttons",
        "torso_aspect",
        "sleeve_len_ratio",
        "garment_categories",
    ))
    if not measurable:
        return CarrierPreflightReason(
            "inventory_unmeasurable",
            "carrier construction inventory has no measurable fields",
            severity=RETRY,
        )
    return None


def _vision_reason(vision: Mapping, *, required: bool = False) -> CarrierPreflightReason | None:
    if not vision:
        return (CarrierPreflightReason(
            "vision_unmeasurable",
            "carrier preflight vision observation is required but unavailable",
            severity=REVIEW,
        ) if required else None)
    status = str(vision.get("status") or vision.get("measurement_status") or "").strip().lower()
    if status in {"unmeasurable", "unknown", "low_confidence"}:
        return CarrierPreflightReason(
            "vision_unmeasurable",
            "normalized vision observations are present but not measurable",
            severity=REVIEW,
            metrics={"status": status},
        )
    confidence = vision.get("confidence")
    if isinstance(confidence, (int, float)) and float(confidence) < MIN_GEOMETRY_CONFIDENCE:
        return CarrierPreflightReason(
            "vision_unmeasurable",
            f"normalized vision confidence {float(confidence):.2f} < {MIN_GEOMETRY_CONFIDENCE}",
            severity=REVIEW,
            metrics={"confidence": round(float(confidence), 3)},
        )
    return None


def _vision_failure_reasons(
    vision: Mapping, *, matching_expected: bool = False,
) -> list[CarrierPreflightReason]:
    """Map normalized observations to typed policy failures; Vision never decides."""
    if not vision:
        return []
    reasons: list[CarrierPreflightReason] = []
    mapping = {
        "hemPlausible": ("hem_mismatch_gross", "carrier shirt hem is implausible"),
        "sleevesPlausible": ("sleeve_mismatch_gross", "carrier sleeves/cuffs are implausible"),
        "lowerBodyPresent": ("expected_lower_missing", "full-body lower region is missing"),
        "mannequinFramePreserved": ("frame_mismatch_gross", "canonical mannequin frame is not preserved"),
        "garmentCategoryMatches": ("garment_category_mismatch", "carrier no longer reads as the source shirt"),
    }
    for field, (code, detail) in mapping.items():
        if vision.get(field) is False:
            reasons.append(CarrierPreflightReason(code, detail))
    if matching_expected and vision.get("matchingGarmentPresent") is False:
        reasons.append(CarrierPreflightReason(
            "matching_garment_missing", "required matching garment is missing"))
    uncertain = list(vision.get("uncertainFields") or [])
    if not matching_expected:
        uncertain = [field for field in uncertain if field != "matchingGarmentPresent"]
    if isinstance(uncertain, (list, tuple)) and uncertain:
        reasons.append(CarrierPreflightReason(
            "vision_unmeasurable",
            "carrier preflight contains uncertain observations",
            severity=REVIEW,
            metrics={"uncertainFields": sorted(str(value) for value in uncertain)[:8]},
        ))
    return reasons


def _silhouette_reasons(
    carrier: Mapping,
    vision: Mapping,
    geometry: Mapping,
    inventory: Mapping,
) -> list[CarrierPreflightReason]:
    reasons: list[CarrierPreflightReason] = []
    labels = _labels(
        carrier.get("silhouette"),
        carrier.get("artifact_defects"),
        vision.get("shirtSilhouette"),
        vision.get("silhouette"),
        vision.get("artifact_defects"),
        vision.get("garment_shape"),
    )
    if {"cape", "poncho", "tent"} & labels:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_cape",
            "carrier silhouette is marked as cape-like",
        ))
    if {"slab", "slab_torso", "rectangular_torso", "flat_panel"} & labels:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_slab_torso",
            "carrier torso is marked as slab-like",
        ))

    hem_ratio = geometry.get("hemToShoulderRatio")
    if isinstance(hem_ratio, (int, float)) and hem_ratio > MAX_CAPE_HEM_TO_SHOULDER:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_cape",
            f"hem/shoulder ratio {hem_ratio:.2f} > {MAX_CAPE_HEM_TO_SHOULDER}",
            metrics={"hemToShoulderRatio": hem_ratio},
        ))
    side_delta = geometry.get("sideEdgeDelta")
    torso_aspect = inventory.get("torso_aspect")
    if (isinstance(side_delta, (int, float)) and side_delta <= MAX_SLAB_EDGE_RATIO
            and isinstance(torso_aspect, (int, float)) and float(torso_aspect) > 2.4):
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_slab_torso",
            "near-parallel torso edges with excessive torso aspect",
            metrics={"sideEdgeDelta": side_delta, "torsoAspect": round(float(torso_aspect), 3)},
        ))
    return reasons


def _missing_garment_reasons(
    carrier: Mapping,
    canonical: Mapping,
    matching: Mapping,
    inventory: Mapping,
    canonical_inventory: Mapping,
    vision: Mapping,
) -> list[CarrierPreflightReason]:
    reasons: list[CarrierPreflightReason] = []
    expected = _category_set(
        canonical.get("expected_categories"),
        canonical.get("garment_categories"),
        canonical_inventory.get("garment_categories"),
    )
    observed = _category_set(
        carrier.get("garment_categories"),
        matching.get("garment_categories"),
        inventory.get("garment_categories"),
        vision.get("garment_categories"),
    )
    expected_lower = bool(canonical.get("expected_lower") or canonical_inventory.get("expected_lower"))
    if (expected_lower or expected & LOWER_CATEGORIES) and not (observed & LOWER_CATEGORIES):
        reasons.append(CarrierPreflightReason(
            "expected_lower_missing",
            "canonical expects a lower garment but carrier evidence does not contain one",
            metrics={"expected": sorted(expected), "observed": sorted(observed)},
        ))

    matched = matching.get("matched")
    score = matching.get("score")
    matched_categories = _category_set(matching.get("matched_categories"), matching.get("garment_categories"))
    if matched is False or matching.get("missing") is True:
        reasons.append(CarrierPreflightReason(
            "matching_garment_missing",
            "matching evidence says the required garment is absent",
        ))
    elif isinstance(score, (int, float)) and float(score) < 0.35 and not matched_categories:
        reasons.append(CarrierPreflightReason(
            "matching_garment_missing",
            "matching score is too low and no matched garment category is present",
            metrics={"score": round(float(score), 3)},
        ))
    return reasons


def _mismatch_reasons(
    canonical: Mapping,
    matching: Mapping,
    geometry: Mapping,
    inventory: Mapping,
    canonical_inventory: Mapping,
) -> list[CarrierPreflightReason]:
    reasons: list[CarrierPreflightReason] = []
    expected_hem_y = _number(canonical.get("hem_y"), canonical_inventory.get("hem_y"))
    actual_hem_y = _number(geometry.get("hemY"), inventory.get("hem_y"))
    if expected_hem_y is not None and actual_hem_y is not None:
        diff = abs(actual_hem_y - expected_hem_y)
        if diff > MAX_HEM_Y_ABS_DIFF:
            reasons.append(CarrierPreflightReason(
                "hem_mismatch_gross",
                f"hem y differs by {diff:.3f}",
                metrics={"expectedHemY": expected_hem_y, "carrierHemY": actual_hem_y, "absDiff": round(diff, 4)},
            ))

    expected_sleeve = _number(canonical.get("sleeve_len_ratio"), canonical_inventory.get("sleeve_len_ratio"))
    actual_sleeve = _number(inventory.get("sleeve_len_ratio"))
    if expected_sleeve and actual_sleeve is not None:
        rel = abs(actual_sleeve - expected_sleeve) / max(abs(expected_sleeve), 1e-6)
        if rel > MAX_SLEEVE_REL_ERR:
            reasons.append(CarrierPreflightReason(
                "sleeve_mismatch_gross",
                f"sleeve length relative error {rel:.3f} > {MAX_SLEEVE_REL_ERR}",
                metrics={
                    "expectedSleeveLenRatio": expected_sleeve,
                    "carrierSleeveLenRatio": actual_sleeve,
                    "relErr": round(rel, 4),
                },
            ))

    frame_iou = _number(matching.get("frame_iou"), matching.get("frameIoU"))
    if frame_iou is not None and frame_iou < MIN_FRAME_IOU:
        reasons.append(CarrierPreflightReason(
            "frame_mismatch_gross",
            f"frame IoU {frame_iou:.3f} < {MIN_FRAME_IOU}",
            metrics={"frameIoU": frame_iou},
        ))
    return reasons


def _decision_for(reasons: list[CarrierPreflightReason]) -> str:
    severities = {reason.severity for reason in reasons}
    if REJECT in severities:
        return REJECT
    if RETRY in severities:
        return RETRY
    return REVIEW


def _dedupe_reasons(reasons: list[CarrierPreflightReason]) -> list[CarrierPreflightReason]:
    out: list[CarrierPreflightReason] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason.code in seen:
            continue
        seen.add(reason.code)
        out.append(reason)
    return out


def _point(value) -> tuple[float, float] | None:
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(x, (int, float)) for x in value)
            and all(0.0 <= float(x) <= 1.0 for x in value)):
        return float(value[0]), float(value[1])
    return None


def _number(*values) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _labels(*values) -> set[str]:
    labels: set[str] = set()
    for value in values:
        if isinstance(value, str):
            labels.add(value.strip().lower())
        elif isinstance(value, Mapping):
            labels.update(str(k).strip().lower() for k, v in value.items() if bool(v))
        elif isinstance(value, (list, tuple, set, frozenset)):
            labels.update(str(v).strip().lower() for v in value)
    return {label for label in labels if label}


def _category_set(*values) -> set[str]:
    return _labels(*values)
