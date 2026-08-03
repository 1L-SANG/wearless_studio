"""주기 패턴의 방향·간격을 결정적으로 비교하는 Level-1 QC."""

from __future__ import annotations

import cv2
import numpy as np

SUPPORTED = {"stripe", "check", "plaid", "gingham", "tartan"}
MIN_MASK_RATIO = 0.05


def _prepare(image, mask):
    gray = cv2.cvtColor(np.asarray(image, np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    if mask is None:
        m = np.ones(gray.shape, bool)
    else:
        raw = np.asarray(mask)
        if raw.shape != gray.shape:
            raw = cv2.resize(raw.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
        m = raw > 0
    return gray, m


def _axis_period(gray, mask):
    fill = float(np.median(gray[mask])) if mask.any() else 0.0
    weighted = np.where(mask, gray, fill)
    profiles = {"vertical": weighted.mean(axis=0), "horizontal": weighted.mean(axis=1)}
    best = None
    for direction, prof in profiles.items():
        # profile은 가로 1행이다. OpenCV ksize 순서는 (width, height)이므로 x축만
        # 저주파를 제거한다. (1, 0)은 y축 blur라 1행 입력에서 원 신호가 그대로 빠진다.
        signal = prof - cv2.GaussianBlur(prof.reshape(1, -1), (0, 1), 5).ravel()
        signal -= signal.mean()
        if np.std(signal) < 1.0:
            continue
        ac = np.correlate(signal, signal, mode="full")[len(signal) - 1:]
        if ac[0] <= 0:
            continue
        ac /= ac[0]
        lo, hi = 4, max(5, len(signal) // 3)
        peaks = [i for i in range(lo, hi) if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]]
        if not peaks:
            continue
        lag = max(peaks, key=lambda i: ac[i])
        binary = signal > 0
        changes = np.flatnonzero(np.diff(np.r_[False, binary, False].astype(np.int8)))
        widths = (changes[1::2] - changes[::2]).astype(np.float32)
        widths = widths[(widths > 0) & (widths <= lag)]
        positive_width = float(np.median(widths)) if len(widths) else None

        power = np.abs(np.fft.rfft(signal)) ** 2
        power[0] = 0
        indices = np.arange(len(power))
        periods = np.divide(len(signal), indices, out=np.full_like(indices, np.inf, dtype=float),
                            where=indices > 0)
        valid = (periods >= lo) & (periods < hi)
        fft_period = None
        fft_confidence = 0.0
        if valid.any() and float(power[valid].sum()) > 0:
            valid_indices = indices[valid]
            peak_index = int(valid_indices[np.argmax(power[valid])])
            fft_period = float(len(signal) / peak_index)
            fft_confidence = float(power[peak_index] / power[valid].sum())
        candidate = (float(ac[lag]), direction, float(lag), positive_width,
                     fft_period, fft_confidence)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best


def _hough_orientation(gray, mask):
    """Hough line evidence. 생성 모델의 줄 방향이 90° 돌아간 경우를 주기와 별도로 잡는다."""
    image = np.clip(gray, 0, 255).astype(np.uint8)
    edges = cv2.Canny(image, 40, 120)
    edges[~mask] = 0
    minimum = max(8, min(image.shape) // 10)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=max(10, minimum // 2),
        minLineLength=minimum, maxLineGap=max(3, minimum // 3))
    if lines is None:
        return None
    strengths = {"vertical": 0.0, "horizontal": 0.0}
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = float(x2 - x1), float(y2 - y1)
        length = float(np.hypot(dx, dy))
        if length <= 0:
            continue
        angle = abs(float(np.degrees(np.arctan2(dy, dx)))) % 180.0
        if angle <= 15.0 or angle >= 165.0:
            strengths["horizontal"] += length
        elif 75.0 <= angle <= 105.0:
            strengths["vertical"] += length
    total = sum(strengths.values())
    if total <= 0:
        return None
    ratios = {key: value / total for key, value in strengths.items()}
    dominant = max(ratios, key=ratios.get)
    return {"dominant": dominant, "confidence": ratios[dominant],
            "verticalRatio": ratios["vertical"],
            "horizontalRatio": ratios["horizontal"]}


def _overlay(image, direction, period):
    out = np.asarray(image, np.uint8).copy()
    step = max(4, int(round(period)))
    if direction == "vertical":
        for x in range(0, out.shape[1], step):
            cv2.line(out, (x, 0), (x, out.shape[0] - 1), (0, 255, 0), 1)
    else:
        for y in range(0, out.shape[0], step):
            cv2.line(out, (0, y), (out.shape[1] - 1, y), (0, 255, 0), 1)
    ok, encoded = cv2.imencode(".png", out)
    return encoded.tobytes() if ok else None


def compare_pattern(source_bgr, output_bgr, *, pattern_type: str, source_mask=None,
                    output_mask=None, period_tolerance: float = 0.18) -> dict:
    kind = str(pattern_type or "unknown").lower()
    if kind not in SUPPORTED:
        return {"check": "pattern_fidelity", "status": "not_applicable", "score": None,
                "metrics": {}, "debugOverlayPng": None}
    sg, sm = _prepare(source_bgr, source_mask)
    og, om = _prepare(output_bgr, output_mask)
    if sm.mean() < MIN_MASK_RATIO or om.mean() < MIN_MASK_RATIO:
        return {"check": "pattern_fidelity", "status": "unavailable", "score": None,
                "metrics": {"maskConfidence": float(min(sm.mean(), om.mean()))},
                "warnings": ["low_mask_confidence"], "debugOverlayPng": None}
    sp, op = _axis_period(sg, sm), _axis_period(og, om)
    if sp is None or op is None:
        return {"check": "pattern_fidelity", "status": "unavailable", "score": None,
                "metrics": {"patternConfidence": 0.0}, "warnings": ["pattern_unmeasurable"],
                "debugOverlayPng": None}
    relative = abs(op[2] - sp[2]) / max(sp[2], 1.0)
    source_hough = _hough_orientation(sg, sm)
    output_hough = _hough_orientation(og, om)
    direction_match = sp[1] == op[1]
    if source_hough and output_hough:
        if kind in {"check", "plaid", "gingham", "tartan"}:
            direction_match = (
                abs(source_hough["verticalRatio"] - output_hough["verticalRatio"]) <= 0.35
                and abs(source_hough["horizontalRatio"] - output_hough["horizontalRatio"]) <= 0.35
            )
        elif min(source_hough["confidence"], output_hough["confidence"]) >= 0.55:
            direction_match = source_hough["dominant"] == output_hough["dominant"]
    source_width_ratio = (sp[3] / sp[2]) if sp[3] is not None else None
    output_width_ratio = (op[3] / op[2]) if op[3] is not None else None
    width_relative = (
        abs(output_width_ratio - source_width_ratio) / max(source_width_ratio, 1e-6)
        if source_width_ratio is not None and output_width_ratio is not None else None)
    width_match = width_relative is None or width_relative <= 0.35
    confidence = min(sp[0], op[0])
    status = "pass" if direction_match and width_match and relative <= period_tolerance else "fail"
    hough_confidence = min(
        (source_hough or {}).get("confidence", 1.0),
        (output_hough or {}).get("confidence", 1.0))
    width_factor = 1.0 if width_relative is None else 1.0 - min(1.0, width_relative)
    return {
        "check": "pattern_fidelity", "status": status,
        "score": max(0.0, min(1.0, confidence * hough_confidence * width_factor
                              * (1.0 - min(1.0, relative)))),
        "metrics": {"sourceDirection": sp[1], "outputDirection": op[1],
                    "sourcePeriodPx": sp[2], "outputPeriodPx": op[2],
                    "periodRelativeError": relative, "patternConfidence": confidence,
                    "directionMatch": direction_match,
                    "sourceStripeWidthRatio": source_width_ratio,
                    "outputStripeWidthRatio": output_width_ratio,
                    "stripeWidthRelativeError": width_relative,
                    "stripeWidthMatch": width_match,
                    "sourceFftPeriodPx": sp[4], "outputFftPeriodPx": op[4],
                    "sourceFftConfidence": sp[5], "outputFftConfidence": op[5],
                    "sourceHough": source_hough, "outputHough": output_hough},
        "failedRegions": [] if status == "pass" else [{"region": "garment", "reason": "pattern_drift"}],
        "regenerationInstructions": [] if status == "pass" else ["preserve_source_pattern_period_and_direction"],
        "debugOverlayPng": _overlay(output_bgr, op[1], op[2]),
    }
