"""fix-loop v2 — P1 게이트: 페인트 영역은 quad 슬랩이 아니라 실루엣 마스크다.

aed4e94 산출물의 사각 슬랩(QA FAIL 결함 #5)의 뿌리는 mask 품질이 아니라
`work = garment ∩ poly_union` + `region = warp(quad rect)` — 페인트가 구조적으로
quad 에 클리핑되는 것. 이 테스트는 실루엣이 quad 보다 넓은 carrier 에서
mask−quad 영역도 패턴이 칠해짐을 요구한다(현행 코드에서 RED).
"""

import numpy as np
import cv2

from hybrid_stripe_fixtures import render_carrier, render_signal, GEOMETRIES
from app.services.hybrid_composite.panel_map import build_panel_map
from app.services.hybrid_composite.stripe_model import extract_stripe_model
from app.services.hybrid_composite.types import CompositeFailure
from app.services.hybrid_composite.warp_composite import composite_stripe


def _flared_carrier():
    """G1 carrier 변형 — 밑단이 hem quad 밖으로 12% 벌어진 실루엣(실제 셔츠의 자연 형상)."""
    cx = render_carrier("G1_regular", 0)
    img = cx["image"].copy()
    h, w = img.shape[:2]
    torso = np.array(cx["torso_poly"], np.float32)
    hem_y = torso[:, 1].max()
    top_y = torso[:, 1].min()
    left_x, right_x = torso[:, 0].min(), torso[:, 0].max()
    flare = int((right_x - left_x) * 0.12)
    # 하단 40% 구간에서 몸판을 좌우로 벌린다 (garment mask 도 함께 갱신)
    mask = cx["garment_mask"].copy()
    add = np.zeros_like(mask)
    y0 = int(top_y + (hem_y - top_y) * 0.6)
    pts = np.array([
        [left_x, y0], [left_x - flare, hem_y], [left_x, hem_y]], np.int32)
    pts2 = np.array([
        [right_x, y0], [right_x + flare, hem_y], [right_x, hem_y]], np.int32)
    for p in (pts, pts2):
        cv2.fillPoly(add, [p], 255)
    sel = add > 0
    img[sel] = (176, 176, 176)
    # 기존 음영과 비슷한 gain 적용(단색이면 stripe-energy 는 못 잡지만 bg_diff 가 잡는다)
    mask[sel] = 255
    cx = dict(cx)
    cx["image"] = img
    cx["garment_mask"] = mask
    return cx


def test_paint_region_covers_silhouette_beyond_panel_quads():
    """mask ⊋ quad 인 carrier 에서 quad 밖 mask 영역도 칠해져야 한다 — 사각 슬랩 금지."""
    model = extract_stripe_model(
        render_signal("S1_blue_brown_fine", "illum"),
        source_asset_id="fx", source_sha256="0" * 8, source_roi=(0, 0, 768, 768))
    assert not isinstance(model, CompositeFailure)
    cx = _flared_carrier()
    pm = build_panel_map(cx["image"], cx["landmarks"])
    assert not isinstance(pm, CompositeFailure), pm
    torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=torso_h / 22.0, target_axis="horizontal")
    assert not isinstance(art, CompositeFailure), art

    h, w = cx["image"].shape[:2]
    quad_union = np.zeros((h, w), np.uint8)
    cv2.fillPoly(quad_union, [np.array(cx["torso_poly"], np.int32)], 255)
    cv2.fillPoly(quad_union, [np.array(cx["sleeve_l_poly"], np.int32)], 255)
    cv2.fillPoly(quad_union, [np.array(cx["sleeve_r_poly"], np.int32)], 255)
    # 실루엣은 **픽스처 GT** 기준 — 반환 mask 로 재면 클리핑 결함이 전제 단계에서 숨는다
    # (실측: pm.garment_mask 는 이미 quad 로 잘려 quad 밖 0px).
    beyond = cv2.bitwise_and(cx["garment_mask"], cv2.bitwise_not(quad_union))
    beyond = cv2.erode(beyond, np.ones((9, 9), np.uint8))
    n_beyond = np.count_nonzero(beyond)
    assert n_beyond > 500, f"픽스처 전제 실패 — quad 밖 실루엣이 {n_beyond}px 뿐"
    painted_frac = np.count_nonzero(
        cv2.bitwise_and(art.painted, beyond)) / n_beyond
    assert painted_frac >= 0.90, (
        f"quad 밖 실루엣의 {painted_frac:.1%} 만 칠해짐 — 사각 슬랩 클리핑 잔존")


def test_synthetic_gates_still_green_after_workregion_change():
    """작업영역 확장이 기존 12 carrier 게이트를 깨면 안 된다(스모크 — 대표 2케이스)."""
    from app.services.hybrid_composite.deterministic_qc import verify_composite

    model = extract_stripe_model(
        render_signal("S1_blue_brown_fine", "illum"),
        source_asset_id="fx", source_sha256="0" * 8, source_roi=(0, 0, 768, 768))
    for gid in ("G1_regular", "G4_long"):
        cx = render_carrier(gid, 0)
        pm = build_panel_map(cx["image"], cx["landmarks"])
        assert not isinstance(pm, CompositeFailure)
        torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
        art = composite_stripe(cx["image"], pm, model,
                               target_period_px=torso_h / 22.0, target_axis="horizontal")
        assert not isinstance(art, CompositeFailure), (gid, art)
        qc = verify_composite(art.image_bgr, cx["image"], pm, model,
                              target_period_px=torso_h / 22.0, target_axis="horizontal")
        assert qc.passed, (gid, qc.metrics.get("failure_details", [])[:3])
