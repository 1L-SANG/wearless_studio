"""Deterministic Frame Lock measurements and fixed decision policy."""

import cv2
import numpy as np

from . import edit_intent_qc
from . import qc as pillow_qc

MIN_MEASUREMENT_CONFIDENCE = edit_intent_qc.MIN_MEASURE_CONFIDENCE
MIN_VISION_CONFIDENCE = 0.65
CENTER_X_MAX = 0.12
CENTER_Y_MAX = 0.10
SUBJECT_HEIGHT_MAX = 0.15
BACKGROUND_DELTA_E_MAX = 12.0


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def measure(canonical_bytes: bytes, candidate_bytes: bytes) -> dict:
    canonical = _decode(canonical_bytes)
    candidate = _decode(candidate_bytes)
    if canonical is None or candidate is None:
        return {
            "confidence": 0.0, "delta": {}, "backgroundDeltaE": None,
            "outputCropReasons": ["decode_failed"],
        }
    metrics = edit_intent_qc.measure(canonical, candidate)
    crop = pillow_qc.evaluate_mannequin_qc(candidate_bytes)
    return {
        **metrics,
        "outputCropReasons": [reason for reason in crop.reasons if reason in {
            "decode_failed", "bad_aspect_ratio", "full_body_crop", "missing_lower_body",
        }],
        "outputCropMetrics": crop.metrics,
    }


def _deterministic_errors(metrics: dict) -> list[str]:
    if float(metrics.get("confidence") or 0.0) < MIN_MEASUREMENT_CONFIDENCE:
        return []
    delta = metrics.get("delta") or {}
    errors = []
    if abs(float(delta.get("centerX") or 0.0)) > CENTER_X_MAX:
        errors.append("subject_center_drift")
    if abs(float(delta.get("centerY") or 0.0)) > CENTER_Y_MAX:
        errors.append("subject_center_drift")
    if abs(float(delta.get("subjectHeight") or 0.0)) > SUBJECT_HEIGHT_MAX:
        errors.append("subject_scale_drift")
    if float(metrics.get("backgroundDeltaE") or 0.0) > BACKGROUND_DELTA_E_MAX:
        errors.append("background_mismatch")
    if any(reason in {"decode_failed", "bad_aspect_ratio", "full_body_crop"}
           for reason in (metrics.get("outputCropReasons") or ())):
        errors.append("severe_crop")
    return sorted(set(errors))


def decide(metrics: dict, vision: dict | None) -> dict:
    """Return pass/review/reject from fixed policy; Vision is observation only."""
    deterministic = _deterministic_errors(metrics)
    measurement_available = (
        float(metrics.get("confidence") or 0.0) >= MIN_MEASUREMENT_CONFIDENCE
        and bool(metrics.get("delta")))
    vision_available = isinstance(vision, dict)
    vision_trusted = vision_available and float(vision.get("confidence") or 0.0) >= \
        MIN_VISION_CONFIDENCE

    critical = []
    warnings = []
    instructions = []
    conflict = False

    if vision_trusted:
        canonical_view = vision.get("canonicalViewFamily")
        result_view = vision.get("resultViewFamily")
        if canonical_view not in (None, "unknown") and result_view not in (None, "unknown") \
                and canonical_view != result_view:
            critical.append("wrong_view_family")
            instructions.append(
                f"Keep the canonical {canonical_view} view; do not output {result_view}.")
        for field, code in (
            ("orientationMatches", "orientation_mismatch"),
            ("cameraYawMatches", "severe_yaw"),
            ("framingMatches", "framing_mismatch"),
            ("fullBodyVisible", "severe_crop"),
            ("backgroundMatches", "background_mismatch"),
        ):
            if vision.get(field) is False:
                critical.append(code)
        for field, code in (("lightingMatches", "lighting_mismatch"),
                            ("shadowMatches", "shadow_mismatch")):
            if vision.get(field) is False:
                warnings.append(code)

        # Deterministic and Vision share only composition/background observables. If they
        # disagree, neither automatically wins. View-family/yaw has no deterministic proxy.
        deterministic_shared = bool(set(deterministic) & {
            "subject_center_drift", "subject_scale_drift", "background_mismatch", "severe_crop",
        })
        vision_shared_clean = all(vision.get(field) is True for field in (
            "framingMatches", "fullBodyVisible", "backgroundMatches"))
        vision_shared_bad = any(vision.get(field) is False for field in (
            "framingMatches", "fullBodyVisible", "backgroundMatches"))
        if deterministic_shared and vision_shared_clean:
            conflict = True
        elif measurement_available and not deterministic_shared and vision_shared_bad:
            conflict = True

    critical.extend(deterministic)
    critical = sorted(set(critical))
    warnings = sorted(set(warnings))

    uncertain = [] if not vision_available else list(vision.get("uncertainFields") or ())
    if conflict:
        decision = "review"
        warnings.append("deterministic_vision_conflict")
    elif critical:
        decision = "reject"
    elif not measurement_available or not vision_available or not vision_trusted or uncertain:
        decision = "review"
        if not measurement_available:
            warnings.append("measurement_unavailable")
        if not vision_available:
            warnings.append("vision_unavailable")
        elif not vision_trusted:
            warnings.append("vision_low_confidence")
        if uncertain:
            warnings.append("vision_uncertain")
    elif warnings:
        decision = "review"
    else:
        decision = "pass"

    return {
        "decision": decision,
        "criticalErrors": critical,
        "warnings": sorted(set(warnings)),
        "regenerationInstructions": instructions,
        "checks": {
            "measurementAvailable": measurement_available,
            "visionAvailable": vision_available,
            "visionTrusted": bool(vision_trusted),
            "visionConflict": conflict,
            "visionUncertainFields": uncertain,
        },
        "metrics": metrics,
        "vision": vision,
    }
