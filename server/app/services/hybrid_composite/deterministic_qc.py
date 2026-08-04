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
from .stripe_model import _autocorr_period, _detrended_profile, _fold_profile, _fold_samples
from .types import QC_VERSION, StripeModel

REPEAT_COUNT_TOL = 0.15        # panel 주기 상대 오차 상한 (live gate 와 동일)
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
SEAM_HARD_EDGE_MAX = 0.25      # painted 내부 경계에서 alpha 가 계단인 픽셀 허용 비율
BOUNDARY_CHROMA_DE_MAX = 10.0  # painted↔인접 unpainted 밴드 ΔE00 (L 정렬 후 chroma)
DRAPE_CORR_MIN = 0.60          # carrier↔output 저주파 L 상관 하한 (의류 내부)
STRICT_PANEL_MIN_FRAC = 0.20   # 칠한 픽셀의 이 비율 이상을 가진 패널은 strict 필수
DRAPE_SIGMA_FRAC = 0.03        # 드레이프 측정용 저주파 척도 (짧은 변 대비)


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


def _measure_panel_local(
    out_bgr: np.ndarray, panel, model: StripeModel, *,
    target_period_px: float, target_axis: str, garment_mask: np.ndarray | None = None,
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
    if garment_mask is not None:
        local_mask = cv2.warpPerspective(garment_mask, Hinv, (bw2, bh2),
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
    det = _detrended_profile(lab[..., 0], axis=mean_axis)
    ax = _autocorr_period(det)
    orth = _autocorr_period(_detrended_profile(lab[..., 0], axis=other_mean_axis))
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
    prof = lab.mean(axis=mean_axis)
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


def _interface_seam(alpha, painted, garment_mask) -> dict:
    """painted 영역의 **내부** 경계에서 alpha 가 계단인지 경사인지.

    panel assign-cost 등고선은 이미지 공간에서 직선이라, 여기서 alpha 가 1픽셀에
    1→0 으로 떨어지면 결과가 '붙여넣은 직사각형 판' 으로 보인다. 기존 검사는 panel
    가장자리 15% 를 잘라내고 재기 때문에 이 구간을 구조적으로 보지 못했다 — 그래서
    결함 위치를 자르지 않고 경계 **위에서** 잰다.
    """
    if alpha is None or painted is None:
        return {}
    kern = np.ones((3, 3), np.uint8)
    rim = (garment_mask > 0) & (painted > 0) & (cv2.erode(painted, kern) == 0)
    n = int(rim.sum())
    if n < 50:
        return {}
    hard = float((alpha[rim] > 0.98).mean())
    return {"seam_px": n, "seam_hard_edge_frac": round(hard, 4)}


def _boundary_chroma(out_bgr, painted, garment_mask, band_px: int) -> dict:
    """경계 안쪽(painted)과 바깥쪽(같은 의류의 unpainted)의 색 연속성.

    painted 를 자기 source 모델과만 비교하면 '몸통은 그늘색, 커프는 스튜디오색'인
    상태가 만점을 받는다(v6). 인접한 carrier 와 직접 비교해야 한 벌로 보이는지 알 수 있다.
    두 밴드는 높이가 달라 음영(L)이 다르므로 L 은 맞추고 chroma 만 본다.
    """
    if painted is None:
        return {}
    k = max(3, int(band_px) | 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    inner = (cv2.erode(painted, kern) == 0) & (painted > 0) & (garment_mask > 0)
    outer = (cv2.dilate(painted, kern) > 0) & (painted == 0) & (garment_mask > 0)
    if inner.sum() < 50 or outer.sum() < 50:
        return {}
    lab = bgr_to_lab(out_bgr)
    a = np.median(lab[inner], axis=0)
    b = np.median(lab[outer], axis=0)
    de = float(ciede2000(a, np.array([a[0], b[1], b[2]], np.float64)))
    return {"boundary_chroma_de00": round(de, 2),
            "boundary_inner_px": int(inner.sum()),
            "boundary_outer_px": int(outer.sum())}


def _drape_preservation(out_bgr, carrier_bgr, garment_mask) -> dict:
    """carrier 의 주름·접힘 음영이 합성 후에도 남아 있는가.

    패턴 지표는 L 을 두 번 정규화해 없애므로, 음영이 사라질수록 오히려 점수가 좋아진다.
    의류 **내부**에서 carrier 와 저주파 L 을 직접 비교해 그 역전을 막는다.
    """
    sel = garment_mask > 0
    if int(sel.sum()) < 500:
        return {}
    h, w = out_bgr.shape[:2]
    sigma = max(3.0, float(min(h, w)) * DRAPE_SIGMA_FRAC)
    lo = cv2.GaussianBlur(cv2.cvtColor(out_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
                          (0, 0), sigmaX=sigma)[sel]
    lc = cv2.GaussianBlur(cv2.cvtColor(carrier_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
                          (0, 0), sigmaX=sigma)[sel]
    if float(lo.std()) < 1e-3 or float(lc.std()) < 1e-3:
        return {}
    return {"drape_corr": round(float(np.corrcoef(lo, lc)[0, 1]), 4)}


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
    for panel in panel_map.panels:
        if panel.kind != "stripe":
            continue
        pm, fs = _measure_panel_local(
            out_bgr, panel, model,
            target_period_px=target_period_px, target_axis=target_axis,
            garment_mask=sample_mask)
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

    seam = _interface_seam(alpha, painted_mask, panel_map.garment_mask)
    metrics.update(seam)
    if seam.get("seam_hard_edge_frac", 0.0) > SEAM_HARD_EDGE_MAX:
        failures.append({
            "code": "interface_seam",
            "detail": (f"painted 내부 경계의 {seam['seam_hard_edge_frac']:.0%} 가 계단 "
                       f"(> {SEAM_HARD_EDGE_MAX:.0%}) — 직선 이음매로 보인다")})

    band = int(max(3, panel_map.metrics.get("boundary_band_px", 4)))
    chroma = _boundary_chroma(out_bgr, painted_mask, panel_map.garment_mask, band)
    metrics.update(chroma)
    if chroma.get("boundary_chroma_de00", 0.0) > BOUNDARY_CHROMA_DE_MAX:
        failures.append({
            "code": "boundary_chroma_discontinuity",
            "detail": (f"경계 양쪽 chroma ΔE00 {chroma['boundary_chroma_de00']:.1f} "
                       f"> {BOUNDARY_CHROMA_DE_MAX} — 한 벌로 보이지 않는다")})

    drape = _drape_preservation(out_bgr, carrier_bgr, panel_map.garment_mask)
    metrics.update(drape)
    if "drape_corr" in drape and drape["drape_corr"] < DRAPE_CORR_MIN:
        failures.append({
            "code": "drape_lost",
            "detail": (f"carrier 대비 저주파 L 상관 {drape['drape_corr']:.2f} "
                       f"< {DRAPE_CORR_MIN} — 주름·음영이 평면화됐다")})

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
