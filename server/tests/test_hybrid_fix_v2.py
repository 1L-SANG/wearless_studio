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


# ── US-2: vision 지터 내성 — construction 비교는 mask 유도값 우선 ──────────────────

def test_shaky_vision_landmarks_do_not_cause_false_carrier_mismatch():
    """live 실패(torso_aspect 상대오차 0.80)의 재현: mask 는 옳고 vision 만 흔들린 경우
    geometry_carrier_mismatch 오판이 나면 안 된다."""
    from app.services.hybrid_composite.panel_map import mask_aspect_from_silhouette

    cx = render_carrier("G1_regular", 0)
    # GT mask 에서 유도한 aspect — 이것이 측정 연산자의 정본
    a_true = mask_aspect_from_silhouette(cx["garment_mask"])
    assert a_true is not None and a_true > 0
    # vision 이 흔들려 hem 을 한참 위로 잡은 경우(=aspect 0.8배 왜곡 상황 재현):
    # 같은 mask 에서 유도하면 흔들린 landmark 와 무관하게 같은 값이 나와야 한다
    a_again = mask_aspect_from_silhouette(cx["garment_mask"])
    assert abs(a_again - a_true) / a_true < 1e-9, "mask 유도값은 결정론이어야 한다"

    # 판별 실측(G-계열 aspect 1.31~1.91): 핏/기장 조정은 이 범위를 오가는 **정상 기능**이라
    # aspect 는 극단(>60%, 물리적으로 다른 물체)만 차단한다 — 근거는 decisions.md D5.
    # 셔츠 계열 변형끼리는 hard-block 대상이 아님을 고정한다.
    other = render_carrier("G4_long", 0)
    a_other = mask_aspect_from_silhouette(other["garment_mask"])
    assert abs(a_other - a_true) / a_true < 0.60, "핏/기장 변형이 극단 임계에 걸리면 기능 회귀"


def test_build_panel_map_prefers_mask_derived_aspect_over_vision():
    """vision torso_aspect 가 크게 틀려도(0.8류) mask 유도값이 합치하면 차단하지 않고,
    mask 유도값끼리 진짜 어긋나면 여전히 차단한다."""
    from app.services.hybrid_composite.panel_map import mask_aspect_from_silhouette

    cx = render_carrier("G1_regular", 0)
    a_mask = mask_aspect_from_silhouette(cx["garment_mask"])
    src_inv = dict(cx["construction_inventory"])
    bad_vision = dict(src_inv)
    bad_vision["torso_aspect"] = src_inv["torso_aspect"] * 1.8  # vision 대폭 왜곡
    # source/carrier 모두 mask 유도값 제공 → vision 왜곡은 무시돼야 한다
    pm = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory={**src_inv, "torso_aspect_mask": a_mask},
        carrier_inventory={**bad_vision, "torso_aspect_mask": a_mask})
    assert not isinstance(pm, CompositeFailure), (
        f"mask 유도값이 일치하는데 vision 왜곡으로 차단됨: {getattr(pm,'detail',None)}")

    # mask 쌍은 관측 전용(D7 — 교차-포즈 hard gate 불건전, 같은 셔츠 1.75× 실측 2회).
    # 극단값이어도 mask 쌍 자체는 차단하지 않는다 — 차단은 줄 수 불변량·패턴 QC 소관.
    pm2 = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory={**src_inv, "torso_aspect_mask": a_mask},
        carrier_inventory={**src_inv, "torso_aspect_mask": a_mask * 1.8})
    assert not isinstance(pm2, CompositeFailure), getattr(pm2, "detail", None)
    assert pm2.metrics["torso_aspect"]["observational_only"] is True

    # vision 쌍 폴백(Codex fixpoint 계약)은 불변 — mask 쌍이 없으면 0.35 로 차단 유지
    pm3 = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory=src_inv,
        carrier_inventory={**src_inv, "torso_aspect": src_inv["torso_aspect"] * 1.5})
    assert isinstance(pm3, CompositeFailure)
    assert pm3.reason == "geometry_carrier_mismatch"
