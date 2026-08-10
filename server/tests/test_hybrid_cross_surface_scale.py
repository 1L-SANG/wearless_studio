import cv2
import numpy as np
import pytest

from app.services.hybrid_composite.color import bgr_to_lab, lab_to_bgr
from app.services.hybrid_composite.deterministic_qc import (
    _component_output_scale,
    verify_composite,
)
from app.services.hybrid_composite.panel_map import Panel, PanelMap
from app.services.hybrid_composite.stripe_model import _autocorr_period, _detrended_profile
from app.services.hybrid_composite.types import CompositeFailure, StripeModel
from app.services.hybrid_composite.warp_composite import composite_stripe


def _stripe_source(*, period=20, size=400):
    img = np.full((size, size, 3), 220, np.uint8)
    for y in range(0, size, period):
        img[y + period // 2:y + period] = 80
    return img


def _stripe_source_at_angle(*, period=20, size=400, normal=(1.0, 0.0)):
    """Binary stripe cloth whose repeat advances along an arbitrary source axis."""
    unit = np.asarray(normal, np.float32)
    unit /= np.linalg.norm(unit)
    yy, xx = np.indices((size, size), dtype=np.float32)
    phase = np.mod(xx * unit[0] + yy * unit[1], float(period))
    img = np.full((size, size, 3), 220, np.uint8)
    img[phase >= period / 2.0] = 80
    return img


def _stripe_model(period=20):
    src = _stripe_source(period=period, size=period)
    prof = bgr_to_lab(src).mean(axis=1).astype(np.float32)
    light = tuple(np.median(prof[: period // 2], axis=0))
    dark = tuple(np.median(prof[period // 2:], axis=0))
    return StripeModel(
        axis="horizontal",
        period_px=float(period),
        period_profile_lab=prof,
        ground_color_lab=light,
        color_sequence_lab=(light, dark),
        line_width_ratios=(0.5, 0.5),
        n_periods_used=12,
        confidence=0.95,
        source_asset_id="synthetic",
        source_sha256="0" * 64,
        source_roi=(0, 0, period, period),
    )


def _fine_multicolor_model():
    ratios = np.asarray([0.676, 0.123, 0.084, 0.117], np.float64)
    colors = np.asarray([
        [82.0, 0.0, 6.0],
        [70.0, -7.0, -12.0],
        [66.0, 4.0, 12.0],
        [77.0, 1.0, 4.0],
    ], np.float32)
    K = 192
    phase = (np.arange(K, dtype=np.float64) + 0.5) / K
    run = np.searchsorted(np.cumsum(ratios), phase, side="right")
    profile = colors[np.minimum(run, len(colors) - 1)]
    return StripeModel(
        axis="vertical",
        period_px=30.0,
        period_profile_lab=profile,
        ground_color_lab=tuple(colors[0]),
        color_sequence_lab=tuple(map(tuple, colors)),
        line_width_ratios=tuple(ratios),
        n_periods_used=12,
        confidence=0.95,
        source_asset_id="synthetic-fine",
        source_sha256="1" * 64,
        source_roi=(0, 0, K, K),
    )


def _render_irregular_component(model, *, period, size=320, unit=(0.82, 0.57)):
    u = np.asarray(unit, np.float64)
    u /= np.linalg.norm(u)
    yy, xx = np.indices((size, size), dtype=np.float64)
    K = len(model.period_profile_lab)
    phase = np.mod((xx * u[0] + yy * u[1]) / float(period), 1.0) * K
    i0 = np.floor(phase).astype(np.int64) % K
    i1 = (i0 + 1) % K
    frac = (phase - i0)[..., None]
    lab = (model.period_profile_lab[i0] * (1.0 - frac)
           + model.period_profile_lab[i1] * frac).astype(np.float32)
    # final-output evidence includes smooth carrier illumination but no extra pattern.
    lab[..., 0] += (5.0 * np.sin(yy / 73.0) + 3.0 * np.cos(xx / 91.0)).astype(np.float32)
    mask = np.zeros((size, size), np.uint8)
    cv2.fillPoly(mask, [np.asarray([
        [35, 78], [270, 32], [302, 150], [248, 286], [72, 260], [18, 170],
    ], np.int32)], 255)
    return lab_to_bgr(lab), mask, u


def _carrier_panel_map():
    h, w = 360, 240
    carrier = np.full((h, w, 3), 236, np.uint8)
    garment = np.zeros((h, w), np.uint8)
    garment[40:321, 40:201] = 255
    yy = np.arange(h, dtype=np.float32)[:, None]
    shade = 170 + 18 * np.sin(yy / 38.0)
    carrier[garment > 0] = np.repeat(shade, w, axis=1)[garment > 0, None]
    panel = Panel(
        "torso",
        "stripe",
        np.array([[40, 40], [200, 40], [200, 320], [40, 320]], np.float32),
    )
    return carrier, PanelMap(
        garment_mask=garment,
        protected=garment.copy(),
        boundary=np.zeros_like(garment),
        panels=(panel,),
        confidence=1.0,
        strategy="synthetic",
        metrics={"boundary_band_px": 8},
    )


def _component_boxes(source_size=320, target_height=160):
    source = np.array(
        [[40, 40], [40 + source_size, 40], [40 + source_size, 40 + source_size],
         [40, 40 + source_size]],
        np.float32,
    )
    target = np.array(
        [[70, 80], [170, 80], [170, 80 + target_height], [70, 80 + target_height]],
        np.float32,
    )
    return source, target


def _measure_horizontal_period(img_bgr, quad):
    x0, y0 = np.floor(quad.min(axis=0)).astype(int)
    x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
    crop = img_bgr[y0 + 18:y1 - 18, x0 + 18:x1 - 18]
    lab = bgr_to_lab(crop)
    det = _detrended_profile(lab[..., 0], axis=1)
    return _autocorr_period(det).period_px


def test_component_decal_is_resampled_to_the_shared_torso_period():
    """Mutation: direct source→component warp keeps a 10px period; corrected path returns 20px."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source(period=20, size=400)
    source_box, target_box = _component_boxes(source_size=320, target_height=160)
    target_period = 20.0

    direct = cv2.warpPerspective(
        source_bgr,
        cv2.getPerspectiveTransform(source_box, target_box),
        (carrier.shape[1], carrier.shape[0]),
        flags=cv2.INTER_LINEAR,
    )
    direct_period = _measure_horizontal_period(direct, target_box)
    assert direct_period == pytest.approx(10.0, abs=1.5)

    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=target_period,
        target_axis="horizontal",
        component_boxes={"collar": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"collar": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art

    corrected_period = _measure_horizontal_period(art.image_bgr, target_box)
    assert abs(corrected_period - target_period) < abs(direct_period - target_period)
    assert corrected_period == pytest.approx(target_period, rel=0.12)

    cross = art.metrics["cross_surface_scale"]
    assert panel_map.metrics["cross_surface_scale"] == cross
    collar = cross["components"]["collar"]
    assert collar["source_projected_period_px"] == pytest.approx(10.0, abs=1.5)
    assert collar["scale_resample_factor"] == pytest.approx(0.5, abs=0.08)
    assert "final_period_px" not in collar  # planned scale is not final-output evidence
    assert "phase_delta_period_frac" in collar
    assert "component_chroma_delta_ab" in collar

    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=target_period,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics=art.metrics["cross_surface_scale"],
        component_boxes={"collar": target_box.tolist()},
    )
    assert "cross_surface_scale" in qc.metrics
    assert "pattern_metric_failed" not in qc.failures
    measured = qc.metrics["cross_surface_scale"]["components"]["collar"]
    assert measured["final_period_px"] == pytest.approx(target_period, rel=0.12)
    assert measured["period_rel_err"] <= 0.12
    assert measured["stripe_width_rel_err"] <= 0.15


def test_component_decal_already_at_shared_scale_records_healthy_metrics():
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source(period=20, size=400)
    source_box, target_box = _component_boxes(source_size=160, target_height=160)
    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        component_boxes={"placket": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"placket": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art

    placket = art.metrics["cross_surface_scale"]["components"]["placket"]
    assert placket["scale_measurable"] is True
    assert placket["source_projected_period_px"] == pytest.approx(20.0, abs=1.5)
    assert placket["scale_resample_factor"] == pytest.approx(1.0, abs=0.08)
    assert placket["component_texture_source"] == "source_component_homography"
    assert _measure_horizontal_period(art.image_bgr, target_box) == pytest.approx(20.0, rel=0.12)

    # Separately cut cloth need not share torso phase when the component is projected
    # exactly once. It must still prove common period and stripe-run widths in final pixels.
    planned = {**art.metrics["cross_surface_scale"], "components": {
        "placket": {**placket, "phase_error_p95": 0.49},
    }}
    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics=planned,
        component_region_masks=art.component_region_masks,
        component_boxes={"placket": target_box.tolist()},
    )
    measured = qc.metrics["cross_surface_scale"]["components"]["placket"]
    assert measured["phase_policy"] == "not_applicable_single_projection"
    assert measured["period_measurement"] == "phase_guided_final_pixels_homography_hint"
    assert measured["period_rel_err"] <= 0.12
    assert not any(
        f.get("panel") == "placket" for f in qc.metrics["failure_details"]
    ), qc.metrics["failure_details"]


def test_component_scale_normalization_never_tiles_a_nonperiodic_label():
    """원단 주기를 맞춘다고 source 라벨/단추 좌표를 반복 복제하면 안 된다."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source(period=20, size=400)
    source_box, target_box = _component_boxes(source_size=320, target_height=160)
    # Source label and its direct-homography target location.  The old raw-coordinate
    # period wrap repeated this red block all along the component.
    source_bgr[145:205, 135:235] = (20, 30, 220)
    carrier[132:162, 100:132] = (20, 30, 220)

    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        component_boxes={"collar": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"collar": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art

    red = ((art.image_bgr[..., 2].astype(np.int16)
            - art.image_bgr[..., 1].astype(np.int16)) > 70)
    label = np.zeros(red.shape, bool)
    label[132:162, 100:132] = True
    component = np.zeros(red.shape, np.uint8)
    cv2.fillPoly(component, [target_box.astype(np.int32)], 255)
    assert float(red[label].mean()) >= 0.75
    assert int((red & (component > 0) & ~label).sum()) < 40


def test_multi_orientation_component_is_verified_region_by_region():
    """칼라 잎처럼 축이 다른 영역을 합쳐 한 축으로 재면 정상 합성도 측정 불가가 된다."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source_at_angle(period=20, size=400, normal=(1.0, 0.0))
    source_bgr[:, 200:] = _stripe_source_at_angle(
        period=20, size=400, normal=(0.0, 1.0))[:, 200:]
    source_box = np.array(
        [[40, 40], [360, 40], [360, 360], [40, 360]], np.float32)
    target_box = np.array(
        [[52, 70], [188, 70], [188, 245], [52, 245]], np.float32)

    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        component_boxes={"collar": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"collar": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art
    planned = art.metrics["cross_surface_scale"]["components"]["collar"]
    assert len(planned["region_metrics"]) >= 2, planned
    assert len(art.component_region_masks["collar"]) == len(planned["region_metrics"])

    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics=art.metrics["cross_surface_scale"],
        component_region_masks=art.component_region_masks,
        component_boxes={"collar": target_box.tolist()},
    )
    measured = qc.metrics["cross_surface_scale"]["components"]["collar"]
    assert measured["scale_measurable"] is True, measured
    assert all(r["scale_measurable"] for r in measured["region_metrics"]), measured
    assert not any(
        f.get("panel") == "collar" for f in qc.metrics["failure_details"]
    ), qc.metrics["failure_details"]


def test_final_pixel_period_fit_handles_fine_multicolor_irregular_component():
    """10.7px 다색 반복을 정수축으로 접어 9/12px harmonic으로 오독하지 않는다."""
    model = _fine_multicolor_model()
    target = 10.67
    out, mask, unit = _render_irregular_component(model, period=target)
    quad = np.asarray([[10, 10], [309, 10], [309, 309], [10, 309]], np.float32)

    measured = _component_output_scale(
        out, quad, model,
        target_period_px=target,
        target_axis="vertical",
        target_axis_unit=unit,
        painted_mask=mask,
        alpha=(mask > 0).astype(np.float32),
        evidence_mask=mask,
    )

    assert measured["scale_measurable"] is True, measured
    assert measured["period_measurement"] == "phase_guided_final_pixels"
    assert measured["period_rel_err"] <= 0.03, measured


def test_final_pixel_period_fit_does_not_bless_a_wrong_encoded_period():
    model = _fine_multicolor_model()
    target = 10.67
    out, mask, unit = _render_irregular_component(model, period=8.5)
    quad = np.asarray([[10, 10], [309, 10], [309, 309], [10, 309]], np.float32)

    measured = _component_output_scale(
        out, quad, model,
        target_period_px=target,
        target_axis="vertical",
        target_axis_unit=unit,
        painted_mask=mask,
        alpha=(mask > 0).astype(np.float32),
        evidence_mask=mask,
    )

    assert measured["scale_measurable"] is True, measured
    assert measured["period_rel_err"] > 0.12, measured


def test_component_phase_mapping_is_anchored_and_qc_gated():
    """주기만 맞고 위상이 반 주기 밀린 decal은 다른 원단 조각처럼 보여야 한다."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = np.roll(_stripe_source(period=20, size=400), 9, axis=0)
    source_box, target_box = _component_boxes(source_size=320, target_height=160)
    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        component_boxes={"collar": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"collar": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art
    planned = art.metrics["cross_surface_scale"]["components"]["collar"]
    assert planned["phase_error_p95"] <= 0.12, planned

    bad = {**art.metrics["cross_surface_scale"], "components": {
        "collar": {**planned, "phase_error_p95": 0.45},
    }}
    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics=bad,
        component_boxes={"collar": target_box.tolist()},
    )
    assert "pattern_metric_failed" in qc.failures
    assert any("phase" in f["detail"] for f in qc.metrics["failure_details"])


def test_deterministic_qc_fails_closed_when_component_scale_is_unmeasurable():
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
    )
    assert not isinstance(art, CompositeFailure), art

    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics={
            "target_period_px": 20.0,
            "target_axis": "horizontal",
            "components": {
                "cuff": {
                    "scale_measurable": False,
                    "reason": "period_unmeasurable",
                },
            },
        },
    )

    assert not qc.passed
    assert "pattern_metric_failed" in qc.failures
    assert any(
        "cuff component stripe scale unmeasurable" in f["detail"]
        for f in qc.metrics["failure_details"]
    )


@pytest.mark.parametrize("source_normal", [(1.0, 0.0), (1.0, 1.0)])
def test_component_uses_its_own_stripe_axis_but_the_shared_physical_period(source_normal):
    """Cuffs may rotate 90° and collars may be bias-cut; neither inherits torso angle."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source_at_angle(
        period=20, size=400, normal=source_normal)
    source_box, target_box = _component_boxes(source_size=320, target_height=160)
    art = composite_stripe(
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        component_boxes={"cuff": target_box.tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"cuff": source_box.tolist()},
    )
    assert not isinstance(art, CompositeFailure), art
    planned = art.metrics["cross_surface_scale"]["components"]["cuff"]
    source_axis = np.abs(np.asarray(planned["source_pattern_axis_unit"], np.float64))
    if source_normal == (1.0, 0.0):
        assert source_axis[0] > 0.9 and source_axis[1] < 0.15
    else:
        assert source_axis.min() > 0.45

    qc = verify_composite(
        art.image_bgr,
        carrier,
        panel_map,
        model,
        target_period_px=20.0,
        target_axis="horizontal",
        painted_mask=art.painted,
        coverage_mask=art.coverage_scope,
        alpha=art.alpha,
        component_scale_metrics=art.metrics["cross_surface_scale"],
        component_boxes={"cuff": target_box.tolist()},
    )
    measured = qc.metrics["cross_surface_scale"]["components"]["cuff"]
    assert measured["period_rel_err"] <= 0.12
    assert measured["stripe_width_error_px"] <= 3.0
    assert not any(
        f.get("panel") == "cuff" for f in qc.metrics["failure_details"]
    )


# ── 부위 사상 유효성 — torso panel 과 **같은 두 가지**를 본다 ──────────────
def _composite_with_component(source_box, target_box):
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    source_bgr = _stripe_source(period=20, size=400)
    return composite_stripe(
        carrier, panel_map, model,
        target_period_px=20.0, target_axis="horizontal",
        component_boxes={"collar": np.asarray(target_box, np.float32).tolist()},
        source_bgr=source_bgr,
        source_component_boxes={"collar": np.asarray(source_box, np.float32).tolist()},
    )


def _composite_without_component():
    """부위 없이 같은 장면 — '부위가 아무것도 더하지 않았다'의 기준선."""
    model = _stripe_model(period=20)
    carrier, panel_map = _carrier_panel_map()
    return composite_stripe(carrier, panel_map, model,
                            target_period_px=20.0, target_axis="horizontal")


def _assert_component_added_nothing(art):
    """상자 기하로 어림하지 않는다 — 기준선 대비 **더해진 픽셀이 없는지**로 본다.

    가드는 부위 상자 안의 몸통 칠까지 걷어내므로 두 마스크가 같지는 않다(실측: 기준선이
    3,059 px 더 많다). 확인해야 하는 것은 반대 방향이다 — 부위가 **더한** 픽셀이 0.
    """
    base = _composite_without_component()
    assert not isinstance(base, CompositeFailure), base
    added = (art.painted > 0) & ~(base.painted > 0)
    assert int(added.sum()) == 0, int(added.sum())


def test_an_overstretched_component_mapping_is_not_painted():
    """torso panel 은 반전 **과 과신장** 둘 다 거부한다 — 부위도 같아야 한다.

    실측: stretch_over_frac 1.0(한도 0.01), 이방성 8.89 인 부위가 3,059 px 전부
    그려지고 하류 QC 까지 통과했다.
    """
    art = _composite_with_component(
        [[40, 40], [360, 40], [360, 360], [40, 360]],
        [[100, 80], [118, 80], [118, 240], [100, 240]])
    assert not isinstance(art, CompositeFailure), art
    cross = art.metrics["cross_surface_scale"]
    collar = cross["components"]["collar"]
    assert collar["reason"] == "component_mapping_overstretched", collar
    assert collar["scale_measurable"] is False, collar
    # 이름값을 하려면 **실제로 안 그려졌는지** 봐야 한다 — 사유만 보면 칠이 남아도 통과한다.
    _assert_component_added_nothing(art)


def test_a_reversed_component_mapping_is_not_painted():
    """지평선을 넘는 사상은 그리면 안 된다.

    실측: 가드를 지우면 8,163 px 중 6,073 px 이 그려지고 coverage 0.744 로 QC 통과.
    부호 클램프가 있던 시절에는 좌표 폭발로 **우연히** 걸러지던 경우다.
    """
    art = _composite_with_component(
        [[40, 40], [360, 40], [360, 360], [40, 360]],
        [[70, 80], [170, 80], [170, 79.75], [70, 240]])
    assert not isinstance(art, CompositeFailure), art
    collar = art.metrics["cross_surface_scale"]["components"]["collar"]
    assert collar["reason"] == "component_mapping_reversed", collar
    assert collar["scale_measurable"] is False, collar
    _assert_component_added_nothing(art)


def test_the_homogeneous_divide_preserves_negative_w():
    """부호 보존을 **한 곳에서** 못 박는다 — 두 호출부가 같은 헬퍼를 쓴다.

    `np.maximum(w, eps)` 로 클램프하면 w<0 이 1e-9 가 되어 좌표가 1e9 규모로 폭발하고,
    전 영역 w<0 인 정상 부위가 coverage 0 으로 잘못 보류된다(실측: 51,720 px → 0 px).
    """
    from app.services.hybrid_composite.warp_composite import homogeneous_divide
    num = np.array([-3.0, 6.0, 1.5])
    w = np.array([-1.5752619, -0.3077566, -1.0])
    got = homogeneous_divide(num, w)
    assert np.allclose(got, num / w), got
    assert (np.abs(got) < 1e3).all(), got            # 폭발하지 않는다
    # 0 근처는 크기만 막고 **부호는 살린다** — `-1e-10` 이 `+1e-9` 가 되면 좌표 부호가
    # 뒤집힌다(실측: num=1, w=-1e-10 → +1e9).
    assert np.isfinite(homogeneous_divide(np.array([1.0]), np.array([0.0]))).all()
    tiny_neg = homogeneous_divide(np.array([1.0]), np.array([-1e-10]))
    assert tiny_neg[0] < 0, tiny_neg
    tiny_pos = homogeneous_divide(np.array([1.0]), np.array([1e-10]))
    assert tiny_pos[0] > 0, tiny_pos


def test_an_unmeasurable_component_is_not_left_painted():
    """측정 불가로 표시된 부위가 그려진 채 나가면 안 된다 — 이웃 분기들은 전부 걷어낸다.

    실측: `target_mask_insufficient` 분기만 칠을 남겨 961 px 이 carrier 와 다른 채였다.
    """
    art = _composite_with_component(
        [[40, 40], [360, 40], [360, 360], [40, 360]],
        [[100, 100], [130, 100], [130, 130], [100, 130]])
    assert not isinstance(art, CompositeFailure), art
    collar = art.metrics["cross_surface_scale"]["components"]["collar"]
    assert collar["reason"] == "target_mask_insufficient", collar
    box = np.zeros(art.painted.shape, np.uint8)
    cv2.fillPoly(box, [np.int32([[100, 100], [130, 100], [130, 130], [100, 130]])], 255)
    assert int(((art.painted > 0) & (box > 0)).sum()) == 0


def test_the_validity_sample_approximates_the_exhaustive_measurement():
    """한도를 재는 **눈금**이 거칠면 한도 자체가 부정확하게 적용된다.

    실측: 실제 초과 비율 0.0060(한도 0.01 미만)인 정상 사상이 12x12 표본에서는
    0.0139 로 나와 거부됐다. 한도를 바꾸는 것이 아니라 표본을 촘촘히 한 것이므로,
    시험도 특정 숫자가 아니라 **표본이 전수와 가까운가**를 본다.
    """
    from app.services.hybrid_composite.warp_composite import (
        _homography_validity, _quad_area, MAX_LOCAL_STRETCH)
    sq = np.float32([[40, 40], [140, 40], [140, 140], [40, 140]])
    tq = np.float32([[110.325966, 46.164871], [186.413696, 167.042221],
                     [43.029358, 158.785568], [51.884869, 77.469078]])
    H = cv2.getPerspectiveTransform(sq, tq)

    # 전수: quad 안의 모든 정수 픽셀에서 국소 Jacobian 의 이방성을 잰다.
    x0, y0 = int(sq[:, 0].min()), int(sq[:, 1].min())
    x1, y1 = int(sq[:, 0].max()), int(sq[:, 1].max())
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
    pts = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)], axis=0).astype(np.float64)
    a, b, c = H[0], H[1], H[2]
    den = c @ pts
    j = np.stack([
        (a[0] * den - (a @ pts) * c[0]) / den ** 2,
        (a[1] * den - (a @ pts) * c[1]) / den ** 2,
        (b[0] * den - (b @ pts) * c[0]) / den ** 2,
        (b[1] * den - (b @ pts) * c[1]) / den ** 2,
    ])
    sx = np.hypot(j[0], j[2])
    sy = np.hypot(j[1], j[3])
    exhaustive = float((np.maximum(sx, sy) / np.maximum(np.minimum(sx, sy), 1e-9)
                        > MAX_LOCAL_STRETCH).mean())

    bw = int(sq[:, 0].max() - sq[:, 0].min()) + 1
    bh = int(sq[:, 1].max() - sq[:, 1].min()) + 1
    v = _homography_validity(H, bw, bh, _quad_area(sq),
                             origin=(float(sq[:, 0].min()), float(sq[:, 1].min())),
                             quad=sq)
    # 표본이 전수를 대표해야 한다 — 거친 표본은 두 배 넘게 빗나갔다(0.0139 대 0.0060).
    assert abs(v["stretch_over_frac"] - exhaustive) < 0.005, (v, exhaustive)
