"""Is the low-res stripe failure caused by resolution, or by the garment?

Job cf61bc21 failed with `stripe_model_low_confidence — 패치 합의 부족 (성공 2/9)` on a
768x1024 photo, while a 4284x5712 photo of a different shirt resolves. Two different
products, so that pair proves nothing on its own.

The experiment that does prove something is the SAME pixels at two resolutions:

  * take the high-res shirt that production already resolved, shrink it, and see whether
    the committed extractor crosses from success to failure
  * take the failing low-res photo, enlarge it, and see whether the same extractor recovers

Enlarging invents no information, so a recovery there would say the extractor is sensitive
to absolute pixel geometry rather than to how much pattern is actually visible.

Read-only: local CV over bytes already on disk. No provider call, no Vision call, no image
generation, no database or R2 write, and no threshold, window size or ROI touched.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import cv2

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.services.hybrid_composite import stripe_model as sm      # noqa: E402
from app.services.hybrid_composite.types import CompositeFailure  # noqa: E402

OUT = SERVER / "ab_out/diagnostic_stripe_resolution_sensitivity"

#: exactly what production scanned. Both slots fell back to the whole image, so the ROI is
#: the full frame in both cases — the same operation, which makes them comparable.
LOW_RES = Path("/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/줄무늬나시앞면.jpeg")
HIGH_RES = SERVER / "ab_out/frame_lock/stripe-projection-protected-v3/artifacts/source_front.png"

#: the extractor's own geometry, read not redefined — this only counts what it would do
SIZES = (320, 512, 768)
STRIDE_FRACTION = 0.75


def window_geometry(h: int, w: int) -> dict:
    """How many patches the committed scan would enumerate at this size. Observation only."""
    boxes = []
    for size in SIZES:
        if size > min(h, w):
            break
        stride = max(1, int(size * STRIDE_FRACTION))
        for y in range(0, h - size + 1, stride):
            for x in range(0, w - size + 1, stride):
                boxes.append((x, y, size))
    return {"windows": len(boxes), "bySize": dict(Counter(s for _x, _y, s in boxes)),
            "boxes": boxes}


def probe(img) -> dict:
    """Run the committed extractor, plus a read-only per-window census for decomposition."""
    h, w = img.shape[:2]
    geo = window_geometry(h, w)

    ok, reasons = 0, Counter()
    for x, y, size in geo["boxes"]:
        m = sm.extract_stripe_model(
            img[y:y + size, x:x + size], source_asset_id="probe",
            source_sha256="probe", source_roi=(x, y, x + size, y + size))
        if isinstance(m, CompositeFailure):
            reasons[m.reason] += 1
        else:
            ok += 1

    result = sm.extract_stripe_model_scan(
        img, source_asset_id="probe", source_sha256="probe", source_roi=(0, 0, w, h))
    failed = isinstance(result, CompositeFailure)
    return {
        "width": w, "height": h,
        "attemptedWindows": geo["windows"], "windowsBySize": geo["bySize"],
        "successfulWindows": ok,
        "successRatio": round(ok / geo["windows"], 4) if geo["windows"] else None,
        "windowFailureReasons": dict(reasons),
        "scanResolved": not failed,
        "periodPx": None if failed else round(float(result.period_px), 3),
        "confidence": None if failed else round(float(result.confidence), 4),
        "nColors": None if failed else len(result.color_sequence_lab),
        "axis": None if failed else result.axis,
        "normalizedPeriodByWidth": (None if failed
                                    else round(float(result.period_px) / w, 6)),
        "normalizedPeriodByHeight": (None if failed
                                     else round(float(result.period_px) / h, 6)),
        "failureReason": result.reason if failed else None,
        "failureDetail": result.detail[:120] if failed else None,
    }


def scaled(img, factor: float, *, interpolation: int, label: str) -> dict:
    h, w = img.shape[:2]
    nw, nh = max(1, round(w * factor)), max(1, round(h * factor))
    out = probe(cv2.resize(img, (nw, nh), interpolation=interpolation))
    return {"scale": round(factor, 4), "interpolation": label, **out}


def main() -> int:
    low = cv2.imread(str(LOW_RES), cv2.IMREAD_COLOR)
    high = cv2.imread(str(HIGH_RES), cv2.IMREAD_COLOR)
    if low is None or high is None:
        print("missing input image(s)")
        return 1

    report: dict = {"inputs": {
        "lowRes": {"path": str(LOW_RES), "size": [low.shape[1], low.shape[0]],
                   "productionScanRoi": "full frame (Front validation failed -> full fallback)",
                   "productionResult": "stripe_model_low_confidence 패치 합의 부족 (성공 2/9)"},
        "highRes": {"path": str(HIGH_RES), "size": [high.shape[1], high.shape[0]],
                    "productionResult": "SCAN resolved, period 29.63 conf 0.808 (Front full fallback)"}}}

    print("== baseline replay (low-res, exactly what production scanned)")
    base = probe(low)
    report["lowResBaseline"] = base
    print(f"   {base['width']}x{base['height']}  windows {base['successfulWindows']}"
          f"/{base['attemptedWindows']}  resolved={base['scanResolved']}  {base['failureReason']}")
    report["baselineMatchesProduction"] = (
        base["attemptedWindows"] == 9 and base["successfulWindows"] == 2
        and base["failureReason"] == "stripe_model_low_confidence")
    print(f"   matches production 2/9: {report['baselineMatchesProduction']}")

    print("\n== high-res downsample (SAME pixels that production resolved)")
    target = round(low.shape[1] / high.shape[1], 4)   # match the low-res width
    rows = []
    for f in (1.0, 0.75, 0.5, 0.375, 0.25, target):
        r = scaled(high, f, interpolation=cv2.INTER_AREA, label="INTER_AREA")
        rows.append(r)
        print(f"   x{r['scale']:<6} {r['width']:5d}x{r['height']:<5d} "
              f"win {r['successfulWindows']:3d}/{r['attemptedWindows']:<3d} "
              f"resolved={str(r['scanResolved']):5s} period={r['periodPx']} "
              f"{r['failureReason'] or ''}")
    report["highResDownsample"] = rows

    print("\n== low-res upscale (adds no information — diagnostic only)")
    ups = []
    for f in (1, 2, 3, 4):
        r = scaled(low, float(f), interpolation=cv2.INTER_CUBIC, label="INTER_CUBIC")
        ups.append(r)
        print(f"   x{f:<6} {r['width']:5d}x{r['height']:<5d} "
              f"win {r['successfulWindows']:3d}/{r['attemptedWindows']:<3d} "
              f"resolved={str(r['scanResolved']):5s} period={r['periodPx']} "
              f"{r['failureReason'] or ''}")
    report["lowResUpscale"] = ups

    down_resolved = [r for r in rows if r["scanResolved"]]
    report["sameSourceTransitionObserved"] = bool(
        rows[0]["scanResolved"] and any(not r["scanResolved"] for r in rows))
    report["lowestResolvedDownsampleWidth"] = (
        min(r["width"] for r in down_resolved) if down_resolved else None)
    report["upscaleRecoveryObserved"] = bool(
        not ups[0]["scanResolved"] and any(r["scanResolved"] for r in ups[1:]))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "resolution_sensitivity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1))
    print("\n== summary")
    print(json.dumps({k: report[k] for k in (
        "baselineMatchesProduction", "sameSourceTransitionObserved",
        "lowestResolvedDownsampleWidth", "upscaleRecoveryObserved")}, indent=1))
    print(f"wrote {OUT / 'resolution_sensitivity.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
