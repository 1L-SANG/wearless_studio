"""The cape ratio must divide by a shoulder, not by a collar.

A real rejected carrier (2026-08-07 QA run, striped shirt, 4K, three-quarter
mannequin pose) was measured with a shoulder span of 0.1415 against a collar span
of 0.131 — the "shoulder seam" points had landed on the collar corners. Dividing
the hem by a neck produced hem/shoulder = 1.4276 and a cape rejection for a
tailored shirt whose hem is visibly NARROWER than its shoulders.

The fix is to the denominator's provenance. `MAX_CAPE_HEM_TO_SHOULDER` stays at
1.35 — widening the limit to admit one false positive would blind the gate to
real capes, which is the failure the gate exists to catch.

The carrier landmark numbers below are frozen from that run: they are the merged
output of the two production Vision calls, so the fixture reproduces the exact
rejection that motivated this change.
"""
from __future__ import annotations

import inspect

import pytest

from app.services.hybrid_composite import carrier_preflight as cp

# ---------------------------------------------------------------- the fixture
# merged (mean of the two Vision calls) carrier landmarks, verbatim
QA_CARRIER_LANDMARKS = {
    "shoulder_l": [0.4205, 0.207],
    "shoulder_r": [0.562, 0.194],
    "hem_l": [0.38, 0.472],
    "hem_r": [0.582, 0.46],
    "sleeve_l_end": [0.3615, 0.4915],
    "sleeve_r_end": [0.603, 0.5005555555555555],
    "confidence": 0.95,
}
QA_CARRIER_INVENTORY = {
    "collar": True, "placket": True, "cuffs": True, "visible_buttons": 6,
    "torso_aspect": 2.304, "sleeve_len_ratio": 1.121,
    "garment_categories": ["top"],
    "component_boxes": {
        "collar_box": [[0.43, 0.162], [0.561, 0.16], [0.551, 0.222], [0.43, 0.224]],
    },
}
OLD_SHOULDER_WIDTH = 0.1415
OLD_HEM_WIDTH = 0.202
OLD_RATIO = 1.4276
COLLAR_SPAN = 0.131
ARMHOLE_SPAN = 0.2415


def _geo(landmarks=None, inventory=None):
    metrics, _ = cp._geometry_metrics(landmarks or QA_CARRIER_LANDMARKS,
                                      inventory if inventory is not None else QA_CARRIER_INVENTORY)
    return metrics


def _cape_codes(geometry, inventory=None, carrier=None, vision=None):
    return [r.code for r in cp._silhouette_reasons(
        carrier or {}, vision or {}, geometry, inventory or QA_CARRIER_INVENTORY)]


# ------------------------------------------------- 1-3: the old reading stands

def test_1_the_old_shoulder_width_is_reproduced_from_the_frozen_fixture():
    """Without the collar box there is nothing to detect the contamination with."""
    assert _geo(inventory={})["shoulderWidth"] == pytest.approx(OLD_SHOULDER_WIDTH)
    # and the raw reading is still reported after the fix
    assert _geo()["shoulderWidthReported"] == pytest.approx(OLD_SHOULDER_WIDTH)


def test_2_the_old_hem_width_is_reproduced():
    assert _geo(inventory={})["hemWidth"] == pytest.approx(OLD_HEM_WIDTH)


def test_3_the_old_ratio_is_reproduced_and_would_still_reject():
    old = _geo(inventory={})
    assert old["hemToShoulderRatio"] == pytest.approx(OLD_RATIO)
    assert old["hemToShoulderRatio"] > cp.MAX_CAPE_HEM_TO_SHOULDER
    assert "carrier_silhouette_cape" in _cape_codes(old)
    # the corrected record keeps the old ratio so the rejection stays auditable
    assert _geo()["hemToShoulderRatioReported"] == pytest.approx(OLD_RATIO)


# --------------------------------------------- 4: the denominator's provenance

def test_4_the_new_shoulder_comes_from_armhole_geometry_not_the_collar():
    geo = _geo()
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_ARMHOLE
    assert geo["shoulderWidth"] == pytest.approx(ARMHOLE_SPAN)
    assert geo["collarSpan"] == pytest.approx(COLLAR_SPAN)
    assert geo["armholeSpan"] == pytest.approx(ARMHOLE_SPAN)
    # the collar is what the old reading had collapsed onto
    assert geo["shoulderWidthReported"] == pytest.approx(OLD_SHOULDER_WIDTH)
    assert geo["shoulderWidthReported"] / geo["collarSpan"] < cp.SHOULDER_COLLAR_MIN_RATIO
    assert geo["shoulderWidth"] > geo["collarSpan"]


def test_4_the_correction_is_recorded_not_silent():
    for key in ("shoulderWidthSource", "shoulderWidthReported",
                "hemToShoulderRatioReported", "collarSpan", "armholeSpan"):
        assert key in _geo(), key


# --------------------------------------------------- 5: the false positive dies

def test_5_the_same_carrier_now_measures_under_the_limit():
    geo = _geo()
    assert geo["hemToShoulderRatio"] == pytest.approx(0.8364, abs=1e-4)
    assert geo["hemToShoulderRatio"] < cp.MAX_CAPE_HEM_TO_SHOULDER
    assert "carrier_silhouette_cape" not in _cape_codes(geo)


def test_5_the_full_preflight_no_longer_rejects_this_carrier_as_a_cape():
    result = cp.preflight_carrier_quality(
        carrier_evidence={"garment_categories": ["top"]},
        canonical_evidence={"expected_categories": ["top"], "expected_lower": True},
        matching_evidence={"matched": True},
        landmarks=QA_CARRIER_LANDMARKS,
        carrier_inventory=QA_CARRIER_INVENTORY,
        canonical_inventory={"collar": True, "placket": True, "cuffs": True,
                             "visible_buttons": 6, "torso_aspect": 1.254,
                             "sleeve_len_ratio": 1.565},
        vision_observations={"shirtSilhouette": "shirt"},
        require_vision=False, matching_expected=False)
    assert "carrier_silhouette_cape" not in [r.code for r in result.reasons]


# ------------------------------------------------------- 6: the hem is untouched

def test_6_the_hem_measurement_is_identical_before_and_after():
    assert _geo(inventory={})["hemWidth"] == _geo()["hemWidth"] == pytest.approx(OLD_HEM_WIDTH)
    assert _geo(inventory={})["hemY"] == _geo()["hemY"]


def test_6_only_the_shoulder_side_of_the_expression_moved():
    src = inspect.getsource(cp._geometry_metrics)
    assert "hem_width = hr[0] - hl[0]" in src
    # the correction reassigns the denominator and nothing else
    assert "hem_width =" not in src.split("reported_shoulder_width", 1)[1]


# ------------------------------------------------------ 7: Vision path unchanged

def test_7_a_vision_cape_label_still_rejects_regardless_of_geometry():
    codes = _cape_codes(_geo(), carrier={"silhouette": "cape"})
    assert "carrier_silhouette_cape" in codes
    detail = [r for r in cp._silhouette_reasons(
        {"silhouette": "cape"}, {}, _geo(), QA_CARRIER_INVENTORY)][0]
    assert detail.origin == cp.ORIGIN_VISION_LABEL


def test_7_the_label_sets_are_untouched():
    assert cp.CAPE_LABELS == frozenset({"cape", "poncho", "tent"})
    assert cp.SLAB_LABELS == frozenset(
        {"slab", "slab_torso", "rectangular_torso", "flat_panel"})


# --------------------------------------------------------- 8: slab gate unchanged

def test_8_side_edge_delta_still_comes_from_the_reported_shoulder_points():
    """The slab check owns sideEdgeDelta and is out of scope for this fix."""
    lm = QA_CARRIER_LANDMARKS
    expected = abs((lm["hem_l"][0] - lm["shoulder_l"][0])
                   - (lm["shoulder_r"][0] - lm["hem_r"][0]))
    assert _geo()["sideEdgeDelta"] == pytest.approx(round(expected, 4))
    assert _geo(inventory={})["sideEdgeDelta"] == _geo()["sideEdgeDelta"]


def test_8_the_slab_gate_fires_exactly_where_it_did_before():
    geo_corrected = _geo()
    for aspect, expected in ((2.39, []), (2.41, ["carrier_silhouette_slab_torso"])):
        inv = {**QA_CARRIER_INVENTORY, "torso_aspect": aspect}
        codes = [r.code for r in cp._silhouette_reasons(
            {}, {}, {**geo_corrected, "sideEdgeDelta": 0.01}, inv)]
        assert codes == expected, aspect


# --------------------------------------- 9: a genuine cape must still be caught

def test_9_the_existing_cape_geometry_fixture_still_rejects():
    """No collar box, no sleeves — nothing to correct with, so the gate holds."""
    cape = {"shoulder_l": [0.32, 0.24], "shoulder_r": [0.68, 0.24],
            "hem_l": [0.20, 0.72], "hem_r": [0.80, 0.72], "confidence": 0.86}
    geo = _geo(landmarks=cape, inventory={})
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS
    assert geo["hemToShoulderRatio"] == pytest.approx(1.6667, abs=1e-4)
    assert "carrier_silhouette_cape" in _cape_codes(geo, inventory={})


def test_9_a_real_cape_with_a_collar_and_drape_is_not_rescued():
    """A cape's edge falls away from the body, so it is not an arms-down sleeve."""
    cape = {"shoulder_l": [0.34, 0.20], "shoulder_r": [0.46, 0.20],
            "hem_l": [0.20, 0.75], "hem_r": [0.80, 0.75],
            # drape edges drift far sideways relative to their drop
            "sleeve_l_end": [0.10, 0.40], "sleeve_r_end": [0.90, 0.40],
            "confidence": 0.9}
    inv = {"component_boxes": {"collar_box": [[0.36, 0.14], [0.45, 0.14],
                                              [0.45, 0.20], [0.36, 0.20]]}}
    geo = _geo(landmarks=cape, inventory=inv)
    assert geo.get("armholeSpan") is None, "a drape must not count as an armhole"
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS
    assert geo["hemToShoulderRatio"] > cp.MAX_CAPE_HEM_TO_SHOULDER
    assert "carrier_silhouette_cape" in _cape_codes(geo, inventory=inv)


def test_9_the_correction_can_only_widen_never_narrow_the_denominator():
    """A narrower armhole span must never be used to manufacture a cape."""
    lm = {**QA_CARRIER_LANDMARKS,
          "sleeve_l_end": [0.45, 0.49], "sleeve_r_end": [0.53, 0.50]}
    geo = _geo(landmarks=lm)
    assert geo["shoulderWidth"] == pytest.approx(OLD_SHOULDER_WIDTH)
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS


# ---------------------------------------------- 10: normal garments still pass

def test_10_the_baseline_non_cape_fixture_still_passes():
    ok = {"shoulder_l": [0.32, 0.24], "shoulder_r": [0.68, 0.24],
          "hem_l": [0.34, 0.72], "hem_r": [0.66, 0.72], "confidence": 0.86}
    geo = _geo(landmarks=ok, inventory={})
    assert geo["hemToShoulderRatio"] == pytest.approx(0.8889, abs=1e-4)
    assert _cape_codes(geo, inventory={}) == []


def test_10_a_correctly_measured_shoulder_is_left_alone():
    """Shoulder comfortably wider than the collar — nothing to fix."""
    lm = {**QA_CARRIER_LANDMARKS, "shoulder_l": [0.36, 0.207], "shoulder_r": [0.62, 0.194]}
    geo = _geo(landmarks=lm)
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS
    assert geo["shoulderWidth"] == pytest.approx(0.26)


def test_10_a_flared_tunic_is_still_caught_when_its_shoulder_is_measured_right():
    """The correction must not become a blanket amnesty for wide hems."""
    lm = {"shoulder_l": [0.40, 0.20], "shoulder_r": [0.60, 0.20],
          "hem_l": [0.30, 0.70], "hem_r": [0.70, 0.70],
          "sleeve_l_end": [0.38, 0.55], "sleeve_r_end": [0.62, 0.56], "confidence": 0.9}
    inv = {"component_boxes": {"collar_box": [[0.46, 0.14], [0.54, 0.14],
                                              [0.54, 0.20], [0.46, 0.20]]}}
    geo = _geo(landmarks=lm, inventory=inv)
    # shoulder 0.20 vs collar 0.08 — believable, so no correction
    assert geo["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS
    assert geo["hemToShoulderRatio"] == pytest.approx(2.0)
    assert "carrier_silhouette_cape" in _cape_codes(geo, inventory=inv)


# ------------------------------------------------------- 11: three-quarter pose

def test_11_the_fixture_is_the_three_quarter_pose_that_produced_the_false_positive():
    """The pose is why it happened: a turned body foreshortens the shoulder span."""
    lm = QA_CARRIER_LANDMARKS
    # the two shoulders sit at different heights — the body is turned, not square on
    assert lm["shoulder_l"][1] != lm["shoulder_r"][1]
    assert abs(lm["shoulder_l"][1] - lm["shoulder_r"][1]) >= 0.01
    assert lm["hem_l"][1] != lm["hem_r"][1]
    # and it is exactly the carrier that was rejected
    assert _geo(inventory={})["hemToShoulderRatio"] == pytest.approx(OLD_RATIO)
    assert _geo()["hemToShoulderRatio"] < cp.MAX_CAPE_HEM_TO_SHOULDER


# ------------------------------------------------------- 12: the limit did not move

def test_12_the_cape_threshold_is_untouched():
    assert cp.MAX_CAPE_HEM_TO_SHOULDER == 1.35
    assert cp.MAX_SLAB_EDGE_RATIO == 0.08
    assert cp.MIN_SLAB_TORSO_ASPECT == 2.4
    assert cp.MIN_GEOMETRY_CONFIDENCE == 0.62


def test_12_the_threshold_is_still_read_not_restated():
    src = inspect.getsource(cp._silhouette_reasons)
    assert "MAX_CAPE_HEM_TO_SHOULDER" in src and "1.35" not in src


def test_12_the_guards_are_provenance_checks_not_a_second_cape_limit():
    """They decide whether a measurement is believable, never whether it passes."""
    src = inspect.getsource(cp._geometry_metrics)
    guard = src.split("reported_shoulder_width = shoulder_width", 1)[1].split("edge_delta", 1)[0]
    assert "MAX_CAPE_HEM_TO_SHOULDER" not in guard
    assert "hem_width" not in guard, "the hem must not enter the provenance decision"


# --------------------------------------------------- correction preconditions

@pytest.mark.parametrize("drop_key", ["sleeve_l_end", "sleeve_r_end"])
def test_a_missing_sleeve_blocks_the_correction(drop_key):
    lm = {k: v for k, v in QA_CARRIER_LANDMARKS.items() if k != drop_key}
    assert _geo(landmarks=lm)["shoulderWidthSource"] == cp.SHOULDER_FROM_VISION_POINTS


def test_a_missing_collar_box_blocks_the_correction():
    assert _geo(inventory={"component_boxes": {}})["shoulderWidthSource"] \
        == cp.SHOULDER_FROM_VISION_POINTS


def test_a_sleeve_pointing_upward_is_not_an_armhole():
    lm = {**QA_CARRIER_LANDMARKS,
          "sleeve_l_end": [0.3615, 0.10], "sleeve_r_end": [0.603, 0.10]}
    assert _armhole(lm) is None


def _armhole(lm):
    return cp._armhole_span(lm)


def test_a_the_armhole_span_of_the_frozen_fixture():
    assert _armhole(QA_CARRIER_LANDMARKS) == pytest.approx(ARMHOLE_SPAN)


def test_a_degenerate_geometry_still_fails_before_any_correction():
    lm = {"shoulder_l": [0.50, 0.20], "shoulder_r": [0.505, 0.20],
          "hem_l": [0.38, 0.47], "hem_r": [0.58, 0.46], "confidence": 0.9}
    metrics, reason = cp._geometry_metrics(lm, QA_CARRIER_INVENTORY)
    assert reason is not None and reason.code == "geometry_unmeasurable"
    assert "shoulderWidthSource" not in metrics
