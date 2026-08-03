"""Phase-7 색상/패턴 검사를 실제 이미지 바이트에 연결하는 runner."""

from __future__ import annotations

import cv2
import numpy as np

from .color_fidelity_qc import compare_color
from .garment_roi import foreground_mask, mannequin_difference_mask
from .pattern_fidelity_qc import compare_pattern


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def run(*, source_bytes: bytes, base_bytes: bytes, output_bytes: bytes,
        pattern_type: str = "unknown", include_debug_bytes: bool = False) -> list[dict]:
    source, base, output = map(_decode, (source_bytes, base_bytes, output_bytes))
    if source is None or base is None or output is None:
        return [
            {"check": "color_fidelity", "status": "unavailable", "score": None,
             "warnings": ["image_decode_failed"]},
            {"check": "pattern_fidelity", "status": "unavailable", "score": None,
             "warnings": ["image_decode_failed"]},
        ]
    source_mask, source_conf = foreground_mask(source)
    output_mask, output_conf = mannequin_difference_mask(base, output)
    if min(source_conf, output_conf) < 0.25:
        color = {"check": "color_fidelity", "status": "unavailable", "score": None,
                 "metrics": {"sourceMaskConfidence": source_conf,
                             "outputMaskConfidence": output_conf},
                 "warnings": ["low_mask_confidence"]}
        pattern = {"check": "pattern_fidelity", "status": "unavailable", "score": None,
                   "metrics": {"sourceMaskConfidence": source_conf,
                               "outputMaskConfidence": output_conf},
                   "warnings": ["low_mask_confidence"]}
        return [color, pattern]
    color = compare_color(source, output, source_mask=source_mask, output_mask=output_mask)
    pattern = compare_pattern(
        source, output, pattern_type=pattern_type,
        source_mask=source_mask, output_mask=output_mask)
    overlay = pattern.pop("debugOverlayPng", None)
    if overlay:
        import hashlib
        pattern["debugOverlaySha256"] = hashlib.sha256(overlay).hexdigest()
        if include_debug_bytes:
            pattern["_debugOverlayPng"] = overlay
    color.setdefault("metrics", {}).update(
        {"sourceMaskConfidence": source_conf, "outputMaskConfidence": output_conf})
    pattern.setdefault("metrics", {}).update(
        {"sourceMaskConfidence": source_conf, "outputMaskConfidence": output_conf})
    return [color, pattern]
