"""Stage 5 — 합성 결과의 결정론적 재측정. LLM QC 는 이 판정을 뒤집을 수 없다.

검증은 **모델-guided** 다: Stage 2 가 blind 추출이라면, Stage 5 는 "기대한 패턴이 출력에
실제로 존재하는가"를 기대 프로파일과의 직접 대조로 판정한다. blind 재추출을 여기 쓰면
출력 해상도에서 2~3px 로 얇아진 잔줄이 검출기 한계에 걸려, 옳은 합성을 실패로 오판한다
(2026-08-01 스파이크 실측 — 갈색 잔줄이 합성본에는 있는데 재추출이 놓쳤다).

또 소매 panel 은 회전돼 있어 축정렬 ROI 측정이 무의미하다 — panel 로컬 공간으로
역워프한 뒤 잰다(합성과 같은 H 를 결정론적으로 재계산).

같은 잘못된 이미지에 3회 중 2회 pass 를 주던 one-shot vision 판정과 달리 이 측정은
결정론이라 흔들리지 않는다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .color import bgr_to_lab, ciede2000, delta_e76
from .stripe_model import (
    _autocorr_period,
    _detrended_profile,
    _fold_profile,
    _fold_samples,
)
from .types import QC_VERSION, StripeModel

REPEAT_COUNT_TOL = 0.15        # panel 주기 상대 오차 상한 (live gate 와 동일)
CROSS_SURFACE_PERIOD_TOL = 0.12
CROSS_SURFACE_STRIPE_WIDTH_TOL = 0.15
# run 대표색 ΔE00 의 per-run 하드 상한. 집계 gate(median ≤6 / P95 ≤10)는 controlled fixture
# 테스트가 전 셋에 대해 강제한다 — 여기 per-run 컷은 색 정체성 파괴(순서 뒤바뀜 ≈ ΔE 40,
# 다른 색으로 대체)를 잡는 안전망이고, 좁은 소매의 2차 리샘플 측정 스미어(실측 ~10-12)를
# 결함으로 오판하지 않는 수준으로 둔다. 줄 소실은 별도 대비(presence) 검사가 잡는다.
COLOR_DELTA_E_MAX = 16.0
LINE_PRESENCE_MIN_CONTRAST = 0.5   # 기대 대비 실측 색 대비가 이 비율 밑이면 '줄 소실'
MIN_ALIGN_CORR = 0.55          # 기대 프로파일과 실측 fold 의 정렬 상관 하한
OUTSIDE_DRIFT_DELTA_E = 10.0   # carrier 보존 판정 ΔE76 임계
OUTSIDE_DRIFT_MAX_FRAC = 0.01  # mask 밖 ΔE76>10 픽셀 허용 비율
# ── 계면·연속성·드레이프 ──────────────────────────────────────────────────────
# 기존 지표는 panel **내부**의 패턴 통계와 실루엣 **바깥**의 보존만 쟀다. 그 사이 구간,
# 즉 painted 영역의 내부 경계는 어느 검사에도 걸리지 않아 v6 의 '직사각형 판'이 모든
# 수치를 통과했다(period 0.0005, coverage 1.0, outside drift 0). 아래가 그 구멍이다.
DIRECTION_ERROR_MAX = 0.10     # 직교축 주기 강도가 목표축보다 이만큼 세면 줄 방향 오류
# 실측 alpha / 그 거리에서 기대되는 ramp. 정상 합성 6종 전부 0.822 로 모이고, 계단은
# 위치에 따라 2.0~6.67 이다(밴드 안쪽으로 옮길수록 초과분이 줄어든다). 1.6 은 정상의
# 두 배 여유를 두면서 밴드 절반 깊이의 계단까지 잡는 값이다.
SEAM_RAMP_EXCESS_MAX = 1.6
SEAM_GRAD_NORM_MAX = 0.35      # 내부 alpha 로 정규화한 기울기 p99 — 계단이면 1 에 근접
DRAPE_TILES = 4                # 드레이프는 타일별로 재고 최악 타일로 판정한다
# 국소 게이트 — 타일보다 작은 손상과 경계의 좁은 색 단절은 요약 통계로 안 잡힌다.
BOUNDARY_CHROMA_SEVERE_DE = 20.0        # 이 이상이면 한 벌로 안 보이는 수준
BOUNDARY_CHROMA_SEVERE_FRAC_MAX = 0.02  # 경계의 2% 넘게 그러면 국소 단절로 본다
# 창 단위 진폭비 하위 2%. 정상 합성 6종 실측 0.395~0.462, 타일보다 작은 영역 평탄화는
# 0.082 로 떨어진다. 0.30 은 정상 최솟값 아래로 여유를 두면서 그 손상을 잡는 값이다.
DRAPE_LOCAL_AMP_MIN = 0.30
# 경계 양쪽 L 차는 **기록만** 한다. 정상 합성에서 4.06~24.12 로 흩어지고(painted 몸통과
# 미페인트 커프는 원래 밝기가 다르다) +30 주입 손상이 31.4 라 분리되지 않는다. 게이트로
# 쓰면 정상 합성 6종 중 2종이 오거절된다 — 분리되지 않는 지표는 판정에 쓰지 않는다.
# painted↔인접 unpainted 국소 ΔE00 의 p90. 정상 합성 6종 실측 2.95~9.31 이 하한 근거이고,
# 주입한 ±35 불연속은 중앙값 23 으로 이 위에 크게 뜬다. 14 는 그 사이다.
BOUNDARY_CHROMA_DE_MAX = 14.0
# 상관은 **게이트로 쓰지 않는다**. 정상 합성의 최악 타일 상관이 0.298~0.686 으로 흩어져
# 손상본과 분리되지 않는다(실측 6종). 대신 진폭비로 판정한다 — 정상 최악 타일 0.757~0.981,
# 45% 평탄화 0.28 로 명확히 갈린다. 상관은 관측 지표로만 남긴다.
DRAPE_AMP_MIN_OBSERVED_HEALTHY = 0.757
# 상관은 **0 을 기준으로만** 쓴다. 정상 합성의 최악 타일 상관이 0.298~0.686 으로 흩어져
# "얼마나 닮았나" 는 판정 근거가 못 되지만, 음의 상관(접힘이 뒤집힘)은 정상 분포와 겹치지
# 않는다. 즉 이 게이트가 잡는 것은 '덜 닮음' 이 아니라 '반대로 감' 이다.
DRAPE_CORR_MIN = 0.0
STRICT_PANEL_MIN_FRAC = 0.20   # 칠한 픽셀의 이 비율 이상을 가진 패널은 strict 필수
DRAPE_SIGMA_FRAC = 0.03        # 드레이프 측정용 저주파 척도 (짧은 변 대비)
DRAPE_AMP_MIN = 0.55           # 저주파 진폭비 하한 — 상관은 모양만 보므로 진폭을 따로 잰다


@dataclass(frozen=True)
class DeterministicQC:
    passed: bool
    metrics: dict
    failures: tuple = ()
    version: str = QC_VERSION


def ssim_gray(a_bgr: np.ndarray, b_bgr: np.ndarray, mask: np.ndarray | None = None) -> float:
    """표준 SSIM (grayscale, 11×11 gaussian) — 외부 dependency 없이 구현.

    carrier 보존 gate(garment 밖 SSIM ≥ 0.98)용. mask 가 있으면 그 영역 평균만 취한다.
    """
    a = cv2.cvtColor(a_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    sa = blur(a * a) - mu_a * mu_a
    sb = blur(b * b) - mu_b * mu_b
    sab = blur(a * b) - mu_a * mu_b
    ssim_map = ((2 * mu_a * mu_b + C1) * (2 * sab + C2)) / (
        (mu_a ** 2 + mu_b ** 2 + C1) * (sa + sb + C2))
    if mask is not None:
        sel = mask > 0
        return float(ssim_map[sel].mean()) if sel.any() else 1.0
    return float(ssim_map.mean())


def _expected_profile(model: StripeModel, K: int) -> np.ndarray:
    """모델의 한 주기 프로파일을 K 표본으로 리샘플 (Lab)."""
    src = model.period_profile_lab
    idx = np.linspace(0, len(src), K, endpoint=False)
    i0 = np.floor(idx).astype(int) % len(src)
    i1 = (i0 + 1) % len(src)
    frac = (idx - np.floor(idx)).reshape(-1, 1)
    return (src[i0] * (1 - frac) + src[i1] * frac).astype(np.float64)


def _masked_profile(values: np.ndarray, valid: np.ndarray, axis: int) -> np.ndarray:
    """Mask-aware axis mean; fully excluded samples are filled from valid neighbours."""
    weights = valid.astype(np.float64)
    if values.ndim == 3:
        sums = (values * weights[..., None]).sum(axis=axis)
        counts = weights.sum(axis=axis)[..., None]
    else:
        sums = (values * weights).sum(axis=axis)
        counts = weights.sum(axis=axis)
    out = sums / np.maximum(counts, 1.0)
    empty = np.squeeze(counts, axis=-1) <= 0 if values.ndim == 3 else counts <= 0
    if np.any(empty):
        good = np.flatnonzero(~empty)
        bad = np.flatnonzero(empty)
        if not len(good):
            return out
        if values.ndim == 3:
            for channel in range(out.shape[1]):
                out[bad, channel] = np.interp(bad, good, out[good, channel])
        else:
            out[bad] = np.interp(bad, good, out[good])
    return out


def _component_output_scale(
    out_bgr: np.ndarray,
    quad,
    model: StripeModel,
    *,
    target_period_px: float,
    target_axis: str,
    target_axis_unit=None,
    painted_mask: np.ndarray | None = None,
    alpha: np.ndarray | None = None,
) -> dict:
    """Remeasure period and run widths from the final blended component pixels.

    Planned warp factors are not QC evidence. This function samples the encoded output
    surface itself, after shading and feathering, so a resampler regression cannot report
    zero error by construction.
    """
    q = np.asarray(quad, np.float32)
    if q.shape != (4, 2):
        return {"scale_measurable": False, "reason": "target_quad_invalid"}
    bw = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))) + 1
    bh = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))) + 1
    if min(bw, bh) < 18:
        return {"scale_measurable": False, "reason": "component_output_too_small"}
    prof = None
    if target_axis_unit is not None:
        unit = np.asarray(target_axis_unit, np.float64)
        norm = float(np.linalg.norm(unit))
        if unit.shape == (2,) and np.isfinite(unit).all() and norm > 1e-6:
            unit /= norm
            x0, y0 = np.floor(q.min(axis=0)).astype(int)
            x1, y1 = np.ceil(q.max(axis=0)).astype(int) + 1
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(out_bgr.shape[1], x1), min(out_bgr.shape[0], y1)
            crop = out_bgr[y0:y1, x0:x1]
            if min(crop.shape[:2]) >= 18:
                local_q = q - np.array([x0, y0], np.float32)
                valid = np.zeros(crop.shape[:2], np.uint8)
                cv2.fillPoly(valid, [local_q.astype(np.int32)], 255)
                erosion = max(2, int(round(min(crop.shape[:2]) * 0.08)))
                valid = cv2.erode(valid, np.ones((erosion, erosion), np.uint8)) > 0
                # The approved component quad is an observation envelope, not proof that
                # every pixel in its bounding polygon belongs to the garment.  Cuffs in a
                # 3/4 view routinely contain 40–60% background.  Sampling the whole quad
                # made the verifier measure the carrier/background harmonic (36px in the
                # production-sized regression fixture) instead of the 30px projected
                # cloth.  Restrict evidence to pixels actually painted by the compositor;
                # prefer the source-dominant alpha core when it remains measurable.
                if painted_mask is not None:
                    painted_local = painted_mask[y0:y1, x0:x1] > 0
                    valid &= painted_local
                if alpha is not None:
                    alpha_local = alpha[y0:y1, x0:x1]
                    source_core = valid & (alpha_local >= 0.80)
                    if int(source_core.sum()) >= 256:
                        valid = source_core
                yy, xx = np.indices(crop.shape[:2], dtype=np.float64)
                t = xx * unit[0] + yy * unit[1]
                p = -xx * unit[1] + yy * unit[0]
                if int(valid.sum()) >= 256:
                    pv = p[valid]
                    plo, phi = np.percentile(pv, [12, 88])
                    valid &= (p >= plo) & (p <= phi)
                    tv = t[valid]
                    if tv.size >= 256:
                        t0 = int(np.floor(tv.min()))
                        bins = np.floor(tv - t0).astype(np.int32)
                        n = int(bins.max()) + 1
                        counts = np.bincount(bins, minlength=n).astype(np.float64)
                        lab = bgr_to_lab(crop).astype(np.float64)
                        prof = np.stack([
                            np.bincount(bins, weights=lab[..., c][valid], minlength=n)
                            for c in range(3)
                        ], axis=1) / np.maximum(counts[:, None], 1.0)
                        good = counts >= max(
                            3.0, float(np.percentile(counts[counts > 0], 35)) * 0.35)
                        if int(good.sum()) >= 32:
                            idx = np.arange(n)
                            for channel in range(3):
                                prof[~good, channel] = np.interp(
                                    idx[~good], idx[good], prof[good, channel])
                            lo_trim, hi_trim = int(n * 0.08), int(n * 0.92)
                            prof = prof[lo_trim:hi_trim]
                        else:
                            prof = None
    if prof is None:
        span = bh if target_axis == "horizontal" else bw
        if span < max(32, target_period_px * 1.8):
            return {"scale_measurable": False, "reason": "component_output_too_small"}
        rect = np.float32([[0, 0], [bw - 1, 0], [bw - 1, bh - 1], [0, bh - 1]])
        H = cv2.getPerspectiveTransform(q, rect)
        local = cv2.warpPerspective(out_bgr, H, (bw, bh), flags=cv2.INTER_CUBIC)
        mx, my = max(2, int(bw * 0.10)), max(2, int(bh * 0.10))
        local = local[my:bh - my, mx:bw - mx]
        if min(local.shape[:2]) < 16:
            return {"scale_measurable": False, "reason": "component_interior_too_small"}
        lab = bgr_to_lab(local).astype(np.float64)
        mean_axis = 1 if target_axis == "horizontal" else 0
        prof = lab.mean(axis=mean_axis)
    if len(prof) < max(32, target_period_px * 1.8):
        return {"scale_measurable": False, "reason": "component_profile_too_short"}
    signal = np.sqrt(((prof - np.median(prof, axis=0)) ** 2).sum(axis=-1))
    sigma = max(len(signal) / 8.0, 8.0)
    signal = signal - cv2.GaussianBlur(
        signal.reshape(-1, 1), (0, 0), sigmaX=sigma).ravel()
    # This is a verification search around the torso physical scale, not a blind
    # extractor. A distant harmonic must not win merely because a short cuff has only
    # three repeats. Wider errors are correctly reported against the search boundary.
    lo = max(4, int(round(target_period_px * 0.82)))
    hi = min(len(signal) // 2, int(round(target_period_px * 1.18)))
    if hi <= lo or float(np.dot(signal, signal)) < 1e-6:
        return {"scale_measurable": False, "reason": "component_period_unmeasurable"}
    # Guided scale fit against the approved physical profile. Blind autocorrelation on a
    # 2–4 repeat collar/cuff often chooses the wide-ground harmonic (e.g. 35px for a 30px
    # four-color repeat). Evaluate candidate periods by full Lab profile fit instead.
    candidates = np.arange(float(lo), float(hi) + 0.001, 0.5)
    best = None
    for candidate_period in candidates:
        Kc = max(64, _fold_samples(candidate_period))
        folded_c, consistency = _fold_profile(prof, candidate_period, Kc)
        expected_c = _expected_profile(model, Kc)
        for shift in range(Kc):
            candidate = np.roll(folded_c, -shift, axis=0).astype(np.float64)
            candidate += np.median(expected_c - candidate, axis=0)
            cost = float(np.mean(np.linalg.norm(candidate - expected_c, axis=1)))
            score = cost + max(0.0, 0.5 - float(consistency)) * 10.0
            if best is None or score < best[0]:
                best = (score, float(candidate_period), folded_c, expected_c,
                        int(shift), cost, float(consistency))
    if best is None:
        return {"scale_measurable": False, "reason": "component_period_unmeasurable"}
    _score, measured_period, folded, expected, best_shift, best_cost, consistency = best
    strength = max(0.0, min(1.0, consistency))
    if best_cost > 30.0:
        return {"scale_measurable": False, "reason": "component_profile_low_confidence"}

    K = len(expected)
    measured = np.roll(folded, -best_shift, axis=0).astype(np.float64)
    measured += np.median(expected - measured, axis=0)
    # Measure physical run widths from final-output boundary positions. Nearest-color
    # counts collapse pastel blue/beige; FWHM merges adjacent colored runs. The approved
    # model already defines the boundary sequence, so after phase alignment each expected
    # boundary searches only its local ±8% window in the measured Lab gradient.
    expected_runs = np.asarray(model.line_width_ratios, np.float64)
    expected_runs = expected_runs / max(float(expected_runs.sum()), 1e-9)
    boundaries = np.concatenate([[0.0], np.cumsum(expected_runs)[:-1]]) * K
    smooth = cv2.GaussianBlur(measured.astype(np.float32), (1, 5), 0.8)
    gradient = np.linalg.norm(smooth - np.roll(smooth, 1, axis=0), axis=1)
    radius = max(2, int(round(K * 0.08)))
    observed_boundaries = []
    for boundary in boundaries:
        center = int(round(boundary)) % K
        offsets = np.arange(-radius, radius + 1)
        indexes = (center + offsets) % K
        best_local = int(np.argmax(gradient[indexes]))
        observed_boundaries.append(float(boundary + offsets[best_local]))
    observed_boundaries = np.asarray(observed_boundaries, np.float64)
    # Keep the cyclic order while permitting the first boundary to sit just below zero.
    for idx in range(1, len(observed_boundaries)):
        while observed_boundaries[idx] <= observed_boundaries[idx - 1]:
            observed_boundaries[idx] += K
    observed_runs = np.diff(np.concatenate([
        observed_boundaries, [observed_boundaries[0] + K]
    ])) / K
    if (not np.isfinite(observed_runs).all() or np.any(observed_runs <= 0)
            or abs(float(observed_runs.sum()) - 1.0) > 0.02):
        return {"scale_measurable": False, "reason": "component_boundary_order_invalid"}
    width_abs = float(np.max(np.abs(observed_runs - expected_runs)))
    width_err = float(np.max(
        np.abs(observed_runs - expected_runs) / np.maximum(expected_runs, 0.03)))
    return {
        "scale_measurable": True,
        "final_period_px": round(measured_period, 2),
        "period_signal_strength": round(strength, 4),
        "period_rel_err": round(
            abs(measured_period - target_period_px) / max(target_period_px, 1e-6), 4),
        "expected_stripe_run_widths": [round(float(x), 4) for x in expected_runs],
        "observed_stripe_run_widths": [round(float(x), 4) for x in observed_runs],
        "stripe_width_rel_err": round(float(width_err), 4),
        "stripe_width_error_px": round(float(width_abs * target_period_px), 3),
        "profile_fit_error": round(best_cost, 3),
    }


def _measure_panel_local(
    out_bgr: np.ndarray, panel, model: StripeModel, *,
    target_period_px: float, target_axis: str, garment_mask: np.ndarray | None = None,
    exclude_mask: np.ndarray | None = None,
) -> tuple[dict, list[dict]]:
    """panel 을 로컬 공간으로 역워프해 guided 검증. → (metrics, failures)."""
    failures: list[dict] = []
    q = panel.quad
    bw = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))) + 1
    bh = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))) + 1
    if bw < 32 or bh < 32:
        return {"skipped": "panel too small"}, failures
    # 2× 초해상 역워프 — 합성은 리샘플 1회지만 측정은 역워프로 1회를 더 겪는다. 출력 해상도
    # 그대로 재면 3px 잔줄 중심 대비가 측정 쪽에서만 깎여 옳은 합성을 실패로 오판한다.
    ss = 2.0
    bw2, bh2 = int(bw * ss), int(bh * ss)
    dst_rect = np.float32([[0, 0], [bw2 - 1, 0], [bw2 - 1, bh2 - 1], [0, bh2 - 1]])
    Hinv = cv2.getPerspectiveTransform(q, dst_rect)
    local = cv2.warpPerspective(out_bgr, Hinv, (bw2, bh2), flags=cv2.INTER_CUBIC)
    local_mask = None
    local_exclude = None
    if garment_mask is not None:
        local_mask = cv2.warpPerspective(garment_mask, Hinv, (bw2, bh2),
                                         flags=cv2.INTER_NEAREST)
    if exclude_mask is not None:
        local_exclude = cv2.warpPerspective(exclude_mask, Hinv, (bw2, bh2),
                                            flags=cv2.INTER_NEAREST)
    # 경계 feather·이웃 panel 오염을 피해 내부만
    mx, my = int(bw2 * 0.15), int(bh2 * 0.15)
    local = local[my:bh2 - my, mx:bw2 - mx]
    if min(local.shape[:2]) < 32:
        return {"skipped": "interior too small"}, failures
    target_period_px = target_period_px * ss  # 로컬 공간이 ss 배 — 주기도 같이 스케일
    # 측정 영역 순도 — 소매 quad 는 근사 밴드라 내부에 배경/이웃 의류가 섞일 수 있다.
    # 순도 미달 영역의 fold 는 패턴이 아니라 혼입을 재므로(실측: torso ΔE 0.99~1.48 완벽
    # 통과인데 소매 밴드가 3.3/9.3 '소실' 오보) hard fail 근거가 못 된다 → advisory 로
    # 기록하고 소매 시각 판정은 blind visual 이 담당한다. 순도 자체는 항상 기록.
    strict = True
    if local_mask is not None:
        lm_in = local_mask[my:bh2 - my, mx:bw2 - mx]
        purity = float((lm_in > 0).mean())
        strict = purity >= 0.90
    else:
        purity = None
    lab = bgr_to_lab(local)
    mean_axis = 1 if target_axis == "horizontal" else 0
    other_mean_axis = 0 if mean_axis == 1 else 1
    span = local.shape[0] if target_axis == "horizontal" else local.shape[1]
    pm: dict = {"local_size": [local.shape[1], local.shape[0]], "supersample": ss,
                "expected_repeats": round(span / target_period_px, 2),
                "mask_purity": None if purity is None else round(purity, 3),
                "strict": strict}

    # 1) 주기 — 실측 autocorr vs 목표
    valid = ((lm_in > 0) if local_mask is not None
             else np.ones(lab.shape[:2], dtype=bool))
    if local_exclude is not None:
        ex_in = local_exclude[my:bh2 - my, mx:bw2 - mx]
        valid &= ex_in == 0
    prof_lab = _masked_profile(lab, valid, mean_axis)
    det = prof_lab[:, 0].astype(np.float64)
    det -= cv2.GaussianBlur(
        det.reshape(-1, 1), (0, 0), sigmaX=max(len(det) / 8.0, 8.0)).ravel()
    ax = _autocorr_period(det)
    orth_l = _masked_profile(lab[..., 0], valid, other_mean_axis).astype(np.float64)
    orth_l -= cv2.GaussianBlur(
        orth_l.reshape(-1, 1), (0, 0), sigmaX=max(len(orth_l) / 8.0, 8.0)).ravel()
    orth = _autocorr_period(orth_l)
    if ax.period_px is None or ax.strength < 0.15:
        if strict:
            failures.append({"code": "pattern_metric_failed", "panel": panel.name,
                             "detail": f"출력에서 주기 신호 미검출 (strength={ax.strength:.2f})"})
        else:
            pm["advisory"] = f"저순도 영역 주기 미검출 (strength={ax.strength:.2f})"
        return pm, failures
    rep_err = abs(ax.period_px - target_period_px) / target_period_px
    pm["measured_period_px"] = round(float(ax.period_px), 2)
    pm["repeat_period_rel_err"] = round(rep_err, 4)
    pm["measured_repeats"] = round(span / float(ax.period_px), 2)
    pm["direction_error"] = round(max(0.0, float(orth.strength - ax.strength)), 4)
    pm["direction_target_strength"] = round(float(ax.strength), 4)
    pm["direction_orthogonal_strength"] = round(float(orth.strength), 4)
    if rep_err > REPEAT_COUNT_TOL and strict:
        failures.append({"code": "pattern_metric_failed", "panel": panel.name,
                         "detail": f"주기 오차 {rep_err:.3f} > {REPEAT_COUNT_TOL}"})

    # 2) 프로파일 fold + 기대 프로파일과 원형 정렬
    prof = prof_lab
    L = prof[:, 0]
    low = cv2.GaussianBlur(L.reshape(-1, 1), (0, 0),
                           sigmaX=max(target_period_px * 2.0, 16.0)).ravel()
    prof = prof.copy()
    prof[:, 0] = L * np.where(low > 1e-3, L.mean() / np.maximum(low, 1e-3), 1.0)
    K = _fold_samples(target_period_px)
    folded, _cons = _fold_profile(prof, float(ax.period_px), K)
    expected = _expected_profile(model, K)
    # 정렬 축퇴 해소 — 파스텔 패턴은 두 유채 줄의 L 딥이 거의 같아(61 vs 63) L-상관의
    # 최적점이 반주기 이중으로 나타나고, 오정렬 시 기대 중심에서 바탕을 읽어 '줄 소실'을
    # 오보한다(실측 3.2/9.3). 상관 상위 후보들을 **전체 프로파일 평균 ΔE00** 로 재판정한다.
    from .color import ciede2000 as _de00
    sig_m = np.sqrt(((folded - np.median(folded, axis=0)) ** 2).sum(axis=-1))
    sig_e = np.sqrt(((expected - np.median(expected, axis=0)) ** 2).sum(axis=-1))
    a = sig_m - sig_m.mean()
    b = sig_e - sig_e.mean()
    corr = np.fft.irfft(np.fft.rfft(a) * np.conj(np.fft.rfft(b)), n=K)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9
    order = np.argsort(corr)[::-1]
    cands, seen = [], []
    for idx in order[: K // 2]:
        if corr[idx] < 0.85 * corr[order[0]]:
            break
        if all(min(abs(idx - sj), K - abs(idx - sj)) > K // 20 for sj in seen):
            seen.append(int(idx)); cands.append(int(idx))
        if len(cands) >= 5:
            break
    def _mean_de(shift):
        rolled = np.roll(folded, -shift, axis=0).astype(np.float64)
        off = float(np.clip(np.median(expected[:, 0]) - np.median(rolled[:, 0]), -40, 40))
        rolled[:, 0] += off
        return float(np.mean(_de00(rolled, expected)))
    best = min(cands, key=_mean_de) if cands else int(np.argmax(corr))
    align_corr = float(corr[best] / denom)
    pm["align_corr"] = round(align_corr, 3)
    if align_corr < MIN_ALIGN_CORR:
        if strict:
            failures.append({"code": "pattern_metric_failed", "panel": panel.name,
                             "detail": f"기대 프로파일 정렬 상관 {align_corr:.2f} < {MIN_ALIGN_CORR}"})
        return pm, failures
    measured = np.roll(folded, -best, axis=0).astype(np.float64)
    # 잔류 음영·절대 휘도의 전역 L 자유도 제거 — panel 당 **오프셋 하나**만 허용한다.
    # 합성이 절대 L 을 carrier 저주파에 앵커하므로(흰 셔츠 ~L87 vs Detail 노출 ~L67)
    # 측정 베이스는 기대보다 ~20 높다. 곱셈 gain(α≈0.77)은 그 차이를 접는 대신
    # **대비까지 0.77배로 인위 축소**해 옳은 합성을 '줄 소실'로 오판했다(실측:
    # 합성본 라인스캔 대비 8.3/9.3 보존 vs QC 3.9 보고). 가산 오프셋은 베이스만 접고
    # 대비를 완전 보존한다. run 순서·소실 검사를 숨길 수 없음은 동일. chroma 불변.
    Lm, Le = measured[:, 0], expected[:, 0]
    offset = float(np.clip(np.median(Le) - np.median(Lm), -40.0, 40.0))
    measured[:, 0] = Lm + offset
    pm["l_offset_applied"] = round(offset, 2)

    # 3) run 별 guided 대조 — 기대 run 중앙 50% 구간의 실측 중앙값 색
    ground = np.asarray(model.ground_color_lab, np.float64)
    start = 0.0
    color_des, presence = [], []
    for i, (color, width) in enumerate(
            zip(model.color_sequence_lab, model.line_width_ratios)):
        c_lo = int((start + width * 0.25) * K)
        c_hi = max(c_lo + 1, int((start + width * 0.75) * K))
        got = np.median(measured[c_lo:c_hi], axis=0)
        want = np.asarray(color, np.float64)
        de = float(ciede2000(got, want))
        color_des.append(round(de, 2))
        if i > 0:
            want_contrast = float(ciede2000(want, ground))
            got_contrast = float(ciede2000(got, ground))
            ratio = got_contrast / max(want_contrast, 1e-6)
            presence.append(round(ratio, 3))
            if want_contrast >= 5.0 and ratio < LINE_PRESENCE_MIN_CONTRAST and strict:
                failures.append({
                    "code": "pattern_metric_failed", "panel": panel.name,
                    "detail": (f"줄 #{i} 대비 소실 (기대 ΔE {want_contrast:.1f}, "
                               f"실측 {got_contrast:.1f})")})
        start += width
    pm["color_delta_e00"] = color_des
    pm["line_contrast_ratio"] = presence
    if max(color_des) > COLOR_DELTA_E_MAX and strict:
        failures.append({"code": "pattern_metric_failed", "panel": panel.name,
                         "detail": f"대표색 ΔE00 {max(color_des):.1f} > {COLOR_DELTA_E_MAX}"})
    return pm, failures


def _interface_seam(alpha, painted, garment_mask, band_px: float) -> dict:
    """painted 내부 경계에서 alpha 전이가 **몇 픽셀에 걸치는가**.

    레벨 임계(alpha>0.98)도, 절대 기울기 임계도 진폭에 휘둘린다 — 0.65→0 한 픽셀 계단이
    "완만" 으로 분류됐다(독립 검수 실증). 전이의 급격도는 진폭과 무관해야 하므로,
    경계에서의 alpha 를 안쪽 깊은 곳의 alpha 로 **정규화**해서 본다. 제대로 페더하면
    경계(d≈1)의 alpha 는 안쪽의 1/band 수준이고, 계단이면 두 값이 같다 — 진폭이
    0.65 든 1.0 이든 비율은 1 이 된다.
    """
    if alpha is None or painted is None:
        return {}
    band = max(2.0, float(band_px))
    dist = cv2.distanceTransform(painted, cv2.DIST_L2, 3)
    inside = (garment_mask > 0) & (painted > 0)
    deep = inside & (dist >= band * 0.8) & (dist <= band * 2.5)
    # 경계에서의 표본을 한 거리(d<=1.5)로 고정하면, 계단을 그 밖으로 한 픽셀만 옮겨도
    # 표본이 alpha 0 만 잡아 비율이 0 이 된다. 밴드 안쪽 여러 깊이를 훑어 **최악** 을 쓴다.
    probes = [d for d in (1.5, 2.5, 4.0, band * 0.25, band * 0.5) if d < band * 0.8]
    edge = inside & (dist <= max(probes))
    if int(deep.sum()) < 50:
        # painted 가 feather 밴드보다 얇으면 전이를 정의할 깊이가 없다. 통과가 아니라
        # 측정 불가다 — 얇은 띠만 칠해 게이트를 비껴가는 경로를 닫는다.
        return {"seam_measurable": False, "seam_reason": "no_interior_depth"}
    a_deep = float(np.median(alpha[deep]))
    if a_deep < 1e-3:
        return {"seam_measurable": False, "seam_reason": "interior_alpha_zero"}
    # 제대로 페더하면 거리 d 에서 alpha ≈ d/band 다. 그래서 각 거리의 실측 alpha 를
    # **그 거리에서 기대되는 값**으로 나눈 초과분을 본다 — 정상 ramp 는 1 근처, 계단은
    # band/d 만큼 커진다. 계단을 어느 거리로 옮겨도 그 거리의 초과분이 뛴다.
    excesses = []
    for d in probes:
        band_sel = inside & (dist > max(0.0, d - 1.5)) & (dist <= d)
        if int(band_sel.sum()) < 50:
            continue
        observed = float(np.median(alpha[band_sel])) / a_deep
        expected = max(d / band, 1e-3)
        excesses.append(observed / expected)
    if not excesses:
        return {"seam_measurable": False, "seam_reason": "edge_samples"}
    ratio = max(excesses)
    a_edge = float(np.median(alpha[edge]))
    # 위치 비율만으로는 밴드 깊숙이 옮긴 계단을 못 잡는다(초과분이 band/d 로 줄어든다).
    # 기울기를 내부 alpha 로 정규화하면 진폭에도 위치에도 무관해진다: 폭 band 로 고르게
    # 페더하면 어디서나 1/band, 한 픽셀 계단이면 그 지점에서 1 이다.
    gx = cv2.Sobel(alpha.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(alpha.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3) / 8.0
    grad = np.hypot(gx, gy)[inside] / a_deep
    grad_norm = float(np.percentile(grad, 99)) if grad.size else 0.0
    return {"seam_measurable": True, "seam_px": int(edge.sum()),
            "seam_ramp_excess": round(ratio, 4),
            "seam_grad_norm": round(grad_norm, 4),
            "seam_alpha_edge": round(a_edge, 4),
            "seam_alpha_deep": round(a_deep, 4)}


def _boundary_chroma(out_bgr, painted, garment_mask, band_px: int) -> dict:
    """경계를 사이에 둔 **국소** 색 연속성.

    영역 중앙값 하나로 재면 +Δ 와 -Δ 가 상쇄한다. 타일로 쪼개도 타일 **안에서** 상쇄되게
    배치하면 다시 숨는다(독립 검수 실증: 실제 median ΔE00 23 인데 3.46 으로 보고). 그래서
    영역 통계 대신 **픽셀별 국소 비교**를 하고 상위 백분위를 본다 — 상쇄될 통계량이 없다.
    """
    if painted is None:
        return {}
    k = max(3, int(band_px) | 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    inner = ((cv2.erode(painted, kern) == 0) & (painted > 0) & (garment_mask > 0))
    outer = ((cv2.dilate(painted, kern) > 0) & (painted == 0) & (garment_mask > 0))
    if int(inner.sum()) < 50 or int(outer.sum()) < 50:
        return {}
    lab = bgr_to_lab(out_bgr).astype(np.float32)
    win = max(5, int(band_px) * 3 | 1)

    def _local_mean(mask):
        m = mask.astype(np.float32)
        num = cv2.boxFilter(lab * m[..., None], -1, (win, win), normalize=False)
        den = cv2.boxFilter(m, -1, (win, win), normalize=False)
        return num, den

    n_in, d_in = _local_mean(inner)
    n_out, d_out = _local_mean(outer)
    valid = (d_in > 20) & (d_out > 20) & inner
    if int(valid.sum()) < 50:
        return {}
    a = n_in[valid] / d_in[valid][:, None]
    b = n_out[valid] / d_out[valid][:, None]
    b_l_matched = np.stack([a[:, 0], b[:, 1], b[:, 2]], axis=1)   # L 은 맞추고 chroma 만
    des = np.asarray([float(ciede2000(a[i], b_l_matched[i])) for i in range(len(a))])
    severe = float((des > BOUNDARY_CHROMA_SEVERE_DE).mean())
    # L 을 맞추는 것은 높이 차이로 인한 음영을 빼기 위해서지, 휘도 단절을 눈감기 위해서가
    # 아니다. 경계를 사이에 둔 L 차이가 크면 그것도 눈에 보이는 이음매다 — 따로 잰다.
    dl = np.abs(a[:, 0] - b[:, 0])
    return {"boundary_chroma_severe_frac": round(severe, 4),
            "boundary_l_step_p95": round(float(np.percentile(dl, 95)), 2),
            "boundary_chroma_de00": round(float(np.percentile(des, 90)), 2),
            "boundary_chroma_de00_median": round(float(np.median(des)), 2),
            "boundary_chroma_samples": int(valid.sum()),
            "boundary_inner_px": int(inner.sum()),
            "boundary_outer_px": int(outer.sum())}


def _drape_preservation(out_bgr, carrier_bgr, garment_mask) -> dict:
    """carrier 의 주름·접힘 음영이 합성 후에도 남아 있는가 — **국소 최악값**으로.

    전역 상관/진폭은 국소 손상을 희석한다: 좌측 45% 만 평평하게 눌러도 전역 지표는
    통과했다(독립 검수 실증). 의류를 타일로 나눠 타일별 진폭비·상관을 재고 최악 타일로
    판정한다. 표본이 모자라면 통과가 아니라 **측정 불가**로 남긴다.
    """
    h, w = out_bgr.shape[:2]
    sigma = max(3.0, float(min(h, w)) * DRAPE_SIGMA_FRAC)
    # 저주파를 이미지 전체에서 뽑으면 실루엣 경계에서 배경 휘도가 섞여, 의류 내부를
    # 완전히 눌러도 진폭이 안 내려간다(실측 하한 0.63). 경계를 blur 반경만큼 침식한다.
    k = int(max(3, sigma * 2)) | 1
    interior = cv2.erode(garment_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    sel = interior > 0
    if int(sel.sum()) < 500:
        return {"drape_measurable": False}
    lo_f = cv2.GaussianBlur(
        cv2.cvtColor(out_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), sigmaX=sigma)
    lc_f = cv2.GaussianBlur(
        cv2.cvtColor(carrier_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), sigmaX=sigma)
    tiles = max(2, int(DRAPE_TILES))
    ys = np.linspace(0, h, tiles + 1, dtype=int)
    xs = np.linspace(0, w, tiles + 1, dtype=int)
    amps, corrs = [], []
    for i in range(tiles):
        for j in range(tiles):
            sl = (slice(ys[i], ys[i + 1]), slice(xs[j], xs[j + 1]))
            m = sel[sl]
            if int(m.sum()) < 200:
                continue
            a, b = lo_f[sl][m], lc_f[sl][m]
            b_sd = float(b.std())
            if b_sd < 0.5:      # carrier 가 그 타일에서 평탄하면 물어볼 것이 없다
                continue
            amps.append(float(a.std()) / b_sd)
            corrs.append(float(np.corrcoef(a, b)[0, 1]) if float(a.std()) >= 1e-3 else 0.0)
    if not amps:
        return {"drape_measurable": False}
    # 타일보다 작은 손상은 타일 평균에 희석된다. 창 단위 국소 표준편차비를 만들어
    # 하위 백분위를 보면, 타일 격자와 무관하게 국소 평탄화가 드러난다.
    win = int(max(9, sigma * 2)) | 1
    m = sel.astype(np.float32)
    def _local_sd(img):
        mu = cv2.boxFilter(img * m, -1, (win, win), normalize=False)
        n = cv2.boxFilter(m, -1, (win, win), normalize=False)
        mu2 = cv2.boxFilter(img * img * m, -1, (win, win), normalize=False)
        n = np.maximum(n, 1.0)
        return np.sqrt(np.maximum(mu2 / n - (mu / n) ** 2, 0.0)), n
    sd_o, n_o = _local_sd(lo_f)
    sd_c, _ = _local_sd(lc_f)
    ok = sel & (n_o > win * win * 0.5) & (sd_c > 0.5)
    local_ratio = None
    if int(ok.sum()) > 200:
        local_ratio = float(np.percentile(sd_o[ok] / sd_c[ok], 2))
    return {"drape_measurable": True, "drape_tiles": len(amps),
            "drape_local_amp_p2": (round(local_ratio, 4) if local_ratio is not None else None),
            "drape_amp_ratio": round(min(amps), 4),
            "drape_corr": round(min(corrs), 4),
            "drape_amp_ratio_median": round(float(np.median(amps)), 4)}


def verify_composite(
    out_bgr: np.ndarray,
    carrier_bgr: np.ndarray,
    panel_map,
    model: StripeModel,
    *,
    target_period_px: float,
    target_axis: str,
    painted_mask: np.ndarray | None = None,
    coverage_mask: np.ndarray | None = None,
    alpha: np.ndarray | None = None,
    component_scale_metrics: dict | None = None,
    inner_feather_px: float | None = None,
    component_boxes: dict | None = None,
) -> DeterministicQC:
    """합성 결과 재측정 → typed critical. 실패는 기록이지 예외가 아니다(호출자가 라우팅).

    painted_mask 가 오면 패널 측정 표본 = 합성이 **실제 칠한** 픽셀로 제한한다 —
    보호 영역(커프스 밴드 등)은 설계상 미페인트라, mask 전체로 재면 그 밴드가
    '대비 소실'로 오판된다(실측: 커프스 보호 도입 시 G4 sleeve_r 오탐).
    """
    failures: list[dict] = []
    metrics: dict = {"per_panel": {}}

    sample_mask = panel_map.garment_mask
    if painted_mask is not None:
        sample_mask = cv2.bitwise_and(sample_mask, painted_mask)
    component_mask = None
    if component_boxes:
        component_mask = np.zeros(sample_mask.shape[:2], np.uint8)
        for quad in component_boxes.values():
            q = np.asarray(quad, np.float32)
            if q.shape == (4, 2):
                cv2.fillPoly(component_mask, [q.astype(np.int32)], 255)
        # Torso/sleeve model QC and decal QC have separate authorities. Mixing decal
        # pixels into the torso fold made collar area change the measured body colors.
    for panel in panel_map.panels:
        if panel.kind != "stripe":
            continue
        pm, fs = _measure_panel_local(
            out_bgr, panel, model,
            target_period_px=target_period_px, target_axis=target_axis,
            garment_mask=sample_mask, exclude_mask=component_mask)
        metrics["per_panel"][panel.name] = pm
        failures.extend(fs)

    # 순도 게이트가 전 패널을 advisory 로 만들면 검증 자체가 성립하지 않는다 —
    # 최소 1개 패널은 strict 로 실측 통과해야 pass 다(전 패널 저순도 = mask/quad 부실).
    strict_ok = [n for n, m in metrics["per_panel"].items()
                 if m.get("strict") and "color_delta_e00" in m]
    if not strict_ok:
        failures.append({"code": "pattern_metric_failed",
                         "detail": "strict 순도로 검증된 패널이 0개 — 측정 성립 불가"})
    elif painted_mask is not None:
        # "한 패널만 strict 면 통과" 는 넓은 몸통이 advisory 로 강등돼도 좁은 소매 하나로
        # 전체가 통과하는 구멍이다. 칠한 픽셀의 상당 지분을 가진 패널은 반드시 실측돼야 한다.
        total_painted = max(1, int((painted_mask > 0).sum()))
        for panel in panel_map.panels:
            if panel.kind != "stripe" or panel.name in strict_ok:
                continue
            quad = np.zeros(painted_mask.shape[:2], np.uint8)
            cv2.fillPoly(quad, [panel.quad.astype(np.int32)], 255)
            share = float(((quad > 0) & (painted_mask > 0)).sum()) / total_painted
            if share >= STRICT_PANEL_MIN_FRAC:
                failures.append({
                    "code": "pattern_metric_failed", "panel": panel.name,
                    "detail": (f"칠한 면적의 {share:.0%} 를 차지하는 패널이 strict 미검증 "
                               f"(>= {STRICT_PANEL_MIN_FRAC:.0%})")})
    period_errs = [
        float(m["repeat_period_rel_err"])
        for m in metrics["per_panel"].values()
        if "repeat_period_rel_err" in m
    ]
    repeat_errs = [
        abs(float(m["measured_repeats"]) - float(m["expected_repeats"]))
        / max(float(m["expected_repeats"]), 1e-6)
        for m in metrics["per_panel"].values()
        if "measured_repeats" in m and "expected_repeats" in m
    ]
    direction_errs = [
        float(m["direction_error"])
        for m in metrics["per_panel"].values()
        if "direction_error" in m
    ]
    color_des = [
        float(de)
        for m in metrics["per_panel"].values()
        for de in m.get("color_delta_e00", [])
    ]
    if period_errs:
        metrics["period_rel_err_max"] = round(max(period_errs), 4)
    if repeat_errs:
        metrics["repeat_count_rel_err_max"] = round(max(repeat_errs), 4)
    if direction_errs:
        worst_dir = max(direction_errs)
        metrics["direction_error_max"] = round(worst_dir, 4)
        # 기록만 하고 어떤 상수와도 비교하지 않던 지표 — 줄 방향이 틀려도 통과했다.
        if worst_dir > DIRECTION_ERROR_MAX:
            failures.append({"code": "pattern_metric_failed",
                             "detail": f"줄 방향 오차 {worst_dir:.3f} > {DIRECTION_ERROR_MAX}"})
    if color_des:
        metrics["color_delta_e00_max"] = round(max(color_des), 2)
        metrics["color_delta_e00_median"] = round(float(np.median(color_des)), 2)
    if painted_mask is not None:
        garment = (
            coverage_mask > 0
            if coverage_mask is not None
            else panel_map.garment_mask > 0
        )
        metrics["mask_coverage"] = round(
            float(((painted_mask > 0) & garment).sum()) / max(1, int(garment.sum())), 4)
        # coverage=1.0 은 "칠하려던 곳은 다 칠했다" 는 동어반복이다 — 분모가 feather 밴드·
        # component·커프를 이미 뺀 core 이기 때문. 의류 전체 대비 실제 도포율과 제외 비율을
        # 함께 남겨야 그 1.0 이 품질 보증으로 오독되지 않는다.
        full = panel_map.garment_mask > 0
        full_n = max(1, int(full.sum()))
        metrics["garment_coverage"] = round(
            float(((painted_mask > 0) & full).sum()) / full_n, 4)
        metrics["coverage_excluded_frac"] = round(
            float(full_n - int(garment.sum())) / full_n, 4)

    cross_input = component_scale_metrics or panel_map.metrics.get("cross_surface_scale")
    if cross_input:
        cross = cross_input
        if "components" not in cross:
            cross = {"components": cross_input}
        # Copy before enriching so QA metadata remains a snapshot, not a mutation of the
        # PanelMap object shared with other checks.
        cross = {**cross, "components": {
            name: dict(value) for name, value in (cross.get("components") or {}).items()
        }}
        metrics["cross_surface_scale"] = cross
        for name, cm in (cross.get("components") or {}).items():
            if not cm.get("scale_measurable", False):
                failures.append({
                    "code": "pattern_metric_failed",
                    "panel": name,
                    "detail": (
                        f"{name} component stripe scale unmeasurable "
                        f"({cm.get('reason', 'unknown')})"
                    ),
                })
                continue
            target_quad = cm.get("target_quad")
            if target_quad is None and component_boxes:
                target_quad = component_boxes.get(name)
            if target_quad is not None:
                measured = _component_output_scale(
                    out_bgr, target_quad, model,
                    target_period_px=target_period_px, target_axis=target_axis,
                    target_axis_unit=cm.get("target_pattern_axis_unit"),
                    painted_mask=painted_mask,
                    alpha=alpha)
                cm.update(measured)
            elif "final_period_px" not in cm:
                cm.update({"scale_measurable": False,
                           "reason": "final_output_geometry_missing"})
            if not cm.get("scale_measurable", False):
                failures.append({
                    "code": "pattern_metric_failed", "panel": name,
                    "detail": (
                        f"{name} final component stripe scale unmeasurable "
                        f"({cm.get('reason', 'unknown')})"
                    ),
                })
                continue
            period_err = float(cm.get("period_rel_err", 1.0))
            width_err = float(cm.get("stripe_width_rel_err", 1.0))
            width_err_px = float(cm.get(
                "stripe_width_error_px", width_err * target_period_px))
            width_tol_px = max(2.5, CROSS_SURFACE_STRIPE_WIDTH_TOL * target_period_px)
            if period_err > CROSS_SURFACE_PERIOD_TOL:
                failures.append({
                    "code": "pattern_metric_failed",
                    "panel": name,
                    "detail": (
                        f"{name} component period error {period_err:.3f} "
                        f"> {CROSS_SURFACE_PERIOD_TOL}"
                    ),
                })
            if width_err_px > width_tol_px:
                failures.append({
                    "code": "pattern_metric_failed",
                    "panel": name,
                    "detail": (
                        f"{name} component stripe-width error {width_err_px:.2f}px "
                        f"> {width_tol_px:.2f}px (relative={width_err:.3f})"
                    ),
                })

    band = int(max(3, panel_map.metrics.get("boundary_band_px", 4)))
    # 계면 전이의 기준 폭은 합성기가 실제로 쓴 내부 feather 폭이다. 실루엣 밴드로
    # 재면, 얇은 부위 영역에 일부러 좁게 먹인 ramp 가 "계단" 으로 오판된다 — 기준이
    # 틀린 것이지 합성이 계단인 것이 아니다(진폭·위치 무관 지표는 정상을 가리킨다).
    seam_band = float(inner_feather_px) if inner_feather_px else float(band)
    seam = _interface_seam(alpha, painted_mask, panel_map.garment_mask, seam_band)
    if inner_feather_px:
        seam["seam_band_px"] = round(float(inner_feather_px), 2)
    metrics.update(seam)
    if seam.get("seam_measurable") is False:
        failures.append({"code": "interface_seam",
                         "detail": f"계면 전이를 측정할 수 없음 ({seam.get('seam_reason')})"})
    elif seam.get("seam_grad_norm", 0.0) > SEAM_GRAD_NORM_MAX:
        failures.append({
            "code": "interface_seam",
            "detail": (f"정규화 alpha 기울기 {seam['seam_grad_norm']:.2f} "
                       f"> {SEAM_GRAD_NORM_MAX} — 한 픽셀에서 떨어지는 계단이다")})
    elif seam.get("seam_ramp_excess", 0.0) > SEAM_RAMP_EXCESS_MAX:
        failures.append({
            "code": "interface_seam",
            "detail": (f"경계 alpha 가 기대 ramp 의 {seam['seam_ramp_excess']:.1f}배 "
                       f"(> {SEAM_RAMP_EXCESS_MAX}) — 전이 없이 계단으로 떨어진다")})
    chroma = _boundary_chroma(out_bgr, painted_mask, panel_map.garment_mask, band)
    metrics.update(chroma)
    if chroma.get("boundary_chroma_severe_frac", 0.0) > BOUNDARY_CHROMA_SEVERE_FRAC_MAX:
        failures.append({
            "code": "boundary_chroma_discontinuity",
            "detail": (f"경계의 {chroma['boundary_chroma_severe_frac']:.1%} 가 ΔE00 "
                       f"{BOUNDARY_CHROMA_SEVERE_DE} 초과 — 국소 색 단절")})
    elif chroma.get("boundary_chroma_de00", 0.0) > BOUNDARY_CHROMA_DE_MAX:
        failures.append({
            "code": "boundary_chroma_discontinuity",
            "detail": (f"경계 양쪽 chroma ΔE00 {chroma['boundary_chroma_de00']:.1f} "
                       f"> {BOUNDARY_CHROMA_DE_MAX} — 한 벌로 보이지 않는다")})

    drape = _drape_preservation(out_bgr, carrier_bgr, panel_map.garment_mask)
    metrics.update(drape)
    if drape.get("drape_measurable") is False:
        failures.append({"code": "drape_lost",
                         "detail": "carrier 음영이 없어 드레이프 보존을 검증할 수 없음"})
    elif "drape_amp_ratio" in drape:
        if drape["drape_corr"] < DRAPE_CORR_MIN:
            failures.append({
                "code": "drape_lost",
                "detail": (f"최악 타일 저주파 상관 {drape['drape_corr']:.2f} "
                           f"< {DRAPE_CORR_MIN} — 접힘 음영이 뒤집혔다")})
        loc = drape.get("drape_local_amp_p2")
        if loc is not None and loc < DRAPE_LOCAL_AMP_MIN:
            failures.append({
                "code": "drape_lost",
                "detail": (f"국소 저주파 진폭비 p2 {loc:.2f} < {DRAPE_LOCAL_AMP_MIN} "
                           f"— 타일보다 작은 영역이 평면화됐다")})
        if drape["drape_amp_ratio"] < DRAPE_AMP_MIN:
            failures.append({
                "code": "drape_lost",
                "detail": (f"저주파 진폭비 {drape['drape_amp_ratio']:.2f} "
                           f"< {DRAPE_AMP_MIN} — 최악 타일에서 주름이 눌려 평면화됐다")})

    outside = panel_map.garment_mask == 0
    if outside.any():
        de = delta_e76(bgr_to_lab(out_bgr)[outside], bgr_to_lab(carrier_bgr)[outside])
        drift_frac = float((de > OUTSIDE_DRIFT_DELTA_E).mean())
        mean_de = float(de.mean())
        metrics["outside_drift_frac"] = round(drift_frac, 5)
        metrics["outside_mean_de76"] = round(mean_de, 3)
        if drift_frac > OUTSIDE_DRIFT_MAX_FRAC:
            failures.append({"code": "protected_region_drift",
                             "detail": f"mask 밖 ΔE76>10 비율 {drift_frac:.4f}"})
        # 픽셀-임계 하나만 보면 임계 바로 밑의 **균일 틴트**(예: 12% 블렌드 누출 ≈ ΔE76 9)가
        # 통째로 숨는다(mutation 실측 — HM7 생존). 설계상 mask 밖은 carrier 와 정확히
        # 동일해야 하므로 평균 드리프트는 사실상 0 이다 — 1.5 는 인코딩 여유일 뿐.
        if mean_de > 1.5:
            failures.append({"code": "protected_region_drift",
                             "detail": f"mask 밖 평균 ΔE76 {mean_de:.2f} > 1.5 (균일 누출)"})
        metrics["outside_ssim"] = round(ssim_gray(
            out_bgr, carrier_bgr, mask=(panel_map.garment_mask == 0).astype(np.uint8) * 255), 4)

    return DeterministicQC(
        passed=not failures,
        metrics={**metrics, "failure_details": failures},
        failures=tuple(sorted({f["code"] for f in failures})),
    )
