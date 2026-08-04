"""Level-2 2D texture projection planning for deterministic hybrid composite.

This module does not render pixels and does not call an AI provider.  It answers one
small question before warp/composite: "If the source garment shows N repeats across
the same garment span, what period should the carrier use, and is that projection
safe enough to apply?"

The intentionally narrow MVP scope is regular stripes.  Checks/plaids/gingham/
tartan need a different two-axis lattice model and are explicitly unsupported
until that renderer exists. Unsupported or low-confidence cases return a
fail-closed plan so the caller can keep the legacy path in shadow mode, or reject
the deterministic composite in enforce mode.

texture_project을 굳이 MVP라는 이유로 뺼 이유가 있을까?
"""

from __future__ import annotations

from dataclasses import dataclass

PROJECTION_VERSION = "texture_projection_2d_v1"

SUPPORTED_PATTERN_TYPES = frozenset({"stripe", "stripes"})
MIN_SOURCE_REPEATS = 4.0
MIN_TARGET_PERIOD_PX = 6.0
MAX_SCALE_CHANGE = 3.0
MIN_CONFIDENCE = 0.62


@dataclass(frozen=True)
class ProjectionPlan:
    ok: bool
    target_period_px: float | None
    target_axis: str
    confidence: float
    reason: str | None
    metrics: dict
    version: str = PROJECTION_VERSION

    def summary(self) -> dict:
        out = {
            "ok": self.ok,
            "targetPeriodPx": (round(self.target_period_px, 2)
                               if self.target_period_px is not None else None),
            "targetAxis": self.target_axis,
            "confidence": round(self.confidence, 3),
            "version": self.version,
            "metrics": self.metrics,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def _fail(reason: str, *, target_axis: str, metrics: dict | None = None) -> ProjectionPlan:
    return ProjectionPlan(
        ok=False, target_period_px=None, target_axis=target_axis,
        confidence=0.0, reason=reason, metrics=metrics or {})


def plan_periodic_projection(
    *,
    pattern_type: str,
    source_period_px: float,
    source_span_px: float,
    target_span_px: float,
    target_axis: str,
    source_model_confidence: float,
) -> ProjectionPlan:
    """Return a deterministic target-period plan for 2D periodic projection.

    `source_span_px` and `target_span_px` must represent the same garment axis
    (torso width for vertical stripes, torso height for horizontal stripes).  The
    plan preserves repeat count, not raw pixel period, because carrier geometry can
    be wider/narrower than the photographed source.
    """
    kind = str(pattern_type or "unknown").strip().lower()
    axis = "horizontal" if target_axis == "horizontal" else "vertical"
    if kind not in SUPPORTED_PATTERN_TYPES:
        return _fail("unsupported_pattern", target_axis=axis, metrics={"patternType": kind})
    if source_period_px <= 0 or source_span_px <= 0 or target_span_px <= 0:
        return _fail("projection_geometry_invalid", target_axis=axis, metrics={
            "sourcePeriodPx": round(float(source_period_px), 3),
            "sourceSpanPx": round(float(source_span_px), 3),
            "targetSpanPx": round(float(target_span_px), 3),
        })
    repeats = float(source_span_px) / float(source_period_px)
    if repeats < MIN_SOURCE_REPEATS:
        return _fail("reference_insufficient", target_axis=axis, metrics={
            "sourceRepeats": round(repeats, 3),
            "minimumRepeats": MIN_SOURCE_REPEATS,
        })
    target_period = float(target_span_px) / repeats
    if target_period < MIN_TARGET_PERIOD_PX:
        return _fail("target_period_too_small", target_axis=axis, metrics={
            "targetPeriodPx": round(target_period, 3),
            "minimumTargetPeriodPx": MIN_TARGET_PERIOD_PX,
            "sourceRepeats": round(repeats, 3),
        })
    scale = target_period / float(source_period_px)
    if scale < 1.0 / MAX_SCALE_CHANGE or scale > MAX_SCALE_CHANGE:
        return _fail("projection_scale_out_of_bounds", target_axis=axis, metrics={
            "projectionScale": round(scale, 4),
            "maxScaleChange": MAX_SCALE_CHANGE,
        })
    repeat_conf = min(1.0, repeats / 8.0)
    period_conf = min(1.0, target_period / 14.0)
    scale_conf = max(0.0, 1.0 - abs(scale - 1.0) / MAX_SCALE_CHANGE)
    confidence = float(min(max(0.0, source_model_confidence), repeat_conf, period_conf, scale_conf))
    ok = confidence >= MIN_CONFIDENCE
    return ProjectionPlan(
        ok=ok,
        target_period_px=target_period,
        target_axis=axis,
        confidence=confidence,
        reason=None if ok else "projection_low_confidence",
        metrics={
            "patternType": kind,
            "sourcePeriodPx": round(float(source_period_px), 3),
            "sourceSpanPx": round(float(source_span_px), 3),
            "targetSpanPx": round(float(target_span_px), 3),
            "sourceRepeats": round(repeats, 3),
            "projectionScale": round(scale, 4),
            "sourceModelConfidence": round(float(source_model_confidence), 3),
            "repeatConfidence": round(repeat_conf, 3),
            "periodConfidence": round(period_conf, 3),
            "scaleConfidence": round(scale_conf, 3),
        },
    )
