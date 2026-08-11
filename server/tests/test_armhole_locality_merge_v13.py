"""Tests for the offline armhole locality merge primitive (v13).

The primitive exists so that a constrained armhole result can never destroy the
lower sleeve and cuff that live outside the armhole ROI. Tests A-H pin that
contract on synthetic masks; test I replays the real canonical artifacts and
compares against the v11 Variant B baseline.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.armhole_locality_merge_v13 import (
    ANCHOR_REVIEW_RADIUS_PX,
    BBOX_SEMANTICS,
    REASON_LOST_LOCAL_POSITIVE_PROMPT,
    REASON_REJECT_LOCAL_ANCHOR,
    REASON_REVIEW_LOCAL_ANCHOR,
    SELECTED_CONSTRAINED,
    SELECTED_FALLBACK,
    merge_sleeve_inside_roi,
    roi_contract,
    roi_mask_from_xyxy,
    select_local_armhole_candidate,
)

ARTIFACT_ROOT = (Path(__file__).resolve().parents[1]
                 / "ab_out/frame_lock/stripe-projection-protected-v1/artifacts")


def _fixture(h=40, w=30):
    """A tall sleeve with a cuff at the bottom, an ROI band across the top."""
    garment = np.zeros((h, w), bool)
    garment[2:h - 2, 2:w - 2] = True

    pre = np.zeros((h, w), bool)
    pre[4:h - 4, 5:15] = True          # sleeve body, spans ROI and far below it

    roi = np.zeros((h, w), bool)
    roi[4:14, 4:20] = True             # armhole ROI band near the top

    constrained = np.zeros((h, w), bool)
    constrained[6:12, 8:12] = True     # small shoulder-cap style result
    return pre, constrained, roi, garment


# --------------------------------------------------------------- Test A / B

def test_a_outside_roi_is_bitwise_preserved():
    pre, con, roi, gar = _fixture()
    out = merge_sleeve_inside_roi(pre, con, roi, gar)
    assert np.array_equal(out & ~roi, pre & ~roi & gar)


def test_a_outside_roi_preserved_for_a_mirrored_side():
    pre, con, roi, gar = _fixture()
    pre, con, roi, gar = (np.fliplr(m).copy() for m in (pre, con, roi, gar))
    out = merge_sleeve_inside_roi(pre, con, roi, gar)
    assert np.array_equal(out & ~roi, pre & ~roi & gar)


def test_b_inside_roi_uses_the_constrained_result():
    pre, con, roi, gar = _fixture()
    out = merge_sleeve_inside_roi(pre, con, roi, gar)
    assert np.array_equal(out & roi, con & roi & gar)


# ------------------------------------------------------------------- Test C

@pytest.mark.parametrize("bad_index", [0, 1, 2, 3])
def test_c_shape_mismatch_raises(bad_index):
    masks = list(_fixture())
    masks[bad_index] = np.zeros((10, 10), bool)
    with pytest.raises(ValueError, match="shape mismatch|expected a 2D mask"):
        merge_sleeve_inside_roi(*masks)


def test_c_non_2d_input_raises():
    pre, con, roi, gar = _fixture()
    with pytest.raises(ValueError, match="expected a 2D mask"):
        merge_sleeve_inside_roi(pre[None, ...], con, roi, gar)


# ------------------------------------------------------------------- Test D

def test_d_zero_255_input_is_accepted_and_matches_bool():
    pre, con, roi, gar = _fixture()
    as_u8 = merge_sleeve_inside_roi(pre.astype(np.uint8) * 255, con.astype(np.uint8) * 255,
                                    roi.astype(np.uint8) * 255, gar.astype(np.uint8) * 255)
    assert np.array_equal(as_u8, merge_sleeve_inside_roi(pre, con, roi, gar))


def test_d_zero_one_input_is_accepted():
    pre, con, roi, gar = _fixture()
    out = merge_sleeve_inside_roi(pre.astype(np.uint8), con.astype(np.uint8),
                                  roi.astype(np.uint8), gar.astype(np.uint8))
    assert np.array_equal(out, merge_sleeve_inside_roi(pre, con, roi, gar))


def test_d_multiclass_input_fails_closed():
    pre, con, roi, gar = _fixture()
    multiclass = pre.astype(np.uint8) * 255
    multiclass[5, 6] = 7                      # a third label
    with pytest.raises(ValueError, match="not a binary mask"):
        merge_sleeve_inside_roi(multiclass, con, roi, gar)


def test_d_nan_input_fails_closed():
    pre, con, roi, gar = _fixture()
    floaty = pre.astype(np.float32)
    floaty[5, 6] = np.nan
    with pytest.raises(ValueError, match="contains NaN"):
        merge_sleeve_inside_roi(floaty, con, roi, gar)


# ------------------------------------------------------------------- Test E

def test_e_empty_constrained_roi_still_preserves_lower_sleeve_and_cuff():
    pre, _, roi, gar = _fixture()
    empty = np.zeros_like(pre)
    out = merge_sleeve_inside_roi(pre, empty, roi, gar)
    assert not (out & roi).any()                       # nothing survives inside the ROI
    assert np.array_equal(out & ~roi, pre & ~roi & gar)  # everything survives outside it
    below_roi = np.zeros_like(pre)
    below_roi[14:, :] = True
    assert int(np.count_nonzero((out & below_roi) > 0)) == int(np.count_nonzero((pre & below_roi & gar) > 0))


# ------------------------------------------------------------------- Test F

def test_f_constrained_leakage_outside_roi_is_ignored():
    pre, con, roi, gar = _fixture()
    leaky = con.copy()
    leaky[25:30, 20:24] = True                          # pixels far outside the ROI
    baseline = merge_sleeve_inside_roi(pre, con, roi, gar)
    out = merge_sleeve_inside_roi(pre, leaky, roi, gar)
    assert np.array_equal(out, baseline)


# ------------------------------------------------------------------- Test G

def test_g_pixels_outside_the_garment_never_survive():
    pre, con, roi, gar = _fixture()
    pre_leak = pre.copy()
    pre_leak[0, 0] = True                               # outside garment, outside ROI
    holed = gar.copy()
    holed[6, 8] = False                                 # a garment hole inside the ROI, under `con`
    assert con[6, 8] and roi[6, 8]
    out = merge_sleeve_inside_roi(pre_leak, con, roi, holed)
    assert int(np.count_nonzero((out & ~holed) > 0)) == 0
    assert not out[0, 0]                                # clipped on the outside-ROI branch
    assert not out[6, 8]                                # clipped on the inside-ROI branch


# ------------------------------------------------------------------- Test H

def test_h_left_right_conflict_is_reported_not_resolved():
    """The primitive is per-side; conflict detection is the caller's gate."""
    pre, con, roi, gar = _fixture()
    left = merge_sleeve_inside_roi(pre, con, roi, gar)
    right = merge_sleeve_inside_roi(pre, con, roi, gar)   # deliberately identical
    conflict = left & right
    assert int(np.count_nonzero(conflict > 0)) > 0
    # torso must not be derived while a conflict exists
    torso_would_be = gar & ~left & ~right
    assert int(np.count_nonzero(torso_would_be > 0)) < int(np.count_nonzero(gar > 0))


# ------------------------------------------------------- ROI reconstruction

def test_roi_mask_from_xyxy_is_inclusive_and_bounds_checked():
    m = roi_mask_from_xyxy([2, 3, 5, 7], (20, 20))
    assert int(np.count_nonzero(m > 0)) == (5 - 2 + 1) * (7 - 3 + 1)
    with pytest.raises(ValueError, match="degenerate"):
        roi_mask_from_xyxy([5, 3, 5, 7], (20, 20))
    with pytest.raises(ValueError, match="outside image bounds"):
        roi_mask_from_xyxy([2, 3, 25, 7], (20, 20))


# ------------------------------------------------------------------- Test I

_V9 = ARTIFACT_ROOT / "diagnostic_sam2_sleeve_first_residual_v9"
_V10 = ARTIFACT_ROOT / "diagnostic_armhole_boundary_split_v10"
_V11 = ARTIFACT_ROOT / "diagnostic_armhole_locality_v11"
_REQUIRED_FOR_REPLAY = [
    _V9 / "variant_a_sleeve_left_final.npy",
    _V9 / "variant_a_sleeve_right_final.npy",
    _V10 / "v10_sleeve_left_final.npy",
    _V10 / "v10_sleeve_right_final.npy",
    _V11 / "armhole_roi_definition.json",
    _V11 / "recomposition_metrics.json",
    ARTIFACT_ROOT / "garment_mask.png",
]

pytestmark_replay = pytest.mark.skipif(
    not all(p.exists() for p in _REQUIRED_FOR_REPLAY),
    reason="local-only canonical artifacts (server/ab_out is gitignored)")


@pytestmark_replay
@pytest.mark.parametrize("side,short", [("sleeve_left", "left"), ("sleeve_right", "right")])
def test_i_replay_matches_v11_variant_b(side, short):
    import cv2

    garment = cv2.imread(str(ARTIFACT_ROOT / "garment_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    pre = np.load(str(_V9 / f"variant_a_sleeve_{short}_final.npy")) > 0
    con = np.load(str(_V10 / f"v10_sleeve_{short}_final.npy")) > 0
    roi_def = json.loads((_V11 / "armhole_roi_definition.json").read_text())
    roi = roi_mask_from_xyxy(roi_def["sides"][side]["roi_xyxy"], garment.shape)
    assert int(np.count_nonzero(roi > 0)) == roi_def["sides"][side]["roi_pixels"]

    out = merge_sleeve_inside_roi(pre, con, roi, garment)

    # the two invariants, on real data
    assert np.array_equal(out & ~roi, pre & ~roi & garment)
    assert np.array_equal(out & roi, con & roi & garment)

    expected = json.loads((_V11 / "recomposition_metrics.json").read_text())["sleeves"]["variant_b"][side]
    assert int(np.count_nonzero(out > 0)) == expected["sleeve_pixel_count"]
    assert int(np.count_nonzero((out & ~garment) > 0)) == 0


@pytestmark_replay
def test_i_replay_has_no_left_right_conflict_and_matches_v11_torso():
    import cv2

    garment = cv2.imread(str(ARTIFACT_ROOT / "garment_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    roi_def = json.loads((_V11 / "armhole_roi_definition.json").read_text())
    finals = {}
    for side, short in (("sleeve_left", "left"), ("sleeve_right", "right")):
        pre = np.load(str(_V9 / f"variant_a_sleeve_{short}_final.npy")) > 0
        con = np.load(str(_V10 / f"v10_sleeve_{short}_final.npy")) > 0
        roi = roi_mask_from_xyxy(roi_def["sides"][side]["roi_xyxy"], garment.shape)
        finals[side] = merge_sleeve_inside_roi(pre, con, roi, garment)

    conflict = finals["sleeve_left"] & finals["sleeve_right"]
    assert int(np.count_nonzero(conflict > 0)) == 0

    torso = garment & ~finals["sleeve_left"] & ~finals["sleeve_right"]
    expected = json.loads((_V11 / "recomposition_metrics.json").read_text())["torso"]["variant_b"]
    assert int(np.count_nonzero(torso > 0)) == expected["torso_pixel_count"]


# =====================================================================
# v14 — local QC + safe fallback selector
#
# v13 proves a constrained result cannot hurt anything outside the ROI.
# These tests pin the missing half: when the constrained result damages
# canonical evidence INSIDE the ROI, that ROI must fall back to pre-stage.
# =====================================================================

def _selector_fixture():
    """Sleeve with an ROI band on top; one prompt inside the ROI, one below it."""
    pre, _con, roi, gar = _fixture()
    # anchors sit well clear of the ROI's bottom edge (row 13): otherwise pixels
    # preserved from pre-stage just below the ROI would satisfy the radius check.
    inside_roi_point = (6, 9)      # [x, y] — inside pre and inside roi
    outside_roi_point = (8, 30)    # [x, y] — inside pre, well below the roi
    shoulder = (13, 5)
    underarm = (13, 9)
    for x, y in (inside_roi_point, outside_roi_point, shoulder, underarm):
        assert pre[y, x], f"fixture broken: pre-stage missing ({x}, {y})"
    healthy = (pre & roi).copy()   # a constrained result that keeps all local evidence
    healthy[11:14, 13:15] = False  # trim a corner that holds no prompt or anchor
    return {"pre": pre, "roi": roi, "gar": gar, "healthy": healthy,
            "points": [inside_roi_point, outside_roi_point],
            "shoulder": shoulder, "underarm": underarm}


def _decide(fx, constrained, **kw):
    return select_local_armhole_candidate(
        fx["pre"], constrained, fx["roi"], fx["gar"], fx["points"],
        fx["shoulder"], fx["underarm"], side=kw.pop("side", "sleeve_left"), **kw)


def _punch(mask, xy, radius):
    out = mask.copy()
    h, w = mask.shape
    x, y = xy
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy * dy + dx * dx <= radius * radius:
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w:
                    out[yy, xx] = False
    return out


# ------------------------------------------------------- coordinate contract

def test_coordinate_contract_declares_inclusive_semantics_and_one_conversion():
    c = roi_contract([10, 20, 30, 40])
    assert c["bbox_semantics"] == BBOX_SEMANTICS == "inclusive_xyxy"
    assert c["slice_xyxy_half_open"] == [10, 20, 31, 41]
    m = roi_mask_from_xyxy([10, 20, 30, 40], (60, 60))
    assert int(np.count_nonzero(m > 0)) == (30 - 10 + 1) * (40 - 20 + 1)


# --------------------------------------------------------- Test B (v14 spec)

def test_v14_b_valid_constrained_is_selected():
    fx = _selector_fixture()
    d = _decide(fx, fx["healthy"])
    assert d.selected_candidate == SELECTED_CONSTRAINED, d.rejection_reasons
    assert d.fallback_required is False
    assert d.rejection_reasons == []
    assert np.array_equal(d.selected_mask, d.constrained_mask)


# --------------------------------------------------------- Test C (v14 spec)

def test_v14_c_prompt_loss_forces_fallback():
    fx = _selector_fixture()
    broken = _punch(fx["healthy"], fx["points"][0], 2)
    d = _decide(fx, broken)
    assert d.selected_candidate == SELECTED_FALLBACK
    assert REASON_LOST_LOCAL_POSITIVE_PROMPT in d.rejection_reasons
    ga = d.gate_results["A_local_positive_prompt_preservation"]
    assert ga["pre_stage_inside_roi_prompt_hits"] == 1
    assert ga["constrained_inside_roi_prompt_hits"] == 0
    assert ga["lost_prompt_indices"] == [0]
    assert np.array_equal(d.selected_mask, d.fallback_mask)


# --------------------------------------------------------- Test D (v14 spec)

def test_v14_d_anchor_exact_loss_only_is_review_and_still_falls_back():
    fx = _selector_fixture()
    broken = fx["healthy"].copy()
    broken[fx["underarm"][1], fx["underarm"][0]] = False      # exact pixel only
    d = _decide(fx, broken)
    gb = d.gate_results["B_anchor_preservation"]
    assert gb["anchors"]["underarm_anchor"]["exact_hit"] is False
    assert gb["anchors"]["underarm_anchor"]["radius_hit"] is True
    assert gb["status"] == "REVIEW_LOCAL_ANCHOR"
    assert REASON_REVIEW_LOCAL_ANCHOR in d.rejection_reasons
    assert d.selected_candidate == SELECTED_FALLBACK


def test_v14_d_anchor_loss_beyond_radius_is_rejected():
    fx = _selector_fixture()
    broken = _punch(fx["healthy"], fx["underarm"], ANCHOR_REVIEW_RADIUS_PX + 2)
    d = _decide(fx, broken)
    gb = d.gate_results["B_anchor_preservation"]
    assert gb["anchors"]["underarm_anchor"]["radius_hit"] is False
    assert gb["status"] == "REJECT_LOCAL_ANCHOR"
    assert REASON_REJECT_LOCAL_ANCHOR in d.rejection_reasons
    assert d.selected_candidate == SELECTED_FALLBACK


def test_v14_anchor_outside_pre_stage_is_not_applicable():
    fx = _selector_fixture()
    d = select_local_armhole_candidate(
        fx["pre"], fx["healthy"], fx["roi"], fx["gar"], fx["points"],
        (0, 0), fx["underarm"], side="sleeve_left")          # shoulder far outside the sleeve
    assert d.gate_results["B_anchor_preservation"]["anchors"]["shoulder_anchor"]["applicable"] is False
    assert d.selected_candidate == SELECTED_CONSTRAINED


# --------------------------------------------------------- Test E (v14 spec)

def test_v14_e_outside_roi_leakage_is_isolated():
    fx = _selector_fixture()
    leaky = fx["healthy"].copy()
    leaky[25:30, 20:24] = True                                # far outside the ROI
    d = _decide(fx, leaky)
    gc = d.gate_results["C_outside_roi_invariant"]
    assert gc["outside_roi_changed_pixels"] == 0
    assert gc["pass"] is True
    assert np.array_equal(d.constrained_mask & ~fx["roi"], fx["pre"] & ~fx["roi"] & fx["gar"])


# --------------------------------------------------------- Test F (v14 spec)

def test_v14_f_fallback_is_bitwise_pre_stage_and_garment():
    fx = _selector_fixture()
    d = _decide(fx, _punch(fx["healthy"], fx["points"][0], 2))
    assert d.selected_candidate == SELECTED_FALLBACK
    assert np.array_equal(d.selected_mask, fx["pre"] & fx["gar"])


# --------------------------------------------------------- Test G (v14 spec)

def test_v14_g_selector_fails_closed_on_bad_input():
    fx = _selector_fixture()
    with pytest.raises(ValueError, match="shape mismatch"):
        select_local_armhole_candidate(fx["pre"], np.zeros((5, 5), bool), fx["roi"], fx["gar"],
                                       fx["points"], fx["shoulder"], fx["underarm"], side="sleeve_left")
    with pytest.raises(ValueError, match="expected a 2D mask"):
        _decide(fx, fx["healthy"][None, ...])
    nan_mask = fx["healthy"].astype(np.float32)
    nan_mask[5, 6] = np.nan
    with pytest.raises(ValueError, match="contains NaN"):
        _decide(fx, nan_mask)
    multiclass = fx["healthy"].astype(np.uint8) * 255
    multiclass[5, 6] = 7
    with pytest.raises(ValueError, match="not a binary mask"):
        _decide(fx, multiclass)
    with pytest.raises(ValueError, match="side must be"):
        _decide(fx, fx["healthy"], side="sleeve_middle")


# --------------------------------------------------------- Test H (v14 spec)

def test_v14_h_conflict_between_selected_sides_blocks_torso():
    fx = _selector_fixture()
    left = _decide(fx, fx["healthy"], side="sleeve_left")
    right = _decide(fx, fx["healthy"], side="sleeve_right")   # deliberately the same geometry
    conflict = left.selected_mask & right.selected_mask
    assert int(np.count_nonzero(conflict > 0)) > 0
    # the caller must stop before deriving torso; nothing in the selector resolves it
    assert not hasattr(left, "resolved_conflict")


# ------------------------------------------------- Test I (v14 spec) determinism

def test_v14_determinism_three_runs_identical():
    import hashlib

    fx = _selector_fixture()
    broken = _punch(fx["healthy"], fx["points"][0], 2)
    signatures = []
    for _ in range(3):
        d = _decide(fx, broken)
        signatures.append(json.dumps({
            "selected": d.selected_candidate,
            "reasons": d.rejection_reasons,
            "metrics": d.local_metrics,
            "sha256": hashlib.sha256(np.ascontiguousarray(d.selected_mask).tobytes()).hexdigest(),
        }, sort_keys=True))
    assert len(set(signatures)) == 1


# --------------------------------- Test A + J (v14 spec) canonical replay

@pytestmark_replay
def test_j_canonical_replay_left_constrained_rejected_for_prompt_loss():
    import cv2

    garment = cv2.imread(str(ARTIFACT_ROOT / "garment_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    pre = np.load(str(_V9 / "variant_a_sleeve_left_final.npy")) > 0
    con = np.load(str(_V10 / "v10_sleeve_left_final.npy")) > 0
    roi_def = json.loads((_V11 / "armhole_roi_definition.json").read_text())
    roi = roi_mask_from_xyxy(roi_def["sides"]["sleeve_left"]["roi_xyxy"], garment.shape)
    anchors = json.loads((_V10 / "anchor_candidates.json").read_text())["sleeve_left"]
    prompts = json.loads((ARTIFACT_ROOT / "diagnostic_sam2_candidate_combo_v8"
                          / "prompt_identity_audit.json").read_text())["sleeve_left"]

    d = select_local_armhole_candidate(
        pre, con, roi, garment, prompts["pos_pts_full"],
        anchors["shoulder_anchor"]["xy"], anchors["underarm_anchor"]["xy"], side="sleeve_left")

    assert d.selected_candidate == SELECTED_FALLBACK
    assert REASON_LOST_LOCAL_POSITIVE_PROMPT in d.rejection_reasons
    ga = d.gate_results["A_local_positive_prompt_preservation"]
    assert ga["lost_prompt_indices"] == [3]          # the known v13 inside-ROI loss
    assert np.array_equal(d.selected_mask, pre & garment)
    inside = [i for i, (x, y) in enumerate(prompts["pos_pts_full"])
              if d.selected_mask[int(round(y)), int(round(x))]]
    assert len(inside) == 5                           # 5/6, i.e. the pre-stage coverage


@pytestmark_replay
def test_j_canonical_replay_right_decision_and_partition():
    import cv2

    garment = cv2.imread(str(ARTIFACT_ROOT / "garment_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    roi_def = json.loads((_V11 / "armhole_roi_definition.json").read_text())
    anchors_all = json.loads((_V10 / "anchor_candidates.json").read_text())
    prompts_all = json.loads((ARTIFACT_ROOT / "diagnostic_sam2_candidate_combo_v8"
                              / "prompt_identity_audit.json").read_text())

    selected, pres = {}, {}
    for side, short in (("sleeve_left", "left"), ("sleeve_right", "right")):
        pre = np.load(str(_V9 / f"variant_a_sleeve_{short}_final.npy")) > 0
        con = np.load(str(_V10 / f"v10_sleeve_{short}_final.npy")) > 0
        roi = roi_mask_from_xyxy(roi_def["sides"][side]["roi_xyxy"], garment.shape)
        d = select_local_armhole_candidate(
            pre, con, roi, garment, prompts_all[side]["pos_pts_full"],
            anchors_all[side]["shoulder_anchor"]["xy"], anchors_all[side]["underarm_anchor"]["xy"],
            side=side)
        selected[side], pres[side] = d.selected_mask, pre
        # the locality invariant must hold whichever candidate won
        assert np.array_equal(d.selected_mask & ~roi, pre & ~roi & garment)
        # coverage must never drop below the pre-stage baseline
        pts = prompts_all[side]["pos_pts_full"]
        sel_hits = sum(1 for x, y in pts if d.selected_mask[int(round(y)), int(round(x))])
        pre_hits = sum(1 for x, y in pts if (pre & garment)[int(round(y)), int(round(x))])
        assert sel_hits >= pre_hits
        assert int(np.count_nonzero((d.selected_mask & ~garment) > 0)) == 0

    conflict = selected["sleeve_left"] & selected["sleeve_right"]
    assert int(np.count_nonzero(conflict > 0)) == 0
    torso = garment & ~selected["sleeve_left"] & ~selected["sleeve_right"]
    assert int(np.count_nonzero((torso & ~garment) > 0)) == 0
