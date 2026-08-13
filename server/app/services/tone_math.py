"""톤 조정 수식의 파이썬 판 — **시각 QA 전용**이며 요청 경로에서는 쓰이지 않는다.

실제 렌더는 브라우저(`src/lib/toneRender.js`)가 전담한다. 미리보기와 최종 결과가 같은 코드를
쓰게 하려면 그래야 한다. 그런데 16건 코퍼스를 눈으로 검수하려면 같은 수식이 파이썬에도
있어야 하고, 그 순간 "미리보기 알고리즘 A / 최종 알고리즘 B" 함정이 열린다.

그래서 이 모듈은 존재하되 **증명된 채로만** 존재한다:
`tests/test_tone_renderer_equivalence.py` 가 고정 픽셀 집합에서 JS 출력과 한 바이트라도
다르면 실패한다. 한쪽만 고치면 그 테스트가 먼저 깨진다.
"""

from __future__ import annotations

import numpy as np

SATURATION_RANGE = 100
EXPOSURE_RANGE = 100
_SATURATION_SPAN = 1.0
_EXPOSURE_SPAN_EV = 1.0

#: Rec.709. 채도를 이 축 둘레로만 움직여야 색상이 보존된다.
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float64)

_SRGB_TO_LINEAR = np.array(
    [(c / 12.92) if (c := i / 255) <= 0.04045 else (((c + 0.055) / 1.055) ** 2.4)
     for i in range(256)], np.float64)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def clamp(saturation, exposure) -> tuple[int, int]:
    def one(v, limit):
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            return 0
        return max(-limit, min(limit, n))
    return one(saturation, SATURATION_RANGE), one(exposure, EXPOSURE_RANGE)


def params(saturation, exposure) -> tuple[float, float]:
    s, e = clamp(saturation, exposure)
    return (1.0 + (s / SATURATION_RANGE) * _SATURATION_SPAN,
            (e / EXPOSURE_RANGE) * _EXPOSURE_SPAN_EV)


def _gamut_safe(rgb: np.ndarray, y: np.ndarray) -> np.ndarray:
    """색역 밖으로 나간 색은 채널을 자르지 않고 **채도만** 되돌린다.

    채널 클립은 가장 진한 채널만 깎아 색상을 돌려버린다(빨강 → 주황). 휘도를 축으로 잡고
    필요한 만큼만 중립 쪽으로 당기면 밝기도 색상도 유지된다.
    """
    lo = rgb.min(axis=-1)
    hi = rgb.max(axis=-1)
    t = np.ones_like(y)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(lo < 0, np.minimum(t, np.divide(y, y - lo, out=np.ones_like(y),
                                                     where=(y - lo) != 0)), t)
        t = np.where(hi > 1, np.minimum(t, np.divide(1 - y, hi - y, out=np.ones_like(y),
                                                     where=(hi - y) != 0)), t)
    t = np.where(np.isfinite(t) & (t >= 0), t, 0.0)
    return y[..., None] + (rgb - y[..., None]) * t[..., None]


def _round_half_even(x: np.ndarray) -> np.ndarray:
    """`Uint8ClampedArray` 대입과 같은 반올림. JS 는 round-half-to-even 이다."""
    return np.clip(np.rint(x), 0, 255).astype(np.uint8)


def apply_tone(src_rgba: np.ndarray, mask_alpha: np.ndarray, saturation, exposure) -> np.ndarray:
    """(N,4) uint8 + (N,) uint8 → (N,4) uint8. 마스크 밖은 원본 바이트 그대로."""
    factor, ev = params(saturation, exposure)
    gain = 2.0 ** ev
    out = src_rgba.copy()
    if factor == 1.0 and gain == 1.0:
        return out

    touched = mask_alpha > 0
    if not touched.any():
        return out

    lin = _SRGB_TO_LINEAR[src_rgba[touched, :3]]
    y = lin @ _LUMA
    adj = y[..., None] + factor * (lin - y[..., None])
    adj = _gamut_safe(adj, y) * gain
    srgb = _linear_to_srgb(np.clip(adj, 0.0, 1.0)) * 255.0

    a = (mask_alpha[touched] / 255.0)[..., None]
    blended = np.where(a == 1.0, srgb, src_rgba[touched, :3] * (1 - a) + srgb * a)
    out[touched, :3] = _round_half_even(blended)
    return out


def apply_tone_image(rgb: np.ndarray, mask: np.ndarray, saturation, exposure) -> np.ndarray:
    """(H,W,3) RGB + (H,W) 0..255 마스크 → 조정된 RGB. QA 시트용 편의 래퍼."""
    h, w = mask.shape
    rgba = np.dstack([rgb, np.full((h, w, 1), 255, np.uint8)]).reshape(-1, 4)
    out = apply_tone(rgba, mask.reshape(-1), saturation, exposure)
    return out.reshape(h, w, 4)[..., :3]
