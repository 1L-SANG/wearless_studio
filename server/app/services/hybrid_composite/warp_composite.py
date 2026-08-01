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
from .types import CompositeFailure, StripeModel, WARP_VERSION

MAX_LOCAL_STRETCH = 2.0        # protected 내부에서 허용하는 국소 신장 상한
MAX_STRETCH_FRAC = 0.01        # stretch 초과 픽셀 허용 비율 (< 1%)
MIN_SOURCE_COVERAGE = 0.90     # protected 내부 source-derived 비율 하한
MIN_DECAL_RES_PX = 48          # decal source ROI 최소 변 — 미달이면 component needs_review


@dataclass(frozen=True)
class CompositeArtifacts:
    image_bgr: np.ndarray
    alpha: np.ndarray                # 최종 합성 가중치 (0~1)
    painted: np.ndarray              # source-derived 픽셀 (0/255)
    panel_metrics: dict
    components_needing_review: tuple
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
) -> CompositeArtifacts | CompositeFailure:
    """stripe panel 합성 + component decal + shading transfer + feather blend."""
    h, w = carrier_bgr.shape[:2]
    if target_period_px < 2.0:
        return CompositeFailure("pattern_metric_failed", f"target period {target_period_px:.2f}px 비현실")
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
    component_boxes = component_boxes or {}
    source_component_boxes = source_component_boxes or {}
    for name, tgt in component_boxes.items():
        tq = np.asarray(tgt, np.float32)
        sq = source_component_boxes.get(name)
        comp_mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(comp_mask, [tq.astype(np.int32)], 255)
        comp_mask = cv2.bitwise_and(comp_mask, panel_map.garment_mask)
        if sq is None or source_bgr is None:
            # source 픽셀 없음 — carrier 유지 + 그 component 는 검수 대상
            painted[comp_mask > 0] = 0
            components_review.append(name)
            continue
        sq = np.asarray(sq, np.float32)
        side = min(np.linalg.norm(sq[1] - sq[0]), np.linalg.norm(sq[3] - sq[0]))
        if side < MIN_DECAL_RES_PX:
            painted[comp_mask > 0] = 0
            components_review.append(name)
            continue
        Hc = cv2.getPerspectiveTransform(sq, tq)
        decal_bgr = cv2.warpPerspective(source_bgr, Hc, (w, h), flags=cv2.INTER_LINEAR)
        decal_lab = bgr_to_lab(decal_bgr)
        sel = comp_mask > 0
        pattern_lab[sel] = decal_lab[sel]
        painted[sel] = 255

    # ── shading transfer — carrier 의 저주파 luminance 만 ─────────────────────────
    sigma = max(target_period_px * 1.2, 15.0)
    blur_l = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma)
    work_sel = panel_map.garment_mask > 0
    # 절대 휘도의 정본은 carrier 의 저주파 L 이다(계약: 저주파 luminance/fold 는 carrier 에서).
    # 패턴은 L **구조**(줄 간 상대차)와 chroma 만 기여한다 — Detail 사진의 노출(그늘에서
    # 찍혀 L~65)이 절대 레벨로 새면 장면과 동떨어진 어두운 슬랩이 된다(실측).
    shaded = pattern_lab.copy()
    pat_sel = painted > 0
    pat_mean = float(pattern_lab[..., 0][pat_sel].mean()) if pat_sel.any() else 0.0
    shaded[..., 0] = np.clip(pattern_lab[..., 0] - pat_mean + blur_l, 0.0, 100.0)

    # ── feather blend ────────────────────────────────────────────────────────────
    alpha = np.zeros((h, w), np.float32)
    alpha[panel_map.protected > 0] = 1.0
    if panel_map.boundary.any():
        dist_in = cv2.distanceTransform(
            cv2.bitwise_not(cv2.bitwise_not(panel_map.garment_mask)), cv2.DIST_L2, 3)
        band_px = max(1, panel_map.metrics.get("boundary_band_px", 4))
        ramp = np.clip(dist_in / band_px, 0.0, 1.0)
        bsel = panel_map.boundary > 0
        alpha[bsel] = ramp[bsel]
    alpha[painted == 0] = 0.0  # 패턴이 실제로 칠해진 곳만 합성

    shaded_bgr = lab_to_bgr(shaded).astype(np.float32)
    out = (alpha[..., None] * shaded_bgr
           + (1.0 - alpha[..., None]) * carrier_bgr.astype(np.float32))
    out_bgr = np.clip(out, 0, 255).astype(np.uint8)

    protected_sel = panel_map.protected > 0
    comp_masks = np.zeros((h, w), np.uint8)
    for name, tgt in component_boxes.items():
        cv2.fillPoly(comp_masks, [np.asarray(tgt, np.int32)], 255)
    core = protected_sel & (comp_masks == 0)
    coverage = float((painted[core] > 0).mean()) if core.any() else 0.0
    if coverage < MIN_SOURCE_COVERAGE:
        return CompositeFailure(
            "source_coverage_low",
            f"protected source-derived {coverage:.3f} < {MIN_SOURCE_COVERAGE}",
            {"coverage": round(coverage, 4)})

    return CompositeArtifacts(
        image_bgr=out_bgr, alpha=alpha, painted=painted,
        panel_metrics=panel_metrics,
        components_needing_review=tuple(components_review),
        source_coverage=coverage,
        metrics={"target_period_px": round(float(target_period_px), 2),
                 "target_axis": target_axis, "shading_sigma": round(sigma, 1)})
