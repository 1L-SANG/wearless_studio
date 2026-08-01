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


def _detrended_profile(channel: np.ndarray, axis: int) -> np.ndarray:
    """축 방향 평균 프로파일 - 저주파(조명) 성분. axis=1 → 행 프로파일(수평 줄)."""
    prof = channel.mean(axis=axis).astype(np.float64)
    sigma = max(len(prof) / 8.0, 8.0)
    low = cv2.GaussianBlur(prof.reshape(-1, 1), (0, 0), sigmaX=sigma).ravel()
    return prof - low


def _autocorr_period(prof: np.ndarray) -> AxisPeriodicity:
    """정규화 autocorrelation 의 fundamental peak. 하모닉이 있으면 가장 짧은 유효 lag 채택."""
    n = len(prof)
    if n < 32:
        return AxisPeriodicity(None, 0.0)
    p = prof - prof.mean()
    denom = float((p * p).sum())
    if denom < 1e-9:
        return AxisPeriodicity(None, 0.0)
    ac = np.correlate(p, p, mode="full")[n - 1:] / denom
    lo, hi = max(4, int(MIN_PERIOD_PX)), n // 2
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
    """양 축의 주기성 측정 — extractor 와 deterministic QC 가 같은 눈으로 본다."""
    lab = bgr_to_lab(roi_bgr)
    L = lab[..., 0]
    # axis=1: 행 평균 → y 방향 주기 = 수평 줄. axis=0: 열 평균 → x 방향 주기 = 수직 줄.
    horizontal = _autocorr_period(_detrended_profile(L, axis=1))
    vertical = _autocorr_period(_detrended_profile(L, axis=0))
    return {"horizontal": horizontal, "vertical": vertical}


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
    ref = stack[0, :, 0] - stack[0, :, 0].mean()
    ref_f = np.conj(np.fft.rfft(ref))
    for j in range(1, len(stack)):
        row = stack[j, :, 0] - stack[j, :, 0].mean()
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
    """cyclic run 분할 — **plateau 기반**. → [(start, length), ...]

    에지 검출(인접/윈도우 ΔE)은 전이 램프 폭에 민감하다: 원근·tilt 잔차로 에지가 주기의
    ~5% 로 번지면 임계를 넘는 지점이 사라지거나(병합) 램프 안에서 두 번 잡힌다(고스트).
    실측(2026-08-01 스파이크)으로 두 실패가 모두 재현됐다.

    대신 **변화율이 낮은 구간(plateau)** 을 run 의 몸통으로 삼는다. 램프가 아무리 넓어도
    plateau 는 plateau 다 — 경계는 인접 plateau 사이 램프의 중점으로 결정한다. run 색도
    램프 오염 없이 plateau core 의 중앙값으로 얻는다.
    """
    K = len(folded)
    nxt = np.roll(folded, -1, axis=0)
    d = np.sqrt(((nxt - folded) ** 2).sum(axis=-1))       # 인접 표본 ΔE76 (cyclic)
    k_smooth = max(3, K // 128) | 1
    d = cv2.blur(d.reshape(-1, 1), (1, k_smooth)).ravel()
    tau = max(0.30, 4.0 * float(np.median(d)))
    flat = d < tau

    # cyclic 연속 plateau 구간 추출
    segments = []
    k = 0
    while k < K:
        if flat[k]:
            j = k
            while j + 1 < K and flat[j + 1]:
                j += 1
            segments.append((k, j - k + 1))
            k = j + 2
        else:
            k += 1
    if segments and flat[0] and flat[K - 1] and len(segments) > 1:
        # 0 을 가로지르는 plateau 병합
        first_s, first_l = segments[0]
        last_s, last_l = segments[-1]
        if first_s == 0 and last_s + last_l == K:
            segments = segments[1:-1] + [(last_s, last_l + first_l)]
    min_core = max(2, K // 100)
    segments = [(s, ln) for s, ln in segments if ln >= min_core]
    if len(segments) <= 1:
        return [(0, K)]

    segments.sort()
    # run 경계 = 인접 plateau 사이 램프의 중점 (cyclic)
    cuts = []
    for i in range(len(segments)):
        s_end = (segments[i][0] + segments[i][1]) % K
        n_start = segments[(i + 1) % len(segments)][0]
        gap = (n_start - s_end) % K
        cuts.append((s_end + gap // 2) % K)
    cuts = sorted(set(cuts))
    runs = []
    for i, start in enumerate(cuts):
        end = cuts[(i + 1) % len(cuts)]
        runs.append((start, (end - start) % K or K))
    return runs


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


def extract_stripe_model(
    roi_bgr: np.ndarray, *, source_asset_id: str, source_sha256: str, source_roi: tuple,
) -> StripeModel | CompositeFailure:
    """원단 ROI → StripeModel. 모든 판정 불가는 typed 실패로 (fail closed)."""
    if roi_bgr is None or roi_bgr.size == 0 or min(roi_bgr.shape[:2]) < 64:
        return CompositeFailure("reference_insufficient", "ROI 가 너무 작음")

    axes = measure_axes(roi_bgr)
    h_ax, v_ax = axes["horizontal"], axes["vertical"]
    primary_name = "horizontal" if h_ax.strength >= v_ax.strength else "vertical"
    primary = axes[primary_name]
    secondary = axes["vertical" if primary_name == "horizontal" else "horizontal"]

    if primary.period_px is None or primary.strength < MIN_PERIODICITY:
        return CompositeFailure(
            "stripe_model_low_confidence",
            f"주기성 미달 (strength={primary.strength:.3f})",
            {"strength": primary.strength})
    if (secondary.period_px is not None
            and secondary.strength >= MIN_PERIODICITY
            and secondary.strength / primary.strength >= CHECK_AXIS_RATIO):
        return CompositeFailure(
            "unsupported_pattern",
            "양 축 모두 주기적 — 규칙 체크로 판정, 스트라이프 MVP 범위 밖",
            {"primary_strength": primary.strength, "secondary_strength": secondary.strength})

    lab = bgr_to_lab(roi_bgr)
    mean_axis = 1 if primary_name == "horizontal" else 0

    # 주기 합의: autocorr vs FFT (전역)
    det = _detrended_profile(lab[..., 0], axis=mean_axis)
    p_fft = _fft_period(det)
    period = primary.period_px
    consensus = 1.0
    if p_fft is not None:
        # FFT 는 sub-line 이 많은 신호에서 하모닉(주기/4 등)에 잡힌다(스파이크 실측: 88px 를
        # 22px 로). 두 측정의 비가 8 이하의 정수배로 맞으면 같은 fundamental 의 합의로 본다.
        ratio = max(period, p_fft) / max(min(period, p_fft), 1e-9)
        nearest = max(1.0, round(ratio))
        rel = abs(ratio - nearest) / nearest
        if nearest > 8 or rel > PERIOD_CONSENSUS_TOL:
            return CompositeFailure(
                "stripe_model_low_confidence",
                f"주기 불합의 (autocorr={period:.1f}px, fft={p_fft:.1f}px)",
                {"period_ac": period, "period_fft": p_fft})
        consensus = 1.0 - min(1.0, rel / PERIOD_CONSENSUS_TOL)

    # ── strip 별 독립 fold → 위상 공간 정렬 → 중앙값 ─────────────────────────────
    # 원근은 strip(줄 방향 위치)마다 **국소 주기 자체**를 바꾼다. 전장 프로파일을 하나의
    # shift 로 정렬하려던 이전 설계는 주기가 다른 두 신호의 상호상관이 비트 패턴이 되면서
    # 전부를 스미어로 만들었다(스파이크 실측 3회의 진범). fold 가 주기를 정규화하므로,
    # strip 별로 접은 **위상 공간**에서는 단일 원형 shift 정렬이 정확해진다.
    if mean_axis == 1:
        strips = np.array_split(lab, PHASE_STRIPS, axis=1)
    else:
        strips = np.array_split(lab.transpose(1, 0, 2), PHASE_STRIPS, axis=1)
    K = _fold_samples(period)
    n = strips[0].shape[0]
    folded_strips, strip_cons, strip_periods = [], [], []
    for s in strips:
        prof = s.mean(axis=1)                          # (N,3)
        L = prof[:, 0]
        sigma = max(period * 2.0, 16.0)
        low = cv2.GaussianBlur(L.reshape(-1, 1), (0, 0), sigmaX=sigma).ravel()
        prof = prof.copy()
        prof[:, 0] = L * np.where(low > 1e-3, L.mean() / np.maximum(low, 1e-3), 1.0)
        # strip 국소 주기 — 전역치의 ±10% 안에서만 신뢰(밖이면 그 strip 은 버린다)
        ax = _autocorr_period(prof[:, 0] - cv2.GaussianBlur(
            prof[:, 0].reshape(-1, 1), (0, 0), sigmaX=max(period, 8.0)).ravel())
        p_local = ax.period_px if (
            ax.period_px is not None and abs(ax.period_px - period) / period <= 0.10) else None
        if p_local is None:
            continue
        if int(n // p_local) < 3:
            continue
        f, c = _fold_profile(prof, p_local, K)
        folded_strips.append(f)
        strip_cons.append(c)
        strip_periods.append(p_local)
    if len(folded_strips) < max(3, PHASE_STRIPS // 3):
        return CompositeFailure(
            "stripe_model_low_confidence",
            f"유효 strip {len(folded_strips)}/{PHASE_STRIPS} — 국소 주기 불안정",
            {"valid_strips": len(folded_strips)})
    period = float(np.median(strip_periods))

    n_periods = int(n // period)
    if n_periods < MIN_PERIODS_IN_ROI:
        return CompositeFailure(
            "reference_insufficient",
            f"ROI 내 반복 {n_periods}회 < {MIN_PERIODS_IN_ROI}",
            {"n_periods": n_periods})

    # 위상 공간 원형 정렬 (기준 = 중앙 strip)
    ref_idx = len(folded_strips) // 2
    ref = folded_strips[ref_idx][:, 0] - folded_strips[ref_idx][:, 0].mean()
    ref_f = np.conj(np.fft.rfft(ref))
    aligned = []
    for f in folded_strips:
        row = f[:, 0] - f[:, 0].mean()
        corr = np.fft.irfft(np.fft.rfft(row) * ref_f, n=K)
        best = int(np.argmax(corr))
        aligned.append(np.roll(f, -best, axis=0) if best else f)
    folded = np.median(np.stack(aligned), axis=0).astype(np.float32)
    fold_consistency = float(np.median(strip_cons))
    runs = _merge_runs(folded, _runs_from_folded(folded))
    K = len(folded)

    def run_color(start, length):
        idx = (np.arange(start, start + length) % K)
        return tuple(float(x) for x in np.median(folded[idx], axis=0))

    # canonical 순서: 가장 넓은 run(=바탕)에서 시작하는 cyclic 순서.
    # **프로파일도 같은 기준으로 회전**한다 — 색/폭 시퀀스는 ground-시작인데 프로파일이
    # fold 위상 그대로면, 소비자(합성·guided QC)의 run-center 인덱싱이 어긋난다
    # (2026-08-01 실측: crop 위상이 0 이 아닐 때 QC 가 바탕색만 읽어 '줄 소실'로 오판).
    widest = max(range(len(runs)), key=lambda i: runs[i][1])
    ordered = runs[widest:] + runs[:widest]
    colors = tuple(run_color(s, ln) for s, ln in ordered)
    widths = tuple(ln / K for _s, ln in ordered)
    folded = np.roll(folded, -ordered[0][0], axis=0)

    axis_separation = 1.0 - min(1.0, (secondary.strength / primary.strength)
                                if primary.strength > 0 else 1.0)
    confidence = float(min(primary.strength / max(MIN_PERIODICITY * 2, 1e-6),
                           1.0, consensus, fold_consistency, 0.5 + axis_separation / 2))
    confidence = min(confidence, 1.0)

    return StripeModel(
        axis=primary_name,
        period_px=float(period),
        period_profile_lab=folded,
        ground_color_lab=colors[0],
        color_sequence_lab=colors,
        line_width_ratios=widths,
        n_periods_used=n_periods,
        confidence=confidence,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        source_roi=tuple(source_roi),
    )
