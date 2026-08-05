"""Stage 4 — source 패턴의 결정론적 warp·shading transfer·보호 합성 (OpenCV, 생성 호출 0).

계약:
  · 의류 내부(protected)의 색·순서·주기는 **StripeModel 에서만** 온다. carrier 에서 가져오는
    것은 저주파 luminance(주름·음영)뿐이다 — chroma/고주파를 carrier 에서 가져오면 잘못된
    패턴이 다시 섞인다.
  · collar/placket/cuff 는 torso 타일을 이어 붙이지 않는다. source ROI 의 실제 픽셀을
    decal 로 warp 하거나, 불가하면 component 를 needs_review 로 남긴다(재그리기 금지).
  · warp 유효성(사상 방향·과신장)을 검출해 fail closed.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import bgr_to_lab, lab_to_bgr
from .panel_map import PanelMap
from .stripe_model import _fold_profile, _fold_samples
from .types import CompositeFailure, StripeModel, WARP_VERSION

MAX_LOCAL_STRETCH = 2.0        # protected 내부에서 허용하는 국소 신장 상한
MAX_STRETCH_FRAC = 0.01        # stretch 초과 픽셀 허용 비율 (< 1%)
MIN_SOURCE_COVERAGE = 0.90     # protected 내부 source-derived 비율 하한
# decal source ROI 적격성 — 단순 "짧은 변 48px" 은 가늘고 긴 플래킷을 구조적으로
# 거절한다(플래킷 실측 종횡비 1:15~1:20). 형상으로 판정한다: 얇아도 길면 원단 정보량은
# 충분하고, 반대로 짧은 변이 극단적으로 얇으면 어떤 길이여도 무늬를 옮길 수 없다.
MIN_DECAL_SHORT_SIDE_PX = 16   # 이보다 얇으면 원단 무늬 자체가 표본에 안 담긴다
MIN_DECAL_AREA_PX = 2304       # 48×48 상당 — 총 정보량 하한
MAX_DECAL_ASPECT = 30.0        # 이보다 가늘고 길면 landmark 오지정으로 본다
MIN_DECAL_SCALE = 0.9          # source 면적 / target 면적 — 확대 합성 금지(선명도 손실)
MAX_DECAL_SCALE_RESAMPLE = 4.0  # source 픽셀 재표본으로 맞출 수 있는 주기 보정 상한
# painted 내부 계면의 alpha 전이 폭(줄 주기 배수). 주기에 비례시키는 이유는 해상도
# 독립성이다 — 고정 픽셀이면 1K 에서 적당한 값이 4K 에서 계단으로 남는다. 1.25 주기는
# 이음매를 지우면서도 줄 한 쌍 이상을 흐리지 않는 폭이다.
INNER_FEATHER_PERIODS = 1.25
# painted 와 carrier 의 chroma(Lab a/b) 중앙값 차 상한. 같은 옷의 조명 차이는 이 안에서
# 흡수하고, 넘어가면 다른 옷·잘못된 source 로 보고 fail-closed 한다.
MAX_CHROMA_CAST = 18.0
# shading transfer 의 저주파 척도 — 이미지 짧은 변 대비 비율.
#   하한 1.8% : 기존 1K 실측(짧은 변 848px 에서 15px)과 같은 비율. 줄 주기가 이미지에
#               비해 아주 촘촘할 때도 carrier 고주파가 휘도 앵커에 새지 않게 한다.
#   상한 8%   : 주름·드레이프는 짧은 변의 10~25% 규모라, 그보다 작게 유지해야 접힘 음영이
#               저주파에 남는다. 넘어가면 옷이 평면으로 보인다.
SHADING_SIGMA_MIN_FRAC = 0.018
SHADING_SIGMA_MAX_FRAC = 0.08
MIN_COMPONENT_AXIS_COHERENCE = 0.16
MAX_COMPONENT_PROFILE_FIT = 30.0


@dataclass(frozen=True)
class CompositeArtifacts:
    image_bgr: np.ndarray
    alpha: np.ndarray                # 최종 합성 가중치 (0~1)
    painted: np.ndarray              # source-derived 픽셀 (0/255)
    coverage_scope: np.ndarray       # source-derived 여야 하는 투영 가능 core (0/255)
    panel_metrics: dict
    components_needing_review: tuple
    component_review_reasons: dict
    source_coverage: float
    version: str = WARP_VERSION
    metrics: dict = field(default_factory=dict)


def synthesize_pattern_lab(
    model: StripeModel, width: int, height: int, period_px: float, axis: str,
) -> np.ndarray:
    """panel-local 공간에 패턴 생성 — folded profile 을 주기 반복 샘플링 (Lab)."""
    K = len(model.period_profile_lab)
    if axis == "horizontal":
        coord = np.arange(height, dtype=np.float64)
        phase = (coord / period_px * K) % K
        i0 = np.floor(phase).astype(int) % K
        i1 = (i0 + 1) % K
        frac = (phase - np.floor(phase)).reshape(-1, 1)
        line = model.period_profile_lab[i0] * (1 - frac) + model.period_profile_lab[i1] * frac
        return np.repeat(line[:, None, :], width, axis=1).astype(np.float32)
    coord = np.arange(width, dtype=np.float64)
    phase = (coord / period_px * K) % K
    i0 = np.floor(phase).astype(int) % K
    i1 = (i0 + 1) % K
    frac = (phase - np.floor(phase)).reshape(-1, 1)
    line = model.period_profile_lab[i0] * (1 - frac) + model.period_profile_lab[i1] * frac
    return np.repeat(line[None, :, :], height, axis=0).astype(np.float32)


def _homography_validity(H: np.ndarray, w: int, h: int, quad_area: float) -> dict:
    """단위 사각형→quad 사상의 det/stretch 를 격자에서 검사 (해석적 Jacobian)."""
    xs = np.linspace(0, w - 1, 12)
    ys = np.linspace(0, h - 1, 12)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)], axis=0)
    a, b, c = H[0], H[1], H[2]
    denom = c @ pts
    du_dx = (a[0] * denom - (a @ pts) * c[0]) / denom ** 2
    du_dy = (a[1] * denom - (a @ pts) * c[1]) / denom ** 2
    dv_dx = (b[0] * denom - (b @ pts) * c[0]) / denom ** 2
    dv_dy = (b[1] * denom - (b @ pts) * c[1]) / denom ** 2
    det = du_dx * dv_dy - du_dy * dv_dx
    sx = np.hypot(du_dx, dv_dx)
    sy = np.hypot(du_dy, dv_dy)
    stretch = np.maximum(sx, sy) / np.maximum(np.minimum(sx, sy), 1e-9)
    # 스케일 정규화 — panel 전체가 커지는 것은 신장이 아니다. 이방성(가로세로 불균형)만 잰다.
    return {
        "neg_jacobian": int((det <= 0).sum()),
        "anisotropy_p99": float(np.percentile(stretch, 99)),
        "stretch_over_frac": float((stretch > MAX_LOCAL_STRETCH).mean()),
    }


def _quad_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _decal_source_eligible(sq: np.ndarray, tq: np.ndarray) -> tuple[bool, str]:
    """component decal 의 source ROI 가 쓸 만한가 — 형상으로 본다 (순수).

    플래킷은 가늘고 길다. 짧은 변 하나로 자르면 정상 플래킷이 매번 걸린다. 대신
    (짧은 변, 면적, 종횡비, 확대율)을 함께 본다.
    """
    e1 = float(np.linalg.norm(sq[1] - sq[0]))
    e2 = float(np.linalg.norm(sq[3] - sq[0]))
    short, long_ = min(e1, e2), max(e1, e2)
    if short < MIN_DECAL_SHORT_SIDE_PX:
        return False, f"short_side_{short:.0f}px"
    area = _quad_area(sq)
    if area < MIN_DECAL_AREA_PX:
        return False, f"area_{area:.0f}px2"
    if long_ / max(short, 1e-6) > MAX_DECAL_ASPECT:
        return False, f"aspect_{long_ / max(short, 1e-6):.0f}"
    if area < _quad_area(tq) * MIN_DECAL_SCALE:
        return False, "upscale_from_source"
    return True, ""


def _apply_homography(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    ph = np.concatenate([pts.astype(np.float64), np.ones((len(pts), 1))], axis=1)
    out = (H @ ph.T).T
    return (out[:, :2] / np.maximum(out[:, 2:3], 1e-9)).astype(np.float32)


def _expected_component_profile(model: StripeModel, K: int) -> np.ndarray:
    src = np.asarray(model.period_profile_lab, np.float64)
    idx = np.linspace(0, len(src), K, endpoint=False)
    i0 = np.floor(idx).astype(int) % len(src)
    i1 = (i0 + 1) % len(src)
    frac = (idx - np.floor(idx)).reshape(-1, 1)
    return src[i0] * (1 - frac) + src[i1] * frac


def _profile_along_unit(lab: np.ndarray, unit: np.ndarray) -> np.ndarray | None:
    """Arbitrary stripe-normal 1D profile from a central component interior."""
    h, w = lab.shape[:2]
    if min(h, w) < 18:
        return None
    yy, xx = np.indices((h, w), dtype=np.float32)
    margin = max(2, int(round(min(h, w) * 0.08)))
    valid = (xx >= margin) & (xx < w - margin) & (yy >= margin) & (yy < h - margin)
    t = xx * float(unit[0]) + yy * float(unit[1])
    p = -xx * float(unit[1]) + yy * float(unit[0])
    # End caps of a diagonal rectangle have very little support and inject border edges.
    pv = p[valid]
    if pv.size < 256:
        return None
    plo, phi = np.percentile(pv, [12, 88])
    valid &= (p >= plo) & (p <= phi)
    tv = t[valid]
    if tv.size < 256:
        return None
    t0 = int(np.floor(tv.min()))
    bins = np.floor(tv - t0).astype(np.int32)
    n = int(bins.max()) + 1
    counts = np.bincount(bins, minlength=n).astype(np.float64)
    prof = np.stack([
        np.bincount(bins, weights=lab[..., c][valid], minlength=n)
        for c in range(3)
    ], axis=1) / np.maximum(counts[:, None], 1.0)
    good = counts >= max(3.0, float(np.percentile(counts[counts > 0], 35)) * 0.35)
    if int(good.sum()) < 32:
        return None
    idx = np.arange(n)
    for c in range(3):
        prof[~good, c] = np.interp(idx[~good], idx[good], prof[good, c])
    lo, hi = int(n * 0.08), int(n * 0.92)
    prof = prof[lo:hi]
    return prof if len(prof) >= 32 else None


def _fit_component_period(
    profile: np.ndarray, model: StripeModel, *, expected_period_px: float,
) -> tuple[float, float] | None:
    lo = max(3.0, expected_period_px * 0.50)
    hi = min(len(profile) / 2.0, expected_period_px * 1.80)
    if hi <= lo:
        return None
    best: tuple[float, float] | None = None
    for period in np.arange(lo, hi + 0.001, 0.5):
        K = max(64, _fold_samples(float(period)))
        folded, consistency = _fold_profile(profile, float(period), K)
        expected = _expected_component_profile(model, K)
        for shift in range(K):
            candidate = np.roll(folded, -shift, axis=0).astype(np.float64)
            candidate += np.median(expected - candidate, axis=0)
            cost = float(np.mean(np.linalg.norm(candidate - expected, axis=1)))
            score = cost + max(0.0, 0.45 - float(consistency)) * 12.0
            if best is None or score < best[0]:
                best = (score, float(period))
    if best is None or best[0] > MAX_COMPONENT_PROFILE_FIT:
        return None
    return best[1], best[0]


def _component_pattern_geometry(
    source_bgr: np.ndarray, sq: np.ndarray, model: StripeModel,
) -> dict | None:
    """Detect the component's own stripe normal and period; do not inherit torso angle.

    Collar fabric is commonly cut on the bias and cuffs rotate the same cloth by 90°.
    Their direction is therefore source evidence, while their physical period is normalized
    to the torso target later. Returning source-image geometry keeps both truths separate.
    """
    bw = int(max(np.linalg.norm(sq[1] - sq[0]), np.linalg.norm(sq[2] - sq[3]))) + 1
    bh = int(max(np.linalg.norm(sq[3] - sq[0]), np.linalg.norm(sq[2] - sq[1]))) + 1
    if min(bw, bh) < MIN_DECAL_SHORT_SIDE_PX:
        return None
    rect = np.float32([[0, 0], [bw - 1, 0], [bw - 1, bh - 1], [0, bh - 1]])
    H_to_local = cv2.getPerspectiveTransform(sq, rect)
    local = cv2.warpPerspective(source_bgr, H_to_local, (bw, bh), flags=cv2.INTER_CUBIC)
    lab = bgr_to_lab(local).astype(np.float32)
    margin = max(2, int(round(min(bh, bw) * 0.10)))
    core = lab[margin:bh - margin, margin:bw - margin]
    if min(core.shape[:2]) < 12:
        return None
    tensors = np.zeros((2, 2), np.float64)
    for channel in range(3):
        plane = core[..., channel]
        scale = max(float(np.std(plane)), 1.0)
        gx = cv2.Sobel(plane, cv2.CV_32F, 1, 0, ksize=3) / scale
        gy = cv2.Sobel(plane, cv2.CV_32F, 0, 1, ksize=3) / scale
        mag = np.hypot(gx, gy)
        cap = max(float(np.percentile(mag, 92)), 1e-6)
        weight = np.minimum(1.0, cap / np.maximum(mag, 1e-6))
        tensors += np.array([
            [float(np.sum(weight * gx * gx)), float(np.sum(weight * gx * gy))],
            [float(np.sum(weight * gx * gy)), float(np.sum(weight * gy * gy))],
        ])
    values, vectors = np.linalg.eigh(tensors)
    total = max(float(values.sum()), 1e-9)
    coherence = float((values[-1] - values[0]) / total)
    if coherence < MIN_COMPONENT_AXIS_COHERENCE:
        return None
    local_unit = vectors[:, -1].astype(np.float32)  # x,y stripe normal
    local_unit /= max(float(np.linalg.norm(local_unit)), 1e-9)
    profile = _profile_along_unit(lab, local_unit)
    if profile is None:
        return None
    fit = _fit_component_period(profile, model, expected_period_px=float(model.period_px))
    if fit is None:
        return None
    local_period, fit_error = fit
    H_to_source = cv2.getPerspectiveTransform(rect, sq)
    center_local = np.array([[bw / 2.0, bh / 2.0]], np.float32)
    step_local = center_local + local_unit[None, :] * float(local_period)
    mapped = _apply_homography(H_to_source, np.concatenate([center_local, step_local]))
    source_vec = mapped[1] - mapped[0]
    source_period = float(np.linalg.norm(source_vec))
    if not np.isfinite(source_period) or source_period < 1.0:
        return None
    source_unit = source_vec / source_period
    return {
        "source_axis_unit": source_unit.astype(np.float32),
        "source_period_px": source_period,
        "axis_coherence": coherence,
        "profile_fit_error": fit_error,
    }


def _component_period_under_homography(
    H: np.ndarray, center: np.ndarray, source_axis_unit: np.ndarray, source_period_px: float,
) -> tuple[float, np.ndarray] | None:
    """Map a source stripe *normal covector* into target pixels.

    A stripe normal is not an ordinary direction vector under anisotropic warp.  Mapping
    ``source_axis_unit`` with ``H`` (the previous implementation) is only correct for a
    similarity transform.  A rectangular cuff mapped to a non-square target made a 45°
    source pattern report the wrong 8.34px period and wrong target angle.  Phase gradients
    transform by the inverse transpose of the local Jacobian: ``g_t = J^-T g_s``.
    """
    if source_period_px <= 0:
        return None
    center = np.asarray(center, np.float32)
    mapped = _apply_homography(H, np.stack([
        center,
        center + np.array([1.0, 0.0], np.float32),
        center + np.array([0.0, 1.0], np.float32),
    ]))
    jacobian = np.column_stack([mapped[1] - mapped[0], mapped[2] - mapped[0]])
    try:
        target_gradient = np.linalg.inv(jacobian).T @ np.asarray(
            source_axis_unit, np.float64)
    except np.linalg.LinAlgError:
        return None
    gradient_norm = float(np.linalg.norm(target_gradient))
    if not np.isfinite(gradient_norm) or gradient_norm <= 1e-9:
        return None
    period = float(source_period_px) / gradient_norm
    if not np.isfinite(period) or period <= 0:
        return None
    return period, (target_gradient / gradient_norm).astype(np.float32)


def _scale_decal_source_coords(
    map_x: np.ndarray, map_y: np.ndarray, sq: np.ndarray, scale: float,
    source_axis_unit: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """target→source map 의 stripe 진행축만 component 내부에서 재표본한다.

    단순히 중심에서 좌표를 ``scale`` 배 하면 긴 placket/cuff가 source 이미지 경계를
    벗어난다. OpenCV border reflection에 맡기면 셔츠 바깥 배경까지 component 안으로
    접혀 들어올 수 있다. 진행축 좌표를 **source component quad 범위 안에서** triangle
    reflection해 패턴의 연속 표본은 확보하되, 직교축(구조 폭)은 건드리지 않는다.
    """
    if abs(scale - 1.0) < 1e-6:
        return map_x, map_y
    center = sq.mean(axis=0).astype(np.float32)
    unit = np.asarray(source_axis_unit, np.float32)
    axis_len = float(np.linalg.norm(unit))
    if axis_len < 1e-6:
        return map_x, map_y
    unit = (unit / axis_len).astype(np.float32)
    dx = map_x - center[0]
    dy = map_y - center[1]
    along = dx * unit[0] + dy * unit[1]
    half_extent = float(np.max(np.abs((sq - center) @ unit)))
    if half_extent < 1e-6:
        return map_x, map_y
    scaled = along * float(scale)
    # reflect into [-half_extent, +half_extent] without ever sampling beyond the
    # approved source component. This is the vectorized BORDER_REFLECT_101 analogue.
    span = 2.0 * half_extent
    folded = np.mod(scaled + half_extent, 2.0 * span)
    reflected = np.where(folded <= span, folded, 2.0 * span - folded) - half_extent
    delta = reflected - along
    return map_x + delta * unit[0], map_y + delta * unit[1]


def composite_stripe(
    carrier_bgr: np.ndarray,
    panel_map: PanelMap,
    model: StripeModel,
    *,
    target_period_px: float,
    target_axis: str,
    component_boxes: dict | None = None,     # {"collar": {...}} — carrier 쪽 component quad
    source_bgr: np.ndarray | None = None,    # decal 용 source(Front) 이미지
    source_component_boxes: dict | None = None,
    allow_low_source_coverage: bool = False,
) -> CompositeArtifacts | CompositeFailure:
    """stripe panel 합성 + component decal + shading transfer + feather blend."""
    h, w = carrier_bgr.shape[:2]
    if target_period_px < 2.0:
        return CompositeFailure(
            "pattern_metric_failed", f"target period {target_period_px:.2f}px 비현실")
    carrier_lab = bgr_to_lab(carrier_bgr)

    pattern_lab = np.zeros((h, w, 3), np.float32)
    painted = np.zeros((h, w), np.uint8)
    panel_metrics = {}

    # ── per-pixel panel 배정 — 페인트 영역 = 실루엣 mask 전체 ─────────────────────
    # quad 사각형을 warp 해 붙이는 방식은 페인트를 quad 로 클리핑해 사각 슬랩을 만든다.
    # 대신 mask 의 모든 픽셀을 각 panel 의 H⁻¹ 로 역사상해, 로컬 좌표가 [0,1]² 에
    # 가장 가까운 panel 에 배정하고 그 panel 좌표계의 위상으로 프로파일을 샘플한다.
    # v(줄 진행) 축은 주기적이라 범위 밖 확장이 자연스럽고, quad 경계 아티팩트가 없다.
    K = len(model.period_profile_lab)
    profile = model.period_profile_lab.astype(np.float32)
    ys, xs = np.nonzero(panel_map.garment_mask)
    if len(xs) == 0:
        return CompositeFailure("panel_landmarks_invalid", "실루엣 mask 가 비어 있음")
    pts = np.stack([xs.astype(np.float64), ys.astype(np.float64),
                    np.ones(len(xs))], axis=0)
    best_cost = np.full(len(xs), np.inf)
    best_coord = np.zeros(len(xs))
    best_panel = np.full(len(xs), -1, np.int32)
    cuff_zone = np.zeros(len(xs), bool)     # 소매 원위 밴드 — 어느 panel 이 이기든 보존
    MAX_ASSIGN_COST = 0.75  # panel 로컬 박스에서 이보다 먼 픽셀은 미배정(=carrier 유지)
    CUFF_BAND_FRAC = 0.78   # 소매 길이축 원위 ~22% = 커프스 보호 밴드 — 넓고 기울어진
    # 소매에선 폭·틸트 교차투영으로 커프스 픽셀의 t 가 0.77 까지 흩어진다(fixture 실측
    # p5=0.772). 과차단의 비용은 carrier 유지(=원본 커프스 모습)라 낮다.
    # 0.5 는 좌측 소매(카메라 각도로 quad 오차 큼)를 미페인트로 남겼다 — mask 가 이미
    # 실루엣·해부학 y-경계로 제한하므로 상한 완화의 번짐 위험은 mask 가 흡수한다.
    for p_idx, panel in enumerate(panel_map.panels):
        if panel.kind != "stripe":
            continue
        q = panel.quad
        bw = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))) + 1
        bh = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))) + 1
        if bw < 4 or bh < 4:
            continue
        src_rect = np.float32([[0, 0], [bw - 1, 0], [bw - 1, bh - 1], [0, bh - 1]])
        H = cv2.getPerspectiveTransform(src_rect, q)
        validity = _homography_validity(H, bw, bh, 0.0)
        if validity["neg_jacobian"] > 0:
            return CompositeFailure("warp_invalid", f"{panel.name}: 사상 방향 반전",
                                    {"panel": panel.name, **validity})
        if validity["stretch_over_frac"] > MAX_STRETCH_FRAC:
            return CompositeFailure(
                "warp_invalid",
                f"{panel.name}: 이방 신장 {validity['stretch_over_frac']:.3f} > {MAX_STRETCH_FRAC}",
                {"panel": panel.name, **validity})
        Hinv = np.linalg.inv(H)
        loc = Hinv @ pts
        lu = loc[0] / loc[2]
        lv = loc[1] / loc[2]
        u = lu / max(bw - 1, 1)
        v = lv / max(bh - 1, 1)
        du = np.maximum(np.maximum(-u, u - 1.0), 0.0)
        dv = np.maximum(np.maximum(-v, v - 1.0), 0.0)
        cost = np.hypot(du, dv)
        coord = lv if target_axis == "horizontal" else lu
        if panel.name.startswith("sleeve"):
            # 커프스 보호 밴드 — 소매 원위 10% 는 커프스 구조(단추·셔링·시접) 영역이라
            # 패턴을 덧칠하면 구조가 소실된다. quad 로컬 uv 는 _quad() 코너 재정렬로
            # 축 의미가 뒤집힐 수 있어(실측: 밴드 미적중), **이미지 공간 투영**으로 잰다:
            # torso 중심에서 가까운 코너쌍 중점→먼 코너쌍 중점 축에 픽셀을 투영. 이 밴드는
            # 어느 panel 이 배정 경쟁에서 이기든(torso 가 가로채는 실측 사례) 보존한다.
            if panel.axis_ends is not None:
                prox_mid = np.array(panel.axis_ends[0], np.float64)
                dist_mid = np.array(panel.axis_ends[1], np.float64)
            else:
                torso_c = panel_map.panels[0].quad.mean(axis=0)
                order = np.argsort(np.linalg.norm(q - torso_c, axis=1))
                prox_mid = q[order[:2]].mean(axis=0)
                dist_mid = q[order[2:]].mean(axis=0)
            axis_vec = dist_mid - prox_mid
            denom = float(axis_vec @ axis_vec)
            if denom > 1e-6:
                t_len = ((xs - prox_mid[0]) * axis_vec[0]
                         + (ys - prox_mid[1]) * axis_vec[1]) / denom
                perp = np.abs((xs - prox_mid[0]) * (-axis_vec[1])
                              + (ys - prox_mid[1]) * axis_vec[0]) / np.sqrt(denom)
                edge = np.roll(q, -1, axis=0) - q
                halfw = float(np.sort(np.linalg.norm(edge, axis=1))[:2].mean()) / 2.0
                # 마진 관대: vision 의 sleeve_end 는 실제 손목보다 안쪽일 수 있고(실측
                # fixture 0.9L 모사), 과차단의 비용은 carrier 유지(=원본 커프스)라 낮다.
                cuff_zone |= ((t_len > CUFF_BAND_FRAC) & (t_len < 1.25)
                              & (perp < halfw * 1.45))
        sel = cost < best_cost
        best_cost[sel] = cost[sel]
        best_coord[sel] = coord[sel]
        best_panel[sel] = p_idx
        panel_metrics[panel.name] = {
            **{k: round(vv, 4) if isinstance(vv, float) else vv
               for k, vv in validity.items()}}
    assigned = (best_panel >= 0) & (best_cost <= MAX_ASSIGN_COST) & ~cuff_zone
    if not assigned.any():
        return CompositeFailure("panel_landmarks_invalid", "합성 가능한 stripe panel 이 없음")
    phase = (best_coord[assigned] / target_period_px * K) % K
    i0 = np.floor(phase).astype(int) % K
    i1 = (i0 + 1) % K
    frac = (phase - np.floor(phase)).astype(np.float32).reshape(-1, 1)
    colors = profile[i0] * (1 - frac) + profile[i1] * frac
    ay, ax_ = ys[assigned], xs[assigned]
    pattern_lab[ay, ax_] = colors
    painted[ay, ax_] = 255
    for p_idx, panel in enumerate(panel_map.panels):
        if panel.name in panel_metrics:
            panel_metrics[panel.name]["painted_px"] = int(
                ((best_panel == p_idx) & assigned).sum())

    if not panel_metrics:
        return CompositeFailure("panel_landmarks_invalid", "합성 가능한 stripe panel 이 없음")

    # ── component decal (collar/placket/cuff) — torso 타일 금지, source 픽셀 우선 ──
    components_review = []
    component_reasons: dict = {}
    cross_surface_components: dict = {}
    component_boxes = component_boxes or {}
    source_component_boxes = source_component_boxes or {}
    for name, tgt in component_boxes.items():
        tq = np.asarray(tgt, np.float32)
        sq = source_component_boxes.get(name)
        comp_mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(comp_mask, [tq.astype(np.int32)], 255)
        comp_mask = cv2.bitwise_and(comp_mask, panel_map.garment_mask)
        sel = comp_mask > 0
        if int(sel.sum()) < MIN_DECAL_AREA_PX:
            # 박스 자체는 멀쩡해도 garment mask 와 교집합이 비거나 너무 작으면 이후
            # median/remap 은 NaN 을 만들고 "측정 가능"으로 위장한다. 좌표·마스크 계약을
            # 먼저 닫아 JSON metric 에 NaN 이 들어갈 여지도 없앤다.
            components_review.append(name)
            component_reasons[name] = "target_mask_insufficient"
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": "target_mask_insufficient",
                "target_mask_px": int(sel.sum()),
                "target_period_px": round(float(target_period_px), 2),
            }
            continue
        if sq is None or source_bgr is None:
            # source 픽셀 없음 — carrier 유지 + 그 component 는 검수 대상.
            # 사유를 함께 남긴다: "source box 부재" 와 "해상도 미달" 이 로그에서
            # 구분되지 않아 실패를 진단할 수 없었다.
            painted[comp_mask > 0] = 0
            components_review.append(name)
            component_reasons[name] = (
                "source_box_absent" if sq is None else "source_image_absent")
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": component_reasons[name],
                "target_period_px": round(float(target_period_px), 2),
            }
            continue
        sq = np.asarray(sq, np.float32)
        ok, why = _decal_source_eligible(sq, tq)
        if not ok:
            painted[comp_mask > 0] = 0
            components_review.append(name)
            component_reasons[name] = why
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": why,
                "target_period_px": round(float(target_period_px), 2),
            }
            continue
        component_geometry = _component_pattern_geometry(source_bgr, sq, model)
        if component_geometry is None:
            painted[comp_mask > 0] = 0
            components_review.append(name)
            component_reasons[name] = "component_pattern_axis_unmeasurable"
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": "component_pattern_axis_unmeasurable",
                "target_period_px": round(float(target_period_px), 2),
            }
            continue
        Hc = cv2.getPerspectiveTransform(sq, tq)
        projected = _component_period_under_homography(
            Hc,
            sq.mean(axis=0),
            component_geometry["source_axis_unit"],
            float(component_geometry["source_period_px"]),
        )
        if projected is None:
            painted[comp_mask > 0] = 0
            components_review.append(name)
            component_reasons[name] = "period_unmeasurable"
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": "period_unmeasurable",
                "target_period_px": round(float(target_period_px), 2),
            }
            continue
        projected_period, target_axis_unit = projected
        scale = float(projected_period) / float(target_period_px)
        if not np.isfinite(scale) or scale <= 0 or scale > MAX_DECAL_SCALE_RESAMPLE:
            painted[comp_mask > 0] = 0
            components_review.append(name)
            component_reasons[name] = "scale_resample_unsupported"
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": "scale_resample_unsupported",
                "source_projected_period_px": round(float(projected_period), 2),
                "target_period_px": round(float(target_period_px), 2),
                "scale_resample_factor": round(float(scale), 4) if np.isfinite(scale) else None,
            }
            continue
        Hinv = np.linalg.inv(Hc)
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                             np.arange(h, dtype=np.float32))
        ones = np.ones(gx.size, dtype=np.float32)
        dst = np.stack([gx.ravel(), gy.ravel(), ones], axis=0)
        src = Hinv @ dst
        map_x = (src[0] / np.maximum(src[2], 1e-9)).reshape(h, w).astype(np.float32)
        map_y = (src[1] / np.maximum(src[2], 1e-9)).reshape(h, w).astype(np.float32)
        map_x, map_y = _scale_decal_source_coords(
            map_x, map_y, sq, scale, component_geometry["source_axis_unit"])
        in_bounds = (
            (map_x >= 0) & (map_x <= source_bgr.shape[1] - 1)
            & (map_y >= 0) & (map_y <= source_bgr.shape[0] - 1)
        )
        out_frac = float((sel & ~in_bounds).sum()) / max(1, int(sel.sum()))
        if out_frac > 0.05:
            painted[sel] = 0
            components_review.append(name)
            component_reasons[name] = "scale_resample_out_of_source"
            cross_surface_components[name] = {
                "scale_measurable": False,
                "reason": "scale_resample_out_of_source",
                "source_projected_period_px": round(float(projected_period), 2),
                "target_period_px": round(float(target_period_px), 2),
                "scale_resample_factor": round(float(scale), 4),
                "out_of_source_frac": round(out_frac, 4),
            }
            continue
        decal_bgr = cv2.remap(
            source_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101)
        decal_lab = bgr_to_lab(decal_bgr)
        pattern_lab[sel] = decal_lab[sel]
        painted[sel] = 255
        center_t = tq.mean(axis=0)
        center_s = sq.mean(axis=0)
        target_coord = float(np.dot(center_t, target_axis_unit))
        source_coord = float(np.dot(
            center_s, component_geometry["source_axis_unit"]))
        phase_delta = (
            (target_coord / float(target_period_px))
            - (source_coord / max(
                float(component_geometry["source_period_px"]), 1e-6))
        ) % 1.0
        phase_delta = min(float(phase_delta), 1.0 - float(phase_delta))
        comp_lab = decal_lab[sel]
        car_lab = carrier_lab[sel]
        chroma_delta = float(np.linalg.norm(
            np.median(comp_lab[:, 1:3], axis=0) - np.median(car_lab[:, 1:3], axis=0)))
        cross_surface_components[name] = {
            "scale_measurable": True,
            "source_projected_period_px": round(float(projected_period), 2),
            "target_period_px": round(float(target_period_px), 2),
            "scale_resample_factor": round(float(scale), 4),
            "source_component_period_px": round(
                float(component_geometry["source_period_px"]), 2),
            "source_pattern_axis_unit": [round(float(x), 5) for x in
                                         component_geometry["source_axis_unit"]],
            "target_pattern_axis_unit": [round(float(x), 5) for x in target_axis_unit],
            "component_axis_coherence": round(
                float(component_geometry["axis_coherence"]), 4),
            "component_profile_fit_error": round(
                float(component_geometry["profile_fit_error"]), 3),
            # Actual final period/width are measured by deterministic_qc from the
            # post-shading, post-feather output. Never report zeros by construction.
            "target_quad": [[round(float(x), 3), round(float(y), 3)] for x, y in tq],
            "phase_delta_period_frac": round(float(phase_delta), 4),
            "component_chroma_delta_ab": round(chroma_delta, 2),
            "out_of_source_frac": round(out_frac, 4),
        }

    # ── shading transfer — carrier 의 저주파 luminance 만 ─────────────────────────
    # 저주파의 척도는 이미지 크기에 묶여야 한다. 줄 주기는 "줄을 지우는" 하한을 주지만,
    # 상수 픽셀 하한(예전 15px)은 1K 에서 적당하고 4K 에서는 사실상 무보정이다 — 4K 에선
    # 15px 위의 carrier 고주파(생성된 줄 자국·원단 노이즈)가 그대로 휘도 앵커에 실려
    # 투영 패턴을 변조한다. 짧은 변 대비 비율로 바꿔 1K/4K 가 같은 물리 역할을 갖게 한다.
    short_side = float(min(h, w))
    sigma = float(np.clip(target_period_px * 1.2,
                          short_side * SHADING_SIGMA_MIN_FRAC,
                          short_side * SHADING_SIGMA_MAX_FRAC))
    blur_l = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma)
    work_sel = panel_map.garment_mask > 0
    # 절대 휘도의 정본은 carrier 의 저주파 L 이다(계약: 저주파 luminance/fold 는 carrier 에서).
    # 패턴은 L **구조**(줄 간 상대차)와 chroma 만 기여한다 — Detail 사진의 노출(그늘에서
    # 찍혀 L~65)이 절대 레벨로 새면 장면과 동떨어진 어두운 슬랩이 된다(실측).
    shaded = pattern_lab.copy()
    pat_sel = painted > 0
    # 칠한 영역 전체 평균은 component 면적/위상 분포에 종속된다. collar/cuff decal을
    # 추가했더니 그 면적만으로 torso 전체 L 기준이 5~10씩 움직여 같은 원단의 대표색 QC가
    # 깨졌다. 물리 패턴의 정본은 StripeModel 한 주기이므로 그 평균을 단일 기준으로 쓴다.
    # component가 늘거나 줄어도 몸통 색은 불변이고, carrier의 국소 주름은 blur_l로 유지된다.
    pat_mean = (
        float(np.asarray(model.period_profile_lab, np.float32)[:, 0].mean())
        if pat_sel.any() else 0.0
    )
    shaded[..., 0] = np.clip(pattern_lab[..., 0] - pat_mean + blur_l, 0.0, 100.0)

    # ── chroma cast 정합 — painted 와 인접 carrier 를 한 벌로 ──────────────────────
    # L 만 carrier 에 앵커링하고 a/b 를 source 사진 그대로 두면, painted 몸통은 평면
    # 촬영(그늘) 조명색이고 unpainted 커프·밑단은 스튜디오 조명색이라 한 벌에 두 원단이
    # 된다(v6 실측). 같은 옷이므로 두 영역의 **중앙값** chroma 는 같아야 한다 — 차이는
    # 조명 cast 다. 중앙값을 쓰는 이유는 줄무늬 진폭에 흔들리지 않고 바탕 원단색을
    # 집기 때문이다. 그리고 **균일 오프셋**만 더하므로 줄 사이 상대 색차, 즉 색 순서와
    # 파랑/베이지 구분은 그대로 보존된다(평균화가 아니다).
    chroma_cast = (0.0, 0.0)
    if pat_sel.any():
        cast = (np.median(carrier_lab[..., 1:3][pat_sel], axis=0)
                - np.median(pattern_lab[..., 1:3][pat_sel], axis=0))
        cast_mag = float(np.hypot(cast[0], cast[1]))
        if cast_mag > MAX_CHROMA_CAST:
            # 같은 옷이라면 이만큼 벌어질 수 없다. 조용히 큰 보정을 먹여 그럴듯한
            # 색으로 만드는 것이 가장 나쁜 결과다 — 여기서 닫는다.
            return CompositeFailure(
                "chroma_cast_excessive",
                f"source/carrier chroma cast {cast_mag:.1f} > {MAX_CHROMA_CAST}",
                {"chroma_cast": round(cast_mag, 2)})
        shaded[..., 1:3] = pattern_lab[..., 1:3] + cast
        chroma_cast = (round(float(cast[0]), 3), round(float(cast[1]), 3))

    # ── feather blend ────────────────────────────────────────────────────────────
    # 경계는 두 종류다.
    #  (1) 실루엣 테두리 — 의류와 배경의 이음매.
    #  (2) painted 영역의 **내부** 계면 — cuff 보존 밴드, component decal 경계,
    #      panel assign-cost 등고선이 만든다.
    # 예전에는 (1) 만 ramp 를 먹이고 마지막에 `alpha[painted == 0] = 0` 으로 (2) 를
    # 1픽셀 계단으로 잘랐다. assign-cost 등고선은 이미지 공간에서 직선이라, 그 계단이
    # 그대로 '붙여넣은 직사각형 판' 으로 보였다(v6 실측). 이제 두 거리장을 각각 만들고
    # 최솟값으로 합친다 — 곱하면 두 경계가 만나는 모서리가 과투명해진다.
    # alpha 만 전이시키므로 패턴 자체는 blur 되지 않는다.
    band_px = max(1.0, float(panel_map.metrics.get("boundary_band_px", 4)))
    silhouette_ramp = np.clip(
        cv2.distanceTransform(panel_map.garment_mask, cv2.DIST_L2, 3) / band_px,
        0.0, 1.0)
    # 내부 계면은 줄 주기에 비례해 전이한다 — 한 주기 남짓이면 이음매는 사라지고
    # 패턴은 살아 있다. 해상도가 올라가면 주기도 같이 커지므로 1K/4K 에서 물리적으로
    # 같은 폭을 갖는다(고정 픽셀 상수를 쓰면 4K 에서 사실상 계단으로 남는다).
    # 상한은 실루엣 밴드다(= 이미지 짧은 변의 고정 비율). 주기가 이미지에 비해 굵은
    # 저해상도/거친 패턴에서 전이 폭이 패널을 잠식해 대표색을 carrier 쪽으로 끌어당기는
    # 것을 막는다 — 이음매를 지우자고 패턴 색을 흐리면 본말전도다.
    inner_band_px = float(np.clip(
        float(target_period_px) * INNER_FEATHER_PERIODS, 3.0, band_px))
    inner_ramp = np.clip(
        cv2.distanceTransform(painted, cv2.DIST_L2, 3) / inner_band_px, 0.0, 1.0)
    alpha = np.minimum(silhouette_ramp, inner_ramp).astype(np.float32)
    # 의류 밖으로는 절대 나가지 않는다 — mask 밖 drift 는 정확히 0 이어야 한다.
    alpha[panel_map.garment_mask == 0] = 0.0

    shaded_bgr = lab_to_bgr(shaded).astype(np.float32)
    out = (alpha[..., None] * shaded_bgr
           + (1.0 - alpha[..., None]) * carrier_bgr.astype(np.float32))
    out_bgr = np.clip(out, 0, 255).astype(np.uint8)

    protected_sel = panel_map.protected > 0
    comp_masks = np.zeros((h, w), np.uint8)
    for name, tgt in component_boxes.items():
        cv2.fillPoly(comp_masks, [np.asarray(tgt, np.int32)], 255)
    core = protected_sel & (comp_masks == 0)
    # Cuff bands are deliberately carrier-owned so buttons, seams and gathering
    # are not painted over.  They must not be counted as missing source coverage
    # when Vision omitted an explicit cuff component box.  The prior denominator
    # made a perfectly painted long sleeve report ~0.78 coverage solely because
    # its intentional 22% cuff preserve band stayed unpainted.
    preserved_structure = np.zeros((h, w), bool)
    preserved_structure[ys[cuff_zone], xs[cuff_zone]] = True
    core &= ~preserved_structure
    coverage = float((painted[core] > 0).mean()) if core.any() else 0.0
    if coverage < MIN_SOURCE_COVERAGE and not allow_low_source_coverage:
        return CompositeFailure(
            "source_coverage_low",
            f"protected source-derived {coverage:.3f} < {MIN_SOURCE_COVERAGE}",
            {"coverage": round(coverage, 4)})

    cross_surface_scale = {
        "target_period_px": round(float(target_period_px), 2),
        "target_axis": target_axis,
        "components": cross_surface_components,
    }
    panel_map.metrics["cross_surface_scale"] = cross_surface_scale

    return CompositeArtifacts(
        image_bgr=out_bgr, alpha=alpha, painted=painted,
        coverage_scope=core.astype(np.uint8) * 255,
        panel_metrics=panel_metrics,
        components_needing_review=tuple(components_review),
        component_review_reasons=dict(component_reasons),
        source_coverage=coverage,
        metrics={"target_period_px": round(float(target_period_px), 2),
                 "target_axis": target_axis, "shading_sigma": round(sigma, 1),
                 "chroma_cast_ab": list(chroma_cast),
                 "inner_feather_px": round(float(inner_band_px), 2),
                 "cross_surface_scale": cross_surface_scale})
