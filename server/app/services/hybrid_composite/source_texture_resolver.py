"""A source period read from several crops instead of one.

`extract_stripe_model_scan` already votes across windows INSIDE a crop: 320/512/768
patches, grouped by axis and colour count, period within ±15% of the group median.
What it cannot see is the crop boundary itself. That boundary comes from Vision
landmarks, and their jitter moved it enough that the scan failed on 11 of 16 probe
crops of one photo — and when the scan fails, production falls back to the guided
search, whose winner flipped between 15, 30 and 45 depending on the same boundary.

So the two layers answer different questions:

    inner (existing)  — is this reading stable across patches of one crop?
    outer (here)      — is it stable across where we drew the crop?

This module is measurement only. Nothing here decides anything: P3 runs it beside
the production reading and records both. Turning it into the authority needs a
recovery route for UNCERTAIN, which does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Sequence

from .stripe_model import PATCH_PERIOD_AGREEMENT_TOL

SCHEMA = "source_texture_multi_roi_v1"

STATUS_RELIABLE = "RELIABLE"
STATUS_UNCERTAIN = "UNCERTAIN"

REASON_NO_SUCCESSFUL_SCAN = "NO_SUCCESSFUL_SCAN"
REASON_SINGLE_ROI_NOT_A_CONSENSUS = "SINGLE_ROI_NOT_A_CONSENSUS"
REASON_CONFLICTING_CLUSTERS = "CONFLICTING_CLUSTERS"

#: The scan needs both sides of a crop at 480px or it drops to the single-window
#: extractor, which is a different measurement. Insets stop before that.
MIN_SCAN_SIDE_PX = 480

#: Sampling geometry, not a decision threshold: how far each ROI pushes OUT past
#: the production crop's edges, as a fraction of its shorter side.
#:
#: The direction is measured, not assumed. The scan only accepts a period once at
#: least three patches agree, so a smaller crop has fewer windows and loses that
#: floor: on the real photo the production crop and both outsets resolved
#: (20.649 / 20.267 / 20.811) while every inset failed with
#: stripe_model_low_confidence. Members must therefore never be smaller than the
#: crop production already trusts.
#:
#: The specific fractions are a starting point, not a tuned result. How many ROIs
#: and how far apart they should sit is TBD until a dataset exists.
ROI_OUTSET_FRACTIONS = (0.0, 0.0625, 0.125)

#: What this module's own RELIABLE requires: two agreeing readings. One reading is
#: not a consensus, so this is a structural definition rather than a tuned number —
#: nothing was measured to pick it and nothing would be measured to move it.
SHADOW_CONSENSUS_MIN_ROIS = 2

#: What PRODUCTION should require before trusting this reading. Deliberately unset:
#: "two worked on one photo" is not evidence for a production floor, and P3 changes
#: no decision, so nothing here needs it yet.
MINIMUM_SUPPORTING_ROIS_FOR_PRODUCTION = None


@dataclass(frozen=True)
class RoiSpec:
    roi_id: str
    bounds: tuple
    construction: str
    role: str
    deterministic_given_source_context: bool = True
    #: This family does not call Vision and does not read anything candidate-specific.
    #: It is NOT Vision-free: every member is derived from the production torso crop,
    #: which upstream builds from Vision landmarks. P3 samples around that boundary;
    #: it does not remove the dependency on it.
    adds_vision_dependency: bool = False
    upstream_vision_dependent: bool = True
    candidate_dependent: bool = False

    def as_dict(self) -> dict:
        return {
            "roiId": self.roi_id, "roi": list(self.bounds),
            "construction": self.construction, "role": self.role,
            "deterministicGivenSourceContext": self.deterministic_given_source_context,
            "addsVisionDependency": self.adds_vision_dependency,
            "upstreamVisionDependent": self.upstream_vision_dependent,
            "candidateDependent": self.candidate_dependent,
        }


@dataclass
class RoiReading:
    roi_id: str
    bounds: tuple
    success: bool
    period_px: float | None = None
    confidence: float | None = None
    axis: str | None = None
    failure_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "roiId": self.roi_id, "roi": list(self.bounds), "success": self.success,
            "periodPx": (round(float(self.period_px), 4)
                         if self.period_px is not None else None),
            "confidence": (round(float(self.confidence), 4)
                           if self.confidence is not None else None),
            "axis": self.axis, "failureReason": self.failure_reason,
        }


@dataclass(frozen=True)
class MultiRoiResolution:
    status: str
    selected_period_px: float | None
    selected_axis: str | None
    confidence: float | None
    consensus_method: str
    successful_scan_count: int
    attempted_roi_count: int
    roi_results: tuple = ()
    period_cluster: tuple = ()
    uncertainty_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "selectedPeriodPx": (round(float(self.selected_period_px), 4)
                                 if self.selected_period_px is not None else None),
            "selectedAxis": self.selected_axis,
            "confidence": (round(float(self.confidence), 4)
                           if self.confidence is not None else None),
            "consensusMethod": self.consensus_method,
            "successfulScanCount": self.successful_scan_count,
            "attemptedRoiCount": self.attempted_roi_count,
            "roiResults": [r.as_dict() if isinstance(r, RoiReading) else r
                           for r in self.roi_results],
            "periodCluster": [round(float(p), 4) for p in self.period_cluster],
            "uncertaintyReason": self.uncertainty_reason,
            "agreementTolerance": PATCH_PERIOD_AGREEMENT_TOL,
            # RELIABLE here means "these crops agreed", not "safe to act on"
            "shadowConsensusMinRois": SHADOW_CONSENSUS_MIN_ROIS,
            "statusMeaning": "internal consensus across crops; not a production readiness claim",
            "minimumSupportingRoisForProduction": MINIMUM_SUPPORTING_ROIS_FOR_PRODUCTION,
        }


def build_roi_family(base_roi: Sequence[int], *, width: int, height: int,
                     placket_box=None) -> list[RoiSpec]:
    """Concentric crops around the production one, none of them smaller.

    The crop edge is where landmark jitter lives, so varying it is the point. The
    direction is fixed by the scan's own rule: it needs at least three agreeing
    patches, and a smaller crop has fewer windows to find them in — measured on the
    real photo, every inset failed while the production crop and both outsets
    resolved to 20.649 / 20.267 / 20.811.

    Deterministic given the production crop and the image size; needs no Vision
    call of its own, since P1 already resolves that crop once per run.

    `placket_box` is accepted and unused: splitting the torso at the placket was
    tried and every panel failed the same patch-consensus floor, so shipping it
    would have cost CV time for no reading.
    """
    x0, y0, x1, y1 = (int(v) for v in base_roi)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(int(width), x1), min(int(height), y1)
    if x1 - x0 < MIN_SCAN_SIDE_PX or y1 - y0 < MIN_SCAN_SIDE_PX:
        return []
    short_side = min(x1 - x0, y1 - y0)
    family: list[RoiSpec] = []
    seen: set = set()
    for fraction in ROI_OUTSET_FRACTIONS:
        grow = int(round(short_side * fraction))
        bounds = (max(0, x0 - grow), max(0, y0 - grow),
                  min(int(width), x1 + grow), min(int(height), y1 + grow))
        if bounds in seen:
            continue                      # clamped against the image edge already
        seen.add(bounds)
        family.append(RoiSpec(
            roi_id="baseline" if grow == 0 else f"outset-{fraction:.4f}".rstrip("0").rstrip("."),
            bounds=bounds,
            construction=("production torso crop" if grow == 0 else
                          f"production torso crop grown by {fraction:.4f} of its shorter side"),
            role="baseline" if grow == 0 else "boundary_robustness"))
    return family


def resolve(family: Sequence[RoiSpec], scan: Callable[[RoiSpec], Any]) -> MultiRoiResolution:
    """Scan each ROI, then look for one cluster every successful reading belongs to.

    `scan(spec)` returns the production scan result for that crop — a model with
    `.period_px` / `.confidence` / `.axis`, or a failure carrying `.reason`. Nothing
    is measured here; this only asks whether the readings agree.

    Deliberately free of a support-count threshold: "every successful reading is
    compatible" needs no tuned number, and anything short of that is reported as
    UNCERTAIN rather than resolved by a number no dataset has justified yet.
    """
    readings: list[RoiReading] = []
    for spec in family:
        result = scan(spec)
        reason = getattr(result, "reason", None)
        period = getattr(result, "period_px", None)
        if reason is not None or period is None:
            readings.append(RoiReading(spec.roi_id, spec.bounds, False,
                                       failure_reason=reason or "no_period"))
            continue
        readings.append(RoiReading(
            spec.roi_id, spec.bounds, True, period_px=float(period),
            confidence=(float(result.confidence)
                        if getattr(result, "confidence", None) is not None else None),
            axis=getattr(result, "axis", None)))

    ok = [r for r in readings if r.success]
    base = dict(attempted_roi_count=len(family), successful_scan_count=len(ok),
                roi_results=tuple(readings), consensus_method="median_of_compatible_scans")

    if not ok:
        return MultiRoiResolution(STATUS_UNCERTAIN, None, None, None,
                                  uncertainty_reason=REASON_NO_SUCCESSFUL_SCAN, **base)
    if len(ok) < SHADOW_CONSENSUS_MIN_ROIS:
        return MultiRoiResolution(
            STATUS_UNCERTAIN, None, None, None,
            period_cluster=(ok[0].period_px,),
            uncertainty_reason=REASON_SINGLE_ROI_NOT_A_CONSENSUS, **base)

    # order-independent: cluster from the median, not from whichever ROI came first
    periods = sorted(r.period_px for r in ok)
    centre = float(median(periods))
    compatible = [p for p in periods
                  if abs(p - centre) / centre <= PATCH_PERIOD_AGREEMENT_TOL]
    if len(compatible) != len(periods):
        return MultiRoiResolution(
            STATUS_UNCERTAIN, None, None, None, period_cluster=tuple(periods),
            uncertainty_reason=REASON_CONFLICTING_CLUSTERS, **base)

    axes = {r.axis for r in ok if r.axis}
    confidences = [r.confidence for r in ok if r.confidence is not None]
    return MultiRoiResolution(
        STATUS_RELIABLE,
        float(median(compatible)),
        axes.pop() if len(axes) == 1 else None,
        float(median(confidences)) if confidences else None,
        period_cluster=tuple(compatible), uncertainty_reason=None, **base)
