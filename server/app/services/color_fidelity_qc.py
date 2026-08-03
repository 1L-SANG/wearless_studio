"""의류 ROI의 Lab/Delta-E 기반 색상 충실도 검사."""

from __future__ import annotations

import cv2
import numpy as np

from .hybrid_composite.color import bgr_to_lab, ciede2000

MIN_MASK_PIXELS = 64
MIN_MASK_RATIO = 0.01


def _mask(mask, shape):
    if mask is None:
        return np.ones(shape, bool)
    m = np.asarray(mask)
    if m.shape != shape:
        m = cv2.resize(m.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return m > 0


def _summary(lab, mask):
    pixels = lab[mask]
    if len(pixels) < MIN_MASK_PIXELS or len(pixels) / mask.size < MIN_MASK_RATIO:
        return None
    light = pixels[:, 0]
    q35, q85 = np.quantile(light, [0.35, 0.85])
    shadow = pixels[light <= q35]
    mid = pixels[(light > q35) & (light < q85)]
    if not len(mid):
        mid = pixels
    return np.median(shadow, axis=0), np.median(mid, axis=0), np.median(pixels, axis=0)


def compare_color(source_bgr, output_bgr, *, source_mask=None, output_mask=None,
                  pass_delta_e: float = 6.0, review_delta_e: float = 12.0) -> dict:
    src = np.asarray(source_bgr, dtype=np.uint8)
    out = np.asarray(output_bgr, dtype=np.uint8)
    sm = _mask(source_mask, src.shape[:2])
    om = _mask(output_mask, out.shape[:2])
    ss = _summary(bgr_to_lab(src), sm)
    os = _summary(bgr_to_lab(out), om)
    if ss is None or os is None:
        return {"check": "color_fidelity", "status": "unavailable", "score": None,
                "metrics": {"maskConfidence": 0.0}, "warnings": ["low_mask_confidence"]}
    shadow_de = float(ciede2000(ss[0], os[0]))
    mid_de = float(ciede2000(ss[1], os[1]))
    dominant_de = float(ciede2000(ss[2], os[2]))
    effective = 0.75 * mid_de + 0.25 * dominant_de
    status = "pass" if effective <= pass_delta_e else ("review" if effective <= review_delta_e else "fail")
    return {
        "check": "color_fidelity", "status": status,
        "score": max(0.0, min(1.0, 1.0 - effective / 25.0)),
        "metrics": {"shadowDeltaE00": shadow_de, "midtoneDeltaE00": mid_de,
                    "dominantDeltaE00": dominant_de, "effectiveDeltaE00": effective,
                    "maskConfidence": 1.0},
        "failedRegions": [] if status == "pass" else [{"region": "garment", "reason": "color_drift"}],
        "regenerationInstructions": [] if status == "pass" else ["preserve_source_garment_color"],
    }
