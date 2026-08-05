import cv2
import numpy as np
import pytest

from app.services.hybrid_composite.color import bgr_to_lab
from app.services.hybrid_composite.deterministic_qc import verify_composite
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
    assert _measure_horizontal_period(art.image_bgr, target_box) == pytest.approx(20.0, rel=0.12)


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
