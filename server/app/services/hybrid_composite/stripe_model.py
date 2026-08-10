"""Stage 2 — 원단 ROI 에서 스트라이프 모델 추출 (OpenCV/NumPy 만, 결정론).

설계 원칙:
  · 단순 평균색이 아니라 **한 주기의 Lab albedo 신호**를 보존한다. 파랑/갈색 잔줄이 회색
    단색으로 뭉개졌던 실패는 평균화가 원인이었다 — 신호를 남기면 평균화가 불가능하다.
  · 조명은 곱셈 성분으로 보고 L 채널만 저주파 정규화한다. a/b(chroma)는 건드리지 않는다 —
    조명 정규화가 색 정체성을 바꾸면 안 된다.
  · 자동 판정이 흔들리면 **fail closed** — 낮은 신뢰도로 그럴듯한 합성을 내보내는 것이
    가장 나쁜 결과다(틀린 패턴이 정상처럼 보인다).

주기 산출은 autocorrelation 과 FFT 의 **합의값**만 신뢰한다. 두 측정이 15% 이상 어긋나면
그 ROI 는 규칙 스트라이프가 아니거나 측정 불능이다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .color import bgr_to_lab, ciede2000
from .types import CompositeFailure, StripeModel

# ── 임계 (synthetic fixture 셋으로 검증 — evidence/fixture-gates.json) ─────────────
MIN_PERIODICITY = 0.25       # 주 축 autocorr peak 최소 강도
CHECK_AXIS_RATIO = 0.60      # 부 축/주 축 강도 비가 이 이상이고 부 축에도 주기가 있으면 체크로 판정
PERIOD_CONSENSUS_TOL = 0.15  # autocorr vs FFT 주기 상대 오차 허용

#: 같은 ROI 안의 패치들이 "같은 주기"로 묶이는 상대 오차. 값은 스캔이 원래 쓰던 0.15
#: 그대로이고, 이름만 붙였다 — 바깥(멀티 ROI) 합의가 같은 기준을 **재사용**해야지
#: 두 번째 사본을 만들면 안 되기 때문이다.
PATCH_PERIOD_AGREEMENT_TOL = 0.15
MIN_PERIODS_IN_ROI = 8       # ROI 안 최소 반복 수 (미달 = 근거 부족)
MIN_PERIOD_PX = 4.0          # 이보다 짧은 주기는 JPEG/직조 노이즈로 간주
RUN_EDGE_DELTA_E = 5.0       # run 경계로 인정할 윈도우 간 ΔE00
RUN_MERGE_DELTA_E = 4.0      # 인접 run 병합 임계
MIN_RUN_WIDTH_FRAC = 0.02    # period 대비 이보다 좁은 run 은 노이즈로 흡수
PHASE_STRIPS = 12            # 기울어진 줄(원근·회전)의 위상 정렬용 strip 수. strip lag 는 개별
                             # 추정치가 아니라 **선형 적합**을 쓴다 — 원근의 위상 드리프트는 strip
                             # 위치의 1차 함수이고, 개별 lag 의 잡음이 고스트 에지를 만들었다
                             # (24-strip 원시 lag 실측: S2 가 2 run → 4 run 으로 갈라짐)


def _fold_samples(period: float) -> int:
    """주기당 표본 수를 주기 크기에 맞춘다 — 고정 256 은 12px 잔줄에서 에지를 21표본에
    펴 발라 인접-표본 ΔE 가 임계 밑으로 희석됐다(2026-08-01 스파이크 실측)."""
    return int(np.clip(round(period * 6), 64, 768))


@dataclass(frozen=True)
class AxisPeriodicity:
    period_px: float | None
    strength: float


#: ΔE 신호가 "무너졌다"고 볼 상대 기준(= std/mean). 합성 픽스처에서 무너진 쪽은 ≤0.014,
#: 성한 쪽은 ≥0.177 로 12배 넘게 벌어져 있어 이 값은 빈 구간 한가운데 있다.
#:
#: 실사진 보정(2026-08-10, `ab_out` 826 크롭 / 무늬 있는 축 판독 1384건): 퇴화 판정
#: **0건**, 관측된 최소 비율 0.253, p1 0.374. 즉 이 분기는 실제 코퍼스에서 한 번도
#: 발화하지 않는다 — 실사진은 음영·잡음이 대칭을 깨서 중앙색이 두 색 사이에 정확히
#: 놓이지 않기 때문이다. 그래서 이 수정의 실운영 회귀 위험은 사실상 0 이고, 동시에
#: 편익도 "아직 안 만난 실패를 미리 막는 것" 이지 지금 뭔가를 고치는 것이 아니다.
#: 문턱을 올리려면 저 최소값 0.253 이 상한이라는 점을 먼저 보라.
_DEGENERATE_SIGNAL_RATIO = 0.15

#: `None` 이 "ΔE 를 쓰라"는 **뜻 있는** basis 값이라서 기본값과 구분할 표식이 따로 필요하다.
_UNSET: object = object()

# ── 주기 숫자의 **단위** 어휘 ───────────────────────────────────────────────
# 한 숫자가 "선 하나의 폭" 과 "색이 한 바퀴 도는 거리" 를 겸하면, 대칭 무늬에서 두 뜻이
# 정확히 2배 차이로 알리아싱되어 소비자가 어느 쪽인지 알 방법이 없다. 그래서 주기를
# 내보내는 자리는 그 수가 **무엇을 재는 수인지** 함께 말한다.
#: 색이 한 바퀴 도는 거리. `StripeModel.period_px` 의 동결된 뜻이다.
PERIOD_UNIT_FULL_COLOR_REPEAT = "FULL_COLOR_REPEAT"
#: 선 하나의 폭. full repeat 이 아니다.
PERIOD_UNIT_BAND_WIDTH = "BAND_WIDTH"
#: 둘 중 어느 쪽인지 이 측정만으로는 알 수 없다 — blind autocorrelation 이 그렇다.
#: 자기상관은 자기유사성이 최대인 lag 을 잡을 뿐이라, 대칭 무늬에서는 band width 에
#: 걸려도 똑같이 높은 점수가 난다. 모르는 것을 아는 척하지 않기 위한 값이다.
PERIOD_UNIT_UNKNOWN = "UNKNOWN"


def _signal_basis(lab_prof: np.ndarray) -> np.ndarray | None:
    """이 프로파일이 쓸 표현을 정한다 → 부호 사영 축 (N,) 또는 `None`(=ΔE 그대로).

    **두 프로파일을 비교하는 자리에서는 이 값을 한 번만 구해 양쪽에 함께 넘겨야 한다.**
    각자 정하게 두면 한쪽은 ΔE, 다른 쪽은 부호 사영이 되어 단위가 어긋난다 — 그러면
    상관이 0 근처로 무너져서, 신호를 고쳐 놓고도 정답 주기를 못 고른다(2026-08-10 실측:
    보간 램프 때문에 접힌 쪽만 비율 0.166 로 문턱을 넘어 갈라졌고 정답 24px 점수가
    0.0004 였다). 표현 선택은 **비교 단위**의 성질이지 프로파일 하나의 성질이 아니다.
    """
    med = np.median(lab_prof, axis=0)
    dev = lab_prof - med
    sig = np.sqrt((dev ** 2).sum(axis=-1))
    if sig.std() >= _DEGENERATE_SIGNAL_RATIO * sig.mean():
        return None
    try:
        _u, _s, vt = np.linalg.svd(dev, full_matrices=False)
    except np.linalg.LinAlgError:                     # 수렴 실패 = 방향 없음
        return None
    axis = np.asarray(vt[0], dtype=np.float64)
    if axis[int(np.argmax(np.abs(axis)))] < 0:        # SVD 부호 모호성 제거 규약
        axis = -axis
    return axis


def _pattern_signal(lab_prof: np.ndarray, basis=_UNSET) -> np.ndarray:
    """(N,3) Lab 프로파일 → (N,) 패턴 신호 = 중앙색으로부터의 ΔE76.

    주기·정렬 신호를 L 만으로 잡으면 **파스텔 스트라이프**(흰 바탕 + 연파랑/연베이지 —
    L 은 거의 같고 정체성이 chroma 에 있는)에서 파랑≠베이지 구분이 사라져, autocorr 이
    그룹 주기 대신 내부 선 간격에 잠긴다(2026-08-01 실사진 실측: 10.7px vs 실제 ~38px).
    ΔE 신호는 L·chroma 를 함께 보므로 색이 다른 선은 다른 값이 된다.

    ΔE 가 **무너지는 경우** — 대칭 이봉 분포
    ------------------------------------------
    ΔE 는 거리이므로 부호가 없다. 두 색이 **같은 폭**이면 중앙색이 정확히 두 색 사이에
    놓여 둘 다 등거리가 되고, 신호는 상수가 된다(2026-08-10 실측: std 0.000). 그러면
    남는 구조는 밝은선-어두운선 **쌍**뿐이라 주기가 2배로 잡힌다 — σ=1 잡음만 있어도
    24px 무늬를 47~49px 로, 그것도 `valid=True` 로 보고했다(autocorr 과 FFT 가 **함께**
    틀린 값에 합의하므로 consensus 검사가 잡지 못한다).

    2색 등폭은 가장 흔한 줄무늬(브르통)이고, ΔE 를 도입하게 만든 파스텔 조합조차 폭이
    같으면 똑같이 무너진다. 그래서 무너진 경우에만 **부호 있는** 사영으로 갈아탄다:
    성한 프로파일은 지금까지와 비트 단위로 동일하고, 죽어 있던 프로파일만 신호를 얻는다.
    무늬가 아예 없는 원단은 편차 자체가 0 이라 이 분기에서도 0 이 나온다 — 없는 주기를
    지어내지 않는다.

    이것은 주기에 가하는 하모닉 보정이 아니라 **측정 신호의 퇴화를 없애는 것**이다.

    `basis` 를 주면 표현 선택을 넘겨받는다 — 비교 상대와 단위를 맞추기 위한 것이다.
    자세한 이유는 `_signal_basis` 참조. 안 주면 이 프로파일 혼자 정한다(단독 측정용).
    """
    med = np.median(lab_prof, axis=0)
    dev = lab_prof - med
    if basis is _UNSET:
        basis = _signal_basis(lab_prof)
    if basis is not None:
        return dev @ basis
    return np.sqrt((dev ** 2).sum(axis=-1))


def _detrended_profile(channel: np.ndarray, axis: int) -> np.ndarray:
    """축 방향 평균 프로파일 - 저주파(조명) 성분. axis=1 → 행 프로파일(수평 줄)."""
    prof = channel.mean(axis=axis).astype(np.float64)
    sigma = max(len(prof) / 8.0, 8.0)
    low = cv2.GaussianBlur(prof.reshape(-1, 1), (0, 0), sigmaX=sigma).ravel()
    return prof - low


def _autocorr_period(prof: np.ndarray, min_lag: int | None = None) -> AxisPeriodicity:
    """정규화 autocorrelation 의 fundamental peak. 하모닉이 있으면 가장 짧은 유효 lag 채택.

    `min_lag` 는 스케일 인지 하한 — 3000px 실사진에서 원단 **직조**(threads)가 5px 주기로
    autocorr 0.98 을 만든다(2026-08-01 실측). 의류 스트라이프는 span 대비 훨씬 크므로
    span/256 미만 lag 는 마이크로 텍스처로 보고 후보에서 제외한다.
    """
    n = len(prof)
    if n < 32:
        return AxisPeriodicity(None, 0.0)
    p = prof - prof.mean()
    denom = float((p * p).sum())
    if denom < 1e-9:
        return AxisPeriodicity(None, 0.0)
    ac = np.correlate(p, p, mode="full")[n - 1:] / denom
    lo = max(4, int(MIN_PERIOD_PX), int(min_lag or 0))
    hi = n // 2
    if hi <= lo:
        return AxisPeriodicity(None, 0.0)
    seg = ac[lo:hi]
    peaks = [i for i in range(1, len(seg) - 1)
             if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.0]
    if not peaks:
        return AxisPeriodicity(None, 0.0)
    best = max(seg[i] for i in peaks)
    # fundamental = 최대 peak 의 80% 이상인 가장 짧은 lag — 하모닉(2P, 3P)이 최대일 때 복원
    fundamental = next(i for i in peaks if seg[i] >= 0.8 * best)
    lag = lo + fundamental
    # 포물선 보간으로 subpixel 정밀화
    if 1 <= lag < len(ac) - 1:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom2 = (y0 - 2 * y1 + y2)
        if abs(denom2) > 1e-12:
            lag = lag + 0.5 * (y0 - y2) / denom2
    return AxisPeriodicity(float(lag), float(seg[fundamental]))


def _fft_period(prof: np.ndarray) -> float | None:
    """FFT 지배 주파수의 주기. DC 와 초저주파(전체 길이의 1/3 이상 주기)는 제외."""
    n = len(prof)
    p = prof - prof.mean()
    mag = np.abs(np.fft.rfft(p))
    k_min = max(3, int(np.ceil(n / (n / 3.0))))  # 주기 < n/3 인 성분만
    if len(mag) <= k_min + 1:
        return None
    k = int(np.argmax(mag[k_min:]) + k_min)
    if mag[k] < 1e-9:
        return None
    # 인접 bin 포물선 보간
    if 1 <= k < len(mag) - 1:
        y0, y1, y2 = mag[k - 1], mag[k], mag[k + 1]
        denom = (y0 - 2 * y1 + y2)
        if abs(denom) > 1e-12:
            k = k + 0.5 * (y0 - y2) / denom
    return float(n / k)


def measure_axes(roi_bgr: np.ndarray) -> dict:
    """양 축의 주기성 측정 — extractor 와 deterministic QC 가 같은 눈으로 본다.

    축별로 autocorr 과 FFT 를 함께 재고 **합의 여부**를 붙인다. 실사진에서 직조 텍스처가
    autocorr 강도만으로는 의류 스트라이프를 이기는 사례가 있어(5.1px vs 실제 36px),
    두 측정이 정수배(≤8)로 맞는 축만 신뢰 후보가 된다.
    """
    lab = bgr_to_lab(roi_bgr)
    out = {}
    for name, axis in (("horizontal", 1), ("vertical", 0)):
        span = lab.shape[0] if axis == 1 else lab.shape[1]
        # 의류 스케일 하한 — 직조(threads)는 min_lag 를 올려도 **하모닉**(20px 에서도
        # autocorr ~0.9)과 FFT 정수비 합의로 뚫는다(2026-08-01 실사진 실측). 결정적 판별은
        # "FFT 지배 주기 자체가 의류 스케일인가"다: 직조는 지배 에너지가 5px 에 있고,
        # 의류 스트라이프/체크는 span 의 1% 이상에 있다.
        min_p = max(int(MIN_PERIOD_PX), span // 128)
        prof = lab.mean(axis=axis)
        sig = _pattern_signal(prof)
        low = cv2.GaussianBlur(sig.reshape(-1, 1), (0, 0), sigmaX=max(len(sig) / 8.0, 8.0)).ravel()
        det = sig - low
        ax = _autocorr_period(det, min_lag=min_p)
        p_fft = _fft_period(det)
        consensus = False
        if ax.period_px is not None and p_fft is not None:
            ratio = max(ax.period_px, p_fft) / max(min(ax.period_px, p_fft), 1e-9)
            nearest = max(1.0, round(ratio))
            consensus = nearest <= 8 and abs(ratio - nearest) / nearest <= PERIOD_CONSENSUS_TOL
        valid = bool(consensus and ax.period_px is not None
                     and p_fft is not None and p_fft >= min_p and ax.period_px >= min_p)
        out[name] = AxisPeriodicity(ax.period_px, ax.strength)
        out[f"{name}_fft"] = p_fft
        out[f"{name}_consensus"] = consensus
        out[f"{name}_valid"] = valid
    return out


def _fold_profile(lab_prof: np.ndarray, period: float, K: int) -> tuple[np.ndarray, float]:
    """(N,3) Lab 프로파일을 period 로 접어 (K,3) 중앙값 프로파일로.

    → (folded, fold_consistency). consistency = 1 - (주기간 MAD / 신호 진폭).
    """
    n = len(lab_prof)
    n_periods = int(n // period)
    grid = np.arange(K, dtype=np.float64) * (period / K)
    stacks = []
    for j in range(n_periods):
        pos = j * period + grid
        idx = np.clip(pos, 0, n - 1.000001)
        i0 = np.floor(idx).astype(int)
        frac = (idx - i0).reshape(-1, 1)
        stacks.append(lab_prof[i0] * (1 - frac) + lab_prof[np.minimum(i0 + 1, n - 1)] * frac)
    stack = np.stack(stacks)                       # (P, K, 3)
    # 주기별 원형 위상 정렬 — 원근 단축은 국소 주기를 ~2% 씩 바꿔서, 고정 주기로 접으면
    # 누적 위상 오차가 마지막 주기에서 0.5 주기를 넘는다(실측: 잔줄 run 붕괴의 진범).
    # 위상은 주기 모듈로 정의되므로 탐색은 **전체 원형**이어야 한다(부분 창은 누적 드리프트가
    # 창을 벗어나는 순간 무작위 정렬이 된다). FFT 원형 상호상관으로 K 개 shift 를 한 번에 본다.
    basis = _signal_basis(stack[0])            # 정렬 상대끼리 표현을 맞춘다
    ref_sig = _pattern_signal(stack[0], basis)
    ref = ref_sig - ref_sig.mean()
    ref_f = np.conj(np.fft.rfft(ref))
    for j in range(1, len(stack)):
        row_sig = _pattern_signal(stack[j], basis)
        row = row_sig - row_sig.mean()
        corr = np.fft.irfft(np.fft.rfft(row) * ref_f, n=K)
        best = int(np.argmax(corr))  # row 를 -best 만큼 roll 하면 ref 와 최대 상관
        if best:
            stack[j] = np.roll(stack[j], -best, axis=0)
    folded = np.median(stack, axis=0).astype(np.float32)
    amp = float(folded[:, 0].max() - folded[:, 0].min())
    mad = float(np.median(np.abs(stack[..., 0] - folded[None, :, 0])))
    consistency = 1.0 - min(1.0, mad / (amp + 1e-6))
    return folded, consistency


def _runs_from_folded(folded: np.ndarray) -> list[tuple[int, int]]:
    """cyclic run 분할 — **FWHM(바탕 대비 반치폭)** 기반. → [(start, length), ...]

    에지 검출은 램프 폭에, plateau 검출은 램프 유무에 민감했다(둘 다 실측 실패:
    합성 crisp 에지는 통과해도 **실제 직물의 부드러운 전이**에서는 plateau 방식이
    전체를 한 run 으로 붕괴시켰다 — 2026-08-01 실사진 n_colors=1). FWHM 은 둘 다에
    불변이다: 바탕색으로부터의 거리 d(k) 가 최대치의 절반을 넘는 구간이 줄(line)이고,
    반치 교차점이 곧 경계다. crisp 에지에선 정확히 그린 폭과 일치한다.
    """
    K = len(folded)
    # 바탕색 = 표본 중앙값에 가까운 하위 40% 의 중앙값 (줄이 40% 미만이라는 가정은
    # 두지 않는다 — 중앙값 자체가 최빈 색 쪽으로 끌리므로 넓은 run 이 바탕이 된다)
    med = np.median(folded, axis=0)
    d_to_med = np.sqrt(((folded - med) ** 2).sum(axis=-1))
    core = folded[d_to_med <= np.percentile(d_to_med, 40)]
    ground = np.median(core, axis=0) if len(core) else med
    d = ciede2000(folded, np.broadcast_to(ground, folded.shape))
    k_smooth = max(3, K // 128) | 1
    d = cv2.blur(d.reshape(-1, 1).astype(np.float32), (1, k_smooth)).ravel()

    peak = float(np.percentile(d, 98))
    if peak < RUN_EDGE_DELTA_E:
        return [(0, K)]  # 단색 — run 하나
    line_mask = d > 0.5 * peak

    # cyclic 세그먼트 추출 + 극소 세그먼트 흡수
    min_core = max(2, K // 100)
    labels = line_mask.copy()
    segs = []
    k = 0
    while k < K:
        j = k
        while j + 1 < K and labels[j + 1] == labels[k]:
            j += 1
        segs.append([k, j - k + 1, bool(labels[k])])
        k = j + 1
    if len(segs) > 1 and segs[0][2] == segs[-1][2]:
        segs[0][0] = segs[-1][0]
        segs[0][1] += segs[-1][1]
        segs.pop()
    segs = [s for s in segs if s[1] >= min_core] or [[0, K, False]]
    # 흡수 후 같은 라벨이 인접하면 병합
    merged = []
    for s in segs:
        if merged and merged[-1][2] == s[2]:
            merged[-1][1] += s[1]
        else:
            merged.append(list(s))
    if len(merged) > 1 and merged[0][2] == merged[-1][2]:
        merged[0][0] = merged[-1][0]
        merged[0][1] += merged[-1][1]
        merged.pop()
    if len(merged) <= 1:
        return [(0, K)]
    return [(s % K, ln) for s, ln, _is_line in merged]


def find_period_guided(
    roi_bgr: np.ndarray, model: StripeModel, *, collect: list | None = None,
) -> tuple[str, float, float] | None:
    """Front(의류 전체 사진)에서 **모델-guided** 로 줄 방향·주기를 찾는다.

    → (axis, period_px, corr) | None.

    blind 측정은 주름·광택·낮은 줌 배율에서 sub-line lag 에 잠기거나 신호를 잃는다
    (실측: 세로 잔줄 셔츠 Front 에서 anchor 실패). 우리는 이미 Detail 에서 패턴의
    **모양**을 알고 있으므로, Front 에서는 후보 주기마다 접어 모델 프로파일과의 원형
    상관이 최대가 되는 스케일만 찾으면 된다 — Stage 5 guided QC 와 같은 철학이다.

    `collect` 를 주면 이 **한 번의** 탐색에서 이미 계산되는 후보 표를 그대로 받아간다
    (주기·점수·autocorr peak 여부·기반 peak·배수). 승자만 돌려주고 나머지를 버리면
    "30 은 15 의 2배 후보였다"는 사실이 사라져 하모닉 오선택을 사후에 증명할 수 없다.
    관측 전용이다 — 후보 생성·점수식·선택 규칙은 그대로고 재계산도 하지 않는다.
    """
    lab = bgr_to_lab(roi_bgr)
    L = lab[..., 0]
    # 표현은 **모델**(이미 아는 기준 패턴)이 정하고 양쪽에 같이 쓴다. 접힌 ROI 가 따로
    # 정하게 두면 보간 램프 때문에 한쪽만 ΔE 로 남아 단위가 어긋난다(`_signal_basis` 참조).
    model_basis = _signal_basis(model.period_profile_lab)
    best = None
    for axis_name, axis in (("horizontal", 1), ("vertical", 0)):
        span = L.shape[0] if axis == 1 else L.shape[1]
        if span < 64:
            continue
        det = _detrended_profile(L, axis=axis)
        n = len(det)
        p = det - det.mean()
        denom = float((p * p).sum())
        if denom < 1e-9:
            continue
        ac = np.correlate(p, p, mode="full")[n - 1:] / denom
        lo, hi = 4, max(5, n // 6)
        cand = [i for i in range(max(lo, 1) + 1, hi - 1)
                if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > 0.2]
        # sub-line lag 대비 — 각 후보의 2·3배도 시도한다
        periods = sorted({round(float(c) * m, 1) for c in cand for m in (1, 2, 3)
                          if 4 <= c * m <= n // 6})
        # 어느 peak 의 몇 배였는지. 같은 주기를 여러 (peak, 배수) 가 만들 수 있어
        # 전부 남긴다 — 위에서 set 이 뭉갠 유일한 정보가 이것이다.
        origins: dict = {}
        for c in cand:
            for m in (1, 2, 3):
                if 4 <= c * m <= n // 6:
                    origins.setdefault(round(float(c) * m, 1), []).append(
                        {"basePeakPx": float(c), "multiplier": m})
        prof = lab.mean(axis=axis)
        L1 = prof[:, 0]
        low = cv2.GaussianBlur(L1.reshape(-1, 1), (0, 0), sigmaX=16.0).ravel()
        prof = prof.copy()
        prof[:, 0] = L1 * np.where(low > 1e-3, L1.mean() / np.maximum(low, 1e-3), 1.0)
        for period in periods:
            if int(n // period) < 4:
                continue
            K = _fold_samples(period)
            folded, _c = _fold_profile(prof, float(period), K)
            exp_src = model.period_profile_lab
            idx = np.linspace(0, len(exp_src), K, endpoint=False)
            i0 = np.floor(idx).astype(int) % len(exp_src)
            expected_sig = _pattern_signal(exp_src[i0], model_basis)
            folded_sig = _pattern_signal(folded, model_basis)
            a = folded_sig - folded_sig.mean()
            b = expected_sig - expected_sig.mean()
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na < 1e-6 or nb < 1e-6:
                continue
            corr = np.fft.irfft(np.fft.rfft(a) * np.conj(np.fft.rfft(b)), n=K)
            score = float(corr.max() / (na * nb))
            if collect is not None:
                origin = origins.get(period) or [{"basePeakPx": None, "multiplier": None}]
                collect.append({
                    "axis": axis_name,
                    "periodPx": float(period),
                    # 이 수는 모델의 **한 full repeat** 프로파일과 맞춰 본 결과다 —
                    # 맞다면 full repeat 을 재는 수라는 뜻이지, 맞다는 주장이 아니다.
                    "periodUnit": PERIOD_UNIT_FULL_COLOR_REPEAT,
                    "score": score,
                    # a multiplier candidate is not itself an autocorrelation peak;
                    # that distinction is what the 15 / 30 / 45 question turns on
                    "autocorrelationPeak": any(o["multiplier"] == 1 for o in origin),
                    "basePeakPx": origin[0]["basePeakPx"],
                    "multiplier": origin[0]["multiplier"],
                    "origins": origin,
                })
            if best is None or score > best[2]:
                best = (axis_name, float(period), score)
    if best is None or best[2] < 0.5:
        return None
    return best


def _merge_runs(folded: np.ndarray, runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """비슷한 색의 인접 run 병합 + 극소 run 을 이웃에 흡수 (cyclic)."""
    K = len(folded)

    def run_color(start, length):
        idx = (np.arange(start, start + length) % K)
        return np.median(folded[idx], axis=0)

    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i in range(len(runs)):
            j = (i + 1) % len(runs)
            ci, cj = run_color(*runs[i]), run_color(*runs[j])
            tiny = runs[i][1] < MIN_RUN_WIDTH_FRAC * K or runs[j][1] < MIN_RUN_WIDTH_FRAC * K
            if float(ciede2000(ci, cj)) < RUN_MERGE_DELTA_E or tiny:
                merged = (runs[i][0], runs[i][1] + runs[j][1])
                runs = [r for k, r in enumerate(runs) if k not in (i, j)]
                runs.insert(min(i, j) if j != 0 else len(runs), merged)
                changed = True
                break
    return runs


def _extract_axis_candidate(
    lab: np.ndarray, axis_name: str, global_period: float,
) -> dict | CompositeFailure:
    """한 축을 따라 실제 추출을 시도한다 — strip 별 fold → 위상 정렬 → run 분할.

    축 선택의 정본은 이 결과다: 진짜 스트라이프 축에서만 다중 run 색 구조가 나온다.
    직조 하모닉 축은 fold 에서 평균돼 단일 run 으로 붕괴한다(자기 탈락).
    """
    mean_axis = 1 if axis_name == "horizontal" else 0
    if mean_axis == 1:
        strips = np.array_split(lab, PHASE_STRIPS, axis=1)
    else:
        strips = np.array_split(lab.transpose(1, 0, 2), PHASE_STRIPS, axis=1)
    n = strips[0].shape[0]
    # 1단계 — strip 별 독립 주기 추정 → **strip 합의(중앙값)** 가 주기의 정본이다.
    # 전역 프로파일 AC 는 위상 드리프트로 진짜 주기의 peak 이 깎여 sub-line lag 에 잠길 수
    # 있다(실사진 실측: 전역 20.8px vs strip 합의 36px — ±10% 전역 필터가 전 strip 기각).
    normed, raw_periods = [], []
    for st in strips:
        prof = st.mean(axis=1)                          # (N,3)
        L = prof[:, 0]
        sigma = max(global_period * 2.0, 16.0)
        low = cv2.GaussianBlur(L.reshape(-1, 1), (0, 0), sigmaX=sigma).ravel()
        prof = prof.copy()
        prof[:, 0] = L * np.where(low > 1e-3, L.mean() / np.maximum(low, 1e-3), 1.0)
        sig = _pattern_signal(prof)
        det_local = sig - cv2.GaussianBlur(
            sig.reshape(-1, 1), (0, 0), sigmaX=max(global_period * 2.0, 16.0)).ravel()
        ax = _autocorr_period(det_local, min_lag=max(4, n // 256))
        normed.append((prof, det_local))
        if ax.period_px is not None and ax.strength >= 0.2:
            raw_periods.append(float(ax.period_px))
    if len(raw_periods) < max(3, PHASE_STRIPS // 3):
        return CompositeFailure(
            "stripe_model_low_confidence",
            f"{axis_name}: 주기 신호 strip {len(raw_periods)}/{PHASE_STRIPS}",
            {"periodic_strips": len(raw_periods)})
    consensus_p = float(np.median(raw_periods))

    def _reconcile(p_raw: float) -> float | None:
        """strip 주기를 합의 주기의 하모닉 관계(1, 2, 3, 1/2, 1/3)로 정합."""
        for m in (1.0, 2.0, 3.0, 0.5, 1.0 / 3.0):
            cand = p_raw * m
            if abs(cand - consensus_p) / consensus_p <= 0.12:
                return cand
        return None

    K = _fold_samples(consensus_p)
    folded_strips, strip_cons, strip_periods = [], [], []
    for (prof, det_local) in normed:
        ax = _autocorr_period(det_local, min_lag=max(4, n // 256))
        p_local = _reconcile(float(ax.period_px)) if (
            ax.period_px is not None and ax.strength >= 0.2) else None
        if p_local is None or int(n // p_local) < 3:
            continue
        f, c = _fold_profile(prof, p_local, K)
        folded_strips.append(f)
        strip_cons.append(c)
        strip_periods.append(p_local)
    if len(folded_strips) < max(3, PHASE_STRIPS // 3):
        return CompositeFailure(
            "stripe_model_low_confidence",
            f"{axis_name}: 유효 strip {len(folded_strips)}/{PHASE_STRIPS}",
            {"valid_strips": len(folded_strips)})
    period = float(np.median(strip_periods))
    n_periods = int(n // period)
    if n_periods < MIN_PERIODS_IN_ROI:
        return CompositeFailure(
            "reference_insufficient",
            f"{axis_name}: ROI 내 반복 {n_periods}회 < {MIN_PERIODS_IN_ROI}",
            {"n_periods": n_periods})

    ref_idx = len(folded_strips) // 2
    basis = _signal_basis(folded_strips[ref_idx])   # 정렬 상대끼리 표현을 맞춘다
    ref_sig = _pattern_signal(folded_strips[ref_idx], basis)
    ref = ref_sig - ref_sig.mean()
    ref_f = np.conj(np.fft.rfft(ref))
    aligned = []
    for f in folded_strips:
        row_sig = _pattern_signal(f, basis)
        row = row_sig - row_sig.mean()
        corr = np.fft.irfft(np.fft.rfft(row) * ref_f, n=K)
        best = int(np.argmax(corr))
        aligned.append(np.roll(f, -best, axis=0) if best else f)
    folded = np.median(np.stack(aligned), axis=0).astype(np.float32)
    fold_consistency = float(np.median(strip_cons))

    runs = _merge_runs(folded, _runs_from_folded(folded))

    def run_color(start, length):
        idx = (np.arange(start, start + length) % K)
        return tuple(float(x) for x in np.median(folded[idx], axis=0))

    widest = max(range(len(runs)), key=lambda i: runs[i][1])
    ordered = runs[widest:] + runs[:widest]
    colors = tuple(run_color(st, ln) for st, ln in ordered)

    # --- chroma 재앵커: strip 접합은 strip 폭(≈W/12) 안의 줄 기울기 때문에 얇은 run 의
    # ab 를 lateral smear 로 뭉갠다(실측: raw 라인 b* -5~+7 → 접합 후 ±2). L 대비는 커서
    # 살아남지만 chroma 는 죽는다. 복원: 같은 strip 모집단의 raw 암픽셀에서 두 chroma
    # 계열(bluest/warmest)을 재고, 라인 run 들의 ab 를 그 범위로 **순서 보존 affine 매핑**
    # 한다. 접합 값의 상대 순서는 보존되므로(실측 확인) 계열 배정이 뒤집히지 않고, ground
    # run 은 건드리지 않는다. 색 발명이 아니라 같은 소스에서의 측정 복원 — 완전 정렬
    # (합성 fixture)에선 raw 스프레드 ≤ 접합 스프레드라 no-op.
    if len(colors) >= 3:
        pix = np.concatenate([st.reshape(-1, 3) for st in strips], axis=0)
        dark = pix[:, 0] < np.percentile(pix[:, 0], 25.0)
        if int(dark.sum()) >= 200:
            a_dark, b_dark = pix[dark, 1], pix[dark, 2]
            lo_m = b_dark < np.percentile(b_dark, 15.0)
            hi_m = b_dark > np.percentile(b_dark, 85.0)
            fam_lo = (float(np.median(a_dark[lo_m])), float(np.median(b_dark[lo_m])))
            fam_hi = (float(np.median(a_dark[hi_m])), float(np.median(b_dark[hi_m])))
            line_runs = ordered[1:]
            lb = [run_color(st_, ln_)[2] for st_, ln_ in line_runs]
            la = [run_color(st_, ln_)[1] for st_, ln_ in line_runs]
            b0, b1 = min(lb), max(lb)
            a0, a1 = min(la), max(la)
            if b1 - b0 > 0.15 and (fam_hi[1] - fam_lo[1]) > (b1 - b0):
                for st_, ln_ in line_runs:
                    idx = np.arange(st_, st_ + ln_) % K
                    t_b = (folded[idx, 2] - b0) / (b1 - b0)
                    folded[idx, 2] = fam_lo[1] + np.clip(t_b, -0.25, 1.25) * (fam_hi[1] - fam_lo[1])
                    # a-매핑은 family 간 a·b 가 공단조일 때만 유효 — b-백분위로 뽑은
                    # 끝점을 a 에 재사용하므로, 순서가 어긋난 팔레트(마젠타/옐로그린류)
                    # 에선 a 가 반대 family 로 회전한다(final-code 리뷰 M1). 순서 불일치
                    # 시 a 는 접합값 유지(보수적).
                    a_monotone = (fam_hi[0] - fam_lo[0]) * (a1 - a0) > 0
                    if a1 - a0 > 0.1 and a_monotone:
                        t_a = (folded[idx, 1] - a0) / (a1 - a0)
                        folded[idx, 1] = fam_lo[0] + np.clip(t_a, -0.25, 1.25) * (fam_hi[0] - fam_lo[0])
                colors = tuple(run_color(st_, ln_) for st_, ln_ in ordered)
    widths = tuple(ln / K for _st, ln in ordered)
    folded = np.roll(folded, -ordered[0][0], axis=0)
    amplitude = 0.0
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            amplitude = max(amplitude, float(ciede2000(
                np.array(colors[i]), np.array(colors[j]))))
    return {
        "axis": axis_name, "period": period, "folded": folded,
        "colors": colors, "widths": widths, "n_periods": n_periods,
        "fold_consistency": fold_consistency, "amplitude": amplitude,
    }


def extract_stripe_model(
    roi_bgr: np.ndarray, *, source_asset_id: str, source_sha256: str, source_roi: tuple,
) -> StripeModel | CompositeFailure:
    """원단 ROI → StripeModel. 모든 판정 불가는 typed 실패로 (fail closed).

    축 선택은 휴리스틱(강도·FFT 합의)이 아니라 **실추출 자기검증**이다: 두 축 모두에서
    추출을 시도하고, 다중 run 색 구조가 실존하는 축을 채택한다. 직조 텍스처·그 하모닉이
    강도/합의 휴리스틱을 이기는 사례가 실사진에서 2회 실측됐다 — 하지만 잘못된 축의 fold 는
    반드시 단일 run 으로 붕괴하므로 구조 존재가 가장 신뢰할 수 있는 판별자다.
    """
    if roi_bgr is None or roi_bgr.size == 0 or min(roi_bgr.shape[:2]) < 64:
        return CompositeFailure("reference_insufficient", "ROI 가 너무 작음")

    lab = bgr_to_lab(roi_bgr)
    axes = measure_axes(roi_bgr)
    attempts: dict = {}
    axis_fails: dict = {}
    for name in ("horizontal", "vertical"):
        ax = axes[name]
        if ax.period_px is None or ax.strength < MIN_PERIODICITY:
            axis_fails[name] = f"주기성 미달 (strength={ax.strength:.3f})"
            continue
        r = _extract_axis_candidate(lab, name, float(ax.period_px))
        if isinstance(r, CompositeFailure):
            axis_fails[name] = r
            continue
        attempts[name] = r

    structured = {n: r for n, r in attempts.items()
                  if len(r["colors"]) >= 2 and r["amplitude"] >= RUN_EDGE_DELTA_E}
    if not structured:
        # 진짜 축이 반복 부족으로 죽었다면 그 사유가 더 정확한 보고다
        for f in axis_fails.values():
            if isinstance(f, CompositeFailure) and f.reason == "reference_insufficient":
                return f
        return CompositeFailure(
            "stripe_model_low_confidence",
            "어느 축에서도 다중 run 색 구조를 찾지 못함",
            {n: (f.detail if isinstance(f, CompositeFailure) else f)
             for n, f in axis_fails.items()})
    if len(structured) == 2:
        # 양 축 모두 의류 스케일에서 다중 run 색 구조 = 규칙 체크. 진폭 비 조건을 두지
        # 않는다 — 직조·노이즈는 structured 필터(진폭 ≥ RUN_EDGE_DELTA_E)에서 이미 탈락했고,
        # 실측에서 gingham 의 부 축 진폭이 주 축의 0.32 배라 비율 조건이 체크를 놓쳤다.
        return CompositeFailure(
            "unsupported_pattern",
            "양 축 모두 의류 스케일 색 구조 — 규칙 체크로 판정, 스트라이프 MVP 범위 밖",
            {n: round(r["amplitude"], 1) for n, r in structured.items()})

    best_name = max(structured, key=lambda n: structured[n]["amplitude"])
    b = structured[best_name]
    primary = axes[best_name]
    other = structured.get("vertical" if best_name == "horizontal" else "horizontal")
    axis_separation = 1.0 if other is None else 1.0 - min(
        1.0, other["amplitude"] / max(b["amplitude"], 1e-6))
    consensus_factor = 1.0 if axes.get(f"{best_name}_consensus") else 0.75
    confidence = float(min(
        primary.strength / max(MIN_PERIODICITY * 2, 1e-6), 1.0,
        b["fold_consistency"], consensus_factor, 0.5 + axis_separation / 2))

    return StripeModel(
        axis=best_name,
        period_px=b["period"],
        period_profile_lab=b["folded"],
        ground_color_lab=b["colors"][0],
        color_sequence_lab=b["colors"],
        line_width_ratios=b["widths"],
        n_periods_used=b["n_periods"],
        confidence=min(confidence, 1.0),
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        source_roi=tuple(source_roi),
    )


def extract_stripe_model_scan(
    roi_bgr: np.ndarray, *, source_asset_id: str, source_sha256: str, source_roi: tuple,
) -> StripeModel | CompositeFailure:
    """멀티스케일 국소 패치 스캔 추출 — 주름·드레이프로 줄이 휘는 실사진용.

    실측(2026-08-01): 착용 상태 Detail 은 줄이 주름을 따라 수 주기 굽어 전역 축정렬
    평균이 chroma 를 상쇄한다. 국소 320px 창에서는 거의 직선이라 추출이 성립한다
    (창 크기 스윕 실측: 320px 16/140 성공·4색 군집 10, 900px 1/9). 반대로 주기가 큰
    원단은 320px 창의 반복 수가 모자라므로 여러 스케일을 함께 스캔한다.

    합의 규칙: (축, 색 수) 가 같고 주기가 군집 중앙값 ±15% 인 패치 군집 중,
    자격(≥3 패치, 성공의 30% 이상)을 갖춘 군집에서 **색 수가 가장 풍부한** 쪽을 고른다 —
    2색 군집은 4색 패턴의 퇴화 관측(파란 줄만 잡힌 창)일 수 있고, 그 역은 성립하지 않는다.
    """
    h, w = roi_bgr.shape[:2]
    if min(h, w) < 480:
        return extract_stripe_model(
            roi_bgr, source_asset_id=source_asset_id,
            source_sha256=source_sha256, source_roi=source_roi)
    candidates: list[StripeModel] = []
    fail_counts: dict = {}
    n_windows = 0
    for size in (320, 512, 768):
        if size > min(h, w):
            break
        stride = max(1, int(size * 0.75))
        for y0 in range(0, h - size + 1, stride):
            for x0 in range(0, w - size + 1, stride):
                n_windows += 1
                m = extract_stripe_model(
                    roi_bgr[y0:y0 + size, x0:x0 + size],
                    source_asset_id=source_asset_id, source_sha256=source_sha256,
                    source_roi=(source_roi[0] + x0, source_roi[1] + y0,
                                source_roi[0] + x0 + size, source_roi[1] + y0 + size))
                if isinstance(m, CompositeFailure):
                    fail_counts[m.reason] = fail_counts.get(m.reason, 0) + 1
                else:
                    candidates.append(m)
    if not candidates:
        # 실패 사유 집계는 다수결이 아니다 — unsupported_pattern(체크 구조의 **양성 식별**)은
        # 반복 수가 모자란 작은 창의 reference_insufficient 보다 정보가 세다. 스트라이프
        # 추출이 하나도 성공하지 못한 이 분기에서는 체크 양성 1건이 최선의 설명이다
        # (921² ROI 는 768 창이 1개뿐이라 ≥2 요구가 체크를 놓쳤다 — 실측).
        if fail_counts.get("unsupported_pattern", 0) >= 1:
            reason = "unsupported_pattern"
        elif fail_counts:
            reason = max(fail_counts, key=fail_counts.get)
        else:
            reason = "stripe_model_low_confidence"
        return CompositeFailure(reason, f"패치 {n_windows}개 전부 추출 실패", fail_counts)

    groups: list[list[StripeModel]] = []
    for m in candidates:
        group = [m2 for m2 in candidates
                 if m2.axis == m.axis
                 and len(m2.color_sequence_lab) == len(m.color_sequence_lab)
                 and abs(m2.period_px - m.period_px) / m.period_px
                 <= PATCH_PERIOD_AGREEMENT_TOL]
        groups.append(group)
    eligible = [g for g in groups
                if len(g) >= max(3, int(np.ceil(0.3 * len(candidates))))]
    if not eligible:
        return CompositeFailure(
            "stripe_model_low_confidence",
            f"패치 합의 부족 (성공 {len(candidates)}/{n_windows})",
            {"successes": len(candidates), **fail_counts})
    best_group = max(eligible, key=lambda g: (len(g[0].color_sequence_lab), len(g)))
    agreement = len(best_group) / max(len(candidates), 1)
    best = max(best_group, key=lambda m: m.confidence)
    import dataclasses
    return dataclasses.replace(
        best,
        period_px=float(np.median([m.period_px for m in best_group])),
        confidence=float(min(best.confidence, 0.4 + agreement / 2 + 0.1 * min(len(best_group), 6) / 6)),
    )
