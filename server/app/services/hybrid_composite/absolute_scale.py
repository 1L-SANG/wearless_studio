"""**절대 반복 스케일** 독립 검증 — 후보 스칼라를 읽지 않는다.

왜 별도인가
-----------
결정론 QC 는 기대 주기를 렌더를 만든 그 스칼라(`target_period_px`)에서 가져온다. 그래서
"렌더러가 준 스칼라를 그대로 썼는가" 는 확인하지만 "그 스칼라가 옳은 하모닉인가" 는 묻지
못한다. 스칼라가 k 배 틀리면 결과는 **절대 스케일만 배수로 틀린** 그림이 되고, 색 순서·
선 폭 비·팔레트는 전부 보존되므로 나머지 축의 검사에도 걸리지 않는다.

무엇을 재는가
-------------
반복 **횟수**를 픽셀에서 두 번 센다 — 원본 ROI 에서 한 번, 출력 ROI 에서 한 번. 어느
쪽도 후보 스칼라를 쓰지 않는다. 투영은 반복 횟수를 보존해야 하므로(그것이
`target_period_px = target_span / repeats` 의 정의다) 두 수는 같아야 한다. 스칼라가 k 배
틀리면 출력 쪽만 1/k 배가 되어 갈라진다.

무엇을 재지 않는가
------------------
색·폭 비·순서. 그것들은 하모닉 오류에 불변이므로 여기서 볼 이유가 없고, 이미 다른 검사가
본다. 이 모듈의 주장은 단 하나다:

    SOURCE_REPEAT_COUNT_PRESERVED_IN_OUTPUT

한계도 분명히 해 둔다. 이것은 **원본 ROI 가 진짜 원단을 담고 있을 때만** 의미가 있다.
원본이 이미 잘못 잘렸다면 두 수가 함께 틀려 통과할 수 있다 — 그 문제는 ROI 계약이 풀 몫이지
이 가드가 풀 몫이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stripe_model import PERIOD_UNIT_UNKNOWN, measure_axes

ABSOLUTE_SCALE_VERSION = "absolute_repeat_scale_v1"

#: 반복 횟수가 이만큼 어긋나면 하모닉이 다르다고 본다. 하모닉 오류는 **배수**로 나타나므로
#: (0.5×·2×·3×) 가장 가까운 오류도 비율 2.0 이다 — 0.35 는 그 절반에도 한참 못 미치는,
#: 측정 잡음만 흡수하는 폭이다. 픽스처에 맞춘 값이 아니라 하모닉 격자 사이의 간격에서 온다.
MAX_REPEAT_RATIO_DEV = 0.35

#: 자기상관이 이보다 약하면 "줄이 있다" 고 말할 수 없다. 없는 신호에 숫자를 붙이지 않는다.
MIN_AXIS_STRENGTH = 0.25


@dataclass(frozen=True)
class AbsoluteScaleVerdict:
    computable: bool
    ok: bool | None = None
    source_repeats: float | None = None
    output_repeats: float | None = None
    harmonic_ratio: float | None = None
    reason: str | None = None
    #: 반복 횟수는 blind autocorrelation 으로 세므로 그 수가 full repeat 을 센 것인지
    #: band width 를 센 것인지 **이 측정만으로는 모른다**. 그래도 판정은 성립한다 —
    #: 양쪽을 같은 눈으로 세고 **비율**만 보기 때문에 단위가 약분된다. 단위를 모르는
    #: 것과 비교가 무의미한 것은 다르고, 그 구분을 숫자 옆에 적어 둔다.
    period_unit: str = PERIOD_UNIT_UNKNOWN
    version: str = ABSOLUTE_SCALE_VERSION


def _repeats(roi_bgr: np.ndarray, axis: str) -> tuple[float | None, float, bool]:
    """ROI 안에 반복이 몇 번 들어 있는가. → (repeats | None, strength, consensus)

    측정은 `measure_axes` 로 한다 — extractor 와 결정론 QC 가 쓰는 **같은 눈**이다.
    새 추정기를 만들지 않는 이유가 두 가지 있다: (1) 실사진에서 원단 **직조**가 자기상관을
    이기는 사례가 있어 그쪽이 이미 FFT 합의로 막고 있고, (2) 눈이 다르면 이 가드의 불일치가
    "스케일이 틀렸다" 인지 "측정기가 다르다" 인지 구분되지 않는다.

    후보가 알려 준 주기는 어디에서도 쓰지 않는다 — 그것이 이 가드의 존재 이유다.
    """
    arr = np.asarray(roi_bgr)
    if arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
        return None, 0.0, False
    if min(arr.shape[:2]) < 16:
        return None, 0.0, False
    try:
        measured = measure_axes(arr)
    except Exception:                     # noqa: BLE001 — 잴 수 없는 것은 통과가 아니다
        return None, 0.0, False
    entry = measured.get(axis)
    period = getattr(entry, "period_px", None)
    strength = float(getattr(entry, "strength", 0.0) or 0.0)
    consensus = bool(measured.get(f"{axis}_valid"))
    # `measure_axes` 의 축 어휘: "vertical" = 색이 x 를 따라 변하는 세로 줄 → span 은 폭.
    span = arr.shape[1] if axis == "vertical" else arr.shape[0]
    if not period or period <= 0 or not np.isfinite(float(period)):
        return None, strength, consensus
    return float(span) / float(period), strength, consensus


def verify_absolute_repeat_scale(*, source_roi_bgr: np.ndarray,
                                 output_roi_bgr: np.ndarray,
                                 axis: str = "horizontal") -> AbsoluteScaleVerdict:
    """원본과 출력의 **반복 횟수**를 각각 세어 비교한다.

    통과/불통과를 말하되, 그 판정은 후보가 준 어떤 숫자에도 기대지 않는다. 셀 수 없으면
    `computable=False` 로 정직하게 말한다 — 재지 못한 것을 통과로 읽지 않는다.
    """
    src_repeats, src_strength, src_ok = _repeats(source_roi_bgr, axis)
    out_repeats, out_strength, out_ok = _repeats(output_roi_bgr, axis)

    if src_repeats is None or out_repeats is None:
        return AbsoluteScaleVerdict(False, None, src_repeats, out_repeats, None,
                                    "repeat_count_unmeasurable")
    if src_strength < MIN_AXIS_STRENGTH or out_strength < MIN_AXIS_STRENGTH:
        return AbsoluteScaleVerdict(False, None, round(src_repeats, 3),
                                    round(out_repeats, 3), None, "axis_too_weak")
    if not (src_ok and out_ok):
        # autocorr 과 FFT 가 합의하지 못한 축 — 직조/하모닉 혼동 가능. 판정하지 않는다.
        return AbsoluteScaleVerdict(False, None, round(src_repeats, 3),
                                    round(out_repeats, 3), None, "axis_consensus_absent")
    if src_repeats <= 0:
        return AbsoluteScaleVerdict(False, None, src_repeats, out_repeats, None,
                                    "source_repeats_degenerate")

    ratio = out_repeats / src_repeats
    return AbsoluteScaleVerdict(
        computable=True,
        ok=bool(abs(ratio - 1.0) <= MAX_REPEAT_RATIO_DEV),
        source_repeats=round(src_repeats, 3),
        output_repeats=round(out_repeats, 3),
        harmonic_ratio=round(ratio, 4),
        reason=None if abs(ratio - 1.0) <= MAX_REPEAT_RATIO_DEV else "repeat_count_mismatch")
