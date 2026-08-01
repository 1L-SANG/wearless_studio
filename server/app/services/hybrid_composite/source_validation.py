"""Stage 1 — 원본(Front/Detail) 입력 gate. 판독 불가능한 원본으로는 합성을 시작하지 않는다.

fail closed 가 계약이다: 흐리거나, 과노출이거나, 반복이 모자라거나, 원근이 과하면
`reference_insufficient` 로 끝낸다. "일단 해보고 나쁘면 버린다"는 생성 모델의 규율이고,
결정론 경로의 규율은 "증명 못 하면 시작하지 않는다"다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .stripe_model import MIN_PERIODS_IN_ROI, measure_axes
from .types import CompositeFailure

MIN_ROI_SIDE_PX = 800          # 유효 원단 ROI 최소 변 (계획 P1 임계의 P0-적용판)
MIN_LAPLACIAN_VAR = 60.0       # 선명도 하한 — live preflight 실측(front 302, detail 2192) 대비 보수적
# 과노출 gate 는 **하드클립**(전 채널 포화)만 센다. 흰 바탕 셔츠는 정상 촬영에서도 250+에
# 앉으므로, 소프트 하이라이트를 세면 흰 바탕 상품 전부가 오탐된다(fixture 실측 30%).
# 패턴 정보를 실제로 파괴하는 것은 넓은 영역의 전채널 포화다.
MAX_CLIPPED_FRAC = 0.35
_CLIP_LEVEL = 254
CENTER_CROP_FRAC = 0.60        # 원단 ROI 근사 — 중앙 crop (flat-lay/정면 상품 사진 가정)


@dataclass(frozen=True)
class SourceValidation:
    roi: tuple                  # (x0, y0, x1, y1)
    laplacian_var: float
    clipped_frac: float
    n_periods_in_roi: int
    axis: str
    metrics: dict


def center_fabric_roi(image_bgr: np.ndarray, frac: float = CENTER_CROP_FRAC) -> tuple:
    h, w = image_bgr.shape[:2]
    cw, ch = int(w * frac), int(h * frac)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return (x0, y0, x0 + cw, y0 + ch)


def validate_stripe_source(image_bgr: np.ndarray, *, roi: tuple | None = None,
                           ) -> SourceValidation | CompositeFailure:
    """Detail(또는 Front) 원단 ROI 의 입력 적격성 판정."""
    if image_bgr is None or image_bgr.size == 0:
        return CompositeFailure("reference_insufficient", "이미지 없음")
    roi = roi or center_fabric_roi(image_bgr)
    x0, y0, x1, y1 = roi
    crop = image_bgr[y0:y1, x0:x1]
    if min(crop.shape[:2]) < MIN_ROI_SIDE_PX:
        return CompositeFailure(
            "reference_insufficient",
            f"유효 ROI {crop.shape[1]}x{crop.shape[0]} < {MIN_ROI_SIDE_PX}px",
            {"roi": list(roi)})
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap < MIN_LAPLACIAN_VAR:
        return CompositeFailure("reference_insufficient",
                                f"선명도 미달 (laplacian {lap:.0f} < {MIN_LAPLACIAN_VAR})",
                                {"laplacian_var": lap})
    clipped = float((crop.min(axis=-1) >= _CLIP_LEVEL).mean())
    if clipped > MAX_CLIPPED_FRAC:
        return CompositeFailure("reference_insufficient",
                                f"하드클립 {clipped:.2%} > {MAX_CLIPPED_FRAC:.0%}",
                                {"clipped_frac": clipped})
    axes = measure_axes(crop)
    primary_name = ("horizontal" if axes["horizontal"].strength >= axes["vertical"].strength
                    else "vertical")
    primary = axes[primary_name]
    if primary.period_px is None:
        return CompositeFailure("reference_insufficient", "반복 신호 없음")
    span = crop.shape[0] if primary_name == "horizontal" else crop.shape[1]
    n_periods = int(span // primary.period_px)
    if n_periods < MIN_PERIODS_IN_ROI:
        return CompositeFailure(
            "reference_insufficient",
            f"ROI 내 반복 {n_periods}회 < {MIN_PERIODS_IN_ROI}",
            {"n_periods": n_periods, "period_px": primary.period_px})
    return SourceValidation(
        roi=roi, laplacian_var=lap, clipped_frac=clipped,
        n_periods_in_roi=n_periods, axis=primary_name,
        metrics={"periodicity_strength": round(primary.strength, 3),
                 "period_px": round(float(primary.period_px), 2)})
