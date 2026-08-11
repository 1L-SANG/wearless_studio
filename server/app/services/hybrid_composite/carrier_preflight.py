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

#: which check raised a reason. A reason CODE is not unique to a check — cape is
#: raised both by a Vision silhouette label and by the hem/shoulder ratio — so an
#: audit that sees only the code cannot tell a model judgement from a measurement.
ORIGIN_VISION_LABEL = "vision_label"
ORIGIN_GEOMETRY_METRIC = "geometry_metric"
ORIGIN_UNSPECIFIED = "unspecified"
REASON_ORIGINS = frozenset({ORIGIN_VISION_LABEL, ORIGIN_GEOMETRY_METRIC, ORIGIN_UNSPECIFIED})

#: the label sets each silhouette check matches against, named once so the event
#: payload and the check cannot drift apart
CAPE_LABELS = frozenset({"cape", "poncho", "tent"})
SLAB_LABELS = frozenset({"slab", "slab_torso", "rectangular_torso", "flat_panel"})

LOWER_CATEGORIES = frozenset({"bottom", "bottoms", "pants", "trousers", "jeans", "skirt", "shorts"})

MIN_GEOMETRY_CONFIDENCE = 0.62
MAX_CAPE_HEM_TO_SHOULDER = 1.35
MAX_SLAB_EDGE_RATIO = 0.08

# --- shoulder provenance guards -------------------------------------------
# Not gate thresholds: these decide whether the reported shoulder points can be
# BELIEVED, not whether a garment passes. Vision is asked for "shoulder seam"
# points and sometimes returns the collar corners instead; the resulting span is
# roughly the neck width, which silently shrinks the cape denominator and turns a
# tailored shirt into a cape. Measured on a real rejected carrier: shoulder span
# 0.1415 against a collar span of 0.131 — 1.08x, where a real shoulder line is
# far wider than the neck it sits beside.
#: below this multiple of the collar span the "shoulder" points are on the collar
SHOULDER_COLLAR_MIN_RATIO = 1.25
#: a sleeve counts as hanging down when its horizontal drift is small next to its
#: vertical drop — that is the canonical arms-down mannequin frame, and it is what
#: separates a sleeve from the flared drape of an actual cape
ARMS_DOWN_MAX_DX_OVER_DY = 0.5

SHOULDER_FROM_VISION_POINTS = "vision_shoulder_points"
SHOULDER_FROM_ARMHOLE = "armhole_span"
#: same 2.4 the slab check has always used, named so the check and the threshold
#: it reports are one definition rather than two that can drift
MIN_SLAB_TORSO_ASPECT = 2.4
MAX_HEM_Y_ABS_DIFF = 0.18
MAX_SLEEVE_REL_ERR = 0.40
MIN_FRAME_IOU = 0.45


@dataclass(frozen=True)
class CarrierPreflightReason:
    code: str
    detail: str = ""
    severity: str = REJECT
    metrics: dict = field(default_factory=dict)
    #: which check produced this reason. Two different checks can raise the same
    #: code — `carrier_silhouette_cape` comes from a Vision label OR from the
    #: hem/shoulder ratio — and a code alone cannot tell an auditor which fired.
    origin: str = ""
    matched_labels: tuple[str, ...] = ()
    thresholds: dict = field(default_factory=dict)
    comparison: str = ""

    def __post_init__(self):
        if self.code not in CARRIER_PREFLIGHT_REASONS:
            raise ValueError(f"unknown carrier preflight reason: {self.code!r}")
        if self.severity not in DECISIONS - {PASS}:
            raise ValueError(f"invalid carrier preflight severity: {self.severity!r}")
        if self.origin and self.origin not in REASON_ORIGINS:
            raise ValueError(f"unknown carrier preflight origin: {self.origin!r}")

    def summary(self) -> dict:
        out = {"code": self.code, "severity": self.severity}
        if self.detail:
            out["detail"] = self.detail
        if self.metrics:
            out["metrics"] = self.metrics
        if self.origin:
            out["origin"] = self.origin
        if self.matched_labels:
            out["matchedLabels"] = list(self.matched_labels)
        if self.thresholds:
            out["thresholds"] = self.thresholds
        if self.comparison:
            out["comparison"] = self.comparison
        return out

    def detail_record(self) -> dict:
        """The full, always-shaped record an audit reads — never a partial guess."""
        return {
            "code": self.code,
            "severity": self.severity,
            "origin": self.origin or ORIGIN_UNSPECIFIED,
            "detail": self.detail,
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
            "comparison": self.comparison,
            "matchedLabels": list(self.matched_labels),
        }


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

    def reason_details(self) -> list[dict]:
        """One fully-shaped record per reason, in the order they were raised.

        Two entries can share a code with different origins; that is the point.
        """
        return [r.detail_record() for r in self.reasons]

    def geometry_observed(self) -> dict:
        """The geometry the silhouette checks actually read, for the audit trail."""
        geo = self.metrics.get("geometry") or {}
        return {
            "hemToShoulderRatio": geo.get("hemToShoulderRatio"),
            "sideEdgeDelta": geo.get("sideEdgeDelta"),
            "shoulderWidth": geo.get("shoulderWidth"),
            "hemWidth": geo.get("hemWidth"),
            "confidence": geo.get("confidence"),
            # which reading the ratio was taken against — without this a corrected
            # measurement and a raw one are indistinguishable in the record
            "shoulderWidthSource": geo.get("shoulderWidthSource"),
            "shoulderWidthReported": geo.get("shoulderWidthReported"),
            "hemToShoulderRatioReported": geo.get("hemToShoulderRatioReported"),
            "collarSpan": geo.get("collarSpan"),
            "armholeSpan": geo.get("armholeSpan"),
        }


def silhouette_labels_observed(carrier: Mapping | None, vision: Mapping | None) -> list[str]:
    """The silhouette labels the cape/slab checks matched against.

    Reads the same fields `_silhouette_reasons` reads, so an event that reports
    "no cape label" cannot be reporting on a different set than the check used.
    """
    labels = _labels(
        (carrier or {}).get("silhouette"),
        (carrier or {}).get("artifact_defects"),
        (vision or {}).get("shirtSilhouette"),
        (vision or {}).get("silhouette"),
        (vision or {}).get("artifact_defects"),
        (vision or {}).get("garment_shape"),
    )
    return sorted(labels)


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

    geometry_metrics, geometry_reason = _geometry_metrics(lm, inv)
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


def _collar_span(inventory: Mapping | None) -> float | None:
    """Horizontal extent of the collar box, or None when it was not returned."""
    box = ((inventory or {}).get("component_boxes") or {}).get("collar_box")
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    xs = [p[0] for p in box
          if isinstance(p, (list, tuple)) and len(p) >= 2
          and isinstance(p[0], (int, float))]
    if len(xs) != 4:
        return None
    span = max(xs) - min(xs)
    return span if span > 0 else None


def _armhole_span(landmarks: Mapping) -> float | None:
    """Outer sleeve-to-sleeve span, but only for sleeves that hang straight down.

    With arms down the sleeve outer line sits directly under the armhole, so this
    span tracks the real shoulder width. A cape has no such sleeve: its edge falls
    away from the body, which shows up as horizontal drift and disqualifies it
    here — so this can never widen the denominator for a genuinely caped garment.
    """
    left, right = _point(landmarks.get("sleeve_l_end")), _point(landmarks.get("sleeve_r_end"))
    if not (left and right):
        return None
    for side, end in (("l", left), ("r", right)):
        shoulder = _point(landmarks.get(f"shoulder_{side}"))
        if not shoulder:
            return None
        drop = end[1] - shoulder[1]
        if drop <= 0 or abs(end[0] - shoulder[0]) > drop * ARMS_DOWN_MAX_DX_OVER_DY:
            return None
    span = right[0] - left[0]
    return span if span > 0 else None


def _geometry_metrics(
    landmarks: Mapping, inventory: Mapping | None = None,
) -> tuple[dict, CarrierPreflightReason | None]:
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

    # The cape ratio divides by the shoulder span, so a shoulder span measured on
    # the collar makes a tailored shirt look like a cape. Correct the DENOMINATOR's
    # provenance, never the limit: only when the reported span is no wider than the
    # collar (physically impossible for a real shoulder line) and arms-down sleeves
    # give a true outer span, swap in the armhole span. Both readings are reported.
    reported_shoulder_width = shoulder_width
    collar_span = _collar_span(inventory)
    armhole_span = _armhole_span(landmarks)
    shoulder_source = SHOULDER_FROM_VISION_POINTS
    if (collar_span is not None and armhole_span is not None
            and reported_shoulder_width <= collar_span * SHOULDER_COLLAR_MIN_RATIO
            and armhole_span > reported_shoulder_width):
        shoulder_width = armhole_span
        shoulder_source = SHOULDER_FROM_ARMHOLE

    # measured from the reported points either way — the slab check owns these and
    # is not part of this fix
    edge_delta = abs((hl[0] - sl[0]) - (sr[0] - hr[0]))
    metrics = {
        "shoulderWidth": round(shoulder_width, 4),
        "hemWidth": round(hem_width, 4),
        "hemToShoulderRatio": round(hem_width / shoulder_width, 4),
        "torsoHeight": round(torso_height, 4),
        "hemY": round((hl[1] + hr[1]) / 2.0, 4),
        "sideEdgeDelta": round(edge_delta, 4),
        "shoulderWidthSource": shoulder_source,
        "shoulderWidthReported": round(reported_shoulder_width, 4),
        "hemToShoulderRatioReported": round(hem_width / reported_shoulder_width, 4),
    }
    if collar_span is not None:
        metrics["collarSpan"] = round(collar_span, 4)
    if armhole_span is not None:
        metrics["armholeSpan"] = round(armhole_span, 4)
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
    cape_labels = CAPE_LABELS & labels
    if cape_labels:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_cape",
            "carrier silhouette is marked as cape-like",
            origin=ORIGIN_VISION_LABEL,
            matched_labels=tuple(sorted(cape_labels)),
        ))
    slab_labels = SLAB_LABELS & labels
    if slab_labels:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_slab_torso",
            "carrier torso is marked as slab-like",
            origin=ORIGIN_VISION_LABEL,
            matched_labels=tuple(sorted(slab_labels)),
        ))

    # the same codes can also be raised by measurement below. Both are appended,
    # so a carrier flagged by the model AND by the ratio keeps two separate records
    hem_ratio = geometry.get("hemToShoulderRatio")
    if isinstance(hem_ratio, (int, float)) and hem_ratio > MAX_CAPE_HEM_TO_SHOULDER:
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_cape",
            f"hem/shoulder ratio {hem_ratio:.2f} > {MAX_CAPE_HEM_TO_SHOULDER}",
            metrics={"hemToShoulderRatio": hem_ratio},
            origin=ORIGIN_GEOMETRY_METRIC,
            thresholds={"MAX_CAPE_HEM_TO_SHOULDER": MAX_CAPE_HEM_TO_SHOULDER},
            comparison=">",
        ))
    side_delta = geometry.get("sideEdgeDelta")
    torso_aspect = inventory.get("torso_aspect")
    if (isinstance(side_delta, (int, float)) and side_delta <= MAX_SLAB_EDGE_RATIO
            and isinstance(torso_aspect, (int, float))
            and float(torso_aspect) > MIN_SLAB_TORSO_ASPECT):
        reasons.append(CarrierPreflightReason(
            "carrier_silhouette_slab_torso",
            "near-parallel torso edges with excessive torso aspect",
            metrics={"sideEdgeDelta": side_delta, "torsoAspect": round(float(torso_aspect), 3)},
            origin=ORIGIN_GEOMETRY_METRIC,
            thresholds={"MAX_SLAB_EDGE_RATIO": MAX_SLAB_EDGE_RATIO,
                        "MIN_SLAB_TORSO_ASPECT": MIN_SLAB_TORSO_ASPECT},
            comparison="sideEdgeDelta <= MAX_SLAB_EDGE_RATIO and torsoAspect > MIN_SLAB_TORSO_ASPECT",
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


# --------------------------------------------------------------------------
# Carrier shape-fidelity observation (observation only — changes no decision)
#
# `build_panel_map` compares source and carrier torso proportions through two
# different measurements, and only one of them is ever evaluated:
#
#   * when both sides carry a numeric `torso_aspect_mask`, the mask branch runs
#     a max/min collapse-hygiene ratio and returns early;
#   * when both are present but None, the worker has already decided the stripe
#     repeat invariant covers identity, and the comparison is skipped;
#   * only when the mask keys are absent entirely does the vision/construction
#     relative-error check against the construction tolerance run at all.
#
# So a carrier whose construction relative error is far past tolerance passes
# silently as long as the mask ratio stays under the collapse guard.  This
# helper computes BOTH readings and reports whether they disagree.  It decides
# nothing: every caller keeps its existing accept/reject path unchanged.
# --------------------------------------------------------------------------

SHAPE_OBSERVATION_VERSION = "carrier_shape_fidelity_observation_v1"

BRANCH_MASK = "mask"
BRANCH_CONSTRUCTION = "construction"
BRANCH_REPEAT_INVARIANT_SKIP = "repeat_invariant_skip"
BRANCH_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CarrierShapeFidelityObservation:
    """Both shape readings side by side, plus whether they disagree.

    Every field is optional: a missing input yields ``None`` and a reason, never
    a guess. ``observation_only`` is always ``True`` — nothing here feeds an
    acceptance decision.
    """

    source_torso_aspect_mask: float | None = None
    carrier_torso_aspect_mask: float | None = None
    mask_aspect_ratio: float | None = None
    mask_aspect_ratio_carrier_over_source: float | None = None

    source_torso_aspect_construction: float | None = None
    carrier_torso_aspect_construction: float | None = None
    construction_relative_error: float | None = None

    source_sleeve_length_ratio: float | None = None
    carrier_sleeve_length_ratio: float | None = None
    sleeve_length_relative_error: float | None = None

    mask_hygiene_gate_would_pass: bool | None = None
    construction_gate_would_pass: bool | None = None
    branch_disagreement: bool | None = None

    active_production_branch: str = BRANCH_UNAVAILABLE
    observation_only: bool = True
    missing_reasons: tuple[str, ...] = ()
    version: str = SHAPE_OBSERVATION_VERSION

    def to_metadata(self) -> dict:
        """camelCase dict for the existing hybrid metadata JSON. Always finite."""
        return {
            "observationOnly": True,
            "version": self.version,
            "activeProductionBranch": self.active_production_branch,
            "sourceTorsoAspectMask": self.source_torso_aspect_mask,
            "carrierTorsoAspectMask": self.carrier_torso_aspect_mask,
            "maskAspectRatio": self.mask_aspect_ratio,
            "maskAspectRatioCarrierOverSource": self.mask_aspect_ratio_carrier_over_source,
            "maskHygieneGateWouldPass": self.mask_hygiene_gate_would_pass,
            "sourceTorsoAspectConstruction": self.source_torso_aspect_construction,
            "carrierTorsoAspectConstruction": self.carrier_torso_aspect_construction,
            "constructionRelativeError": self.construction_relative_error,
            "constructionGateWouldPass": self.construction_gate_would_pass,
            "branchDisagreement": self.branch_disagreement,
            "sourceSleeveLengthRatio": self.source_sleeve_length_ratio,
            "carrierSleeveLengthRatio": self.carrier_sleeve_length_ratio,
            "sleeveLengthRelativeError": self.sleeve_length_relative_error,
            "missingReasons": list(self.missing_reasons),
        }


CARRIER_OBSERVATION_LINEAGE_VERSION = "carrier_observation_lineage_v1"

#: stable reason strings — never invent an id or a hash to fill a gap
REASON_CARRIER_SHA_UNAVAILABLE = "carrier_sha_unavailable"
REASON_SOURCE_SHA_UNAVAILABLE = "source_sha_unavailable"
REASON_JOB_ID_UNAVAILABLE = "job_id_unavailable"
REASON_GENERATION_RUN_ID_UNAVAILABLE = "generation_run_id_unavailable"
REASON_CANDIDATE_ID_UNAVAILABLE = "candidate_id_unavailable"
REASON_SOURCE_ASSET_ID_UNAVAILABLE = "source_asset_id_unavailable"
REASON_PRODUCT_TRUTH_ID_UNAVAILABLE = "source_product_truth_id_unavailable"

#: one reason per representation field. A single populated field used to suppress
#: the reason for all the others, which read as "representation captured" when
#: four fifths of it was missing.
REPRESENTATION_REASONS = {
    "garment_category": "garment_category_unavailable",
    "garment_lane": "garment_lane_unavailable",
    "frame_intent": "frame_intent_unavailable",
    "pattern_type": "pattern_type_unavailable",
    "source_type": "source_type_unavailable",
    "image_resolution": "image_resolution_unavailable",
    "generation_mode": "generation_mode_unavailable",
}

SHA_ALGORITHM = "sha256"

#: what the digest was actually taken over. The basis is a claim about the
#: bytes, so it has to match the call site that produced the digest:
#:   * ENCODED_MEMORY — we ran a hash ourselves over encoded bytes in memory
#:   * ENCODED_FILE   — we ran a hash ourselves over an encoded file on disk
#:   * IMMUTABLE_ASSET — reserved: the digest was READ from a store that had
#:     already recorded it. Hashing bytes we happen to have fetched from an
#:     asset is still ENCODED_MEMORY; an asset id identifies where the bytes
#:     came from, it is not itself a stored hash. No production caller may use
#:     this value until such a store exists.
SHA_BASIS_ENCODED_MEMORY = "encoded_memory_bytes"
SHA_BASIS_ENCODED_FILE = "encoded_file_bytes"
SHA_BASIS_IMMUTABLE_ASSET = "immutable_asset_hash"

#: where a value came from, so a later sweep never has to guess which parameter
#: a column was filled from
SHA_SOURCE_CARRIER_RESULT = "GeminiImageResult.image"
SHA_SOURCE_FRONT_ASSET_BYTES = "front_ref.image.data"
ID_SOURCE_WORKER_JOB_ID = "worker_parameter:job_id"
ID_SOURCE_WORKER_CANDIDATE = "worker_parameter:candidate"
ID_SOURCE_WORKER_ATTEMPT = "worker_parameter:attempt"


@dataclass(frozen=True)
class CarrierObservationLineage:
    """Identifiers that let a later sweep deduplicate carriers and join them back.

    Nothing here is computed: every value is one the worker already holds. A hash
    is never recomputed from decoded pixels, and a missing id is never invented.

    ``job_id`` and ``generation_run_id`` are deliberately separate fields. The
    worker is handed a job id; a generation run is a different concept that no
    runtime value currently expresses. Filling the run field from the job id
    would make two distinct identities look like one in every row a later sweep
    reads, so the run field stays null and says why.
    """

    carrier_sha256: str | None = None
    carrier_sha_basis: str | None = None
    carrier_sha_algorithm: str | None = None
    carrier_sha_source: str | None = None
    source_sha256: str | None = None
    source_sha_basis: str | None = None
    source_sha_algorithm: str | None = None
    source_sha_source: str | None = None
    source_path_or_id: str | None = None

    job_id: str | None = None
    job_id_source: str | None = None
    generation_run_id: str | None = None
    candidate_id: str | None = None
    candidate_id_source: str | None = None
    attempt_number: int | None = None
    attempt_number_source: str | None = None
    source_asset_id: str | None = None
    source_product_truth_id: str | None = None

    garment_category: str | None = None
    garment_lane: str | None = None
    frame_intent: str | None = None
    pattern_type: str | None = None
    source_type: str | None = None
    image_resolution: str | None = None
    generation_mode: str | None = None

    version: str = CARRIER_OBSERVATION_LINEAGE_VERSION

    def missing_reasons(self) -> tuple[str, ...]:
        out = []
        if not self.carrier_sha256:
            out.append(REASON_CARRIER_SHA_UNAVAILABLE)
        if not self.source_sha256:
            out.append(REASON_SOURCE_SHA_UNAVAILABLE)
        if not self.job_id:
            out.append(REASON_JOB_ID_UNAVAILABLE)
        if not self.generation_run_id:
            out.append(REASON_GENERATION_RUN_ID_UNAVAILABLE)
        if self.candidate_id in (None, ""):
            out.append(REASON_CANDIDATE_ID_UNAVAILABLE)
        if not self.source_asset_id:
            out.append(REASON_SOURCE_ASSET_ID_UNAVAILABLE)
        if not self.source_product_truth_id:
            out.append(REASON_PRODUCT_TRUTH_ID_UNAVAILABLE)
        for field, reason in REPRESENTATION_REASONS.items():
            if not getattr(self, field):
                out.append(reason)
        return tuple(out)

    def to_metadata(self) -> dict:
        return {
            "lineageVersion": self.version,
            "carrierSha256": self.carrier_sha256,
            "carrierShaAlgorithm": self.carrier_sha_algorithm,
            "carrierShaBasis": self.carrier_sha_basis,
            "carrierShaSource": self.carrier_sha_source,
            "sourceSha256": self.source_sha256,
            "sourceShaAlgorithm": self.source_sha_algorithm,
            "sourceShaBasis": self.source_sha_basis,
            "sourceShaSource": self.source_sha_source,
            "sourcePathOrId": self.source_path_or_id,
            "jobId": self.job_id,
            "jobIdSource": self.job_id_source,
            "generationRunId": self.generation_run_id,
            "candidateId": self.candidate_id,
            "candidateIdSource": self.candidate_id_source,
            "attemptNumber": self.attempt_number,
            "attemptNumberSource": self.attempt_number_source,
            "sourceAssetId": self.source_asset_id,
            "sourceProductTruthId": self.source_product_truth_id,
            "garmentCategory": self.garment_category,
            "garmentLane": self.garment_lane,
            "frameIntent": self.frame_intent,
            "patternType": self.pattern_type,
            "sourceType": self.source_type,
            "imageResolution": self.image_resolution,
            "generationMode": self.generation_mode,
            "lineageMissingReasons": list(self.missing_reasons()),
        }


def build_carrier_shape_metadata(
    observation: "CarrierShapeFidelityObservation | None",
    lineage: CarrierObservationLineage | None = None,
) -> dict:
    """Merge the v19 observation with its lineage into one metadata block.

    The v19 field names are carried through untouched; lineage keys are added
    alongside them. Either half may be absent — the other still records.
    """
    out: dict = {}
    if observation is not None:
        out.update(observation.to_metadata())
        out["metricMissingReasons"] = list(observation.missing_reasons)
    else:
        out.update({"observationOnly": True, "version": SHAPE_OBSERVATION_VERSION,
                    "metricMissingReasons": ["observation_unavailable"]})
    if lineage is not None:
        out.update(lineage.to_metadata())
    else:
        out["lineageMissingReasons"] = list(CarrierObservationLineage().missing_reasons())
    return out


# --------------------------------------------------------------------------
# Where this metadata lives.
#
# Only the location contract belongs here. Whether the code is deployed and
# whether any production record exists yet are facts about a moment in time,
# not about this module: a constant asserting them would keep asserting them
# long after they stopped being true. Those live in the diagnostic artifacts.
# --------------------------------------------------------------------------

#: The authoritative place to read carrier lineage from. `samples.jsonl` is
#: written by `scripts/shadow_collect.py`, a diagnostic collector outside the
#: production path — it may lag, be absent, or predate this metadata entirely,
#: so a sweep that reads it is sampling, not enumerating.
DATASET_DISCOVERY_CONTRACT = {
    "primary": "qc_scores.hybridComposite.carrierShapeFidelity",
    "secondary": "cut metadata hybridComposite.carrierShapeFidelity",
    "diagnosticSamplesJsonlIsAuthoritative": False,
}


def _finite(value) -> float | None:
    """Accept only a finite real number; NaN, Inf, bools and junk become None."""
    # a numeric string is a type smell in these inventories, not a value to coerce
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def observe_carrier_shape_fidelity(
    *,
    source_inventory: Mapping | None,
    carrier_inventory: Mapping | None,
    mask_hygiene_ratio_threshold: float,
    construction_ratio_tolerance: float,
    source_torso_aspect_mask_measured=None,
    carrier_torso_aspect_mask_measured=None,
) -> CarrierShapeFidelityObservation:
    """Record both shape readings without touching any decision.

    The two thresholds are passed in by the caller on purpose: this helper must
    never hold a second copy of a production constant that could drift from the
    original. ``*_measured`` carry the raw aspect values the worker computed
    before it may have blanked them for the repeat-invariant path, so the
    observation survives that blanking.
    """
    src = source_inventory or {}
    car = carrier_inventory or {}
    missing: list[str] = []

    mask_keys_present = ("torso_aspect_mask" in src and "torso_aspect_mask" in car)
    s_mask = _finite(src.get("torso_aspect_mask"))
    c_mask = _finite(car.get("torso_aspect_mask"))
    if s_mask is None:
        s_mask = _finite(source_torso_aspect_mask_measured)
    if c_mask is None:
        c_mask = _finite(carrier_torso_aspect_mask_measured)

    mask_ratio = mask_ratio_directional = None
    if s_mask is None or c_mask is None:
        missing.append("torso_aspect_mask_unavailable")
    elif min(abs(s_mask), abs(c_mask)) <= 0:
        missing.append("torso_aspect_mask_non_positive")
    else:
        # identical expression to the production hygiene branch (max/min)
        mask_ratio = max(s_mask, c_mask) / max(min(s_mask, c_mask), 1e-6)
        mask_ratio_directional = c_mask / s_mask

    s_con = _finite(src.get("torso_aspect"))
    c_con = _finite(car.get("torso_aspect"))
    con_rel = None
    if s_con is None or c_con is None:
        missing.append("torso_aspect_construction_unavailable")
    elif s_con <= 0:
        missing.append("torso_aspect_construction_non_positive")
    else:
        # identical expression to the production construction branch
        con_rel = abs(c_con - s_con) / s_con

    s_sleeve = _finite(src.get("sleeve_len_ratio"))
    c_sleeve = _finite(car.get("sleeve_len_ratio"))
    sleeve_rel = None
    if s_sleeve is None or c_sleeve is None:
        missing.append("sleeve_len_ratio_unavailable")
    elif s_sleeve <= 0:
        missing.append("sleeve_len_ratio_non_positive")
    else:
        sleeve_rel = abs(c_sleeve - s_sleeve) / s_sleeve

    mask_pass = None if mask_ratio is None else bool(mask_ratio <= mask_hygiene_ratio_threshold)
    # production rounds to 3 decimals before deciding; mirror that exactly
    con_pass = None if con_rel is None else bool(round(con_rel, 3) <= construction_ratio_tolerance)
    disagreement = None if (mask_pass is None or con_pass is None) else bool(mask_pass != con_pass)

    if mask_keys_present and src.get("torso_aspect_mask") is None and car.get("torso_aspect_mask") is None:
        branch = BRANCH_REPEAT_INVARIANT_SKIP
    elif _finite(src.get("torso_aspect_mask")) is not None and _finite(car.get("torso_aspect_mask")) is not None:
        branch = BRANCH_MASK
    elif con_rel is not None:
        branch = BRANCH_CONSTRUCTION
    else:
        branch = BRANCH_UNAVAILABLE

    return CarrierShapeFidelityObservation(
        source_torso_aspect_mask=s_mask, carrier_torso_aspect_mask=c_mask,
        mask_aspect_ratio=mask_ratio, mask_aspect_ratio_carrier_over_source=mask_ratio_directional,
        source_torso_aspect_construction=s_con, carrier_torso_aspect_construction=c_con,
        construction_relative_error=con_rel,
        source_sleeve_length_ratio=s_sleeve, carrier_sleeve_length_ratio=c_sleeve,
        sleeve_length_relative_error=sleeve_rel,
        mask_hygiene_gate_would_pass=mask_pass, construction_gate_would_pass=con_pass,
        branch_disagreement=disagreement, active_production_branch=branch,
        observation_only=True, missing_reasons=tuple(missing),
    )
