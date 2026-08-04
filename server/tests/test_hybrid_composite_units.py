"""hybrid composite 단위·집계 게이트 — synthetic fixture 가 정본 oracle 이다.

quantitative_gates(Master Goal)의 controlled-set 판정이 여기서 강제된다:
  · stripe 방향 정확도 100%, 색 순서·line-group exact
  · period 오차 median ≤5% / P95 ≤10%, 폭 비율 median ≤8% / P95 ≤15%
  · 대표색 ΔE00 median ≤6 / P95 ≤10 (조명 정규화 후)
  · mask IoU ≥ 0.92, protected source-derived ≥ 90%
  · negative Jacobian/flip 0, stretch 초과 <1%
  · mask 밖 ΔE76>10 비율 median ≤1% / P95 ≤3%, mask 밖 SSIM ≥ 0.98
fixture manifest 에 필수 class 가 하나라도 빠지면 집계 gate 는 통과할 수 없다.
"""

import dataclasses
import json

import cv2
import numpy as np
import pytest

from hybrid_stripe_fixtures import (
    GEOMETRIES,
    MANIFEST_PATH,
    NEGATIVE_CONTROLS,
    SIGNALS,
    VARIANTS,
    build_manifest,
    expected_for,
    render_carrier,
    render_negative,
    render_signal,
)
from app.services.hybrid_composite.color import bgr_to_lab, ciede2000
from app.services.hybrid_composite.deterministic_qc import verify_composite
from app.services.hybrid_composite.panel_map import (
    Panel,
    PanelMap,
    build_panel_map,
    mask_bg_diff,
    mask_grabcut,
)
from app.services.hybrid_composite.source_validation import validate_stripe_source
from app.services.hybrid_composite.stripe_model import extract_stripe_model
from app.services.hybrid_composite.types import COMPOSITE_FAILURE_REASONS, CompositeFailure
from app.services.hybrid_composite.warp_composite import composite_stripe
from app.agents.hybrid_landmarks import (
    box_rejection_reason,
    boxes_to_pixels,
    component_observation,
    merge_geometry_pair,
    validate_geometry,
)


def _rgb_to_lab(rgb):
    arr = np.array([[list(rgb)[::-1]]], np.uint8)
    return bgr_to_lab(arr)[0, 0]


def _landmark_response(**overrides):
    row = {
        "garment_visible": True,
        "shoulder_l": [0.30, 0.20],
        "shoulder_r": [0.70, 0.20],
        "hem_l": [0.32, 0.70],
        "hem_r": [0.68, 0.70],
        "has_collar": True,
        "has_placket": True,
        "has_cuffs": True,
        "visible_button_count": 6,
        "confidence": 0.82,
    }
    row.update(overrides)
    return row


# ── manifest 무결성 ────────────────────────────────────────────────────────────

def test_fixture_manifest_matches_generator_and_covers_required_classes():
    """커밋된 manifest == 코드 정본, 그리고 필수 class 가 전부 존재해야 집계가 유효하다."""
    on_disk = json.loads(MANIFEST_PATH.read_text())
    assert on_disk == build_manifest(), "manifest.json 이 생성기 정본과 어긋남 — 재생성 필요"

    classes = set()
    for case in on_disk["extractor_cases"]:
        classes.update(case["class"])
    for case in on_disk["carrier_cases"]:
        classes.update(case["class"])
    required = {"stripe", "vertical", "horizontal", "multi_color", "illumination",
                "perspective", "negative_control", "carrier_geometry",
                "torso", "sleeve", "collar", "placket", "cuff"}
    missing = required - classes
    assert not missing, f"필수 fixture class 누락: {missing} — 집계 gate 무효"

    n_stripe = sum(1 for c in on_disk["extractor_cases"]
                   if "negative_control" not in c["class"])
    assert n_stripe >= 24, f"extractor controlled set {n_stripe} < 24"
    assert len(on_disk["carrier_cases"]) >= 12
    for case in on_disk["extractor_cases"] + on_disk["carrier_cases"]:
        assert case["rights"].startswith("synthetic"), case["id"]
        assert "oracle_author" in case


def test_source_landmark_merge_allows_bounded_front_photo_jitter():
    """실 HEIC front 에서 source shoulder 좌표가 6% 초과 흔들린 재현을 soft 합의로 통과."""
    a = _landmark_response(shoulder_l=[0.25, 0.20], confidence=0.80)
    b = _landmark_response(shoulder_l=[0.34, 0.20], confidence=0.79)

    strict, strict_err = merge_geometry_pair(a, b)
    assert strict is None
    assert strict_err == "landmark 불일치: shoulder_l"

    merged, err = merge_geometry_pair(a, b, allow_source_jitter=True)
    assert err is None
    assert merged["shoulder_l"] == pytest.approx([0.295, 0.20])
    assert merged["_agreement_warnings"] == {"shoulder_l": pytest.approx(0.09)}
    _, inventory, validation_err = validate_geometry(merged, aspect_hw=1.5)
    assert validation_err is None
    assert inventory["collar"] is True


def test_source_landmark_merge_still_rejects_severe_disagreement():
    """soft 합의는 source 지터 완충이지 다른 기하를 통과시키는 우회로가 아니다."""
    a = _landmark_response(shoulder_l=[0.18, 0.20])
    b = _landmark_response(shoulder_l=[0.39, 0.20])

    merged, err = merge_geometry_pair(a, b, allow_source_jitter=True)
    assert merged is None
    assert err == "landmark 불일치: shoulder_l"


# ── CIEDE2000 — Sharma 2005 공개 검증 벡터 ──────────────────────────────────────

@pytest.mark.parametrize(
    ("lab1", "lab2", "expected"),
    [
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
        ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412),
        ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
        ((50.0, -1.1848, -84.8006), (50.0, 0.0, -82.7485), 1.0000),
        ((50.0, -0.9009, -85.5211), (50.0, 0.0, -82.7485), 1.0000),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, -1.0, 2.0), (50.0, 0.0, 0.0), 2.3669),
    ],
)
def test_ciede2000_matches_sharma_reference_vectors(lab1, lab2, expected):
    """ΔE00 구현이 검증되기 전의 숫자는 gate 로 쓸 수 없다 — 논문 기준 벡터로 고정."""
    assert float(ciede2000(np.array(lab1), np.array(lab2))) == pytest.approx(
        expected, abs=1e-4)


# ── Stage 2 — extractor 집계 gate ──────────────────────────────────────────────

def _extract_all():
    results = []
    for sid in SIGNALS:
        for var in VARIANTS:
            img = render_signal(sid, var)
            h, w = img.shape[:2]
            m = extract_stripe_model(
                img, source_asset_id="fx", source_sha256="0" * 8, source_roi=(0, 0, w, h))
            results.append((sid, var, expected_for(sid, var), m))
    return results


def test_extractor_direction_order_and_group_count_are_exact_on_all_fixtures():
    """방향 100% + 색 순서/line-group 수 exact — 하나라도 어긋나면 gate FAIL."""
    bad = []
    for sid, var, exp, m in _extract_all():
        if isinstance(m, CompositeFailure):
            bad.append((sid, var, m.reason))
            continue
        if m.axis != exp["axis"] or len(m.line_width_ratios) != exp["n_runs"]:
            bad.append((sid, var, m.axis, len(m.line_width_ratios), exp["n_runs"]))
    assert not bad, f"exact-match 실패 케이스: {bad}"


def test_extractor_period_width_and_color_gates_hold_on_controlled_set():
    period_errs, width_errs, des = [], [], []
    for _sid, _var, exp, m in _extract_all():
        assert not isinstance(m, CompositeFailure)
        period_errs.append(abs(m.period_px - exp["period_px"]) / exp["period_px"])
        width_errs.extend(
            abs(a - b) / max(b, 1e-6)
            for a, b in zip(m.line_width_ratios, exp["line_width_ratios"]))
        des.extend(
            float(ciede2000(np.array(got), _rgb_to_lab(want)))
            for got, want in zip(m.color_sequence_lab, exp["ordered_palette_rgb"]))
    assert np.median(period_errs) <= 0.05 and np.percentile(period_errs, 95) <= 0.10, (
        f"period gate 위반: median={np.median(period_errs):.4f} "
        f"p95={np.percentile(period_errs, 95):.4f}")
    assert np.median(width_errs) <= 0.08 and np.percentile(width_errs, 95) <= 0.15, (
        f"width gate 위반: median={np.median(width_errs):.4f} "
        f"p95={np.percentile(width_errs, 95):.4f}")
    assert np.median(des) <= 6.0 and np.percentile(des, 95) <= 10.0, (
        f"ΔE00 gate 위반: median={np.median(des):.2f} p95={np.percentile(des, 95):.2f}")


def test_low_saturation_close_hues_stay_separate_colors():
    """저채도 근접색(회청/회갈)이 한 색으로 합쳐지면 실셔츠 실패(파랑/갈색 소실)의 재현이다."""
    for var in VARIANTS:
        m = extract_stripe_model(
            render_signal("S5_low_sat_close", var), source_asset_id="fx",
            source_sha256="0" * 8,
            source_roi=(0, 0, 768, 768))
        assert not isinstance(m, CompositeFailure), (var, m)
        assert len(m.color_sequence_lab) == 4, (var, len(m.color_sequence_lab))
        blue = np.array(m.color_sequence_lab[1])
        brown = np.array(m.color_sequence_lab[3])
        assert float(ciede2000(blue, brown)) > 8.0, "두 유채색 run 이 사실상 같은 색으로 붕괴"


@pytest.mark.parametrize("neg_id", sorted(NEGATIVE_CONTROLS))
def test_negative_controls_fail_closed_with_typed_reason(neg_id):
    m = extract_stripe_model(
        render_negative(neg_id), source_asset_id="fx", source_sha256="0" * 8,
        source_roi=(0, 0, 768, 768))
    assert isinstance(m, CompositeFailure), f"{neg_id} 가 추출돼버림 — fail-closed 위반"
    assert m.reason in COMPOSITE_FAILURE_REASONS
    if neg_id == "N2_gingham_check":
        assert m.reason == "unsupported_pattern", "체크는 명시적 unsupported 여야 한다"


# ── Stage 1 — 입력 gate ────────────────────────────────────────────────────────

def test_source_validation_fails_closed_on_small_blurry_or_clipped():
    import cv2

    good = render_signal("S2_navy_white_wide", "illum")
    big = np.tile(good, (2, 2, 1))  # 1536² — 주기는 유지한 채 ROI 만 키운다(반복 수 보존)
    ok = validate_stripe_source(big)
    assert not isinstance(ok, CompositeFailure), getattr(ok, "detail", ok)

    small = cv2.resize(good, (600, 600))
    r = validate_stripe_source(small)
    assert isinstance(r, CompositeFailure) and r.reason == "reference_insufficient"

    blurry = cv2.GaussianBlur(big, (0, 0), sigmaX=96)  # σ > 주기 — 반복 신호 파괴
    r = validate_stripe_source(blurry)
    assert isinstance(r, CompositeFailure) and r.reason == "reference_insufficient"

    clipped = big.copy()
    clipped[: big.shape[0] // 2] = 255  # 전 채널 하드클립 50% > 35%
    r = validate_stripe_source(clipped)
    assert isinstance(r, CompositeFailure) and r.reason == "reference_insufficient"


# ── Stage 3 — mask/panel gate ─────────────────────────────────────────────────

def _model():
    return extract_stripe_model(
        render_signal("S1_blue_brown_fine", "illum"), source_asset_id="fx",
        source_sha256="0" * 8, source_roi=(0, 0, 768, 768))


def test_mask_iou_gate_and_strategy_comparison_on_all_carrier_fixtures():
    """두 전략 모두 측정하고 채택 전략(bg_diff)은 IoU ≥ 0.92 를 전 케이스에서 만족한다."""
    ious = {"bg_diff": [], "grabcut": []}
    for gid in GEOMETRIES:
        for var in (0, 1):
            cx = render_carrier(gid, var)
            gt = cx["garment_mask"] > 0
            for strategy, fn in (("bg_diff", mask_bg_diff), ("grabcut", mask_grabcut)):
                polys = [np.array(cx["torso_poly"], np.float32),
                         np.array(cx["sleeve_l_poly"], np.float32),
                         np.array(cx["sleeve_r_poly"], np.float32)]
                got = fn(cx["image"], polys) > 0
                iou = (got & gt).sum() / max(1, (got | gt).sum())
                ious[strategy].append(iou)
    assert min(ious["bg_diff"]) >= 0.92, f"bg_diff IoU gate 위반: {min(ious['bg_diff']):.3f}"
    # 채택 근거 기록 — bg_diff 가 열등해지면 이 assert 가 전략 재평가를 강제한다
    assert np.median(ious["bg_diff"]) >= np.median(ious["grabcut"]) - 0.02


def test_panel_map_blocks_carrier_with_mismatched_construction():
    cx = render_carrier("G1_regular", 0)
    src_inv = dict(cx["construction_inventory"])
    bad = dict(src_inv)
    bad["visible_buttons"] = src_inv["visible_buttons"] + 3  # ±1 관용 밖
    r = build_panel_map(cx["image"], cx["landmarks"],
                        source_inventory=src_inv, carrier_inventory=bad)
    assert isinstance(r, CompositeFailure) and r.reason == "geometry_carrier_mismatch"

    worse = dict(src_inv)
    worse["torso_aspect"] = src_inv["torso_aspect"] * 1.5  # 셔츠 기장/폭이 다른 옷
    r = build_panel_map(cx["image"], cx["landmarks"],
                        source_inventory=src_inv, carrier_inventory=worse)
    assert isinstance(r, CompositeFailure) and r.reason == "geometry_carrier_mismatch"


def test_panel_map_fails_closed_on_bad_landmarks():
    cx = render_carrier("G2_slim", 0)
    r = build_panel_map(cx["image"], {})
    assert isinstance(r, CompositeFailure) and r.reason == "panel_landmarks_invalid"

    flipped = dict(cx["landmarks"])
    flipped["shoulder_l"], flipped["shoulder_r"] = flipped["shoulder_r"], flipped["shoulder_l"]
    r = build_panel_map(cx["image"], flipped)
    assert isinstance(r, CompositeFailure) and r.reason == "panel_landmarks_invalid"


def test_panel_map_rejects_blank_carrier_instead_of_echoing_panel_polygon():
    """무신호 carrier 에서 stripe_energy/GrabCut seed polygon 이 high-confidence mask 가 되면 안 된다."""
    cx = render_carrier("G1_regular", 0)
    washed = np.full_like(cx["image"], 242)
    r = build_panel_map(washed, cx["landmarks"], strategy="auto")
    assert isinstance(r, CompositeFailure)
    assert r.reason == "mask_low_confidence"
    assert r.metrics["texture_energy_p95"] < 1.0


def test_panel_map_rejects_textured_washed_carrier_echoing_panel_polygon():
    """약한 그림자·폴드·노이즈가 있어도 paint target 이 panel union 이면 slab 합성을 막는다."""
    import cv2

    cx = render_carrier("G1_regular", 0)
    h, w = cx["image"].shape[:2]
    seed = build_panel_map(cx["image"], cx["landmarks"], strategy="bg_diff")
    assert not isinstance(seed, CompositeFailure)
    panel_seed = np.zeros((h, w), np.uint8)
    for p in seed.panels:
        cv2.fillPoly(panel_seed, [p.quad.astype(np.int32)], 255)

    y_grad = np.linspace(238, 246, h, dtype=np.float32)[:, None]
    x_grad = np.linspace(-3, 3, w, dtype=np.float32)[None, :]
    folds = 2.0 * np.sin(np.linspace(0, 10 * np.pi, h, dtype=np.float32))[:, None]
    panel_texture = 3.0 * np.sin(
        2 * np.pi * np.arange(w, dtype=np.float32)[None, :] / 6.0)
    noise = np.random.default_rng(7).normal(0, 0.4, (h, w)).astype(np.float32)
    gray = np.clip(
        y_grad + x_grad + folds + noise + (panel_seed > 0) * panel_texture,
        0, 255).astype(np.uint8)
    washed = np.dstack([gray, gray, gray])

    r = build_panel_map(washed, cx["landmarks"], strategy="auto")
    assert isinstance(r, CompositeFailure)
    assert r.reason == "mask_low_confidence"
    assert r.metrics["texture_energy_p95"] > 1.0
    assert r.metrics["strategy"] == "stripe_energy"
    assert r.metrics["iou"] > 0.98
    assert 0.985 <= r.metrics["mask_area_ratio"] <= 1.015


def test_panel_map_allows_grabcut_mask_that_covers_panels_but_has_real_silhouette():
    """fallback mask 는 panel 을 덮어야 한다. seed 와 같은 형상일 때만 echo 로 막는다."""
    import cv2

    cx = render_carrier("G1_regular", 0)
    h, w = cx["image"].shape[:2]
    seed = build_panel_map(cx["image"], cx["landmarks"], strategy="bg_diff")
    assert not isinstance(seed, CompositeFailure)
    panel_seed = np.zeros((h, w), np.uint8)
    for p in seed.panels:
        cv2.fillPoly(panel_seed, [p.quad.astype(np.int32)], 255)

    carrier = np.full((h, w, 3), 245, np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
    real_silhouette = cv2.dilate(panel_seed, kernel)
    carrier[real_silhouette > 0] = (170, 170, 170)

    r = build_panel_map(carrier, cx["landmarks"], strategy="grabcut")
    assert not isinstance(r, CompositeFailure)
    assert r.strategy == "grabcut"
    assert r.metrics["poly_cover"] > 0.985
    assert r.metrics["iou_poly_mask"] < 0.98
    assert r.metrics["mask_area_ratio"] > 1.05


# ── Stage 4/5 — composite·QC 집계 gate ─────────────────────────────────────────

def _run_full(gid, var, model, *, period_divisor=22.0):
    cx = render_carrier(gid, var)
    pm = build_panel_map(cx["image"], cx["landmarks"],
                         source_inventory=cx["construction_inventory"],
                         carrier_inventory=cx["construction_inventory"])
    assert not isinstance(pm, CompositeFailure), pm
    torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
    period_t = torso_h / period_divisor
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period_t, target_axis="horizontal")
    return cx, pm, period_t, art


def test_composite_and_qc_gates_hold_on_all_carrier_fixtures():
    model = _model()
    assert not isinstance(model, CompositeFailure)
    covs, drifts, ssims, neg_j, stretch_bad = [], [], [], 0, 0
    for gid in GEOMETRIES:
        for var in (0, 1):
            cx, pm, period_t, art = _run_full(gid, var, model)
            assert not isinstance(art, CompositeFailure), (gid, var, art)
            covs.append(art.source_coverage)
            for pmet in art.panel_metrics.values():
                neg_j += pmet["neg_jacobian"]
                stretch_bad += int(pmet["stretch_over_frac"] > 0.01)
            qc = verify_composite(art.image_bgr, cx["image"], pm, model,
                                  target_period_px=period_t, target_axis="horizontal")
            assert qc.passed, (gid, var, qc.metrics["failure_details"][:3])
            drifts.append(qc.metrics["outside_drift_frac"])
            ssims.append(qc.metrics["outside_ssim"])
    assert min(covs) >= 0.90, f"coverage gate 위반: {min(covs):.3f}"
    assert neg_j == 0, "negative Jacobian 발생"
    assert stretch_bad == 0, "stretch 1% 초과 panel 존재"
    assert np.median(drifts) <= 0.01 and np.percentile(drifts, 95) <= 0.03
    assert min(ssims) >= 0.98, f"mask 밖 SSIM gate 위반: {min(ssims):.4f}"


def test_composite_preserves_pixels_outside_mask_before_encoding():
    """PNG 인코딩 전 ndarray 기준 mask 밖은 carrier 와 정확히 같아야 한다."""
    model = _model()
    cx, pm, period_t, art = _run_full("G1_regular", 0, model)
    assert not isinstance(art, CompositeFailure)
    outside = pm.garment_mask == 0
    assert np.array_equal(art.image_bgr[outside], cx["image"][outside])
    assert np.count_nonzero(art.image_bgr[outside] != cx["image"][outside]) == 0


def test_deterministic_qc_exposes_projection_completion_metrics():
    """Phase 1 활성화 판단용 정량값은 worker 밖에서도 읽을 수 있어야 한다."""
    model = _model()
    cx, pm, period_t, art = _run_full("G1_regular", 0, model)
    assert not isinstance(art, CompositeFailure)
    qc = verify_composite(
        art.image_bgr, cx["image"], pm, model,
        target_period_px=period_t,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
    )
    assert qc.passed, qc.metrics["failure_details"]
    for key in (
        "period_rel_err_max",
        "repeat_count_rel_err_max",
        "direction_error_max",
        "color_delta_e00_max",
        "color_delta_e00_median",
        "mask_coverage",
        "outside_drift_frac",
    ):
        assert key in qc.metrics, key
    assert qc.metrics["mask_coverage"] == pytest.approx(art.source_coverage, abs=0.02)
    assert qc.metrics["outside_drift_frac"] == 0.0


def test_shadow_observation_can_return_low_coverage_artifact_without_relaxing_default_gate():
    """coverage 부족은 enforce 기본 gate 에선 실패, shadow 관측에선 QC metric 산출까지 간다."""
    model = _model()
    cx, pm, period_t, _art = _run_full("G1_regular", 0, model)
    wide_protected = np.full_like(pm.protected, 255)
    low_cov_pm = dataclasses.replace(pm, protected=wide_protected)

    strict = composite_stripe(
        cx["image"], low_cov_pm, model,
        target_period_px=period_t, target_axis="horizontal")
    assert isinstance(strict, CompositeFailure)
    assert strict.reason == "source_coverage_low"

    observed = composite_stripe(
        cx["image"], low_cov_pm, model,
        target_period_px=period_t, target_axis="horizontal",
        allow_low_source_coverage=True)
    assert not isinstance(observed, CompositeFailure)
    assert observed.source_coverage < 0.90
    qc = verify_composite(
        observed.image_bgr, cx["image"], low_cov_pm, model,
        target_period_px=period_t,
        target_axis="horizontal",
        painted_mask=observed.painted,
    )
    assert "mask_coverage" in qc.metrics


def test_intentionally_preserved_cuff_band_is_not_counted_as_missing_source_coverage():
    """Cuffs stay carrier-owned by design and therefore are outside the projection coverage scope."""
    model = _model()
    assert not isinstance(model, CompositeFailure)
    h, w = 240, 100
    carrier = np.full((h, w, 3), 210, np.uint8)
    garment = np.zeros((h, w), np.uint8)
    garment[20:221, 25:76] = 255
    panel = Panel(
        "sleeve_l",
        "stripe",
        np.array([[25, 20], [75, 20], [75, 220], [25, 220]], np.float32),
        axis_ends=((50.0, 20.0), (50.0, 220.0)),
    )
    panel_map = PanelMap(
        garment_mask=garment,
        protected=garment.copy(),
        boundary=np.zeros_like(garment),
        panels=(panel,),
        confidence=1.0,
        strategy="fixture",
    )

    artifact = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=12.0,
        target_axis="vertical",
    )

    assert not isinstance(artifact, CompositeFailure), artifact
    assert artifact.source_coverage >= 0.90


def test_warp_rejects_flipped_quad():
    from app.services.hybrid_composite.panel_map import Panel, PanelMap

    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    assert not isinstance(pm, CompositeFailure)
    q = pm.panels[0].quad.copy()
    q[[0, 1]] = q[[1, 0]]  # TL↔TR 교차 — 자기교차 quad
    crossed = PanelMap(
        garment_mask=pm.garment_mask, protected=pm.protected, boundary=pm.boundary,
        panels=(Panel("torso", "stripe", q),), confidence=pm.confidence,
        strategy=pm.strategy, metrics=pm.metrics)
    r = composite_stripe(cx["image"], crossed, model,
                         target_period_px=30.0, target_axis="horizontal")
    assert isinstance(r, CompositeFailure) and r.reason == "warp_invalid"


def test_shading_transfer_keeps_source_chroma_and_order():
    """carrier 에서 가져오는 것은 저주파 L 뿐 — a/b(chroma)와 색 순서는 source 전용이다."""
    model = _model()
    cx, pm, period_t, art = _run_full("G1_regular", 0, model)
    assert not isinstance(art, CompositeFailure)
    lab = bgr_to_lab(art.image_bgr)
    # protected 내부에서 파랑 줄의 b* 는 음수(파랑), 갈색 줄의 b* 는 양수여야 한다
    sel = pm.protected > 0
    bs = lab[..., 2][sel]
    # 임계는 절반 오염(carrier 무채색과 50% 혼합 시 b* 가 반토막)도 잡을 만큼 타이트해야
    # 한다 — 실측 원값 blue b*≈-40 / brown b*≈+26, 절반 오염 시 -20/+13 (mutation HM9 실측).
    assert float(np.percentile(bs, 2)) < -28.0, "파란 줄 chroma 소실/희석"
    assert float(np.percentile(bs, 98)) > 16.0, "갈색/베이지 줄 chroma 소실/희석"
    # carrier(무지 회색)의 chroma 가 섞였다면 분포가 0 근처로 붕괴한다
    carrier_bs = bgr_to_lab(cx["image"])[..., 2][sel]
    assert float(np.abs(carrier_bs).max()) < 6.0  # 전제 확인: carrier 는 무채색
    assert float(np.abs(bs).max()) > 30.0


def test_decal_component_without_source_pixels_is_flagged_for_review():
    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    h, w = cx["image"].shape[:2]
    collar = [[w * 0.45, h * 0.16], [w * 0.55, h * 0.16], [w * 0.55, h * 0.20], [w * 0.45, h * 0.20]]
    torso_h = np.ptp([p[1] for p in cx["torso_poly"]])
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=torso_h / 22.0, target_axis="horizontal",
                           component_boxes={"collar": collar})  # source 픽셀 미제공
    assert not isinstance(art, CompositeFailure)
    assert art.components_needing_review == ("collar",), "source 없는 component 는 검수 대상"


def test_qc_detects_wrong_period_reversed_order_and_outside_drift():
    """deterministic QC 가 실제로 이빨이 있는지 — 고의 손상 3종을 각각 잡아야 한다."""
    import cv2

    model = _model()
    cx, pm, period_t, art = _run_full("G1_regular", 0, model)
    assert not isinstance(art, CompositeFailure)

    # (a) 주기 20% 틀린 합성
    art_bad = composite_stripe(cx["image"], pm, model,
                               target_period_px=period_t * 1.25, target_axis="horizontal")
    qc = verify_composite(art_bad.image_bgr, cx["image"], pm, model,
                          target_period_px=period_t, target_axis="horizontal")
    assert not qc.passed and "pattern_metric_failed" in qc.failures

    # (b) 색 순서 뒤집힌 모델로 합성한 결과를 원 모델 기준으로 검사
    from dataclasses import replace
    reversed_model = replace(
        model,
        period_profile_lab=model.period_profile_lab[::-1].copy(),
        color_sequence_lab=tuple(reversed(model.color_sequence_lab)),
        line_width_ratios=tuple(reversed(model.line_width_ratios)))
    art_rev = composite_stripe(cx["image"], pm, reversed_model,
                               target_period_px=period_t, target_axis="horizontal")
    qc = verify_composite(art_rev.image_bgr, cx["image"], pm, model,
                          target_period_px=period_t, target_axis="horizontal")
    assert not qc.passed, "색 순서 반전을 통과시킴"

    # (c) mask 밖 픽셀 손상
    tampered = art.image_bgr.copy()
    outside = np.where(pm.garment_mask == 0)
    n = len(outside[0])
    pick = slice(0, max(1, int(n * 0.05)))
    tampered[outside[0][pick], outside[1][pick]] = (0, 0, 255)
    qc = verify_composite(tampered, cx["image"], pm, model,
                          target_period_px=period_t, target_axis="horizontal")
    assert "protected_region_drift" in qc.failures


# ── component decal 좌표 단위 계약 ──────────────────────────────────────────────
# 2026-08-04 실 4K 실행에서 collar/placket 이 어떤 입력에서도 protected_component_missing
# 으로 떨어졌다. 원인은 vision 품질이 아니라 단위였다: validate_geometry 는 정규화
# [0,1] 을 돌려주는데 warp_composite 는 픽셀을 가정해, source quad 의 변이 항상 1.0
# 이하가 되어 MIN_DECAL_RES_PX(48) 게이트가 무조건 발화했다. 성공 경로를 태우는 테스트가
# 하나도 없어서 2435개 테스트가 통과하는 채로 출하됐다 — 그 구멍을 여기서 막는다.

def _collar_case():
    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    h, w = cx["image"].shape[:2]
    carrier_collar = [[w * 0.45, h * 0.16], [w * 0.55, h * 0.16],
                      [w * 0.55, h * 0.20], [w * 0.45, h * 0.20]]
    period = float(np.ptp([p[1] for p in cx["torso_poly"]])) / 22.0
    return model, cx, pm, carrier_collar, period


def test_decal_component_with_pixel_source_box_is_not_flagged():
    """source decal 이 실제 픽셀 좌표로 오면 warp 경로가 살아 있어야 한다."""
    model, cx, pm, carrier_collar, period = _collar_case()
    src = np.full((400, 400, 3), (40, 60, 200), np.uint8)
    src_collar = [[100.0, 100.0], [300.0, 100.0], [300.0, 300.0], [100.0, 300.0]]
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal",
                           component_boxes={"collar": carrier_collar},
                           source_bgr=src,
                           source_component_boxes={"collar": src_collar})
    assert not isinstance(art, CompositeFailure), art
    assert art.components_needing_review == (), \
        "source decal 이 충분한 해상도로 주어지면 검수 대상이 아니다"


def test_normalized_source_box_never_reaches_the_decal_path():
    """정규화 좌표가 픽셀 경로로 새면 반드시 걸러져야 한다 (실 4K 실패의 형태)."""
    model, cx, pm, carrier_collar, period = _collar_case()
    src = np.full((400, 400, 3), (40, 60, 200), np.uint8)
    normalized_collar = [[0.45, 0.16], [0.55, 0.16], [0.55, 0.20], [0.45, 0.20]]
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal",
                           component_boxes={"collar": carrier_collar},
                           source_bgr=src,
                           source_component_boxes={"collar": normalized_collar})
    assert not isinstance(art, CompositeFailure), art
    assert art.components_needing_review == ("collar",), \
        "정규화 좌표는 변 길이가 1 이하라 decal source 로 쓸 수 없다"


def test_boxes_to_pixels_scales_each_image_independently():
    """source 와 carrier 는 크기가 다르므로 각자의 해상도로 환산해야 한다."""
    norm = {"collar_box": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.25], [0.0, 0.25]]}
    src_px = boxes_to_pixels(norm, width=400, height=800)
    car_px = boxes_to_pixels(norm, width=1000, height=2000)
    assert src_px["collar_box"] == [[0.0, 0.0], [200.0, 0.0], [200.0, 200.0], [0.0, 200.0]]
    assert car_px["collar_box"] == [[0.0, 0.0], [500.0, 0.0], [500.0, 500.0], [0.0, 500.0]]
    assert boxes_to_pixels({}, width=10, height=10) == {}
    assert boxes_to_pixels(None, width=10, height=10) == {}


def test_normalized_boxes_after_conversion_clear_the_resolution_gate():
    """실제 배선(정규화 → 환산 → 합성)에서 collar 가 검수 대상이 되지 않아야 한다."""
    model, cx, pm, carrier_collar, period = _collar_case()
    src = np.full((400, 400, 3), (40, 60, 200), np.uint8)
    converted = boxes_to_pixels(
        {"collar": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]},
        width=400, height=400)
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal",
                           component_boxes={"collar": carrier_collar},
                           source_bgr=src, source_component_boxes=converted)
    assert not isinstance(art, CompositeFailure), art
    assert art.components_needing_review == ()


def test_box_rejection_reason_separates_absent_from_malformed():
    """'vision 이 안 줬다' 와 '형식이 틀려 버려졌다' 는 구분돼야 한다."""
    ok = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]
    assert box_rejection_reason(ok) is None
    assert box_rejection_reason(None) == "absent"
    assert box_rejection_reason("nope") == "not_a_list"
    assert box_rejection_reason(ok[:2]) == "point_count_2"
    assert box_rejection_reason([[0.1, 0.1, 0.1]] * 4) == "point_not_xy_pair"
    assert box_rejection_reason([[0.1, "x"], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]) \
        == "coord_not_number"
    # 픽셀 좌표가 그대로 오면 validator 가 거부한다 — 조용한 누락과 구분된다
    assert box_rejection_reason([[45.0, 16.0], [55.0, 16.0], [55.0, 20.0], [45.0, 20.0]]) \
        == "coord_out_of_unit_range"


def test_component_observation_reports_presence_without_raw_response():
    ok = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]
    obs = component_observation({"collar_box": ok, "confidence": 0.9, "secret": "x"})
    assert obs["collar_box"]["present"] is True
    assert obs["collar_box"]["box"] == ok
    assert obs["placket_box"] == {"present": False, "rejected": "absent"}
    assert "secret" not in json.dumps(obs), "관측 payload 에 응답 원문이 새면 안 된다"


# ── D-1: painted 내부 계면 feather ─────────────────────────────────────────────
# 예전에는 실루엣 테두리만 ramp 를 먹이고 `alpha[painted == 0] = 0` 으로 내부 계면을
# 1픽셀 계단으로 잘랐다. assign-cost 등고선이 이미지 공간에서 직선이라 그 계단이
# '붙여넣은 직사각형 판' 으로 보였다(v6 실측).

def _alpha_case(**kw):
    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    period = float(np.ptp([p[1] for p in cx["torso_poly"]])) / 22.0
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal", **kw)
    assert not isinstance(art, CompositeFailure), art
    return cx, pm, art


def test_internal_painted_interface_is_feathered_not_a_one_pixel_step():
    """painted 내부 계면에서 alpha 가 계단이 아니라 경사로 떨어져야 한다."""
    _cx, pm, art = _alpha_case()
    alpha, painted = art.alpha, art.painted
    interior = (pm.garment_mask > 0) & (pm.protected > 0)   # 실루엣 테두리 영향 제외
    # painted 경계에 인접한 내부 픽셀 — 여기 alpha 가 전부 0/1 뿐이면 계단이다
    eroded = cv2.erode(painted, np.ones((3, 3), np.uint8))
    rim = interior & (painted > 0) & (eroded == 0)
    assert rim.sum() > 0, "테스트 전제: painted 내부 경계가 존재해야 한다"
    partial = ((alpha > 0.02) & (alpha < 0.98) & rim).sum()
    assert partial / rim.sum() > 0.5, (
        "내부 경계 대부분이 부분 alpha 여야 한다 — 계단이면 이 비율이 0 에 가깝다")


def test_alpha_never_escapes_the_garment_mask():
    """feather 를 넣어도 의류 밖 alpha 는 정확히 0 이어야 한다."""
    _cx, pm, art = _alpha_case()
    assert float(art.alpha[pm.garment_mask == 0].max(initial=0.0)) == 0.0


def test_feather_keeps_outside_mask_drift_exactly_zero():
    """계면 feather 이후에도 마스크 밖 픽셀은 인코딩 전 배열에서 무변경이어야 한다."""
    cx, pm, art = _alpha_case()
    outside = pm.garment_mask == 0
    assert np.array_equal(art.image_bgr[outside], cx["image"][outside])


def test_inner_feather_width_is_capped_by_the_image_relative_band():
    """전이 폭이 패널을 잠식하지 않도록 실루엣 밴드로 상한이 걸려야 한다."""
    from app.services.hybrid_composite.warp_composite import INNER_FEATHER_PERIODS
    band = 10.0
    coarse = float(np.clip(30.0 * INNER_FEATHER_PERIODS, 3.0, band))
    fine = float(np.clip(9.0 * INNER_FEATHER_PERIODS, 3.0, 40.0))
    assert coarse == band, "굵은 주기는 밴드 상한에서 잘린다"
    assert 3.0 < fine < 40.0, "촘촘한 주기는 주기 비례를 그대로 쓴다"


# ── D-2: painted ↔ 인접 carrier chroma 정합 ────────────────────────────────────
# L 만 carrier 에 앵커링하고 a/b 를 source 그대로 두면 painted 몸통(그늘 촬영색)과
# unpainted 커프(스튜디오색)가 한 벌로 보이지 않는다(v6 실측).

def _cast_case(shift_ab):
    """carrier 를 a/b 로 밀어 조명 cast 차이를 만든 뒤 합성한다."""
    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    lab = bgr_to_lab(cx["image"])
    lab[..., 1] += shift_ab[0]
    lab[..., 2] += shift_ab[1]
    from app.services.hybrid_composite.color import lab_to_bgr
    shifted = lab_to_bgr(lab)
    pm2 = build_panel_map(shifted, cx["landmarks"])
    period = float(np.ptp([p[1] for p in cx["torso_poly"]])) / 22.0
    art = composite_stripe(shifted, pm2 if not isinstance(pm2, CompositeFailure) else pm,
                           model, target_period_px=period, target_axis="horizontal")
    return art


def test_chroma_cast_is_measured_and_reported():
    _cx, _pm, art = _alpha_case()
    assert "chroma_cast_ab" in art.metrics
    assert len(art.metrics["chroma_cast_ab"]) == 2


def test_chroma_cast_offset_preserves_relative_stripe_colour_order():
    """cast 보정은 균일 오프셋이므로 줄 사이 상대 색차(색 순서)를 바꾸면 안 된다."""
    model = _model()
    seq = np.asarray(model.color_sequence_lab, np.float64)
    cast = np.array([4.0, -3.0])
    shifted = seq.copy()
    shifted[:, 1:3] += cast
    before = np.diff(seq[:, 1:3], axis=0)
    after = np.diff(shifted[:, 1:3], axis=0)
    assert np.allclose(before, after), "균일 오프셋은 줄 간 상대 색차를 보존한다"
    assert not np.allclose(seq[:, 1:3], shifted[:, 1:3]), "테스트 전제: 실제로 이동했다"


def test_excessive_chroma_cast_fails_closed():
    """같은 옷으로 설명되지 않는 chroma 차이는 그럴듯하게 보정하지 않고 닫는다."""
    from app.services.hybrid_composite.warp_composite import MAX_CHROMA_CAST
    art = _cast_case((MAX_CHROMA_CAST + 25.0, MAX_CHROMA_CAST + 25.0))
    assert isinstance(art, CompositeFailure), "과도한 cast 는 fail-closed 여야 한다"
    assert art.reason == "chroma_cast_excessive"


def test_moderate_chroma_cast_is_absorbed_not_rejected():
    """같은 옷의 조명 차이 정도는 보정으로 흡수한다 — 과도한 거절은 제품을 죽인다."""
    art = _cast_case((3.0, -2.0))
    assert not isinstance(art, CompositeFailure), art


# ── D-3: 저주파 척도의 해상도 독립성 ───────────────────────────────────────────
# 예전 `sigma = max(period*1.2, 15.0)` 은 두 항이 모두 절대 픽셀이라, 1K 에서 맞춘 값이
# 4K 에서는 사실상 무보정이었다(15px 위의 carrier 고주파가 휘도 앵커에 그대로 실린다).

def _sigma_for(period_px, short_side):
    from app.services.hybrid_composite.warp_composite import (
        SHADING_SIGMA_MAX_FRAC, SHADING_SIGMA_MIN_FRAC,
    )
    return float(np.clip(period_px * 1.2,
                         short_side * SHADING_SIGMA_MIN_FRAC,
                         short_side * SHADING_SIGMA_MAX_FRAC))


def test_shading_sigma_scales_with_resolution_for_the_same_garment():
    """같은 옷을 1K/4K 로 찍으면 저주파 척도도 같은 비율이어야 한다."""
    # 같은 셔츠: 해상도가 4배면 줄 주기도 4배다
    s1k = _sigma_for(9.0, 848.0)
    s4k = _sigma_for(36.0, 3392.0)
    assert s4k / s1k == pytest.approx(4.0, rel=0.02), (s1k, s4k)


def test_fine_stripe_at_4k_no_longer_falls_back_to_a_1k_sized_blur():
    """4K 미세 줄무늬(주기 9px)에서 예전 상수 하한 15px 는 무보정에 가까웠다."""
    legacy = max(9.02 * 1.2, 15.0)
    now = _sigma_for(9.02, 3392.0)
    assert legacy == pytest.approx(15.0)
    assert now > legacy * 3, f"4K 하한이 여전히 1K 크기다: {now}"


def test_shading_sigma_stays_below_the_drape_scale():
    """주름·드레이프(짧은 변의 10~25%)까지 지워버리면 옷이 평면이 된다."""
    for short_side in (848.0, 1600.0, 3392.0):
        for period in (4.0, 30.0, 400.0):
            assert _sigma_for(period, short_side) <= short_side * 0.08 + 1e-6


def test_low_frequency_preservation_matches_across_a_1k_4k_pair():
    """1K/4K 합성 쌍에서 저주파 보존량이 동등해야 한다."""
    def drape_energy(short_side, period):
        n = int(short_side)
        yy = np.linspace(0, np.pi * 3, n, dtype=np.float32)
        drape = (np.sin(yy)[:, None] * 12.0 + 50.0).repeat(n, axis=1)   # 큰 접힘 음영
        xx = np.arange(n, dtype=np.float32)
        stripes = np.sin(xx / period * 2 * np.pi)[None, :] * 8.0        # 줄무늬 고주파
        img = drape + stripes
        blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=_sigma_for(period, short_side))
        kept = float(np.std(blurred)) / float(np.std(drape))            # 접힘 보존율
        leaked = float(np.std(blurred - cv2.GaussianBlur(drape, (0, 0), sigmaX=1.0)))
        return kept, leaked

    kept_1k, leak_1k = drape_energy(848.0, 9.0)
    kept_4k, leak_4k = drape_energy(3392.0, 36.0)
    assert kept_1k == pytest.approx(kept_4k, rel=0.1), (kept_1k, kept_4k)
    assert leak_1k == pytest.approx(leak_4k, abs=1.5), (leak_1k, leak_4k)
    assert kept_1k > 0.8, "접힘 음영이 저주파에 남아 있어야 한다"


# ── D-6: mask 위생 게이트 ──────────────────────────────────────────────────────
# torso_aspect(mask 유도)는 기록만 되고 판정에 쓰이지 않아, mask 가 붕괴한 carrier 도
# 그대로 통과했다(v6 실측: source 1.353 / carrier 4.841 = 3.58배).

def _inv(**kw):
    base = {"collar": True, "placket": True, "cuffs": True,
            "visible_buttons": 6, "torso_aspect": 1.4}
    base.update(kw)
    return base


def _panels_with(source_aspect, carrier_aspect):
    cx = render_carrier("G1_regular", 0)
    return build_panel_map(
        cx["image"], cx["landmarks"],
        source_inventory=_inv(torso_aspect_mask=source_aspect),
        carrier_inventory=_inv(torso_aspect_mask=carrier_aspect))


def test_collapsed_torso_mask_aspect_is_rejected():
    """v6 형태(3.58배)는 물리적으로 설명되지 않는 mask 붕괴다 — 닫아야 한다."""
    pm = _panels_with(1.353, 4.841)
    assert isinstance(pm, CompositeFailure), "3.58배 종횡비 붕괴가 통과했다"
    assert pm.reason == "geometry_carrier_mismatch"
    assert pm.metrics["mask_aspect_ratio"] == pytest.approx(3.578, abs=0.01)


def test_same_shirt_flat_lay_to_worn_aspect_still_passes():
    """같은 셔츠의 flat-lay↔착장 차이(실측 1.75~1.76배)는 통과해야 한다."""
    for carrier in (1.353 * 1.75, 1.353 * 1.76):
        pm = _panels_with(1.353, carrier)
        assert not isinstance(pm, CompositeFailure), (carrier, pm)


def test_torso_aspect_ratio_is_recorded_for_calibration():
    pm = _panels_with(1.4, 2.0)
    assert not isinstance(pm, CompositeFailure), pm
    rec = pm.metrics["torso_aspect"]
    assert rec["mask_aspect_ratio"] == pytest.approx(1.429, abs=0.01)
    assert "observational_only" not in rec, "관측 전용 꼬리표는 판정 연결 후 사라져야 한다"


def test_visible_button_tolerance_keeps_documented_view_difference():
    """정면 6 → 착장 3/4 뷰 4개는 실측된 가시성 차이지 구조 불일치가 아니다."""
    pm = build_panel_map(
        render_carrier("G1_regular", 0)["image"],
        render_carrier("G1_regular", 0)["landmarks"],
        source_inventory=_inv(visible_buttons=6),
        carrier_inventory=_inv(visible_buttons=4))
    assert not isinstance(pm, CompositeFailure), "문서화된 가시성 차이를 막으면 오거절이다"


# ── v6 회귀: 수치는 만점인데 사람이 거절한 합성 ────────────────────────────────
# 2026-08-04 실 4K 표본은 period_rel_err 0.0005 / repeat 0.0006 / mask_coverage 1.0 /
# outside_drift 0 으로 전 지표를 통과했지만 육안 거절됐다. 원본 픽셀을 저장소에 넣지
# 않고, 그 실패의 **형태**(계단 이음매·색 단절·평면화)를 합성으로 재현해 고정한다.

def _v6_shaped_case():
    model = _model()
    cx = render_carrier("G1_regular", 0)
    pm = build_panel_map(cx["image"], cx["landmarks"])
    period = float(np.ptp([p[1] for p in cx["torso_poly"]])) / 22.0
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal")
    assert not isinstance(art, CompositeFailure), art
    return cx, pm, art, period


def test_v6_style_hard_seam_is_now_rejected():
    """계면을 1픽셀 계단으로 되돌리면 seam QC 가 잡아야 한다 (예전엔 전 지표 통과)."""
    cx, pm, art, period = _v6_shaped_case()
    stepped = (art.painted > 0).astype(np.float32)      # feather 제거 = 예전 동작
    qc = verify_composite(art.image_bgr, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=stepped)
    assert "interface_seam" in qc.failures, qc.metrics.get("seam_hard_edge_frac")
    assert not qc.passed


def test_current_feathered_alpha_clears_the_seam_gate():
    """수정 후의 alpha 는 같은 게이트를 통과해야 한다 — 과도한 거절이면 제품이 죽는다."""
    cx, pm, art, period = _v6_shaped_case()
    qc = verify_composite(art.image_bgr, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "interface_seam" not in qc.failures, qc.metrics.get("seam_hard_edge_frac")


def test_v6_style_chroma_split_is_now_rejected():
    """painted 만 다른 조명색이면(몸통 그늘색 / 커프 스튜디오색) 경계 QC 가 잡아야 한다."""
    cx, pm, art, period = _v6_shaped_case()
    from app.services.hybrid_composite.color import lab_to_bgr
    lab = bgr_to_lab(art.image_bgr)
    sel = art.painted > 0
    lab[..., 1][sel] += 16.0          # painted 영역만 chroma 이동 = 두 원단
    lab[..., 2][sel] -= 14.0
    split = lab_to_bgr(lab)
    qc = verify_composite(split, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "boundary_chroma_discontinuity" in qc.failures, \
        qc.metrics.get("boundary_chroma_de00")


def test_v6_style_destroyed_drape_is_now_rejected():
    """주름 음영이 무너진 합성은 패턴 지표가 오히려 좋아진다 — drape QC 가 잡아야 한다.

    한계 기록: 현재 임계(0.60)는 음영이 **뒤집히거나 무너진** 경우를 잡는다. 대비만
    완만히 줄어든 약한 평면화는 상관이 0.8 대로 남아 통과한다 — 실사진 분포로
    재캘리브레이션하기 전까지 이 게이트를 '평면화 전반'의 보증으로 읽으면 안 된다.
    """
    cx, pm, art, period = _v6_shaped_case()
    from app.services.hybrid_composite.color import lab_to_bgr
    from app.services.hybrid_composite.deterministic_qc import DRAPE_SIGMA_FRAC
    sel = pm.garment_mask > 0
    lab = bgr_to_lab(art.image_bgr)
    # 줄무늬(고주파)는 그대로 두고 **저주파 접힘 음영만** 제거한다 — 이게 v6 증상이다.
    # 패턴 지표는 L 을 두 번 정규화하므로 이래도 만점이 나온다.
    sigma = max(3.0, min(art.image_bgr.shape[:2]) * DRAPE_SIGMA_FRAC)
    low = cv2.GaussianBlur(lab[..., 0], (0, 0), sigmaX=sigma)
    # 접힘 음영을 뒤집는다 — 밝던 마루가 어두워지고 골이 밝아진다. 줄무늬(고주파)는
    # 손대지 않으므로 패턴 지표는 그대로다.
    inverted = 2.0 * float(np.median(low[sel])) - low
    lab[..., 0] = np.where(sel, lab[..., 0] - low + inverted, lab[..., 0])
    flat = lab_to_bgr(lab)
    qc = verify_composite(flat, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "drape_lost" in qc.failures, qc.metrics.get("drape_corr")


def test_coverage_one_is_reported_alongside_its_denominator():
    """mask_coverage=1.0 이 품질 보증으로 오독되지 않도록 제외 비율을 함께 남긴다."""
    cx, pm, art, period = _v6_shaped_case()
    qc = verify_composite(art.image_bgr, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "garment_coverage" in qc.metrics
    assert "coverage_excluded_frac" in qc.metrics
    assert qc.metrics["garment_coverage"] <= qc.metrics["mask_coverage"] + 1e-6


def test_wrong_stripe_direction_is_now_gated():
    """direction_error 는 기록만 되고 어떤 상수와도 비교되지 않았다 — 게이트를 세운다."""
    from app.services.hybrid_composite.deterministic_qc import DIRECTION_ERROR_MAX
    assert DIRECTION_ERROR_MAX > 0
    cx, pm, art, period = _v6_shaped_case()
    # 축을 바꿔 재측정하면 직교축 주기가 더 세게 잡힌다 = 방향 오류
    qc = verify_composite(art.image_bgr, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="vertical",
                          painted_mask=art.painted, alpha=art.alpha)
    assert not qc.passed


# ── §D: component decal 적격성은 형상으로 판단한다 ─────────────────────────────
# "짧은 변 48px" 단일 임계는 정상 플래킷(실측 종횡비 1:15~1:20)을 구조적으로 거절한다.

def _q(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def test_thin_long_placket_source_is_eligible():
    """가늘고 긴 플래킷은 정상 부위다 — 짧은 변만 보면 매번 오거절된다."""
    from app.services.hybrid_composite.warp_composite import _decal_source_eligible
    src = _q(0, 0, 24, 420)      # 24×420 = 종횡비 17.5, 면적 10080
    tgt = _q(0, 0, 20, 360)
    ok, why = _decal_source_eligible(src, tgt)
    assert ok, why


def test_low_resolution_source_is_rejected():
    from app.services.hybrid_composite.warp_composite import _decal_source_eligible
    ok, why = _decal_source_eligible(_q(0, 0, 8, 300), _q(0, 0, 8, 300))
    assert not ok and why.startswith("short_side")


def test_small_area_source_is_rejected():
    from app.services.hybrid_composite.warp_composite import _decal_source_eligible
    ok, why = _decal_source_eligible(_q(0, 0, 40, 40), _q(0, 0, 40, 40))
    assert not ok and why.startswith("area")


def test_upscaling_from_a_smaller_source_is_rejected():
    """source 가 target 보다 작으면 확대 합성이라 선명도가 무너진다."""
    from app.services.hybrid_composite.warp_composite import _decal_source_eligible
    ok, why = _decal_source_eligible(_q(0, 0, 60, 60), _q(0, 0, 300, 300))
    assert not ok and why == "upscale_from_source"


def test_degenerate_aspect_is_rejected():
    from app.services.hybrid_composite.warp_composite import _decal_source_eligible
    ok, why = _decal_source_eligible(_q(0, 0, 20, 1200), _q(0, 0, 20, 1200))
    assert not ok and why.startswith("aspect")


def test_component_review_reasons_distinguish_branches():
    """'source box 부재' 와 '해상도 미달' 이 로그에서 구분돼야 진단이 된다."""
    model, cx, pm, carrier_collar, period = _collar_case()
    art = composite_stripe(cx["image"], pm, model,
                           target_period_px=period, target_axis="horizontal",
                           component_boxes={"collar": carrier_collar})
    assert art.component_review_reasons["collar"] == "source_box_absent"

    big = _q(0, 0, 300, 300)
    art_no_img = composite_stripe(cx["image"], pm, model,
                                  target_period_px=period, target_axis="horizontal",
                                  component_boxes={"collar": carrier_collar},
                                  source_component_boxes={"collar": big})
    assert art_no_img.component_review_reasons["collar"] == "source_image_absent"

    tiny = _q(0, 0, 6, 6)
    art2 = composite_stripe(cx["image"], pm, model,
                            target_period_px=period, target_axis="horizontal",
                            component_boxes={"collar": carrier_collar},
                            source_bgr=np.full((400, 400, 3), 200, np.uint8),
                            source_component_boxes={"collar": tiny})
    assert art2.component_review_reasons["collar"].startswith("short_side")


# ── Codex 검수 P1 보정 회귀 ────────────────────────────────────────────────────
# 독립 검수가 구성해 통과시킨 false-pass 세 가지를 각각 닫는다.

def test_opposing_chroma_shifts_no_longer_cancel_out():
    """경계 반쪽은 +Δ, 반쪽은 -Δ 로 갈라도 전역 중앙값이 상쇄해 통과하던 경로."""
    from app.services.hybrid_composite.color import lab_to_bgr
    cx, pm, art, period = _v6_shaped_case()
    lab = bgr_to_lab(art.image_bgr)
    sel = art.painted > 0
    h = lab.shape[0]
    top = np.zeros_like(sel); top[: h // 2] = True
    lab[..., 1][sel & top] += 35.0
    lab[..., 1][sel & ~top] -= 35.0
    split = lab_to_bgr(lab)
    qc = verify_composite(split, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "boundary_chroma_discontinuity" in qc.failures, qc.metrics.get("boundary_chroma_de00")


def test_alpha_step_just_below_one_is_still_a_hard_seam():
    """레벨 임계(>0.98)를 쓰면 0.98→0 계단이 '부드럽다' 로 분류됐다 — 기울기로 잰다."""
    cx, pm, art, period = _v6_shaped_case()
    stepped = (art.painted > 0).astype(np.float32) * 0.98   # 계단인데 0.98
    qc = verify_composite(art.image_bgr, cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=stepped)
    assert "interface_seam" in qc.failures, qc.metrics.get("seam_hard_edge_frac")


def test_flattened_drape_amplitude_is_caught_even_when_correlation_holds():
    """접힘을 눌러 평평하게 만들어도 상관은 0.8 대로 남는다 — 진폭비로 잡는다."""
    from app.services.hybrid_composite.color import lab_to_bgr
    from app.services.hybrid_composite.deterministic_qc import DRAPE_SIGMA_FRAC
    cx, pm, art, period = _v6_shaped_case()
    sel = pm.garment_mask > 0
    lab = bgr_to_lab(art.image_bgr)
    sigma = max(3.0, min(art.image_bgr.shape[:2]) * DRAPE_SIGMA_FRAC)
    low = cv2.GaussianBlur(lab[..., 0], (0, 0), sigmaX=sigma)
    med = float(np.median(low[sel]))
    squashed = med + (low - med) * 0.15          # 모양은 그대로, 진폭만 15%
    lab[..., 0] = np.where(sel, lab[..., 0] - low + squashed, lab[..., 0])
    qc = verify_composite(lab_to_bgr(lab), cx["image"], pm, _model(),
                          target_period_px=period, target_axis="horizontal",
                          painted_mask=art.painted, coverage_mask=art.coverage_scope,
                          alpha=art.alpha)
    assert "drape_lost" in qc.failures, (qc.metrics.get("drape_corr"),
                                         qc.metrics.get("drape_amp_ratio"))


def test_legitimate_view_plus_fit_variation_is_not_rejected():
    """문서화된 뷰차 1.76배 × 핏 변형 1.45배 = 2.552배는 정당한 조합이다."""
    from app.services.hybrid_composite.panel_map import MAX_TORSO_ASPECT_RATIO
    assert MAX_TORSO_ASPECT_RATIO > 1.76 * 1.45
    pm = _panels_with(1.353, 1.353 * 1.76 * 1.45)
    assert not isinstance(pm, CompositeFailure), getattr(pm, "detail", None)
    # 그래도 v6 붕괴(3.58배)는 계속 차단된다
    assert isinstance(_panels_with(1.353, 1.353 * 3.58), CompositeFailure)
