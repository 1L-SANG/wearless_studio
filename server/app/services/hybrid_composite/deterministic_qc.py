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
    span = local.shape[0] if target_axis == "horizontal" else local.shape[1]
    pm: dict = {"local_size": [local.shape[1], local.shape[0]], "supersample": ss,
                "expected_repeats": round(span / target_period_px, 2),
                "mask_purity": None if purity is None else round(purity, 3),
                "strict": strict}

    # 1) 주기 — 실측 autocorr vs 목표
    det = _detrended_profile(lab[..., 0], axis=mean_axis)
    ax = _autocorr_period(det)
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


def verify_composite(
    out_bgr: np.ndarray,
    carrier_bgr: np.ndarray,
    panel_map,
    model: StripeModel,
    *,
    target_period_px: float,
    target_axis: str,
) -> DeterministicQC:
    """합성 결과 재측정 → typed critical. 실패는 기록이지 예외가 아니다(호출자가 라우팅)."""
    failures: list[dict] = []
    metrics: dict = {"per_panel": {}}

    for panel in panel_map.panels:
        if panel.kind != "stripe":
            continue
        pm, fs = _measure_panel_local(
            out_bgr, panel, model,
            target_period_px=target_period_px, target_axis=target_axis,
            garment_mask=panel_map.garment_mask)
        metrics["per_panel"][panel.name] = pm
        failures.extend(fs)

    # 순도 게이트가 전 패널을 advisory 로 만들면 검증 자체가 성립하지 않는다 —
    # 최소 1개 패널은 strict 로 실측 통과해야 pass 다(전 패널 저순도 = mask/quad 부실).
    strict_ok = [n for n, m in metrics["per_panel"].items()
                 if m.get("strict") and "color_delta_e00" in m]
    if not strict_ok:
        failures.append({"code": "pattern_metric_failed",
                         "detail": "strict 순도로 검증된 패널이 0개 — 측정 성립 불가"})

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
