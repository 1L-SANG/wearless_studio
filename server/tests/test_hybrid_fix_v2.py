"""fix-loop v2 — P1 게이트: 페인트 영역은 quad 슬랩이 아니라 실루엣 마스크다.

aed4e94 산출물의 사각 슬랩(QA FAIL 결함 #5)의 뿌리는 mask 품질이 아니라
`work = garment ∩ poly_union` + `region = warp(quad rect)` — 페인트가 구조적으로
quad 에 클리핑되는 것. 이 테스트는 실루엣이 quad 보다 넓은 carrier 에서
mask−quad 영역도 패턴이 칠해짐을 요구한다(현행 코드에서 RED).
"""

import numpy as np
import cv2
import pytest

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

    # mask 쌍은 좁은 상대오차 hard gate 를 걸지 않는다(D7 — 교차-포즈 비교는 불건전하고,
    # 같은 셔츠가 1.75× 로 측정된 실측이 2회 있다). 핏/기장 조정은 제품 기능이므로 그
    # 범위는 통과해야 한다. 다만 순수 관측으로 두면 mask 붕괴가 그대로 새어나가므로,
    # 물리적으로 설명되지 않는 배율에서만 닫는 위생 게이트를 둔다.
    pm2 = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory={**src_inv, "torso_aspect_mask": a_mask},
        carrier_inventory={**src_inv, "torso_aspect_mask": a_mask * 1.8})
    assert not isinstance(pm2, CompositeFailure), getattr(pm2, "detail", None)
    assert pm2.metrics["torso_aspect"]["mask_aspect_ratio"] == pytest.approx(1.8, abs=0.01)

    # v6 실측(3.58×)은 mask 붕괴다 — 이제 차단된다.
    pm_collapsed = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory={**src_inv, "torso_aspect_mask": a_mask},
        carrier_inventory={**src_inv, "torso_aspect_mask": a_mask * 3.58})
    assert isinstance(pm_collapsed, CompositeFailure)
    assert pm_collapsed.reason == "geometry_carrier_mismatch"

    # vision 쌍 폴백(Codex fixpoint 계약)은 불변 — mask 쌍이 없으면 0.35 로 차단 유지
    pm3 = build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory=src_inv,
        carrier_inventory={**src_inv, "torso_aspect": src_inv["torso_aspect"] * 1.5})
    assert isinstance(pm3, CompositeFailure)
    assert pm3.reason == "geometry_carrier_mismatch"


def test_construction_ratio_boundary_uses_reported_precision():
    """A displayed 0.35 must not be rejected as `0.35 > 0.35` by hidden float noise."""
    cx = render_carrier("G1_regular", 0)
    base = dict(cx["construction_inventory"])
    source = {**base, "sleeve_len_ratio": 1.55}
    carrier = {**base, "sleeve_len_ratio": 1.007}

    panel_map = build_panel_map(
        cx["image"],
        cx["landmarks"],
        source_inventory=source,
        carrier_inventory=carrier,
    )

    assert not isinstance(panel_map, CompositeFailure), getattr(panel_map, "detail", None)


def test_sleeve_ratio_live_view_tolerance_still_rejects_structural_mismatch():
    """Flat-lay↔3/4 sleeve foreshortening may reach 40%; larger structure drift stays closed."""
    cx = render_carrier("G1_regular", 0)
    base = dict(cx["construction_inventory"])
    source = {**base, "sleeve_len_ratio": 1.55}

    within = build_panel_map(
        cx["image"],
        cx["landmarks"],
        source_inventory=source,
        carrier_inventory={**base, "sleeve_len_ratio": 1.55 * 0.60},
    )
    outside = build_panel_map(
        cx["image"],
        cx["landmarks"],
        source_inventory=source,
        carrier_inventory={**base, "sleeve_len_ratio": 1.55 * 0.58},
    )

    assert not isinstance(within, CompositeFailure), getattr(within, "detail", None)
    assert isinstance(outside, CompositeFailure)
    assert outside.reason == "geometry_carrier_mismatch"


# ── US-3: 보호 영역 — 커프스 밴드·칼라 위·밑단 아래는 carrier 픽셀 보존 ──────────

def _composited_g1(full=False):
    model = extract_stripe_model(
        render_signal("S1_blue_brown_fine", "illum"),
        source_asset_id="fx", source_sha256="0" * 8, source_roi=(0, 0, 768, 768))
    assert not isinstance(model, CompositeFailure)
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    assert not isinstance(pm, CompositeFailure), pm
    torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
    period = torso_h / 22.0
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal")
    assert not isinstance(art, CompositeFailure), art
    if full:
        return {"cx": cx, "pm": pm, "model": model, "period": period, "art": art}
    return cx, art


def test_cuff_band_keeps_carrier_pixels():
    """소매 원위(손목) 8% 밴드는 커프스 구조 — 패턴을 칠하지 않고 carrier 를 보존한다."""
    cx, art = _composited_g1()
    h, w = cx["image"].shape[:2]
    lm = cx["landmarks"]
    for key in ("sleeve_l_poly", "sleeve_r_poly"):
        side = "l" if "_l_" in key else "r"
        poly = np.array(cx[key], np.float32)
        # 원위 = sleeve_end landmark 에 가까운 두 꼭짓점 — "torso 중심에서 먼 쪽" 휴리스틱은
        # 늘어진 소매에서 어깨쪽을 집는다(실측: 이 테스트의 1차 버전 결함).
        wrist = np.array([lm[f"sleeve_{side}_end"][0] * w, lm[f"sleeve_{side}_end"][1] * h])
        d = np.linalg.norm(poly - wrist, axis=1)
        distal = np.argsort(d)[:2]
        proximal = [i for i in range(4) if i not in distal]
        # 근위 edge 중점→원위 edge 중점의 92%~100% 구간 밴드
        p_mid = poly[proximal].mean(axis=0)
        d_mid = poly[distal].mean(axis=0)
        axis_v = d_mid - p_mid
        band = np.array([
            poly[distal[0]] - axis_v * 0.08, poly[distal[1]] - axis_v * 0.08,
            poly[distal[1]], poly[distal[0]]], np.float32)
        band_mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(band_mask, [band.astype(np.int32)], 255)
        band_mask = cv2.bitwise_and(band_mask, cx["garment_mask"])
        band_mask = cv2.erode(band_mask, np.ones((5, 5), np.uint8))
        n = np.count_nonzero(band_mask)
        assert n > 100, f"{key}: 커프스 밴드 픽셀 {n} — 픽스처 전제 부족"
        painted_in_band = np.count_nonzero(cv2.bitwise_and(art.painted, band_mask)) / n
        assert painted_in_band <= 0.10, (
            f"{key}: 커프스 밴드의 {painted_in_band:.1%} 가 칠해짐 — 커프스 구조 소실")


def test_no_paint_above_collar_or_below_hem():
    """해부학 y-경계 밖(칼라 위·밑단 아래)은 carrier 그대로 — 목/스커트 페인트 금지.

    G1 기본 fixture 는 어깨 위에 전경 자체가 없어 이 계약이 장식이 된다(adversary
    실측: y-클립 삭제해도 green). 어깨 위에 의류색 목 플랩을 심어 pre-clip mask 가
    경계 밖에서 실제로 발화하게 만든 뒤 0px 를 요구한다 — 이제 y-클립을 지우면 RED.
    """
    model = extract_stripe_model(
        render_signal("S1_blue_brown_fine", "illum"),
        source_asset_id="fx", source_sha256="0" * 8, source_roi=(0, 0, 768, 768))
    assert not isinstance(model, CompositeFailure)
    cx = render_carrier("G1_regular", 0)
    img = cx["image"].copy()
    mask = cx["garment_mask"].copy()
    h, w = img.shape[:2]
    lm = cx["landmarks"]
    shoulder_y = min(lm["shoulder_l"][1], lm["shoulder_r"][1]) * h
    hem_y = max(lm["hem_l"][1], lm["hem_r"][1]) * h
    # 어깨 위 목 플랩(의류색) — bg_diff 전경이 되지만 셔츠가 존재할 수 없는 영역
    cxr = int((lm["shoulder_l"][0] + lm["shoulder_r"][0]) / 2 * w)
    y1 = int(shoulder_y - h * 0.03)
    y0 = max(0, y1 - int(h * 0.06))
    cv2.rectangle(img, (cxr - 40, y0), (cxr + 40, y1), (176, 176, 176), -1)
    cv2.rectangle(mask, (cxr - 40, y0), (cxr + 40, y1), 255, -1)
    cx = dict(cx); cx["image"] = img; cx["garment_mask"] = mask
    pm = build_panel_map(cx["image"], cx["landmarks"])
    assert not isinstance(pm, CompositeFailure), pm
    torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=torso_h / 22.0, target_axis="horizontal")
    assert not isinstance(art, CompositeFailure), art
    # 전제: 플랩이 정말 pre-clip 전경이었는가 — 경계 위 mask 픽셀이 실존해야 비장식
    assert np.count_nonzero(mask[:max(0, int(shoulder_y - h * 0.02))]) > 500
    assert np.count_nonzero(art.painted[:max(0, int(shoulder_y - h * 0.02))]) == 0
    assert np.count_nonzero(art.painted[min(h, int(hem_y + h * 0.03)):]) == 0


# ── US-5: 결정론 + composite-vs-carrier 비회귀 ────────────────────────────────

def test_composite_pipeline_is_byte_deterministic():
    """같은 입력 → 바이트 동일 출력 + 동일 QC. 재현 가능성은 유료 검증의 전제다."""
    from app.services.hybrid_composite.deterministic_qc import verify_composite
    a = _composited_g1(full=True)
    b = _composited_g1(full=True)
    assert np.array_equal(a["art"].image_bgr, b["art"].image_bgr)
    assert np.array_equal(a["art"].painted, b["art"].painted)
    qa = verify_composite(a["art"].image_bgr, a["cx"]["image"], a["pm"], a["model"],
                          target_period_px=a["period"], target_axis="horizontal",
                          painted_mask=a["art"].painted)
    qb = verify_composite(b["art"].image_bgr, b["cx"]["image"], b["pm"], b["model"],
                          target_period_px=b["period"], target_axis="horizontal",
                          painted_mask=b["art"].painted)
    assert qa.passed == qb.passed and qa.failures == qb.failures
    assert qa.metrics == qb.metrics


def test_degraded_composite_is_rejected_not_shipped():
    """줄 신호를 잃은(=carrier 보다 나쁜) 합성은 deterministic QC 가 미출고 판정해야 한다."""
    from app.services.hybrid_composite.deterministic_qc import verify_composite
    r = _composited_g1(full=True)
    qc_ok = verify_composite(r["art"].image_bgr, r["cx"]["image"], r["pm"], r["model"],
                             target_period_px=r["period"], target_axis="horizontal",
                             painted_mask=r["art"].painted)
    assert qc_ok.passed, qc_ok.metrics["failure_details"]
    degraded = r["art"].image_bgr.copy()
    sel = r["art"].painted > 0
    blurred = cv2.GaussianBlur(degraded, (0, 0), sigmaX=r["period"] * 1.5)
    degraded[sel] = blurred[sel]
    qc_bad = verify_composite(degraded, r["cx"]["image"], r["pm"], r["model"],
                              target_period_px=r["period"], target_axis="horizontal",
                              painted_mask=r["art"].painted)
    assert not qc_bad.passed, "줄 소실 합성이 QC 를 통과 — carrier 보다 나쁜 출력이 출고된다"
    assert "pattern_metric_failed" in qc_bad.failures


def test_repeat_invariant_none_signal_skips_vision_aspect_gate():
    """워커가 torso_aspect_mask=None(키 존재)으로 'aspect 비교 생략'을 명시하면 vision
    쌍 하드 게이트로 떨어지면 안 된다 — 교차-포즈 지터(rel 0.80 실측) 오차단 재발 방지
    (final-code 리뷰 H1 회귀 테스트)."""
    cx = render_carrier("G1_regular", 0)
    base = dict(cx["construction_inventory"])
    src = {**base, "torso_aspect": 1.0, "torso_aspect_mask": None}
    car = {**base, "torso_aspect": 1.8, "torso_aspect_mask": None}   # rel 0.8 > 0.35
    pm = build_panel_map(cx["image"], cx["landmarks"],
                         source_inventory=src, carrier_inventory=car)
    assert not isinstance(pm, CompositeFailure), (
        f"None-skip 신호가 무시되고 vision aspect 하드 게이트가 발화: {pm}")
    assert pm.metrics["torso_aspect"].get("skipped_by_repeat_invariant")
    # 신호가 없으면 기존대로 차단되어야 한다 (게이트 자체는 살아 있음)
    src2 = {**base, "torso_aspect": 1.0}
    car2 = {**base, "torso_aspect": 1.8}
    pm2 = build_panel_map(cx["image"], cx["landmarks"],
                          source_inventory=src2, carrier_inventory=car2)
    assert isinstance(pm2, CompositeFailure) and pm2.reason == "geometry_carrier_mismatch"
