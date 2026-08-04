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
from app.agents.hybrid_landmarks import merge_geometry_pair, validate_geometry


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
