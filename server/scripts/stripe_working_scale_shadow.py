"""Would a canonical working scale recover low-res stripe sources — in SOURCE space?

The extractor keys on absolute pixel period, so a 768x1024 photo fails while the same
garment enlarged resolves. That is a measurement-geometry problem, not a truth problem, so
the fix belongs in how we present pixels to the extractor — never in what we persist.

The scale is not invented here. It falls out of two constants the validator already uses:
it crops the centre at CENTER_CROP_FRAC and demands MIN_ROI_SIDE_PX on the short side. The
scale that makes an existing source satisfy the existing rule is therefore

    s = MIN_ROI_SIDE_PX / (min(W, H) * CENTER_CROP_FRAC)      clamped to >= 1.0

Clamping matters: a source that already satisfies the rule must stay at 1.0, or every
high-res product would be silently resampled.

Everything measured in working space is divided back by s before it is reported. A 27.553px
period at s=2 is 13.777 source pixels, and only the latter is ever a fact about the garment.

Shadow only: no production routing, no threshold, no runtime source touched. No provider,
Vision or image-generation call.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import cv2

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.services.hybrid_composite import source_validation as sv   # noqa: E402
from app.services.hybrid_composite import stripe_model as sm        # noqa: E402
from app.services.hybrid_composite.types import CompositeFailure    # noqa: E402

OUT = SERVER / "ab_out/diagnostic_stripe_working_scale_shadow"
GARMENTS = Path("/Users/nojeong-un/Downloads/노션에 있는 의상들/상의")
CAMISOLE = {
    "Front": GARMENTS / "줄무늬나시앞면.jpeg",
    "Detail1": GARMENTS / "줄무늬나시_디테일.jpeg",
    "Detail2": GARMENTS / "줄무늬나시디테일2.jpeg",
}
SHIRT = SERVER / "ab_out/frame_lock/stripe-projection-protected-v3/artifacts/source_front.png"

#: enlargement only ever uses this one method, documented and not tuned
INTERP_UP = (cv2.INTER_CUBIC, "INTER_CUBIC")
#: reduction is used only to BUILD the downsample controls, not by the normalization
INTERP_DOWN = (cv2.INTER_AREA, "INTER_AREA")

LADDER = (1.0, 2.0, 3.0, 4.0)


def geometry_scale(img) -> tuple[float, str]:
    """The scale that makes this source satisfy the validator's own existing rule."""
    h, w = img.shape[:2]
    effective = min(w, h) * sv.CENTER_CROP_FRAC
    s = sv.MIN_ROI_SIDE_PX / effective if effective else 1.0
    if s <= 1.0:
        return 1.0, "source already satisfies MIN_ROI_SIDE_PX; never downscale"
    return round(s, 4), (f"MIN_ROI_SIDE_PX({sv.MIN_ROI_SIDE_PX}) / "
                         f"(min(W,H)={min(w, h)} * CENTER_CROP_FRAC={sv.CENTER_CROP_FRAC})")


def resize(img, s: float):
    if s == 1.0:
        return img, ("none", "s=1.0")
    h, w = img.shape[:2]
    flag, label = INTERP_UP if s > 1 else INTERP_DOWN
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))),
                      interpolation=flag), (label, f"s={s}")


def validate(img) -> dict:
    r = sv.validate_stripe_source(img)
    if isinstance(r, CompositeFailure):
        return {"ok": False, "reason": r.reason, "detail": r.detail[:80]}
    return {"ok": True, "roi": list(r.roi), "nPeriods": r.n_periods_in_roi, "axis": r.axis}


def scan_at(img, s: float) -> dict:
    """Scan at working scale s, then divide every pixel measurement back to source space."""
    work, (interp, _) = resize(img, s)
    h, w = work.shape[:2]
    t0 = time.perf_counter()
    res = sm.extract_stripe_model_scan(work, source_asset_id="shadow",
                                       source_sha256="shadow", source_roi=(0, 0, w, h))
    ms = round((time.perf_counter() - t0) * 1000, 1)
    failed = isinstance(res, CompositeFailure)
    return {
        "workingScale": s, "interpolation": interp,
        "workingWidth": w, "workingHeight": h,
        "resolved": not failed,
        "workingPeriodPx": None if failed else round(float(res.period_px), 4),
        "sourcePeriodPx": None if failed else round(float(res.period_px) / s, 4),
        "confidence": None if failed else round(float(res.confidence), 4),
        "nColors": None if failed else len(res.color_sequence_lab),
        "axis": None if failed else res.axis,
        "failureReason": res.reason if failed else None,
        "elapsedMs": ms,
        "sourceValidation": validate(work),
    }


def consensus(source_periods: list) -> dict:
    """Agreement in SOURCE space, using the tolerance the codebase already uses."""
    if not source_periods:
        return {"members": 0, "median": None, "outliers": [], "agree": False,
                "tolerance": sm.PATCH_PERIOD_AGREEMENT_TOL,
                "toleranceOrigin": "EXISTING_TOLERANCE_REUSED (stripe_model."
                                   "PATCH_PERIOD_AGREEMENT_TOL)"}
    med = float(statistics.median(source_periods))
    dev = [round(abs(p - med) / med, 4) for p in source_periods]
    outliers = [p for p, d in zip(source_periods, dev) if d > sm.PATCH_PERIOD_AGREEMENT_TOL]
    return {"members": len(source_periods), "median": round(med, 4),
            "relativeDeviations": dev, "outliers": outliers,
            "agree": not outliers, "tolerance": sm.PATCH_PERIOD_AGREEMENT_TOL,
            "toleranceOrigin": "EXISTING_TOLERANCE_REUSED (stripe_model."
                               "PATCH_PERIOD_AGREEMENT_TOL)"}


def ladder_for(img) -> dict:
    """The multi-scale ladder, bounded by the validator's own rule.

    A source that already satisfies MIN_ROI_SIDE_PX needs no enlargement, so the ladder
    collapses to 1.0 there. Running 4x on a 4284px photo would build a 391-megapixel
    working image to answer a question the geometry rule has already answered.
    """
    gs, why = geometry_scale(img)
    scales = sorted({*LADDER, gs}) if gs > 1.0 else [1.0]
    runs = [scan_at(img, s) for s in scales]
    ok = [r for r in runs if r["resolved"]]
    return {"geometryScale": gs, "geometryScaleReason": why,
            "runs": runs,
            "sourceSpaceConsensus": consensus([r["sourcePeriodPx"] for r in ok])}


def main() -> int:
    report: dict = {"constants": {
        "sourceValidation": [
            {"constant": "MIN_ROI_SIDE_PX", "value": sv.MIN_ROI_SIDE_PX,
             "function": "validate_stripe_source", "purpose": "minimum short side of the "
             "effective fabric ROI", "scaleDependent": True, "productionGate": True},
            {"constant": "CENTER_CROP_FRAC", "value": sv.CENTER_CROP_FRAC,
             "function": "validate_stripe_source", "purpose": "the effective ROI is a "
             "centre crop of this fraction", "scaleDependent": True, "productionGate": True},
            {"constant": "MIN_LAPLACIAN_VAR", "value": sv.MIN_LAPLACIAN_VAR,
             "function": "validate_stripe_source", "purpose": "sharpness floor",
             "scaleDependent": False, "productionGate": True},
            {"constant": "MIN_PERIODS_IN_ROI", "value": sm.MIN_PERIODS_IN_ROI,
             "function": "validate_stripe_source", "purpose": "repeats inside the ROI (a "
             "count, not a pixel size)", "scaleDependent": False, "productionGate": True}],
        "scan": [
            {"constant": "window sizes", "value": [320, 512, 768],
             "function": "extract_stripe_model_scan", "purpose": "patch sizes",
             "scaleDependent": True, "productionGate": True},
            {"constant": "stride fraction", "value": 0.75,
             "function": "extract_stripe_model_scan", "purpose": "patch stride",
             "scaleDependent": True, "productionGate": True},
            {"constant": "single-window fallback floor", "value": 480,
             "function": "extract_stripe_model_scan", "purpose": "below this the scan "
             "degrades to one window", "scaleDependent": True, "productionGate": True},
            {"constant": "MIN_PERIOD_PX", "value": sm.MIN_PERIOD_PX,
             "function": "extract_stripe_model", "purpose": "absolute period floor",
             "scaleDependent": True, "productionGate": True},
            {"constant": "PATCH_PERIOD_AGREEMENT_TOL", "value": sm.PATCH_PERIOD_AGREEMENT_TOL,
             "function": "extract_stripe_model_scan", "purpose": "patch agreement",
             "scaleDependent": False, "productionGate": True},
            {"constant": "consensus floor", "value": "max(3, ceil(0.3 * successes))",
             "function": "extract_stripe_model_scan", "purpose": "eligible cluster size",
             "scaleDependent": False, "productionGate": True}]}}

    # ---- camisole, every asset, not just the one the filename calls Front
    print("== camisole assets")
    cam = {}
    for role, path in CAMISOLE.items():
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        gs, why = geometry_scale(img)
        before = validate(img)
        work, _ = resize(img, gs)
        after = validate(work)
        single = scan_at(img, gs)
        base = scan_at(img, 1.0)
        cam[role] = {"path": path.name, "sourceSize": [img.shape[1], img.shape[0]],
                     "geometryScale": gs, "geometryScaleReason": why,
                     "sourceValidationBefore": before, "sourceValidationAfter": after,
                     "currentScan": base, "normalizedScan": single,
                     "ladder": ladder_for(img)}
        c = cam[role]
        print(f"  {role:8s} {c['sourceSize'][0]}x{c['sourceSize'][1]}  s={gs}  "
              f"valid {before['ok']}->{after['ok']}  "
              f"scan {base['resolved']}->{single['resolved']}  "
              f"sourcePeriod={single['sourcePeriodPx']}")
        cs = c["ladder"]["sourceSpaceConsensus"]
        print(f"           ladder source periods median={cs['median']} agree={cs['agree']} "
              f"dev={cs.get('relativeDeviations')}")
    report["camisole"] = cam

    # ---- high-res control: must stay at 1.0 and must not change
    print("\n== stripe-shirt high-res no-regression")
    shirt = cv2.imread(str(SHIRT), cv2.IMREAD_COLOR)
    gs, why = geometry_scale(shirt)
    cur = scan_at(shirt, 1.0)
    norm = scan_at(shirt, gs)
    report["shirtNoRegression"] = {
        "sourceSize": [shirt.shape[1], shirt.shape[0]], "geometryScale": gs,
        "geometryScaleReason": why, "current": cur, "normalized": norm,
        "identical": (gs == 1.0 and cur["sourcePeriodPx"] == norm["sourcePeriodPx"]
                      and cur["confidence"] == norm["confidence"])}
    print(f"  s={gs}  current period={cur['sourcePeriodPx']} conf={cur['confidence']}  "
          f"normalized period={norm['sourcePeriodPx']}  identical={report['shirtNoRegression']['identical']}")

    # ---- same-source downsample matrix: the strongest correctness control
    print("\n== stripe-shirt downsample matrix (expected source period from original)")
    expected_at_1 = cur["sourcePeriodPx"]
    rows = []
    for f in (1.0, 0.75, 0.5, 0.375, 0.25, 0.179):
        small, _ = resize(shirt, f)
        cur_s = scan_at(small, 1.0)
        gs_s, _why = geometry_scale(small)
        norm_s = scan_at(small, gs_s)
        lad = ladder_for(small)
        ms_ok = [r["sourcePeriodPx"] for r in lad["runs"] if r["resolved"]]
        ms_period = (round(statistics.median(ms_ok), 4) if ms_ok else None)
        # what the period SHOULD be in this downsampled image's own pixels
        expected = round(expected_at_1 * f, 4) if expected_at_1 else None
        got = norm_s["sourcePeriodPx"] or ms_period
        rows.append({
            "inputScale": f, "inputSize": [small.shape[1], small.shape[0]],
            "currentResolved": cur_s["resolved"],
            "currentSourcePeriod": cur_s["sourcePeriodPx"],
            "normalizationWorkingScale": gs_s,
            "normalizedResolved": norm_s["resolved"],
            "normalizedSourcePeriod": norm_s["sourcePeriodPx"],
            "multiScaleResolved": bool(ms_ok),
            "multiScaleSourcePeriod": ms_period,
            "expectedSourcePeriodFromOriginal": expected,
            "relativeError": (round(abs(got - expected) / expected, 4)
                              if got and expected else None)})
        r = rows[-1]
        print(f"  x{f:<6} {r['inputSize'][0]:5d}px  cur={str(r['currentResolved']):5s} "
              f"norm(s={gs_s})={str(r['normalizedResolved']):5s} p={r['normalizedSourcePeriod']} "
              f"multi={str(r['multiScaleResolved']):5s} p={r['multiScaleSourcePeriod']} "
              f"expected={expected} relErr={r['relativeError']}")
    report["shirtDownsampleMatrix"] = rows

    # ---- cost
    times = {"current1x": [], "geometryNormalized": [], "multiScale": []}
    for role in cam:
        times["current1x"].append(cam[role]["currentScan"]["elapsedMs"])
        times["geometryNormalized"].append(cam[role]["normalizedScan"]["elapsedMs"])
        times["multiScale"].append(sum(r["elapsedMs"] for r in cam[role]["ladder"]["runs"]))
    report["computeCostMs"] = {k: {"median": round(statistics.median(v), 1),
                                   "max": max(v)} for k, v in times.items()}
    print("\n== compute (ms):", json.dumps(report["computeCostMs"]))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "working_scale_shadow.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nwrote {OUT / 'working_scale_shadow.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
