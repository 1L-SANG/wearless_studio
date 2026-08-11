#!/usr/bin/env python3
"""Offline armhole locality merge primitive (v13) + canonical-artifact replay.

Why this exists
---------------
v10 replaced the whole sleeve mask with a constrained shoulder-cap result, so
92.1% (left) / 86.2% (right) of the pre-stage sleeve was reassigned to torso and
the lower sleeve and cuff were destroyed. v12 established that no source for that
step exists in the repository, so there is nothing to patch. This module supplies
the missing safety primitive instead: whatever a constrained armhole step
produces, it may only take effect INSIDE the armhole ROI. Outside the ROI the
pre-stage sleeve is preserved bitwise.

This is an offline experiment module. It is not wired into any runtime path.
No SAM2 inference, no provider calls, no recolor.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import time
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------- primitive

#: unique-value sets a mask is allowed to carry before it is coerced to bool.
_ALLOWED_BINARY_VALUE_SETS = ({0}, {1}, {255}, {0, 1}, {0, 255})


def _to_bool(mask, name: str) -> np.ndarray:
    """Coerce a binary mask to bool, failing closed on anything ambiguous."""
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(f"{name}: expected a 2D mask, got shape {arr.shape}")
    if arr.dtype == np.bool_:
        return arr
    if np.issubdtype(arr.dtype, np.floating):
        if np.isnan(arr).any():
            raise ValueError(f"{name}: contains NaN")
    elif not np.issubdtype(arr.dtype, np.integer):
        raise ValueError(f"{name}: unsupported dtype {arr.dtype}")
    values = set(np.unique(arr).tolist())
    if values not in _ALLOWED_BINARY_VALUE_SETS:
        raise ValueError(
            f"{name}: not a binary mask — unique values {sorted(values)} are outside "
            f"the accepted sets {[sorted(s) for s in _ALLOWED_BINARY_VALUE_SETS]}"
        )
    return arr > 0


def merge_sleeve_inside_roi(
    pre_stage_sleeve: np.ndarray,
    constrained_sleeve: np.ndarray,
    armhole_roi: np.ndarray,
    garment_mask: np.ndarray,
) -> np.ndarray:
    """Apply a constrained armhole result only inside the armhole ROI.

    Outside the ROI the pre-stage sleeve survives bitwise; inside the ROI the
    constrained result wins. Both halves are clipped to the garment mask.

    All four inputs must be 2D binary masks of identical shape in full-image
    coordinates, with ``True`` meaning "inside".
    """
    pre = _to_bool(pre_stage_sleeve, "pre_stage_sleeve")
    con = _to_bool(constrained_sleeve, "constrained_sleeve")
    roi = _to_bool(armhole_roi, "armhole_roi")
    gar = _to_bool(garment_mask, "garment_mask")

    shapes = {"pre_stage_sleeve": pre.shape, "constrained_sleeve": con.shape,
              "armhole_roi": roi.shape, "garment_mask": gar.shape}
    if len(set(shapes.values())) != 1:
        raise ValueError(f"shape mismatch across inputs: {shapes}")

    final = ((pre & ~roi) | (con & roi)) & gar

    # The two invariants this primitive exists to guarantee.
    if not np.array_equal(final & ~roi, pre & ~roi & gar):
        raise AssertionError("outside-ROI locality violated")
    if not np.array_equal(final & roi, con & roi & gar):
        raise AssertionError("inside-ROI identity violated")
    return final


#: v11 and v13 both write ``roi_xyxy`` as an INCLUSIVE bbox. The conversion to
#: half-open NumPy slices happens exactly once, in :func:`roi_contract`.
BBOX_SEMANTICS = "inclusive_xyxy"


def roi_contract(xyxy) -> dict:
    """Describe an inclusive bbox and its single conversion to half-open slices."""
    x1, y1, x2, y2 = (int(v) for v in xyxy)
    return {"bbox_semantics": BBOX_SEMANTICS,
            "bbox_xyxy": [x1, y1, x2, y2],
            "slice_xyxy_half_open": [x1, y1, x2 + 1, y2 + 1]}


def roi_mask_from_xyxy(xyxy, shape_hw) -> np.ndarray:
    """Build a full-image ROI mask from an INCLUSIVE ``[x1, y1, x2, y2]`` box.

    The inclusive reading is the declared contract (:data:`BBOX_SEMANTICS`), not
    something inferred from pixel counts; the conversion to half-open slices is
    done once via :func:`roi_contract`.
    """
    x1, y1, x2, y2 = (int(v) for v in xyxy)
    h, w = int(shape_hw[0]), int(shape_hw[1])
    if not (x1 < x2 and y1 < y2):
        raise ValueError(f"degenerate roi_xyxy {xyxy}")
    if not (0 <= x1 and 0 <= y1 and x2 < w and y2 < h):
        raise ValueError(f"roi_xyxy {xyxy} outside image bounds {[h, w]}")
    sx1, sy1, sx2, sy2 = (roi_contract(xyxy)["slice_xyxy_half_open"][i] for i in range(4))
    m = np.zeros((h, w), bool)
    m[sy1:sy2, sx1:sx2] = True
    return m


# ------------------------------------------------ local QC + fallback selector
# v13 guarantees that a constrained armhole result cannot damage anything
# OUTSIDE the ROI. It says nothing about the ROI interior: on the left sleeve the
# constrained result dropped a canonical positive prompt that the pre-stage mask
# held. The selector below is the missing local gate — if the constrained
# candidate damages canonical local evidence, that ROI falls back to pre-stage.

#: Fixed before execution; never re-tuned after seeing results.
ANCHOR_REVIEW_RADIUS_PX = 3
COMPONENT_INTEGRITY_MIN = 0.95

SELECTED_CONSTRAINED = "constrained"
SELECTED_FALLBACK = "pre_stage_fallback"

REASON_LOST_LOCAL_POSITIVE_PROMPT = "LOST_LOCAL_POSITIVE_PROMPT"
REASON_REJECT_LOCAL_ANCHOR = "REJECT_LOCAL_ANCHOR"
REASON_REVIEW_LOCAL_ANCHOR = "REVIEW_LOCAL_ANCHOR"
REASON_OUTSIDE_ROI_INVARIANT_VIOLATED = "OUTSIDE_ROI_INVARIANT_VIOLATED"
REASON_ASSIGNED_OUTSIDE_GARMENT = "ASSIGNED_OUTSIDE_GARMENT"
REASON_COMPONENT_INTEGRITY_BELOW_MIN = "COMPONENT_INTEGRITY_BELOW_MIN"
REASON_OPPOSITE_CROSSING_WORSE = "OPPOSITE_CROSSING_WORSE_THAN_PRE_STAGE"
REASON_TORSO_CENTER_INTRUSION_WORSE = "TORSO_CENTER_INTRUSION_WORSE_THAN_PRE_STAGE"

LOCAL_GATE_DEFINITIONS = {
    "A_local_positive_prompt_preservation": {
        "rule": ("canonical positive prompts that sit inside the ROI AND inside the pre-stage sleeve must "
                 "survive in the merged constrained result: constrained_hits >= pre_stage_hits"),
        "failure_reason": REASON_LOST_LOCAL_POSITIVE_PROMPT,
    },
    "B_anchor_preservation": {
        "rule": ("for each of shoulder/underarm anchor that the pre-stage sleeve contained: exact hit -> PASS; "
                 f"no exact hit but a hit within a Euclidean radius of {ANCHOR_REVIEW_RADIUS_PX}px -> "
                 "REVIEW_LOCAL_ANCHOR; neither -> REJECT_LOCAL_ANCHOR. REVIEW does not permit automatic "
                 "selection of the constrained candidate"),
        "radius_px": ANCHOR_REVIEW_RADIUS_PX,
        "failure_reasons": [REASON_REVIEW_LOCAL_ANCHOR, REASON_REJECT_LOCAL_ANCHOR],
    },
    "C_outside_roi_invariant": {
        "rule": "outside_roi_added == outside_roi_removed == outside_roi_changed == 0",
        "failure_reason": REASON_OUTSIDE_ROI_INVARIANT_VIOLATED,
    },
    "D_garment_containment": {
        "rule": "assigned_outside_garment_pixels == 0",
        "failure_reason": REASON_ASSIGNED_OUTSIDE_GARMENT,
    },
    "E_component_integrity": {
        "rule": f"dominant_component_fraction >= {COMPONENT_INTEGRITY_MIN}",
        "pixel_count_rule": "np.count_nonzero(mask > 0)",
        "failure_reason": REASON_COMPONENT_INTEGRITY_BELOW_MIN,
    },
    "F_opposite_crossing": {
        "rule": "constrained_merged_opposite_crossing <= pre_stage_opposite_crossing (never an absolute zero)",
        "definition": ("sleeve_left px with x > garment_bbox_center_x, sleeve_right px with "
                       "x < garment_bbox_center_x"),
        "definition_dependent": True,
        "failure_reason": REASON_OPPOSITE_CROSSING_WORSE,
    },
    "G_torso_center_intrusion": {
        "rule": "constrained_merged_torso_center_intrusion_fraction <= pre_stage fraction",
        "definition": ("caller-supplied torso_center_band; when omitted the band defaults to the middle 50% "
                       "of the garment bbox x-range"),
        "definition_dependent": True,
        "failure_reason": REASON_TORSO_CENTER_INTRUSION_WORSE,
    },
}
#: rejection_reasons are emitted in this fixed gate order, so the list is deterministic.
_GATE_ORDER = ("A_local_positive_prompt_preservation", "B_anchor_preservation", "C_outside_roi_invariant",
               "D_garment_containment", "E_component_integrity", "F_opposite_crossing",
               "G_torso_center_intrusion")


class LocalCandidateDecision:
    """Result of the deterministic local armhole candidate selection."""

    __slots__ = ("side", "selected_candidate", "fallback_required", "rejection_reasons",
                 "local_metrics", "gate_results", "selected_mask",
                 "constrained_mask", "fallback_mask")

    def __init__(self, side, selected_candidate, fallback_required, rejection_reasons,
                 local_metrics, gate_results, selected_mask, constrained_mask, fallback_mask):
        self.side = side
        self.selected_candidate = selected_candidate
        self.fallback_required = fallback_required
        self.rejection_reasons = rejection_reasons
        self.local_metrics = local_metrics
        self.gate_results = gate_results
        self.selected_mask = selected_mask
        self.constrained_mask = constrained_mask
        self.fallback_mask = fallback_mask

    def to_json(self) -> dict:
        return {"side": self.side, "selected_candidate": self.selected_candidate,
                "fallback_required": self.fallback_required,
                "rejection_reasons": list(self.rejection_reasons),
                "gate_results": self.gate_results, "local_metrics": self.local_metrics}


def _disc_offsets(radius: int):
    r = int(radius)
    return [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r]


def _hit_within_radius(mask: np.ndarray, xy, radius: int) -> bool:
    h, w = mask.shape
    x, y = int(round(xy[0])), int(round(xy[1]))
    for dy, dx in _disc_offsets(radius):
        yy, xx = y + dy, x + dx
        if 0 <= yy < h and 0 <= xx < w and mask[yy, xx]:
            return True
    return False


def select_local_armhole_candidate(
    pre_stage_sleeve: np.ndarray,
    constrained_sleeve: np.ndarray,
    armhole_roi: np.ndarray,
    garment_mask: np.ndarray,
    positive_points_xy,
    shoulder_anchor_xy=None,
    underarm_anchor_xy=None,
    *,
    side: str,
    torso_center_band: np.ndarray | None = None,
) -> LocalCandidateDecision:
    """Pick between the constrained armhole candidate and a pre-stage fallback.

    Both candidates are built through :func:`merge_sleeve_inside_roi`, so the
    outside-ROI invariant is enforced for either choice. The constrained
    candidate is selected only when every local hard gate passes; otherwise the
    ROI interior falls back to the pre-stage sleeve. The rule is a plain
    conjunction — no scoring, no weighting, no confidence blending.
    """
    if side not in ("sleeve_left", "sleeve_right"):
        raise ValueError(f"side must be 'sleeve_left' or 'sleeve_right', got {side!r}")
    pre = _to_bool(pre_stage_sleeve, "pre_stage_sleeve")
    con = _to_bool(constrained_sleeve, "constrained_sleeve")
    roi = _to_bool(armhole_roi, "armhole_roi")
    gar = _to_bool(garment_mask, "garment_mask")

    c0 = merge_sleeve_inside_roi(pre, con, roi, gar)   # constrained candidate
    c1 = merge_sleeve_inside_roi(pre, pre, roi, gar)   # pre-stage fallback, same code path

    if torso_center_band is None:
        ys, xs = np.nonzero(gar)
        gx0, gx1 = int(xs.min()), int(xs.max())
        gw = gx1 - gx0 + 1
        band = np.zeros(gar.shape, bool)
        band[:, max(int(round(gx0 + 0.25 * gw)), 0):min(int(round(gx1 - 0.25 * gw)) + 1, gar.shape[1])] = True
        band_source = "default: middle 50% of the garment bbox x-range"
    else:
        band = _to_bool(torso_center_band, "torso_center_band")
        band_source = "caller-supplied torso_center_band"
    ys, xs = np.nonzero(gar)
    g_cx = int(round(0.5 * (int(xs.min()) + int(xs.max()))))
    cols = np.arange(gar.shape[1])[None, :]
    opposite_half = (cols > g_cx) if side == "sleeve_left" else (cols < g_cx)

    def crossing(m):
        return PIXELS(m & opposite_half)

    def intrusion_fraction(m):
        return (PIXELS(m & band) / PIXELS(m)) if PIXELS(m) else 0.0

    # ---- Gate A ---------------------------------------------------------
    pts = [(int(round(x)), int(round(y))) for x, y in positive_points_xy]
    pre_hits = [i for i, (x, y) in enumerate(pts) if roi[y, x] and pre[y, x]]
    con_hits = [i for i, (x, y) in enumerate(pts) if roi[y, x] and c0[y, x]]
    gate_a_pass = len(con_hits) >= len(pre_hits)

    # ---- Gate B ---------------------------------------------------------
    anchors = {"shoulder_anchor": shoulder_anchor_xy, "underarm_anchor": underarm_anchor_xy}
    anchor_results = {}
    gate_b_status = "PASS"
    for name, xy in anchors.items():
        if xy is None:
            anchor_results[name] = {"applicable": False, "reason": "anchor not supplied"}
            continue
        x, y = int(round(xy[0])), int(round(xy[1]))
        in_pre = bool(pre[y, x])
        if not in_pre:
            anchor_results[name] = {"applicable": False, "xy": [x, y],
                                    "reason": "anchor was not inside the pre-stage sleeve"}
            continue
        exact = bool(c0[y, x])
        near = exact or _hit_within_radius(c0, (x, y), ANCHOR_REVIEW_RADIUS_PX)
        status = "PASS" if exact else ("REVIEW_LOCAL_ANCHOR" if near else "REJECT_LOCAL_ANCHOR")
        anchor_results[name] = {"applicable": True, "xy": [x, y], "inside_pre_stage": True,
                                "exact_hit": exact, "radius_hit": near,
                                "radius_px": ANCHOR_REVIEW_RADIUS_PX, "status": status}
        if status == "REJECT_LOCAL_ANCHOR":
            gate_b_status = "REJECT_LOCAL_ANCHOR"
        elif status == "REVIEW_LOCAL_ANCHOR" and gate_b_status != "REJECT_LOCAL_ANCHOR":
            gate_b_status = "REVIEW_LOCAL_ANCHOR"
    gate_b_pass = gate_b_status == "PASS"

    # ---- Gates C-G ------------------------------------------------------
    expected_outside = pre & ~roi & gar
    got_outside = c0 & ~roi
    added = PIXELS(got_outside & ~expected_outside)
    removed = PIXELS(expected_outside & ~got_outside)
    gate_c_pass = (added == 0 and removed == 0)

    outside_garment = PIXELS(c0 & ~gar)
    gate_d_pass = outside_garment == 0

    dom, ncc = _dominant_component_fraction(c0)
    gate_e_pass = dom >= COMPONENT_INTEGRITY_MIN

    cross_c0, cross_pre = crossing(c0), crossing(c1)
    gate_f_pass = cross_c0 <= cross_pre

    intr_c0, intr_pre = intrusion_fraction(c0), intrusion_fraction(c1)
    gate_g_pass = intr_c0 <= intr_pre + 1e-12

    gate_results = {
        "A_local_positive_prompt_preservation": {
            "pass": bool(gate_a_pass),
            "pre_stage_inside_roi_prompt_hits": len(pre_hits),
            "pre_stage_inside_roi_prompt_indices": pre_hits,
            "constrained_inside_roi_prompt_hits": len(con_hits),
            "constrained_inside_roi_prompt_indices": con_hits,
            "lost_prompt_indices": [i for i in pre_hits if i not in con_hits]},
        "B_anchor_preservation": {"pass": bool(gate_b_pass), "status": gate_b_status,
                                  "anchors": anchor_results},
        "C_outside_roi_invariant": {"pass": bool(gate_c_pass), "outside_roi_added_pixels": added,
                                    "outside_roi_removed_pixels": removed,
                                    "outside_roi_changed_pixels": added + removed},
        "D_garment_containment": {"pass": bool(gate_d_pass), "assigned_outside_garment_pixels": outside_garment},
        "E_component_integrity": {"pass": bool(gate_e_pass), "dominant_component_fraction": dom,
                                  "connected_component_count": ncc,
                                  "minimum": COMPONENT_INTEGRITY_MIN},
        "F_opposite_crossing": {"pass": bool(gate_f_pass), "constrained_pixels": cross_c0,
                                "pre_stage_pixels": cross_pre, "definition_dependent": True},
        "G_torso_center_intrusion": {"pass": bool(gate_g_pass), "constrained_fraction": intr_c0,
                                     "pre_stage_fraction": intr_pre,
                                     "constrained_pixels": PIXELS(c0 & band),
                                     "pre_stage_pixels": PIXELS(c1 & band),
                                     "band_source": band_source, "definition_dependent": True},
    }

    reasons = []
    if not gate_a_pass:
        reasons.append(REASON_LOST_LOCAL_POSITIVE_PROMPT)
    if not gate_b_pass:
        reasons.append(REASON_REJECT_LOCAL_ANCHOR if gate_b_status == "REJECT_LOCAL_ANCHOR"
                       else REASON_REVIEW_LOCAL_ANCHOR)
    if not gate_c_pass:
        reasons.append(REASON_OUTSIDE_ROI_INVARIANT_VIOLATED)
    if not gate_d_pass:
        reasons.append(REASON_ASSIGNED_OUTSIDE_GARMENT)
    if not gate_e_pass:
        reasons.append(REASON_COMPONENT_INTEGRITY_BELOW_MIN)
    if not gate_f_pass:
        reasons.append(REASON_OPPOSITE_CROSSING_WORSE)
    if not gate_g_pass:
        reasons.append(REASON_TORSO_CENTER_INTRUSION_WORSE)

    all_pass = all(gate_results[g]["pass"] for g in _GATE_ORDER)
    selected = SELECTED_CONSTRAINED if all_pass else SELECTED_FALLBACK
    selected_mask = c0 if all_pass else c1

    local_metrics = {
        "side": side,
        "constrained_candidate_pixels": PIXELS(c0),
        "fallback_candidate_pixels": PIXELS(c1),
        "roi_pixels": PIXELS(roi),
        "constrained_inside_roi_pixels": PIXELS(c0 & roi),
        "fallback_inside_roi_pixels": PIXELS(c1 & roi),
        "selected_pixels": PIXELS(selected_mask),
        "torso_center_band_source": band_source,
    }
    return LocalCandidateDecision(side, selected, not all_pass, reasons, local_metrics,
                                  gate_results, selected_mask, c0, c1)


# ------------------------------------------------------------- replay driver
# Everything below drives the offline replay over existing canonical artifacts.
# Metric definitions are copied verbatim from the v11 audit so the regression
# comparison is meaningful; they are NOT re-tuned here.

PIXELS = lambda m: int(np.count_nonzero(m > 0))  # noqa: E731

METRIC_DEFINITIONS = {
    "pixel_count_rule": "np.count_nonzero(mask > 0)",
    "roi_reconstruction": "inclusive bbox: mask[y1:y2+1, x1:x2+1] = True (matches v11 roi_pixels)",
    "below_underarm": "y > underarm_anchor_y",
    "lower_sleeve_retention": "|mask & below_underarm & v9_sleeve| / |v9_sleeve & below_underarm|",
    "cuff_band": "bottom 15% of the y-extent of the v9 sleeve bbox",
    "cuff_retention": "|mask & cuff_band & v9_sleeve| / |v9_sleeve & cuff_band|",
    "torso_center_band": "middle 50% of the torso_front prompt box x-range (definition_dependent)",
    "opposite_side_crossing": ("sleeve_left px with x > garment_bbox_center_x, sleeve_right px with "
                               "x < garment_bbox_center_x (definition_dependent — baseline is not zero)"),
    "placket_band": ("vertical strip of width 6% of garment bbox width centred on garment_bbox_center_x, "
                     "restricted to the torso_front prompt box y-range, ∩ garment (definition_dependent)"),
    "side_chest": ("outer 25% x-bands of the torso_front prompt box within its y-range, ∩ garment "
                   "(definition_dependent)"),
    "reference_candidate_iou": "IoU against the v8 SAM candidate used as that side's reference",
}
THRESHOLDS = {
    "outside_roi_changed_pixels_max": 0,
    "lower_sleeve_retention_min": 0.85,
    "cuff_retention_min": 0.85,
    "dominant_component_fraction_min": 0.95,
    "assigned_outside_garment_max": 0,
    "left_right_conflict_max": 0,
    "outside_roi_prompt_loss_max": 0,
    "opposite_side_crossing_rule": "v13 <= v9 baseline",
    "torso_placket_rule": "v13 >= v9 baseline",
    "torso_side_chest_max_drop_pp": 5.0,
    "torso_center_intrusion_rule": "v13 <= v9 baseline",
    "v11_regression_pixel_tolerance": 1,
    "v11_regression_fraction_tolerance": 1e-9,
}
SIDES = ("sleeve_left", "sleeve_right")
SHORT = {"sleeve_left": "left", "sleeve_right": "right"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bbox(m):
    if PIXELS(m) == 0:
        return None
    ys, xs = np.nonzero(m)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _dominant_component_fraction(m):
    import cv2
    n, _, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    total = PIXELS(m)
    if total == 0 or n <= 1:
        return 0.0, 0
    areas = st[1:, cv2.CC_STAT_AREA]
    return float(areas.max()) / float(total), int(n - 1)


def run(root: Path, out: Path) -> str:  # noqa: C901 - a linear replay script
    import cv2

    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    v8 = root / "diagnostic_sam2_candidate_combo_v8"
    v9d = root / "diagnostic_sam2_sleeve_first_residual_v9"
    v10d = root / "diagnostic_armhole_boundary_split_v10"
    v11d = root / "diagnostic_armhole_locality_v11"

    required = {
        "v9_sleeve_left": v9d / "variant_a_sleeve_left_final.npy",
        "v9_sleeve_right": v9d / "variant_a_sleeve_right_final.npy",
        "v9_torso_residual": v9d / "variant_a_torso_residual_final.npy",
        "v10_sleeve_left": v10d / "v10_sleeve_left_final.npy",
        "v10_sleeve_right": v10d / "v10_sleeve_right_final.npy",
        "v10_torso": v10d / "v10_torso_final.npy",
        "v11_armhole_roi_definition": v11d / "armhole_roi_definition.json",
        "v11_recomposition_metrics": v11d / "recomposition_metrics.json",
        "v11_criteria_evaluation": v11d / "criteria_evaluation.json",
        "v8_prompt_identity_audit": v8 / "prompt_identity_audit.json",
        "v8_sleeve_left_reference": v8 / "small_t0_sleeve_left_cand1_full_mask.npy",
        "v8_sleeve_right_reference": v8 / "small_t0_sleeve_right_cand0_full_mask.npy",
        "garment_mask": root / "garment_mask.png",
        "carrier": root / "carrier.png",
    }
    provenance = {"files": {}, "missing": []}
    for key, path in required.items():
        entry = {"path": str(path), "exists": path.exists()}
        if path.exists():
            entry["size_bytes"] = path.stat().st_size
            entry["sha256"] = _sha256(path)
            if path.suffix == ".npy":
                arr = np.load(str(path))
                entry.update({"shape_hw": [int(arr.shape[0]), int(arr.shape[1])], "dtype": str(arr.dtype),
                              "unique_values": [bool(v) for v in np.unique(arr).tolist()],
                              "nonzero_count": PIXELS(arr)})
            elif path.suffix == ".png":
                img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if img is not None:
                    u = np.unique(img)
                    entry.update({"shape_hw": [int(img.shape[0]), int(img.shape[1])], "dtype": str(img.dtype),
                                  "unique_values": u.tolist() if u.size <= 8 else [int(u.min()), int(u.max())],
                                  "nonzero_count": PIXELS(img)})
        else:
            provenance["missing"].append(key)
        provenance["files"][key] = entry
    (out / "input_provenance.json").write_text(json.dumps(provenance, indent=2))
    if provenance["missing"]:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_INPUT_ARTIFACT_MISSING", "missing": provenance["missing"],
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "elapsed_seconds": round(time.time() - t0, 3),
             "timestamp": time.time()}, indent=2))
        return "BLOCKED_INPUT_ARTIFACT_MISSING"

    carrier = cv2.imread(str(required["carrier"]), cv2.IMREAD_COLOR)
    garment = cv2.imread(str(required["garment_mask"]), cv2.IMREAD_GRAYSCALE) > 0
    H, W = garment.shape[:2]
    pre = {s: np.load(str(required[f"v9_sleeve_{SHORT[s]}"])) > 0 for s in SIDES}
    con = {s: np.load(str(required[f"v10_sleeve_{SHORT[s]}"])) > 0 for s in SIDES}
    ref = {s: np.load(str(required[f"v8_sleeve_{SHORT[s]}_reference"])) > 0 for s in SIDES}
    v9_torso = np.load(str(required["v9_torso_residual"])) > 0
    v10_torso = np.load(str(required["v10_torso"])) > 0
    roi_def = json.loads(required["v11_armhole_roi_definition"].read_text())
    prompts = json.loads(required["v8_prompt_identity_audit"].read_text())
    v11_metrics = json.loads(required["v11_recomposition_metrics"].read_text())

    manifest = {}

    def persist(name, mask):
        arr = np.asarray(mask, dtype=bool)
        npy, png = out / f"{name}.npy", out / f"{name}.png"
        np.save(str(npy), arr)
        if not cv2.imwrite(str(png), arr.astype(np.uint8) * 255):
            raise SystemExit(f"failed to write {png}")
        back, png_back = np.load(str(npy)), cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        ok = np.array_equal(back, arr) and np.array_equal(png_back > 0, arr)
        manifest[name] = {"npy": str(npy), "png": str(png),
                          "shape_hw": [int(arr.shape[0]), int(arr.shape[1])], "dtype": str(arr.dtype),
                          "nonzero_count": PIXELS(arr), "bbox_xyxy": _bbox(arr),
                          "npy_sha256": _sha256(npy), "png_sha256": _sha256(png),
                          "verification": "VERIFIED_RELOAD_SUCCESS" if ok else "RELOAD_MISMATCH"}
        if not ok:
            raise SystemExit(f"reload verification failed for {name}")

    # ---- ROI reconstruction ------------------------------------------------
    roi = {}
    roi_check = {"rule_used": METRIC_DEFINITIONS["roi_reconstruction"],
                 "note_on_exclusive_rule": ("an exclusive mask[y1:y2, x1:x2] reading does NOT reproduce v11's "
                                            "reported roi_pixels; both counts are reported below"),
                 "sides": {}, "issues": []}
    for s in SIDES:
        xyxy = roi_def["sides"][s]["roi_xyxy"]
        m = roi_mask_from_xyxy(xyxy, (H, W))
        roi[s] = m
        x1, y1, x2, y2 = xyxy
        exclusive = (y2 - y1) * (x2 - x1)
        reported = roi_def["sides"][s]["roi_pixels"]
        entry = {"roi_xyxy": xyxy, "x1_lt_x2": bool(x1 < x2), "y1_lt_y2": bool(y1 < y2),
                 "within_image_bounds": bool(0 <= x1 and 0 <= y1 and x2 < W and y2 < H),
                 "shape_matches_sleeve": bool(m.shape == pre[s].shape),
                 "roi_pixels_inclusive": PIXELS(m), "roi_pixels_exclusive": int(exclusive),
                 "v11_reported_roi_pixels": int(reported),
                 "inclusive_matches_v11": bool(PIXELS(m) == reported),
                 "exclusive_matches_v11": bool(exclusive == reported)}
        roi_check["sides"][s] = entry
        if not (entry["x1_lt_x2"] and entry["y1_lt_y2"] and entry["within_image_bounds"]
                and entry["shape_matches_sleeve"] and entry["inclusive_matches_v11"]):
            roi_check["issues"].append(s)
        persist(f"roi_{SHORT[s]}", m)
    roi_check["status"] = "ROI_RECONSTRUCTION_OK" if not roi_check["issues"] else "ROI_RECONSTRUCTION_MISMATCH"
    (out / "roi_reconstruction_check.json").write_text(json.dumps(roi_check, indent=2))
    if roi_check["issues"]:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_COORDINATE_OR_SHAPE_MISMATCH", "issues": roi_check["issues"],
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "elapsed_seconds": round(time.time() - t0, 3),
             "timestamp": time.time()}, indent=2))
        return "BLOCKED_COORDINATE_OR_SHAPE_MISMATCH"

    # ---- merge -------------------------------------------------------------
    final = {s: merge_sleeve_inside_roi(pre[s], con[s], roi[s], garment) for s in SIDES}
    for s in SIDES:
        persist(f"final_{SHORT[s]}", final[s])

    conflict = final["sleeve_left"] & final["sleeve_right"]
    persist("conflict", conflict)
    conflict_pixels = PIXELS(conflict)

    # ---- locality + identity checks ---------------------------------------
    locality = {"definition": "bitwise comparison outside the ROI", "sides": {}}
    identity = {"definition": "bitwise comparison inside the ROI", "sides": {}}
    for s in SIDES:
        expect_out = pre[s] & ~roi[s] & garment
        got_out = final[s] & ~roi[s]
        added = got_out & ~expect_out
        removed = expect_out & ~got_out
        persist(f"outside_roi_diff_{SHORT[s]}", added | removed)
        locality["sides"][s] = {
            "outside_roi_added_pixels": PIXELS(added),
            "outside_roi_removed_pixels": PIXELS(removed),
            "outside_roi_changed_pixels": PIXELS(added | removed),
            "bitwise_equal": bool(np.array_equal(got_out, expect_out)),
            "outside_roi_reference_pixels": PIXELS(expect_out),
        }
        expect_in = con[s] & roi[s] & garment
        got_in = final[s] & roi[s]
        persist(f"inside_roi_diff_{SHORT[s]}", (got_in & ~expect_in) | (expect_in & ~got_in))
        identity["sides"][s] = {
            "inside_roi_changed_pixels": PIXELS((got_in & ~expect_in) | (expect_in & ~got_in)),
            "bitwise_equal": bool(np.array_equal(got_in, expect_in)),
            "inside_roi_pixels": PIXELS(got_in),
        }
    locality["all_sides_bitwise_equal"] = all(v["bitwise_equal"] for v in locality["sides"].values())
    identity["all_sides_bitwise_equal"] = all(v["bitwise_equal"] for v in identity["sides"].values())
    (out / "locality_invariant_check.json").write_text(json.dumps(locality, indent=2))
    (out / "inside_roi_identity_check.json").write_text(json.dumps(identity, indent=2))

    # ---- torso -------------------------------------------------------------
    if conflict_pixels > 0:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_LEFT_RIGHT_CONFLICT", "left_right_conflict_pixels": conflict_pixels,
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "elapsed_seconds": round(time.time() - t0, 3),
             "timestamp": time.time()}, indent=2))
        return "BLOCKED_LEFT_RIGHT_CONFLICT"
    final_torso = garment & ~final["sleeve_left"] & ~final["sleeve_right"]
    persist("final_torso", final_torso)

    # ---- shared bands ------------------------------------------------------
    gx0, gy0, gx1, gy1 = _bbox(garment)
    gw = gx1 - gx0 + 1
    g_cx = int(round(0.5 * (gx0 + gx1)))
    tb = prompts["torso_front"]["box_full"]
    t_cx, t_w = 0.5 * (tb[0] + tb[2]), tb[2] - tb[0]
    center_band = np.zeros((H, W), bool)
    center_band[:, max(int(round(t_cx - 0.25 * t_w)), 0):min(int(round(t_cx + 0.25 * t_w)), W)] = True
    placket = np.zeros((H, W), bool)
    pw = int(round(0.06 * gw))
    placket[int(tb[1]):int(tb[3]) + 1, max(g_cx - pw // 2, 0):min(g_cx + pw // 2, W)] = True
    placket &= garment
    side_chest = np.zeros((H, W), bool)
    side_w = int(round(0.25 * t_w))
    side_chest[int(tb[1]):int(tb[3]) + 1, int(tb[0]):int(tb[0]) + side_w] = True
    side_chest[int(tb[1]):int(tb[3]) + 1, int(tb[2]) - side_w:int(tb[2]) + 1] = True
    side_chest &= garment
    below, cuff = {}, {}
    for s in SIDES:
        uy = roi_def["sides"][s]["underarm_anchor_xy"][1]
        b = np.zeros((H, W), bool)
        b[uy + 1:, :] = True
        below[s] = b
        by0, by1 = _bbox(pre[s])[1], _bbox(pre[s])[3]
        cut = int(round(by0 + 0.85 * (by1 - by0)))
        c = np.zeros((H, W), bool)
        c[cut:by1 + 1, :] = True
        cuff[s] = c

    def sleeve_metrics(s, m):
        dom, ncc = _dominant_component_fraction(m)
        pts = prompts[s]["pos_pts_full"]
        inside = [i for i, (x, y) in enumerate(pts) if m[int(round(y)), int(round(x))]]
        v9b, v9c = PIXELS(pre[s] & below[s]), PIXELS(pre[s] & cuff[s])
        cols = np.arange(W)[None, :]
        opp = PIXELS(m & (cols > g_cx)) if s == "sleeve_left" else PIXELS(m & (cols < g_cx))
        R = ref[s]
        return {"sleeve_pixel_count": PIXELS(m),
                "prompt_points_inside_count": len(inside), "prompt_points_inside_indices": inside,
                "prompt_coverage": len(inside) / len(pts),
                "reference_candidate_iou": PIXELS(m & R) / PIXELS(m | R) if PIXELS(m | R) else None,
                "dominant_component_fraction": dom, "connected_component_count": ncc,
                "lower_sleeve_retention_fraction": PIXELS(m & pre[s] & below[s]) / v9b if v9b else None,
                "cuff_retention_fraction": PIXELS(m & pre[s] & cuff[s]) / v9c if v9c else None,
                "torso_center_intrusion_fraction": PIXELS(m & center_band) / PIXELS(m) if PIXELS(m) else None,
                "opposite_side_crossing_pixels": int(opp),
                "assigned_outside_garment_pixels": PIXELS(m & ~garment)}

    def torso_metrics(m):
        dom, ncc = _dominant_component_fraction(m)
        return {"torso_pixel_count": PIXELS(m),
                "torso_area_fraction_of_garment": PIXELS(m) / PIXELS(garment),
                "dominant_component_fraction": dom, "connected_component_count": ncc,
                "placket_coverage_fraction": PIXELS(m & placket) / PIXELS(placket) if PIXELS(placket) else None,
                "side_chest_preservation_fraction": (PIXELS(m & side_chest) / PIXELS(side_chest)
                                                     if PIXELS(side_chest) else None),
                "assigned_outside_garment_pixels": PIXELS(m & ~garment)}

    sleeve_integrity = {"definitions": METRIC_DEFINITIONS, "thresholds": THRESHOLDS, "results": {}}
    for label, masks in (("v9", pre), ("v10", con), ("v13", final)):
        sleeve_integrity["results"][label] = {s: sleeve_metrics(s, masks[s]) for s in SIDES}
    sleeve_integrity["left_right_conflict_pixels"] = conflict_pixels
    checks = {}
    for s in SIDES:
        m13, m9 = sleeve_integrity["results"]["v13"][s], sleeve_integrity["results"]["v9"][s]
        checks[s] = {
            "lower_sleeve_retention_ge_min": bool(m13["lower_sleeve_retention_fraction"] >= THRESHOLDS["lower_sleeve_retention_min"]),
            "cuff_retention_ge_min": bool(m13["cuff_retention_fraction"] >= THRESHOLDS["cuff_retention_min"]),
            "dominant_component_fraction_ge_min": bool(m13["dominant_component_fraction"] >= THRESHOLDS["dominant_component_fraction_min"]),
            "assigned_outside_garment_zero": bool(m13["assigned_outside_garment_pixels"] == 0),
            "opposite_side_crossing_not_worse_than_v9": bool(
                m13["opposite_side_crossing_pixels"] <= m9["opposite_side_crossing_pixels"]),
        }
        checks[s]["all_pass"] = bool(all(checks[s].values()))
    sleeve_integrity["checks"] = checks
    sleeve_integrity["all_sides_pass"] = bool(all(checks[s]["all_pass"] for s in SIDES))
    (out / "sleeve_integrity_metrics.json").write_text(json.dumps(sleeve_integrity, indent=2))

    torso_safety = {"definitions": {k: METRIC_DEFINITIONS[k] for k in ("placket_band", "side_chest", "torso_center_band")},
                    "results": {"v9": torso_metrics(v9_torso), "v10": torso_metrics(v10_torso),
                                "v13": torso_metrics(final_torso)}}
    t13, t9 = torso_safety["results"]["v13"], torso_safety["results"]["v9"]
    torso_safety["checks"] = {
        "placket_coverage_ge_v9": bool(t13["placket_coverage_fraction"] >= t9["placket_coverage_fraction"] - 1e-12),
        "side_chest_drop_le_5pp": bool((t9["side_chest_preservation_fraction"]
                                        - t13["side_chest_preservation_fraction"]) * 100.0
                                       <= THRESHOLDS["torso_side_chest_max_drop_pp"]),
        "sleeve_torso_center_intrusion_not_increased_vs_v9": bool(all(
            sleeve_integrity["results"]["v13"][s]["torso_center_intrusion_fraction"]
            <= sleeve_integrity["results"]["v9"][s]["torso_center_intrusion_fraction"] + 1e-12 for s in SIDES)),
        "torso_assigned_outside_garment_zero": bool(t13["assigned_outside_garment_pixels"] == 0),
    }
    torso_safety["all_pass"] = bool(all(torso_safety["checks"].values()))
    (out / "torso_safety_metrics.json").write_text(json.dumps(torso_safety, indent=2))

    # ---- prompt preservation ----------------------------------------------
    preservation = {"rule": ("a v9 positive prompt is 'preserved' if, being inside the v9 sleeve mask, it is "
                             "still inside the merged sleeve; only prompts OUTSIDE the ROI gate the verdict"),
                    "sides": {}}
    for s in SIDES:
        rows, out_loss, in_loss = [], [], []
        v10_in_roi_hits = 0
        v13_in_roi_hits = 0
        for i, (x, y) in enumerate(prompts[s]["pos_pts_full"]):
            xi, yi = int(round(x)), int(round(y))
            in_v9, in_roi = bool(pre[s][yi, xi]), bool(roi[s][yi, xi])
            in_final, in_v10 = bool(final[s][yi, xi]), bool(con[s][yi, xi])
            rows.append({"point_index": i, "full_image_xy": [float(x), float(y)],
                         "inside_v9_sleeve": in_v9, "inside_roi": in_roi,
                         "inside_v10_constrained": in_v10, "inside_v13_final": in_final})
            if in_v9 and not in_final:
                (in_loss if in_roi else out_loss).append(i)
            if in_roi:
                v10_in_roi_hits += int(in_v10)
                v13_in_roi_hits += int(in_final)
        preservation["sides"][s] = {
            "points": rows,
            "outside_roi_prompt_loss_count": len(out_loss),
            "outside_roi_lost_prompt_indices": out_loss,
            "inside_roi_lost_prompt_indices": in_loss,
            "inside_roi_prompt_hits_v10": v10_in_roi_hits,
            "inside_roi_prompt_hits_v13": v13_in_roi_hits,
            "inside_roi_coverage_not_worse_than_v10": bool(v13_in_roi_hits >= v10_in_roi_hits),
        }
    preservation["outside_roi_prompt_loss_total"] = sum(
        preservation["sides"][s]["outside_roi_prompt_loss_count"] for s in SIDES)
    preservation["local_path_review_required"] = bool(any(
        preservation["sides"][s]["inside_roi_lost_prompt_indices"] for s in SIDES))
    preservation["inside_roi_coverage_not_worse_than_v10"] = bool(all(
        preservation["sides"][s]["inside_roi_coverage_not_worse_than_v10"] for s in SIDES))
    (out / "prompt_preservation_check.json").write_text(json.dumps(preservation, indent=2))

    # ---- v11 Variant B regression -----------------------------------------
    reg_fields = ["sleeve_pixel_count", "prompt_coverage", "reference_candidate_iou",
                  "lower_sleeve_retention_fraction", "cuff_retention_fraction",
                  "dominant_component_fraction", "opposite_side_crossing_pixels"]
    regression = {"baseline": "diagnostic_armhole_locality_v11 :: variant_b",
                  "tolerances": {"pixel_counts": THRESHOLDS["v11_regression_pixel_tolerance"],
                                 "fractions": THRESHOLDS["v11_regression_fraction_tolerance"]},
                  "sleeves": {}, "torso": {}}
    reg_ok = True
    for s in SIDES:
        rows = {}
        for f in reg_fields:
            got = sleeve_integrity["results"]["v13"][s][f]
            exp = v11_metrics["sleeves"]["variant_b"][s][f]
            if isinstance(exp, int) and not isinstance(exp, bool):
                ok = abs(int(got) - int(exp)) <= THRESHOLDS["v11_regression_pixel_tolerance"]
            else:
                ok = abs(float(got) - float(exp)) <= THRESHOLDS["v11_regression_fraction_tolerance"]
            rows[f] = {"v13": got, "v11_variant_b": exp, "delta": (got - exp), "matches": bool(ok)}
            reg_ok = reg_ok and ok
        regression["sleeves"][s] = rows
    got_t = torso_safety["results"]["v13"]["torso_pixel_count"]
    exp_t = v11_metrics["torso"]["variant_b"]["torso_pixel_count"]
    ok_t = abs(got_t - exp_t) <= THRESHOLDS["v11_regression_pixel_tolerance"]
    regression["torso"]["torso_pixel_count"] = {"v13": got_t, "v11_variant_b": exp_t,
                                                "delta": got_t - exp_t, "matches": bool(ok_t)}
    reg_ok = reg_ok and ok_t
    regression["all_match"] = bool(reg_ok)
    (out / "v11_regression_comparison.json").write_text(json.dumps(regression, indent=2))

    # ---- verdict -----------------------------------------------------------
    gates = {
        "outside_roi_bitwise_equal": locality["all_sides_bitwise_equal"],
        "inside_roi_identity": identity["all_sides_bitwise_equal"],
        "sleeve_integrity": sleeve_integrity["all_sides_pass"],
        "left_right_conflict_zero": conflict_pixels == 0,
        "outside_roi_prompt_loss_zero": preservation["outside_roi_prompt_loss_total"] == 0,
        "inside_roi_coverage_not_worse_than_v10": preservation["inside_roi_coverage_not_worse_than_v10"],
        "torso_safety": torso_safety["all_pass"],
        "v11_regression_match": regression["all_match"],
    }
    if not gates["outside_roi_bitwise_equal"] or not gates["inside_roi_identity"] \
            or not gates["outside_roi_prompt_loss_zero"]:
        verdict = "BLOCKED_LOCALITY_INVARIANT_FAILED"
    elif not gates["torso_safety"]:
        verdict = "BLOCKED_TORSO_SAFETY_FAILED"
    elif not gates["v11_regression_match"]:
        verdict = "BLOCKED_V11_REGRESSION_MISMATCH"
    elif all(gates.values()):
        verdict = "OFFLINE_LOCALITY_PRIMITIVE_VALIDATED"
    else:
        verdict = "BLOCKED_LOCALITY_INVARIANT_FAILED"

    _write_visuals(out, carrier, garment, pre, con, final, final_torso, roi, prompts, v11_metrics,
                   preservation, conflict)
    consistency = _write_reports(out, {
        "roi_check": roi_check, "locality": locality, "identity": identity,
        "sleeve_integrity": sleeve_integrity, "torso_safety": torso_safety,
        "preservation": preservation, "regression": regression, "gates": gates, "verdict": verdict})

    (out / "implementation_scope.json").write_text(json.dumps({
        "new_files": ["server/scripts/armhole_locality_merge_v13.py",
                      "server/tests/test_armhole_locality_merge_v13.py"],
        "new_file_count": 2, "max_allowed": 3,
        "modified_production_runtime_files": [], "modified_dependencies": [],
        "production_wiring_performed": False, "deployment_performed": False,
        "primitive": {"module": "server/scripts/armhole_locality_merge_v13.py",
                      "function": "merge_sleeve_inside_roi",
                      "signature": ("merge_sleeve_inside_roi(pre_stage_sleeve, constrained_sleeve, "
                                    "armhole_roi, garment_mask) -> np.ndarray")},
    }, indent=2))
    (out / "canonical_mask_manifest.json").write_text(json.dumps(
        {"outputs": manifest,
         "all_verified": all(v["verification"] == "VERIFIED_RELOAD_SUCCESS" for v in manifest.values())}, indent=2))
    meta = {"timestamp": time.time(), "elapsed_seconds": round(time.time() - t0, 3),
            "provider_calls": 0, "sam2_inference_calls": 0,
            "modified_dependencies": [], "modified_production_runtime_files": [],
            "production_wiring_performed": False, "deployment_performed": False,
            "input_artifact_directories": [str(root), str(v8), str(v9d), str(v10d), str(v11d)],
            "output_directory": str(out),
            "persisted_mask_count": len(manifest),
            "all_masks_reload_verified": all(v["verification"] == "VERIFIED_RELOAD_SUCCESS"
                                             for v in manifest.values()),
            "report_json_mismatch_count": consistency["report_json_mismatch_count"],
            "local_path_review_required": preservation["local_path_review_required"],
            "gates": gates, "final_verdict": verdict}
    meta["modified_files"] = sorted(str(p.relative_to(out)) for p in out.iterdir() if p.is_file())
    (out / "execution_meta.json").write_text(json.dumps(meta, indent=2))
    return verdict


def _write_visuals(out, carrier, garment, pre, con, final, final_torso, roi, prompts, v11_metrics,
                   preservation, conflict):
    import cv2
    S = 4

    def down(img):
        return cv2.resize(img, (img.shape[1] // S, img.shape[0] // S), interpolation=cv2.INTER_AREA)

    def dm(m):
        return down(m.astype(np.uint8) * 255) > 127

    def blend(img, m, color, alpha):
        o = img.copy(); ov = np.zeros_like(img); ov[m] = color
        o[m] = (img[m] * (1 - alpha) + ov[m] * alpha).astype(np.uint8)
        return o

    def legend(img, items, note=None):
        for i, (t, c) in enumerate(items):
            cv2.rectangle(img, (18, 18 + i * 32), (52, 44 + i * 32), c, -1)
            for col, th in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                cv2.putText(img, t, (60, 40 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.62, col, th)
        if note:
            for col, th in (((0, 0, 0), 5), ((0, 220, 255), 2)):
                cv2.putText(img, note, (18, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.62, col, th)
        return img

    base = down(carrier)
    C9, C10, C13, CROI = (255, 180, 0), (0, 0, 235), (0, 210, 0), (0, 255, 255)
    for s in SIDES:
        img = blend(base.copy(), dm(pre[s]), C9, 0.35)
        img = blend(img, dm(final[s]), C13, 0.45)
        img = blend(img, dm(con[s]), C10, 0.55)
        cont, _ = cv2.findContours(dm(roi[s]).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cont, -1, CROI, 3)
        legend(img, [("v9 pre-stage", C9), ("v10 constrained", C10), ("v13 merged", C13), ("armhole ROI", CROI)],
               f"{s}: v13 px = {PIXELS(final[s]):,}")
        cv2.imwrite(str(out / f"{SHORT[s]}_v9_v10_v13_overlay.png"), img)

        x1, y1, x2, y2 = _bbox(roi[s])
        px0, py0 = max(x1 - 200, 0), max(y1 - 200, 0)
        px1, py1 = min(x2 + 200, carrier.shape[1]), min(y2 + 200, carrier.shape[0])
        crop = carrier[py0:py1, px0:px1].copy()
        for m, c, a in ((pre[s][py0:py1, px0:px1], C9, 0.30), (final[s][py0:py1, px0:px1], C13, 0.40)):
            ov = np.zeros_like(crop); ov[m] = c
            crop[m] = (crop[m] * (1 - a) + ov[m] * a).astype(np.uint8)
        cv2.rectangle(crop, (x1 - px0, y1 - py0), (x2 - px0, y2 - py0), CROI, 4)
        cv2.imwrite(str(out / f"{SHORT[s]}_roi_zoom.png"),
                    cv2.resize(crop, (crop.shape[1] // 2, crop.shape[0] // 2), interpolation=cv2.INTER_AREA))

    img = blend(base.copy(), dm(final_torso), (0, 0, 220), 0.40)
    img = blend(img, dm(final["sleeve_left"]), (0, 220, 0), 0.50)
    img = blend(img, dm(final["sleeve_right"]), (255, 160, 0), 0.50)
    if PIXELS(conflict):
        img = blend(img, dm(conflict), (0, 0, 255), 0.9)
    legend(img, [("torso", (0, 0, 220)), ("left sleeve", (0, 220, 0)), ("right sleeve", (255, 160, 0))],
           f"conflict px = {PIXELS(conflict)}")
    cv2.imwrite(str(out / "final_partition_overlay.png"), img)

    diff = np.zeros(garment.shape, bool)
    for s in SIDES:
        expect = pre[s] & ~roi[s] & garment
        got = final[s] & ~roi[s]
        diff |= (got & ~expect) | (expect & ~got)
    img = blend(base.copy(), dm(garment), (150, 150, 150), 0.20)
    if PIXELS(diff):
        img = blend(img, dm(diff), (0, 0, 255), 1.0)
    legend(img, [("garment", (150, 150, 150)), ("outside-ROI changed px", (0, 0, 255))],
           f"outside-ROI changed pixels (both sleeves) = {PIXELS(diff)}")
    cv2.imwrite(str(out / "outside_roi_difference_overlay.png"), img)

    img = base.copy()
    for s in SIDES:
        img = blend(img, dm(final[s]), C13, 0.35)
        cont, _ = cv2.findContours(dm(roi[s]).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cont, -1, CROI, 3)
        for r in preservation["sides"][s]["points"]:
            x, y = int(r["full_image_xy"][0] / S), int(r["full_image_xy"][1] / S)
            if r["inside_v13_final"]:
                col = (0, 255, 0)
            elif r["inside_v9_sleeve"]:
                col = (0, 165, 255) if r["inside_roi"] else (0, 0, 255)
            else:
                col = (140, 140, 140)
            cv2.circle(img, (x, y), 11, (0, 0, 0), -1)
            cv2.circle(img, (x, y), 8, col, -1)
            for c2, th in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                cv2.putText(img, str(r["point_index"]), (x + 13, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, c2, th)
    legend(img, [("kept in v13", (0, 255, 0)), ("lost INSIDE ROI (local path review)", (0, 165, 255)),
                 ("lost OUTSIDE ROI (gate)", (0, 0, 255)), ("not in v9 sleeve", (140, 140, 140))],
           f"outside-ROI prompt loss = {preservation['outside_roi_prompt_loss_total']}")
    cv2.imwrite(str(out / "prompt_preservation_overlay.png"), img)

    PWD, PHT = 470, 700

    def panel(ml, mr, title):
        c = np.zeros((PHT, PWD, 3), np.uint8)
        c[..., 1] = cv2.resize(ml.astype(np.uint8) * 255, (PWD, PHT), interpolation=cv2.INTER_AREA)
        c[..., 2] = cv2.resize(mr.astype(np.uint8) * 255, (PWD, PHT), interpolation=cv2.INTER_AREA)
        cv2.rectangle(c, (0, 0), (PWD - 1, 32), (0, 0, 0), -1)
        cv2.putText(c, title, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 2)
        cv2.rectangle(c, (0, 0), (PWD - 1, PHT - 1), (90, 90, 90), 2)
        return c

    v11b = v11_metrics["sleeves"]["variant_b"]
    sheet = np.hstack([
        panel(pre["sleeve_left"], pre["sleeve_right"], "v9 pre-stage"),
        panel(con["sleeve_left"], con["sleeve_right"], "v10 constrained"),
        panel(final["sleeve_left"], final["sleeve_right"],
              f"v13 merged  L={PIXELS(final['sleeve_left']):,} R={PIXELS(final['sleeve_right']):,}"),
        panel(final["sleeve_left"], final["sleeve_right"],
              f"v11 variant_b  L={v11b['sleeve_left']['sleeve_pixel_count']:,} "
              f"R={v11b['sleeve_right']['sleeve_pixel_count']:,}")])
    cv2.imwrite(str(out / "v11_variant_b_vs_v13_contact_sheet.png"), sheet)


def _write_reports(out: Path, ctx: dict) -> dict:
    fields = []

    def F(name, value, src, path):
        fields.append({"report_field": name, "report_value": value, "source_file": src, "source_json_path": path})

    loc, ident = ctx["locality"], ctx["identity"]
    si, ts = ctx["sleeve_integrity"], ctx["torso_safety"]
    pres, reg = ctx["preservation"], ctx["regression"]
    for s in SIDES:
        for k in ("outside_roi_added_pixels", "outside_roi_removed_pixels", "outside_roi_changed_pixels"):
            F(f"locality.{s}.{k}", loc["sides"][s][k], "locality_invariant_check.json", f"sides.{s}.{k}")
        F(f"identity.{s}.inside_roi_changed_pixels", ident["sides"][s]["inside_roi_changed_pixels"],
          "inside_roi_identity_check.json", f"sides.{s}.inside_roi_changed_pixels")
        F(f"roi.{s}.roi_pixels_inclusive", ctx["roi_check"]["sides"][s]["roi_pixels_inclusive"],
          "roi_reconstruction_check.json", f"sides.{s}.roi_pixels_inclusive")
        F(f"roi.{s}.inclusive_matches_v11", ctx["roi_check"]["sides"][s]["inclusive_matches_v11"],
          "roi_reconstruction_check.json", f"sides.{s}.inclusive_matches_v11")
        for label in ("v9", "v10", "v13"):
            for k in ("sleeve_pixel_count", "prompt_coverage", "reference_candidate_iou",
                      "lower_sleeve_retention_fraction", "cuff_retention_fraction",
                      "dominant_component_fraction", "opposite_side_crossing_pixels",
                      "assigned_outside_garment_pixels", "torso_center_intrusion_fraction"):
                F(f"sleeve.{label}.{s}.{k}", si["results"][label][s][k],
                  "sleeve_integrity_metrics.json", f"results.{label}.{s}.{k}")
        F(f"preservation.{s}.outside_roi_prompt_loss_count", pres["sides"][s]["outside_roi_prompt_loss_count"],
          "prompt_preservation_check.json", f"sides.{s}.outside_roi_prompt_loss_count")
        F(f"preservation.{s}.inside_roi_prompt_hits_v10", pres["sides"][s]["inside_roi_prompt_hits_v10"],
          "prompt_preservation_check.json", f"sides.{s}.inside_roi_prompt_hits_v10")
        F(f"preservation.{s}.inside_roi_prompt_hits_v13", pres["sides"][s]["inside_roi_prompt_hits_v13"],
          "prompt_preservation_check.json", f"sides.{s}.inside_roi_prompt_hits_v13")
        for f in reg["sleeves"][s]:
            F(f"regression.{s}.{f}.matches", reg["sleeves"][s][f]["matches"],
              "v11_regression_comparison.json", f"sleeves.{s}.{f}.matches")
            F(f"regression.{s}.{f}.v11_variant_b", reg["sleeves"][s][f]["v11_variant_b"],
              "v11_regression_comparison.json", f"sleeves.{s}.{f}.v11_variant_b")
    for label in ("v9", "v10", "v13"):
        for k in ("torso_pixel_count", "torso_area_fraction_of_garment", "dominant_component_fraction",
                  "placket_coverage_fraction", "side_chest_preservation_fraction",
                  "assigned_outside_garment_pixels"):
            F(f"torso.{label}.{k}", ts["results"][label][k], "torso_safety_metrics.json", f"results.{label}.{k}")
    for k, v in ts["checks"].items():
        F(f"torso_check.{k}", v, "torso_safety_metrics.json", f"checks.{k}")
    F("sleeve.left_right_conflict_pixels", si["left_right_conflict_pixels"],
      "sleeve_integrity_metrics.json", "left_right_conflict_pixels")
    F("preservation.outside_roi_prompt_loss_total", pres["outside_roi_prompt_loss_total"],
      "prompt_preservation_check.json", "outside_roi_prompt_loss_total")
    F("preservation.local_path_review_required", pres["local_path_review_required"],
      "prompt_preservation_check.json", "local_path_review_required")
    F("regression.torso_pixel_count.matches", reg["torso"]["torso_pixel_count"]["matches"],
      "v11_regression_comparison.json", "torso.torso_pixel_count.matches")
    F("regression.all_match", reg["all_match"], "v11_regression_comparison.json", "all_match")
    (out / "report_source_mapping.json").write_text(json.dumps(fields, indent=2))

    def dig(o, p):
        cur = o
        for part in p.split("."):
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return cur

    mismatches = []
    for f in fields:
        src = json.loads((out / f["source_file"]).read_text())
        try:
            sv = dig(src, f["source_json_path"])
        except Exception as exc:  # noqa: BLE001
            mismatches.append({**f, "error": str(exc)})
            continue
        ok = (sv == f["report_value"]) or (
            isinstance(sv, (int, float)) and not isinstance(sv, bool)
            and isinstance(f["report_value"], (int, float)) and not isinstance(f["report_value"], bool)
            and abs(float(sv) - float(f["report_value"])) < 1e-12)
        f["source_value"] = sv
        f["matches"] = bool(ok)
        if not ok:
            mismatches.append({**f, "source_value": sv})
    (out / "report_source_mapping.json").write_text(json.dumps(fields, indent=2))
    consistency = {"total_report_fields_audited": len(fields), "report_json_mismatch_count": len(mismatches),
                   "mismatches": mismatches,
                   "status": "CONSISTENCY_CHECK_PASSED" if not mismatches else "CONSISTENCY_CHECK_FAILED"}
    (out / "report_consistency_check.json").write_text(json.dumps(consistency, indent=2))

    val = {f["report_field"]: f["report_value"] for f in fields}

    def fmt(v, nd=4):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "PASS" if v else "FAIL"
        if isinstance(v, float):
            return f"{v:.{nd}f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    md = ["# v13 Offline Armhole Locality Merge Primitive", "",
          "All numbers below are rendered from the JSON files in this directory "
          "(`report_source_mapping.json` maps each one to its source path).", "",
          "## 1. Execution", "- provider_calls: `0`", "- sam2_inference_calls: `0`",
          "- modified_production_runtime_files: `[]`", "- modified_dependencies: `[]`",
          f"- report_json_mismatch_count: `{consistency['report_json_mismatch_count']}`", "",
          "## 2. ROI reconstruction", "",
          "| side | roi_pixels (inclusive) | matches v11 |", "|---|---:|---:|"]
    for s in SIDES:
        md.append(f"| `{s}` | {fmt(val[f'roi.{s}.roi_pixels_inclusive'])} | "
                  f"{fmt(val[f'roi.{s}.inclusive_matches_v11'])} |")
    md += ["", ctx["roi_check"]["note_on_exclusive_rule"], "",
           "## 3. Outside-ROI locality invariant", "",
           "| side | added | removed | changed |", "|---|---:|---:|---:|"]
    for s in SIDES:
        md.append(f"| `{s}` | {fmt(val[f'locality.{s}.outside_roi_added_pixels'])} | "
                  f"{fmt(val[f'locality.{s}.outside_roi_removed_pixels'])} | "
                  f"**{fmt(val[f'locality.{s}.outside_roi_changed_pixels'])}** |")
    md += ["", "## 4. Inside-ROI identity", "", "| side | changed pixels |", "|---|---:|"]
    for s in SIDES:
        md.append(f"| `{s}` | **{fmt(val[f'identity.{s}.inside_roi_changed_pixels'])}** |")
    md += ["", "## 5. Sleeve integrity", ""]
    for s in SIDES:
        md += [f"### `{s}`", "",
               "| result | px | prompt cov | ref IoU | lower ret | cuff ret | dominant CC | opp crossing | outside garment |",
               "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for label in ("v9", "v10", "v13"):
            md.append(f"| `{label}` | {fmt(val[f'sleeve.{label}.{s}.sleeve_pixel_count'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.prompt_coverage'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.reference_candidate_iou'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.lower_sleeve_retention_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.cuff_retention_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.dominant_component_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.opposite_side_crossing_pixels'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.assigned_outside_garment_pixels'])} |")
        md.append("")
    md += [f"left/right conflict pixels: **{fmt(val['sleeve.left_right_conflict_pixels'])}**", "",
           "## 6. Prompt preservation", "",
           "| side | outside-ROI loss | inside-ROI hits v10 | inside-ROI hits v13 |", "|---|---:|---:|---:|"]
    for s in SIDES:
        md.append(f"| `{s}` | **{fmt(val[f'preservation.{s}.outside_roi_prompt_loss_count'])}** | "
                  f"{fmt(val[f'preservation.{s}.inside_roi_prompt_hits_v10'])} | "
                  f"{fmt(val[f'preservation.{s}.inside_roi_prompt_hits_v13'])} |")
    md += ["", f"`local_path_review_required` = **{val['preservation.local_path_review_required']}**, "
           f"inside-ROI lost prompt indices: "
           + ", ".join(f"`{s}`={pres['sides'][s]['inside_roi_lost_prompt_indices']}" for s in SIDES), "",
           "## 7. Torso safety", "",
           "| result | torso px | area frac | dominant CC | placket | side chest | outside garment |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for label in ("v9", "v10", "v13"):
        md.append(f"| `{label}` | {fmt(val[f'torso.{label}.torso_pixel_count'])} | "
                  f"{fmt(val[f'torso.{label}.torso_area_fraction_of_garment'])} | "
                  f"{fmt(val[f'torso.{label}.dominant_component_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.placket_coverage_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.side_chest_preservation_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.assigned_outside_garment_pixels'])} |")
    md += [""]
    for k in ts["checks"]:
        md.append(f"- `{k}`: **{fmt(val[f'torso_check.{k}'])}**")
    md += ["", "## 8. v11 Variant B regression", "",
           "| side | field | v13 | v11 variant_b | matches |", "|---|---|---:|---:|---:|"]
    for s in SIDES:
        for f in reg["sleeves"][s]:
            r = reg["sleeves"][s][f]
            md.append(f"| `{s}` | `{f}` | {fmt(r['v13'])} | {fmt(r['v11_variant_b'])} | {fmt(r['matches'])} |")
    rt = reg["torso"]["torso_pixel_count"]
    md += [f"| torso | `torso_pixel_count` | {fmt(rt['v13'])} | {fmt(rt['v11_variant_b'])} | {fmt(rt['matches'])} |",
           "", f"`all_match` = **{fmt(val['regression.all_match'])}**", "",
           "## 9. Gates", ""]
    for k, v in ctx["gates"].items():
        md.append(f"- `{k}`: **{fmt(v)}**")
    md += ["", "## 10. Verdict", "", f"**`{ctx['verdict']}`**"]
    (out / "validation_summary.md").write_text("\n".join(md))

    def tab(head, rows):
        return ("<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")

    loc_rows = [[f"<code>{s}</code>", fmt(val[f"locality.{s}.outside_roi_added_pixels"]),
                 fmt(val[f"locality.{s}.outside_roi_removed_pixels"]),
                 f"<b>{fmt(val[f'locality.{s}.outside_roi_changed_pixels'])}</b>",
                 f"<b>{fmt(val[f'identity.{s}.inside_roi_changed_pixels'])}</b>"] for s in SIDES]
    sl_rows = [[f"<code>{s}</code>", f"<code>{label}</code>"]
               + [fmt(val[f"sleeve.{label}.{s}.{k}"]) for k in
                  ("sleeve_pixel_count", "prompt_coverage", "reference_candidate_iou",
                   "lower_sleeve_retention_fraction", "cuff_retention_fraction",
                   "dominant_component_fraction", "opposite_side_crossing_pixels",
                   "assigned_outside_garment_pixels")]
               for s in SIDES for label in ("v9", "v10", "v13")]
    to_rows = [[f"<code>{label}</code>"] + [fmt(val[f"torso.{label}.{k}"]) for k in
               ("torso_pixel_count", "torso_area_fraction_of_garment", "dominant_component_fraction",
                "placket_coverage_fraction", "side_chest_preservation_fraction",
                "assigned_outside_garment_pixels")] for label in ("v9", "v10", "v13")]
    reg_rows = [[f"<code>{s}</code>", f"<code>{f}</code>", fmt(reg["sleeves"][s][f]["v13"]),
                 fmt(reg["sleeves"][s][f]["v11_variant_b"]), fmt(reg["sleeves"][s][f]["matches"])]
                for s in SIDES for f in reg["sleeves"][s]]
    reg_rows.append(["torso", "<code>torso_pixel_count</code>", fmt(rt["v13"]), fmt(rt["v11_variant_b"]),
                     fmt(rt["matches"])])
    gate_rows = [[f"<code>{k}</code>", fmt(v)] for k, v in ctx["gates"].items()]

    html_doc = f"""<meta charset="utf-8"><title>v13 Offline Armhole Locality Merge Primitive</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:32px;max-width:1300px}}
table{{border-collapse:collapse;margin:14px 0;width:100%}}th,td{{border:1px solid #ccc;padding:6px 9px;font-size:13px}}
th{{background:#f2f2f2;text-align:left}}img{{max-width:100%;border:1px solid #ddd;margin:10px 0}}
.v{{font-size:20px;padding:12px;background:#d4edda;border:1px solid #28a745;display:inline-block}}</style>
<h1>v13 Offline Armhole Locality Merge Primitive</h1>
<p>provider_calls=<b>0</b>, sam2_inference_calls=<b>0</b>, modified_production_runtime_files=<b>[]</b>,
modified_dependencies=<b>[]</b>. Report/JSON mismatches: <b>{consistency['report_json_mismatch_count']}</b>.</p>
<h2>Locality invariant &amp; inside-ROI identity</h2>
{tab(["side", "outside added", "outside removed", "outside changed", "inside changed"], loc_rows)}
<h2>Sleeve integrity</h2>
{tab(["side", "result", "px", "prompt cov", "ref IoU", "lower ret", "cuff ret", "dom CC", "opp cross", "outside garment"], sl_rows)}
<p>left/right conflict pixels: <b>{fmt(val['sleeve.left_right_conflict_pixels'])}</b>;
outside-ROI prompt loss: <b>{fmt(val['preservation.outside_roi_prompt_loss_total'])}</b>;
local_path_review_required: <b>{val['preservation.local_path_review_required']}</b>.</p>
<h2>Torso safety</h2>
{tab(["result", "torso px", "area frac", "dom CC", "placket", "side chest", "outside garment"], to_rows)}
<h2>v11 Variant B regression</h2>
{tab(["side", "field", "v13", "v11 variant_b", "matches"], reg_rows)}
<h2>Gates</h2>{tab(["gate", "result"], gate_rows)}
<h2>Visuals</h2>
<img src="left_v9_v10_v13_overlay.png"><img src="right_v9_v10_v13_overlay.png">
<img src="outside_roi_difference_overlay.png"><img src="prompt_preservation_overlay.png">
<img src="left_roi_zoom.png"><img src="right_roi_zoom.png">
<img src="final_partition_overlay.png"><img src="v11_variant_b_vs_v13_contact_sheet.png">
<h2>Verdict</h2><p class="v"><b>{htmllib.escape(ctx['verdict'])}</b></p>
"""
    (out / "validation_report.html").write_text(html_doc)
    return consistency


def run_v14(root: Path, out: Path) -> str:  # noqa: C901 - a linear replay script
    """Offline local QC + safe fallback replay over the canonical artifacts."""
    import subprocess

    import cv2

    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    v8 = root / "diagnostic_sam2_candidate_combo_v8"
    v9d = root / "diagnostic_sam2_sleeve_first_residual_v9"
    v10d = root / "diagnostic_armhole_boundary_split_v10"
    v11d = root / "diagnostic_armhole_locality_v11"
    v13d = root / "diagnostic_armhole_locality_primitive_v13"

    required = {
        "v9_sleeve_left": v9d / "variant_a_sleeve_left_final.npy",
        "v9_sleeve_right": v9d / "variant_a_sleeve_right_final.npy",
        "v9_torso_residual": v9d / "variant_a_torso_residual_final.npy",
        "v10_sleeve_left": v10d / "v10_sleeve_left_final.npy",
        "v10_sleeve_right": v10d / "v10_sleeve_right_final.npy",
        "v10_torso": v10d / "v10_torso_final.npy",
        "v10_anchor_candidates": v10d / "anchor_candidates.json",
        "v11_armhole_roi_definition": v11d / "armhole_roi_definition.json",
        "v11_recomposition_metrics": v11d / "recomposition_metrics.json",
        "v13_sleeve_integrity_metrics": v13d / "sleeve_integrity_metrics.json",
        "v8_prompt_identity_audit": v8 / "prompt_identity_audit.json",
        "v8_sleeve_left_reference": v8 / "small_t0_sleeve_left_cand1_full_mask.npy",
        "v8_sleeve_right_reference": v8 / "small_t0_sleeve_right_cand0_full_mask.npy",
        "garment_mask": root / "garment_mask.png",
        "carrier": root / "carrier.png",
    }
    provenance = {"files": {}, "missing": []}
    for key, path in required.items():
        entry = {"path": str(path), "exists": path.exists()}
        if path.exists():
            entry["size_bytes"] = path.stat().st_size
            entry["sha256"] = _sha256(path)
            if path.suffix == ".npy":
                arr = np.load(str(path))
                entry.update({"shape_hw": [int(arr.shape[0]), int(arr.shape[1])], "dtype": str(arr.dtype),
                              "unique_values": [bool(v) for v in np.unique(arr).tolist()],
                              "nonzero_count": PIXELS(arr)})
            elif path.suffix == ".png":
                img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if img is not None:
                    entry.update({"shape_hw": [int(img.shape[0]), int(img.shape[1])], "dtype": str(img.dtype),
                                  "nonzero_count": PIXELS(img)})
        else:
            provenance["missing"].append(key)
        provenance["files"][key] = entry
    (out / "input_provenance.json").write_text(json.dumps(provenance, indent=2))
    if provenance["missing"]:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_CANONICAL_REPLAY_NOT_EXECUTED", "missing": provenance["missing"],
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "production_wiring_performed": False,
             "elapsed_seconds": round(time.time() - t0, 3), "timestamp": time.time()}, indent=2))
        return "BLOCKED_CANONICAL_REPLAY_NOT_EXECUTED"

    carrier = cv2.imread(str(required["carrier"]), cv2.IMREAD_COLOR)
    garment = cv2.imread(str(required["garment_mask"]), cv2.IMREAD_GRAYSCALE) > 0
    H, W = garment.shape[:2]
    pre = {s: np.load(str(required[f"v9_sleeve_{SHORT[s]}"])) > 0 for s in SIDES}
    con = {s: np.load(str(required[f"v10_sleeve_{SHORT[s]}"])) > 0 for s in SIDES}
    ref = {s: np.load(str(required[f"v8_sleeve_{SHORT[s]}_reference"])) > 0 for s in SIDES}
    v9_torso = np.load(str(required["v9_torso_residual"])) > 0
    v10_torso = np.load(str(required["v10_torso"])) > 0
    roi_def = json.loads(required["v11_armhole_roi_definition"].read_text())
    anchors = json.loads(required["v10_anchor_candidates"].read_text())
    prompts = json.loads(required["v8_prompt_identity_audit"].read_text())
    v13_metrics = json.loads(required["v13_sleeve_integrity_metrics"].read_text())

    manifest = {}

    def persist(name, mask):
        arr = np.asarray(mask, dtype=bool)
        npy, png = out / f"{name}.npy", out / f"{name}.png"
        np.save(str(npy), arr)
        if not cv2.imwrite(str(png), arr.astype(np.uint8) * 255):
            raise SystemExit(f"failed to write {png}")
        back, png_back = np.load(str(npy)), cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        ok = np.array_equal(back, arr) and np.array_equal(png_back > 0, arr)
        manifest[name] = {"npy": str(npy), "png": str(png), "nonzero_count": PIXELS(arr),
                          "shape_hw": [int(arr.shape[0]), int(arr.shape[1])], "dtype": str(arr.dtype),
                          "bbox_xyxy": _bbox(arr), "bbox_semantics": BBOX_SEMANTICS,
                          "npy_sha256": _sha256(npy), "png_sha256": _sha256(png),
                          "verification": "VERIFIED_RELOAD_SUCCESS" if ok else "RELOAD_MISMATCH"}
        if not ok:
            raise SystemExit(f"reload verification failed for {name}")

    # ---- coordinate contract ----------------------------------------------
    contract = {"declared_semantics": BBOX_SEMANTICS,
                "conversion_rule": "x2_exclusive = x2_inclusive + 1; y2_exclusive = y2_inclusive + 1",
                "note": "the semantics are declared, not inferred from pixel counts",
                "sides": {}, "issues": []}
    roi = {}
    for s in SIDES:
        xyxy = roi_def["sides"][s]["roi_xyxy"]
        c = roi_contract(xyxy)
        m = roi_mask_from_xyxy(xyxy, (H, W))
        roi[s] = m
        c.update({"roi_pixels_built": PIXELS(m),
                  "v11_reported_roi_pixels": int(roi_def["sides"][s]["roi_pixels"]),
                  "consistent_with_v11": bool(PIXELS(m) == roi_def["sides"][s]["roi_pixels"]),
                  "shape_matches_sleeve": bool(m.shape == pre[s].shape)})
        contract["sides"][s] = c
        if not (c["consistent_with_v11"] and c["shape_matches_sleeve"]):
            contract["issues"].append(s)
        persist(f"roi_{SHORT[s]}", m)
    contract["status"] = "COORDINATE_CONTRACT_OK" if not contract["issues"] else "COORDINATE_CONTRACT_AMBIGUOUS"
    (out / "coordinate_contract.json").write_text(json.dumps(contract, indent=2))
    if contract["issues"]:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_COORDINATE_CONTRACT_AMBIGUOUS", "issues": contract["issues"],
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "production_wiring_performed": False,
             "elapsed_seconds": round(time.time() - t0, 3), "timestamp": time.time()}, indent=2))
        return "BLOCKED_COORDINATE_CONTRACT_AMBIGUOUS"

    # ---- shared measurement bands (same definitions as v11/v13) ------------
    gx0, gy0, gx1, gy1 = _bbox(garment)
    gw = gx1 - gx0 + 1
    g_cx = int(round(0.5 * (gx0 + gx1)))
    tb = prompts["torso_front"]["box_full"]
    t_cx, t_w = 0.5 * (tb[0] + tb[2]), tb[2] - tb[0]
    center_band = np.zeros((H, W), bool)
    center_band[:, max(int(round(t_cx - 0.25 * t_w)), 0):min(int(round(t_cx + 0.25 * t_w)), W)] = True
    placket = np.zeros((H, W), bool)
    pw = int(round(0.06 * gw))
    placket[int(tb[1]):int(tb[3]) + 1, max(g_cx - pw // 2, 0):min(g_cx + pw // 2, W)] = True
    placket &= garment
    side_chest = np.zeros((H, W), bool)
    side_w = int(round(0.25 * t_w))
    side_chest[int(tb[1]):int(tb[3]) + 1, int(tb[0]):int(tb[0]) + side_w] = True
    side_chest[int(tb[1]):int(tb[3]) + 1, int(tb[2]) - side_w:int(tb[2]) + 1] = True
    side_chest &= garment
    below, cuff = {}, {}
    for s in SIDES:
        uy = anchors[s]["underarm_anchor"]["xy"][1]
        b = np.zeros((H, W), bool)
        b[uy + 1:, :] = True
        below[s] = b
        by0, by1 = _bbox(pre[s])[1], _bbox(pre[s])[3]
        c = np.zeros((H, W), bool)
        c[int(round(by0 + 0.85 * (by1 - by0))):by1 + 1, :] = True
        cuff[s] = c

    # ---- selector ----------------------------------------------------------
    def decide(side):
        return select_local_armhole_candidate(
            pre[side], con[side], roi[side], garment,
            prompts[side]["pos_pts_full"],
            anchors[side]["shoulder_anchor"]["xy"], anchors[side]["underarm_anchor"]["xy"],
            side=side, torso_center_band=center_band)

    decisions = {s: decide(s) for s in SIDES}
    (out / "left_candidate_evaluation.json").write_text(
        json.dumps(decisions["sleeve_left"].to_json(), indent=2))
    (out / "right_candidate_evaluation.json").write_text(
        json.dumps(decisions["sleeve_right"].to_json(), indent=2))

    final = {s: decisions[s].selected_mask for s in SIDES}
    for s in SIDES:
        persist(f"{SHORT[s]}_constrained_candidate", decisions[s].constrained_mask)
        persist(f"{SHORT[s]}_fallback_candidate", decisions[s].fallback_mask)
        persist(f"selected_{SHORT[s]}", final[s])

    # ---- fallback baseline validity ---------------------------------------
    fallback_validity = {"rule": ("the pre-stage fallback must itself be usable: shape, binary, garment "
                                  "containment, dominant component, no left/right conflict"), "sides": {}}
    fb = {s: decisions[s].fallback_mask for s in SIDES}
    for s in SIDES:
        dom, ncc = _dominant_component_fraction(fb[s])
        fallback_validity["sides"][s] = {
            "shape_matches": bool(fb[s].shape == garment.shape),
            "is_binary_bool": bool(fb[s].dtype == np.bool_),
            "assigned_outside_garment_pixels": PIXELS(fb[s] & ~garment),
            "dominant_component_fraction": dom, "connected_component_count": ncc,
            "meets_component_minimum": bool(dom >= COMPONENT_INTEGRITY_MIN),
            "bitwise_equals_pre_stage_and_garment": bool(np.array_equal(fb[s], pre[s] & garment))}
    fallback_conflict = PIXELS(fb["sleeve_left"] & fb["sleeve_right"])
    fallback_validity["left_right_conflict_pixels"] = fallback_conflict
    fallback_validity["all_valid"] = bool(
        all(v["shape_matches"] and v["is_binary_bool"] and v["assigned_outside_garment_pixels"] == 0
            and v["meets_component_minimum"] for v in fallback_validity["sides"].values())
        and fallback_conflict == 0)
    (out / "fallback_baseline_validity.json").write_text(json.dumps(fallback_validity, indent=2))
    if not fallback_validity["all_valid"]:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_FALLBACK_BASELINE_INVALID", "detail": fallback_validity,
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "production_wiring_performed": False,
             "elapsed_seconds": round(time.time() - t0, 3), "timestamp": time.time()}, indent=2))
        return "BLOCKED_FALLBACK_BASELINE_INVALID"

    # ---- conflict + torso --------------------------------------------------
    conflict = final["sleeve_left"] & final["sleeve_right"]
    persist("conflict", conflict)
    conflict_pixels = PIXELS(conflict)
    selector_decisions = {
        "rule": "if every constrained local hard gate passes -> constrained, else pre_stage_fallback",
        "no_scoring": "plain conjunction of gates; no weighted sum, no confidence blending",
        "sides": {s: decisions[s].to_json() for s in SIDES},
        "left_right_conflict_pixels": conflict_pixels,
    }
    (out / "selector_decisions.json").write_text(json.dumps(selector_decisions, indent=2))
    if conflict_pixels > 0:
        (out / "execution_meta.json").write_text(json.dumps(
            {"final_verdict": "BLOCKED_LEFT_RIGHT_CONFLICT", "left_right_conflict_pixels": conflict_pixels,
             "provider_calls": 0, "sam2_inference_calls": 0, "modified_dependencies": [],
             "modified_production_runtime_files": [], "production_wiring_performed": False,
             "elapsed_seconds": round(time.time() - t0, 3), "timestamp": time.time()}, indent=2))
        return "BLOCKED_LEFT_RIGHT_CONFLICT"
    final_torso = garment & ~final["sleeve_left"] & ~final["sleeve_right"]
    persist("selected_torso", final_torso)

    # ---- locality invariant on the SELECTED masks --------------------------
    locality = {"definition": "bitwise comparison outside the ROI, on the selected mask", "sides": {}}
    for s in SIDES:
        expected = pre[s] & ~roi[s] & garment
        got = final[s] & ~roi[s]
        added, removed = got & ~expected, expected & ~got
        persist(f"outside_roi_diff_{SHORT[s]}", added | removed)
        locality["sides"][s] = {"outside_roi_added_pixels": PIXELS(added),
                                "outside_roi_removed_pixels": PIXELS(removed),
                                "outside_roi_changed_pixels": PIXELS(added | removed),
                                "bitwise_equal": bool(np.array_equal(got, expected))}
    locality["all_sides_bitwise_equal"] = all(v["bitwise_equal"] for v in locality["sides"].values())
    (out / "locality_invariant_check.json").write_text(json.dumps(locality, indent=2))

    # ---- metrics -----------------------------------------------------------
    def sleeve_metrics(s, m):
        dom, ncc = _dominant_component_fraction(m)
        pts = prompts[s]["pos_pts_full"]
        inside = [i for i, (x, y) in enumerate(pts) if m[int(round(y)), int(round(x))]]
        v9b, v9c = PIXELS(pre[s] & below[s]), PIXELS(pre[s] & cuff[s])
        cols = np.arange(W)[None, :]
        opp = PIXELS(m & (cols > g_cx)) if s == "sleeve_left" else PIXELS(m & (cols < g_cx))
        R = ref[s]
        return {"sleeve_pixel_count": PIXELS(m),
                "prompt_points_inside_count": len(inside), "prompt_points_inside_indices": inside,
                "prompt_coverage": len(inside) / len(pts),
                "reference_candidate_iou": PIXELS(m & R) / PIXELS(m | R) if PIXELS(m | R) else None,
                "dominant_component_fraction": dom, "connected_component_count": ncc,
                "lower_sleeve_retention_fraction": PIXELS(m & pre[s] & below[s]) / v9b if v9b else None,
                "cuff_retention_fraction": PIXELS(m & pre[s] & cuff[s]) / v9c if v9c else None,
                "torso_center_intrusion_fraction": PIXELS(m & center_band) / PIXELS(m) if PIXELS(m) else None,
                "opposite_side_crossing_pixels": int(opp),
                "assigned_outside_garment_pixels": PIXELS(m & ~garment)}

    def torso_metrics(m):
        dom, ncc = _dominant_component_fraction(m)
        return {"torso_pixel_count": PIXELS(m),
                "torso_area_fraction_of_garment": PIXELS(m) / PIXELS(garment),
                "dominant_component_fraction": dom, "connected_component_count": ncc,
                "placket_coverage_fraction": PIXELS(m & placket) / PIXELS(placket),
                "side_chest_preservation_fraction": PIXELS(m & side_chest) / PIXELS(side_chest),
                "assigned_outside_garment_pixels": PIXELS(m & ~garment)}

    v13_sleeves = {s: {"sleeve_pixel_count": v13_metrics["results"]["v13"][s]["sleeve_pixel_count"]}
                   for s in SIDES}
    sleeve_integrity = {
        "definitions": {**METRIC_DEFINITIONS,
                        "torso_center_intrusion_definition_dependent": True,
                        "opposite_side_crossing_definition_dependent": True},
        "thresholds": {"lower_sleeve_retention_min": THRESHOLDS["lower_sleeve_retention_min"],
                       "cuff_retention_min": THRESHOLDS["cuff_retention_min"],
                       "dominant_component_fraction_min": COMPONENT_INTEGRITY_MIN,
                       "assigned_outside_garment_max": 0,
                       "opposite_side_crossing_rule": "selected <= pre_stage",
                       "torso_center_intrusion_rule": "selected <= pre_stage",
                       "prompt_coverage_rule": "selected >= pre_stage"},
        "results": {"v9_pre_stage": {s: sleeve_metrics(s, pre[s]) for s in SIDES},
                    "v10_constrained_raw": {s: sleeve_metrics(s, con[s]) for s in SIDES},
                    "v13_merged_constrained": {s: sleeve_metrics(s, decisions[s].constrained_mask) for s in SIDES},
                    "v14_selected": {s: sleeve_metrics(s, final[s]) for s in SIDES}},
        "v13_reported_pixel_counts": v13_sleeves,
        "left_right_conflict_pixels": conflict_pixels,
    }
    checks = {}
    for s in SIDES:
        sel, base = sleeve_integrity["results"]["v14_selected"][s], sleeve_integrity["results"]["v9_pre_stage"][s]
        checks[s] = {
            "lower_sleeve_retention_ge_min": bool(sel["lower_sleeve_retention_fraction"] >= THRESHOLDS["lower_sleeve_retention_min"]),
            "cuff_retention_ge_min": bool(sel["cuff_retention_fraction"] >= THRESHOLDS["cuff_retention_min"]),
            "dominant_component_fraction_ge_min": bool(sel["dominant_component_fraction"] >= COMPONENT_INTEGRITY_MIN),
            "assigned_outside_garment_zero": bool(sel["assigned_outside_garment_pixels"] == 0),
            "opposite_side_crossing_not_worse": bool(sel["opposite_side_crossing_pixels"] <= base["opposite_side_crossing_pixels"]),
            "torso_center_intrusion_not_worse": bool(sel["torso_center_intrusion_fraction"] <= base["torso_center_intrusion_fraction"] + 1e-12),
            "prompt_coverage_not_below_pre_stage": bool(sel["prompt_coverage"] >= base["prompt_coverage"] - 1e-12),
        }
        checks[s]["all_pass"] = bool(all(checks[s].values()))
    sleeve_integrity["checks"] = checks
    sleeve_integrity["all_sides_pass"] = bool(all(checks[s]["all_pass"] for s in SIDES))
    (out / "sleeve_integrity_metrics.json").write_text(json.dumps(sleeve_integrity, indent=2))

    torso_safety = {"definitions": {k: METRIC_DEFINITIONS[k] for k in ("placket_band", "side_chest",
                                                                       "torso_center_band")},
                    "results": {"v9": torso_metrics(v9_torso), "v10": torso_metrics(v10_torso),
                                "v14_selected": torso_metrics(final_torso)}}
    t14, t9 = torso_safety["results"]["v14_selected"], torso_safety["results"]["v9"]
    torso_safety["checks"] = {
        "placket_coverage_ge_v9": bool(t14["placket_coverage_fraction"] >= t9["placket_coverage_fraction"] - 1e-12),
        "side_chest_drop_le_5pp": bool((t9["side_chest_preservation_fraction"]
                                        - t14["side_chest_preservation_fraction"]) * 100.0 <= 5.0),
        "sleeve_torso_center_intrusion_not_increased_vs_v9": bool(all(
            sleeve_integrity["results"]["v14_selected"][s]["torso_center_intrusion_fraction"]
            <= sleeve_integrity["results"]["v9_pre_stage"][s]["torso_center_intrusion_fraction"] + 1e-12
            for s in SIDES)),
        "torso_assigned_outside_garment_zero": bool(t14["assigned_outside_garment_pixels"] == 0)}
    torso_safety["all_pass"] = bool(all(torso_safety["checks"].values()))
    (out / "torso_safety_metrics.json").write_text(json.dumps(torso_safety, indent=2))

    # ---- prompt preservation ----------------------------------------------
    preservation = {"rule": ("a pre-stage positive prompt must not be lost by the selection; inside-ROI "
                             "losses are tracked separately as local_path_review_required"), "sides": {}}
    for s in SIDES:
        rows, out_loss, in_loss = [], [], []
        for i, (x, y) in enumerate(prompts[s]["pos_pts_full"]):
            xi, yi = int(round(x)), int(round(y))
            in_pre, in_roi = bool(pre[s][yi, xi]), bool(roi[s][yi, xi])
            rows.append({"point_index": i, "full_image_xy": [float(x), float(y)],
                         "inside_pre_stage": in_pre, "inside_roi": in_roi,
                         "inside_v10_constrained": bool(con[s][yi, xi]),
                         "inside_v13_merged_constrained": bool(decisions[s].constrained_mask[yi, xi]),
                         "inside_v14_selected": bool(final[s][yi, xi])})
            if in_pre and not final[s][yi, xi]:
                (in_loss if in_roi else out_loss).append(i)
        preservation["sides"][s] = {
            "points": rows, "outside_roi_prompt_loss_count": len(out_loss),
            "outside_roi_lost_prompt_indices": out_loss,
            "inside_roi_lost_prompt_indices": in_loss,
            "pre_stage_prompt_coverage": sleeve_integrity["results"]["v9_pre_stage"][s]["prompt_coverage"],
            "selected_prompt_coverage": sleeve_integrity["results"]["v14_selected"][s]["prompt_coverage"],
            "selected_coverage_not_below_pre_stage": bool(
                sleeve_integrity["results"]["v14_selected"][s]["prompt_coverage"]
                >= sleeve_integrity["results"]["v9_pre_stage"][s]["prompt_coverage"] - 1e-12)}
    preservation["outside_roi_prompt_loss_total"] = sum(
        preservation["sides"][s]["outside_roi_prompt_loss_count"] for s in SIDES)
    preservation["local_path_review_required"] = bool(any(
        preservation["sides"][s]["inside_roi_lost_prompt_indices"] for s in SIDES))
    preservation["all_sides_coverage_not_below_pre_stage"] = bool(all(
        preservation["sides"][s]["selected_coverage_not_below_pre_stage"] for s in SIDES))
    (out / "prompt_preservation_check.json").write_text(json.dumps(preservation, indent=2))

    # ---- determinism -------------------------------------------------------
    determinism = {"runs": 3, "sides": {}}
    det_ok = True
    for s in SIDES:
        sigs = []
        for _ in range(3):
            d = decide(s)
            sigs.append({"selected_candidate": d.selected_candidate,
                         "rejection_reasons": list(d.rejection_reasons),
                         "mask_sha256": hashlib.sha256(np.ascontiguousarray(d.selected_mask).tobytes()).hexdigest(),
                         "local_metrics": d.local_metrics})
        same = all(json.dumps(x, sort_keys=True) == json.dumps(sigs[0], sort_keys=True) for x in sigs)
        determinism["sides"][s] = {"identical_across_runs": bool(same), "signature": sigs[0]}
        det_ok = det_ok and same
    determinism["all_deterministic"] = bool(det_ok)
    (out / "determinism_check.json").write_text(json.dumps(determinism, indent=2))

    # ---- tests -------------------------------------------------------------
    server_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [str(server_dir / ".venv/bin/python"), "-m", "pytest",
         "tests/test_armhole_locality_merge_v13.py", "-v", "--tb=short", "-p", "no:cacheprovider"],
        cwd=str(server_dir), capture_output=True, text=True, timeout=900)
    nodes = {}
    for line in proc.stdout.splitlines():
        for status in ("PASSED", "FAILED", "ERROR", "SKIPPED"):
            if f" {status}" in line and "::" in line:
                nodes[line.split("::", 1)[1].split(" ")[0]] = status
                break
    counts = {}
    for st in nodes.values():
        counts[st] = counts.get(st, 0) + 1
    canonical_nodes = {k: v for k, v in nodes.items() if k.startswith("test_i_") or k.startswith("test_j_")}
    test_results = {
        "command": "pytest tests/test_armhole_locality_merge_v13.py -v",
        "returncode": proc.returncode, "counts": counts, "nodes": nodes,
        "canonical_replay_nodes": canonical_nodes,
        "canonical_replay_executed": bool(canonical_nodes) and all(v == "PASSED" for v in canonical_nodes.values()),
        "all_passed": bool(proc.returncode == 0 and counts.get("FAILED", 0) == 0
                           and counts.get("ERROR", 0) == 0 and counts.get("SKIPPED", 0) == 0),
        "summary_tail": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "",
    }
    (out / "test_results.json").write_text(json.dumps(test_results, indent=2))

    # ---- source snapshot ---------------------------------------------------
    src_files = ["server/scripts/armhole_locality_merge_v13.py",
                 "server/tests/test_armhole_locality_merge_v13.py"]
    repo = server_dir.parent
    diff = subprocess.run(["git", "diff", "--stat", "--"] + src_files, cwd=str(repo),
                          capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain", "--"] + src_files, cwd=str(repo),
                            capture_output=True, text=True)
    source_snapshot = {
        "modified_source_files": src_files,
        "sha256": {f: _sha256(repo / f) for f in src_files},
        "function_names": ["merge_sleeve_inside_roi", "roi_contract", "roi_mask_from_xyxy",
                           "select_local_armhole_candidate", "run", "run_v14"],
        "git_diff_stat": diff.stdout.strip() or "(no tracked diff — both files are new/untracked)",
        "git_status_porcelain": status.stdout.strip().splitlines(),
        "production_runtime_files_modified": [],
        "new_files_this_step": 0,
        "modified_files_this_step": 2,
    }
    (out / "source_snapshot.json").write_text(json.dumps(source_snapshot, indent=2))
    (out / "candidate_gate_definitions.json").write_text(json.dumps(
        {"gates": LOCAL_GATE_DEFINITIONS, "gate_order": list(_GATE_ORDER),
         "selection_rule": "all gates pass -> constrained, else pre_stage_fallback",
         "candidates": {"C0_constrained": "merge_sleeve_inside_roi(pre, constrained, roi, garment)",
                        "C1_pre_stage_fallback": "merge_sleeve_inside_roi(pre, pre, roi, garment)"},
         "anchor_review_radius_px": ANCHOR_REVIEW_RADIUS_PX,
         "component_integrity_min": COMPONENT_INTEGRITY_MIN}, indent=2))

    # ---- verdict -----------------------------------------------------------
    left, right = decisions["sleeve_left"], decisions["sleeve_right"]
    gates = {
        "left_constrained_rejected_for_prompt_loss": bool(
            left.selected_candidate == SELECTED_FALLBACK
            and REASON_LOST_LOCAL_POSITIVE_PROMPT in left.rejection_reasons),
        "left_selected_fallback": bool(left.selected_candidate == SELECTED_FALLBACK),
        "right_decision_deterministic": bool(determinism["sides"]["sleeve_right"]["identical_across_runs"]),
        "selected_coverage_not_below_pre_stage": preservation["all_sides_coverage_not_below_pre_stage"],
        "outside_roi_bitwise_invariant": locality["all_sides_bitwise_equal"],
        "sleeve_integrity": sleeve_integrity["all_sides_pass"],
        "conflict_zero": bool(conflict_pixels == 0),
        "torso_safety": torso_safety["all_pass"],
        "canonical_replay_executed": test_results["canonical_replay_executed"],
        "all_tests_passed": test_results["all_passed"],
        "determinism": determinism["all_deterministic"],
    }
    if not gates["canonical_replay_executed"]:
        verdict = "BLOCKED_CANONICAL_REPLAY_NOT_EXECUTED"
    elif not gates["outside_roi_bitwise_invariant"] or not gates["selected_coverage_not_below_pre_stage"] \
            or not gates["sleeve_integrity"] or not gates["determinism"] or not gates["all_tests_passed"] \
            or not gates["left_constrained_rejected_for_prompt_loss"]:
        verdict = "BLOCKED_LOCAL_QC_INVARIANT_FAILED"
    elif not gates["torso_safety"]:
        verdict = "BLOCKED_TORSO_SAFETY_FAILED"
    else:
        verdict = "OFFLINE_LOCAL_QC_FALLBACK_VALIDATED"

    _write_v14_visuals(out, carrier, garment, pre, con, decisions, final, final_torso, roi, anchors,
                       preservation, conflict)
    consistency = _write_v14_reports(out, {
        "contract": contract, "decisions": decisions, "locality": locality,
        "sleeve_integrity": sleeve_integrity, "torso_safety": torso_safety,
        "preservation": preservation, "determinism": determinism, "tests": test_results,
        "source_snapshot": source_snapshot, "gates": gates, "verdict": verdict,
        "conflict_pixels": conflict_pixels})

    (out / "canonical_mask_manifest.json").write_text(json.dumps(
        {"outputs": manifest,
         "all_verified": all(v["verification"] == "VERIFIED_RELOAD_SUCCESS" for v in manifest.values())},
        indent=2))
    meta = {"timestamp": time.time(), "elapsed_seconds": round(time.time() - t0, 3),
            "provider_calls": 0, "sam2_inference_calls": 0,
            "modified_dependencies": [], "modified_production_runtime_files": [],
            "production_wiring_performed": False, "deployment_performed": False,
            "input_artifact_directories": [str(root), str(v8), str(v9d), str(v10d), str(v11d), str(v13d)],
            "output_directory": str(out), "persisted_mask_count": len(manifest),
            "all_masks_reload_verified": all(v["verification"] == "VERIFIED_RELOAD_SUCCESS"
                                             for v in manifest.values()),
            "report_json_mismatch_count": consistency["report_json_mismatch_count"],
            "local_path_review_required": preservation["local_path_review_required"],
            "selected_candidate": {s: decisions[s].selected_candidate for s in SIDES},
            "gates": gates, "final_verdict": verdict}
    meta["modified_files"] = sorted(str(p.relative_to(out)) for p in out.iterdir() if p.is_file())
    (out / "execution_meta.json").write_text(json.dumps(meta, indent=2))
    return verdict


def _write_v14_visuals(out, carrier, garment, pre, con, decisions, final, final_torso, roi, anchors,
                       preservation, conflict):
    import cv2
    S = 4

    def down(img):
        return cv2.resize(img, (img.shape[1] // S, img.shape[0] // S), interpolation=cv2.INTER_AREA)

    def dm(m):
        return down(m.astype(np.uint8) * 255) > 127

    def blend(img, m, color, alpha):
        o = img.copy(); ov = np.zeros_like(img); ov[m] = color
        o[m] = (img[m] * (1 - alpha) + ov[m] * alpha).astype(np.uint8)
        return o

    def label(img, items, title=None, note=None):
        if title:
            cv2.rectangle(img, (0, 0), (img.shape[1], 46), (0, 0, 0), -1)
            cv2.putText(img, title, (14, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        for i, (t, c) in enumerate(items):
            y = 60 + i * 32
            cv2.rectangle(img, (18, y - 22), (52, y + 4), c, -1)
            for col, th in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                cv2.putText(img, t, (60, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, th)
        if note:
            for col, th in (((0, 0, 0), 5), ((0, 220, 255), 2)):
                cv2.putText(img, note, (18, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.62, col, th)
        return img

    base = down(carrier)
    C_CON, C_FB, C_SEL, C_ROI = (0, 0, 235), (255, 180, 0), (0, 210, 0), (0, 255, 255)
    for s in SIDES:
        d = decisions[s]
        img = blend(base.copy(), dm(d.fallback_mask), C_FB, 0.35)
        img = blend(img, dm(d.constrained_mask), C_CON, 0.45)
        cont, _ = cv2.findContours(dm(roi[s]).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cont, -1, C_ROI, 3)
        label(img, [("candidate C1 pre_stage_fallback", C_FB), ("candidate C0 constrained", C_CON),
                    ("armhole ROI", C_ROI)],
              title=f"{s}: candidates (SELECTED = {d.selected_candidate})",
              note=f"rejection_reasons = {d.rejection_reasons or 'none'}")
        cv2.imwrite(str(out / f"{SHORT[s]}_candidate_comparison.png"), img)

        img = blend(base.copy(), dm(d.constrained_mask), C_CON, 0.35)
        cv2.drawContours(img, cont, -1, C_ROI, 3)
        ga = d.gate_results["A_local_positive_prompt_preservation"]
        for r in preservation["sides"][s]["points"]:
            if r["point_index"] in ga["lost_prompt_indices"]:
                x, y = int(r["full_image_xy"][0] / S), int(r["full_image_xy"][1] / S)
                cv2.circle(img, (x, y), 16, (0, 0, 0), -1)
                cv2.circle(img, (x, y), 12, (0, 0, 255), -1)
                for col, th in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                    cv2.putText(img, f"lost #{r['point_index']}", (x + 18, y + 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, th)
        for name, a in d.gate_results["B_anchor_preservation"]["anchors"].items():
            if a.get("applicable") and "xy" in a:
                ax, ay = a["xy"][0] // S, a["xy"][1] // S
                col = (0, 255, 0) if a.get("status") == "PASS" else (0, 165, 255)
                cv2.drawMarker(img, (ax, ay), col, cv2.MARKER_CROSS, 26, 3)
        label(img, [("constrained candidate", C_CON), ("lost inside-ROI prompt", (0, 0, 255)),
                    ("armhole ROI", C_ROI)],
              title=f"{s}: rejection evidence",
              note=(f"gate A: pre_stage hits={ga['pre_stage_inside_roi_prompt_hits']} "
                    f"constrained hits={ga['constrained_inside_roi_prompt_hits']} "
                    f"lost={ga['lost_prompt_indices']}"))
        cv2.imwrite(str(out / f"{SHORT[s]}_rejection_reason_overlay.png"), img)

    img = blend(base.copy(), dm(final_torso), (0, 0, 220), 0.40)
    img = blend(img, dm(final["sleeve_left"]), (0, 220, 0), 0.50)
    img = blend(img, dm(final["sleeve_right"]), (255, 160, 0), 0.50)
    label(img, [("selected torso", (0, 0, 220)), ("selected left sleeve", (0, 220, 0)),
                ("selected right sleeve", (255, 160, 0))],
          title="v14 SELECTED partition",
          note=(f"left={decisions['sleeve_left'].selected_candidate}  "
                f"right={decisions['sleeve_right'].selected_candidate}  conflict={PIXELS(conflict)}"))
    cv2.imwrite(str(out / "selected_partition_overlay.png"), img)

    img = base.copy()
    for s in SIDES:
        img = blend(img, dm(final[s]), C_SEL, 0.32)
        cont, _ = cv2.findContours(dm(roi[s]).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cont, -1, C_ROI, 3)
        for r in preservation["sides"][s]["points"]:
            x, y = int(r["full_image_xy"][0] / S), int(r["full_image_xy"][1] / S)
            if r["inside_v14_selected"]:
                col = (0, 255, 0)
            elif r["inside_pre_stage"]:
                col = (0, 165, 255) if r["inside_roi"] else (0, 0, 255)
            else:
                col = (140, 140, 140)
            cv2.circle(img, (x, y), 11, (0, 0, 0), -1)
            cv2.circle(img, (x, y), 8, col, -1)
            for c2, th in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                cv2.putText(img, str(r["point_index"]), (x + 13, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, c2, th)
    label(img, [("kept in v14 selection", (0, 255, 0)), ("lost inside ROI", (0, 165, 255)),
                ("lost outside ROI", (0, 0, 255)), ("not in pre-stage", (140, 140, 140))],
          title="v14 prompt preservation",
          note=f"outside-ROI prompt loss = {preservation['outside_roi_prompt_loss_total']}")
    cv2.imwrite(str(out / "prompt_preservation_overlay.png"), img)

    PWD, PHT = 430, 660

    def panel(ml, mr, title, subtitle=""):
        c = np.zeros((PHT, PWD, 3), np.uint8)
        c[..., 1] = cv2.resize(ml.astype(np.uint8) * 255, (PWD, PHT), interpolation=cv2.INTER_AREA)
        c[..., 2] = cv2.resize(mr.astype(np.uint8) * 255, (PWD, PHT), interpolation=cv2.INTER_AREA)
        cv2.rectangle(c, (0, 0), (PWD - 1, 50), (0, 0, 0), -1)
        cv2.putText(c, title, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.putText(c, subtitle, (8, 41), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 220, 255), 1)
        cv2.rectangle(c, (0, 0), (PWD - 1, PHT - 1), (90, 90, 90), 2)
        return c

    sheet = np.hstack([
        panel(pre["sleeve_left"], pre["sleeve_right"], "v9 pre-stage",
              f"L={PIXELS(pre['sleeve_left']):,} R={PIXELS(pre['sleeve_right']):,}"),
        panel(con["sleeve_left"], con["sleeve_right"], "v10 constrained raw",
              f"L={PIXELS(con['sleeve_left']):,} R={PIXELS(con['sleeve_right']):,}"),
        panel(decisions["sleeve_left"].constrained_mask, decisions["sleeve_right"].constrained_mask,
              "v13 merged constrained",
              f"L={PIXELS(decisions['sleeve_left'].constrained_mask):,} "
              f"R={PIXELS(decisions['sleeve_right'].constrained_mask):,}"),
        panel(final["sleeve_left"], final["sleeve_right"], "v14 SELECTED",
              f"L={decisions['sleeve_left'].selected_candidate} R={decisions['sleeve_right'].selected_candidate}")])
    cv2.imwrite(str(out / "v9_v10_v13_v14_contact_sheet.png"), sheet)


def _write_v14_reports(out: Path, ctx: dict) -> dict:
    fields = []

    def F(name, value, src, path):
        fields.append({"report_field": name, "report_value": value, "source_file": src, "source_json_path": path})

    dec, loc = ctx["decisions"], ctx["locality"]
    si, ts, pres = ctx["sleeve_integrity"], ctx["torso_safety"], ctx["preservation"]
    det, tst, snap = ctx["determinism"], ctx["tests"], ctx["source_snapshot"]
    side_file = {"sleeve_left": "left_candidate_evaluation.json", "sleeve_right": "right_candidate_evaluation.json"}
    for s in SIDES:
        F(f"decision.{s}.selected_candidate", dec[s].selected_candidate, side_file[s], "selected_candidate")
        F(f"decision.{s}.fallback_required", dec[s].fallback_required, side_file[s], "fallback_required")
        F(f"decision.{s}.rejection_reasons", list(dec[s].rejection_reasons), side_file[s], "rejection_reasons")
        for g in _GATE_ORDER:
            F(f"gate.{s}.{g}.pass", dec[s].gate_results[g]["pass"], side_file[s], f"gate_results.{g}.pass")
        ga = dec[s].gate_results["A_local_positive_prompt_preservation"]
        F(f"gate.{s}.A.pre_stage_inside_roi_prompt_hits", ga["pre_stage_inside_roi_prompt_hits"],
          side_file[s], "gate_results.A_local_positive_prompt_preservation.pre_stage_inside_roi_prompt_hits")
        F(f"gate.{s}.A.constrained_inside_roi_prompt_hits", ga["constrained_inside_roi_prompt_hits"],
          side_file[s], "gate_results.A_local_positive_prompt_preservation.constrained_inside_roi_prompt_hits")
        F(f"gate.{s}.A.lost_prompt_indices", ga["lost_prompt_indices"],
          side_file[s], "gate_results.A_local_positive_prompt_preservation.lost_prompt_indices")
        F(f"gate.{s}.B.status", dec[s].gate_results["B_anchor_preservation"]["status"],
          side_file[s], "gate_results.B_anchor_preservation.status")
        for k in ("outside_roi_added_pixels", "outside_roi_removed_pixels", "outside_roi_changed_pixels"):
            F(f"locality.{s}.{k}", loc["sides"][s][k], "locality_invariant_check.json", f"sides.{s}.{k}")
        for label in ("v9_pre_stage", "v10_constrained_raw", "v13_merged_constrained", "v14_selected"):
            for k in ("sleeve_pixel_count", "prompt_coverage", "reference_candidate_iou",
                      "lower_sleeve_retention_fraction", "cuff_retention_fraction",
                      "dominant_component_fraction", "opposite_side_crossing_pixels",
                      "assigned_outside_garment_pixels", "torso_center_intrusion_fraction"):
                F(f"sleeve.{label}.{s}.{k}", si["results"][label][s][k],
                  "sleeve_integrity_metrics.json", f"results.{label}.{s}.{k}")
        for k, v in si["checks"][s].items():
            F(f"sleeve_check.{s}.{k}", v, "sleeve_integrity_metrics.json", f"checks.{s}.{k}")
        F(f"preservation.{s}.outside_roi_prompt_loss_count", pres["sides"][s]["outside_roi_prompt_loss_count"],
          "prompt_preservation_check.json", f"sides.{s}.outside_roi_prompt_loss_count")
        F(f"preservation.{s}.inside_roi_lost_prompt_indices", pres["sides"][s]["inside_roi_lost_prompt_indices"],
          "prompt_preservation_check.json", f"sides.{s}.inside_roi_lost_prompt_indices")
        F(f"preservation.{s}.pre_stage_prompt_coverage", pres["sides"][s]["pre_stage_prompt_coverage"],
          "prompt_preservation_check.json", f"sides.{s}.pre_stage_prompt_coverage")
        F(f"preservation.{s}.selected_prompt_coverage", pres["sides"][s]["selected_prompt_coverage"],
          "prompt_preservation_check.json", f"sides.{s}.selected_prompt_coverage")
        F(f"contract.{s}.bbox_semantics", ctx["contract"]["sides"][s]["bbox_semantics"],
          "coordinate_contract.json", f"sides.{s}.bbox_semantics")
        F(f"contract.{s}.slice_xyxy_half_open", ctx["contract"]["sides"][s]["slice_xyxy_half_open"],
          "coordinate_contract.json", f"sides.{s}.slice_xyxy_half_open")
        F(f"contract.{s}.consistent_with_v11", ctx["contract"]["sides"][s]["consistent_with_v11"],
          "coordinate_contract.json", f"sides.{s}.consistent_with_v11")
        F(f"determinism.{s}.identical_across_runs", det["sides"][s]["identical_across_runs"],
          "determinism_check.json", f"sides.{s}.identical_across_runs")
    for label in ("v9", "v10", "v14_selected"):
        for k in ("torso_pixel_count", "torso_area_fraction_of_garment", "dominant_component_fraction",
                  "placket_coverage_fraction", "side_chest_preservation_fraction",
                  "assigned_outside_garment_pixels"):
            F(f"torso.{label}.{k}", ts["results"][label][k], "torso_safety_metrics.json", f"results.{label}.{k}")
    for k, v in ts["checks"].items():
        F(f"torso_check.{k}", v, "torso_safety_metrics.json", f"checks.{k}")
    F("sleeve.left_right_conflict_pixels", si["left_right_conflict_pixels"],
      "sleeve_integrity_metrics.json", "left_right_conflict_pixels")
    F("preservation.outside_roi_prompt_loss_total", pres["outside_roi_prompt_loss_total"],
      "prompt_preservation_check.json", "outside_roi_prompt_loss_total")
    F("preservation.local_path_review_required", pres["local_path_review_required"],
      "prompt_preservation_check.json", "local_path_review_required")
    F("tests.returncode", tst["returncode"], "test_results.json", "returncode")
    F("tests.all_passed", tst["all_passed"], "test_results.json", "all_passed")
    F("tests.canonical_replay_executed", tst["canonical_replay_executed"],
      "test_results.json", "canonical_replay_executed")
    F("determinism.all_deterministic", det["all_deterministic"], "determinism_check.json", "all_deterministic")
    F("source.git_diff_stat", snap["git_diff_stat"], "source_snapshot.json", "git_diff_stat")
    F("source.production_runtime_files_modified", snap["production_runtime_files_modified"],
      "source_snapshot.json", "production_runtime_files_modified")
    (out / "report_source_mapping.json").write_text(json.dumps(fields, indent=2))

    def dig(o, p):
        cur = o
        for part in p.split("."):
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return cur

    mismatches = []
    for f in fields:
        src = json.loads((out / f["source_file"]).read_text())
        try:
            sv = dig(src, f["source_json_path"])
        except Exception as exc:  # noqa: BLE001
            mismatches.append({**f, "error": str(exc)})
            continue
        ok = (sv == f["report_value"]) or (
            isinstance(sv, (int, float)) and not isinstance(sv, bool)
            and isinstance(f["report_value"], (int, float)) and not isinstance(f["report_value"], bool)
            and abs(float(sv) - float(f["report_value"])) < 1e-12)
        f["source_value"] = sv
        f["matches"] = bool(ok)
        if not ok:
            mismatches.append({**f, "source_value": sv})
    (out / "report_source_mapping.json").write_text(json.dumps(fields, indent=2))
    consistency = {"total_report_fields_audited": len(fields), "report_json_mismatch_count": len(mismatches),
                   "mismatches": mismatches,
                   "status": "CONSISTENCY_CHECK_PASSED" if not mismatches else "CONSISTENCY_CHECK_FAILED"}
    (out / "report_consistency_check.json").write_text(json.dumps(consistency, indent=2))

    val = {f["report_field"]: f["report_value"] for f in fields}

    def fmt(v, nd=4):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "PASS" if v else "FAIL"
        if isinstance(v, float):
            return f"{v:.{nd}f}"
        if isinstance(v, int):
            return f"{v:,}"
        if isinstance(v, list):
            return "—" if not v else ", ".join(f"`{x}`" for x in v)
        return str(v)

    labels = ("v9_pre_stage", "v10_constrained_raw", "v13_merged_constrained", "v14_selected")
    md = ["# v14 Offline Armhole Local QC & Safe Fallback Selector", "",
          "All numbers are rendered from the JSON files in this directory "
          "(`report_source_mapping.json` maps each field to its source).", "",
          "## 1. Execution", "- provider_calls: `0`", "- sam2_inference_calls: `0`",
          "- modified_production_runtime_files: `[]`", "- production_wiring_performed: `false`",
          f"- report_json_mismatch_count: `{consistency['report_json_mismatch_count']}`", "",
          "## 2. Coordinate contract", "",
          "| side | semantics | bbox (inclusive) | half-open slice | consistent with v11 |",
          "|---|---|---|---|---|"]
    for s in SIDES:
        c = ctx["contract"]["sides"][s]
        md.append(f"| `{s}` | `{c['bbox_semantics']}` | `{c['bbox_xyxy']}` | "
                  f"`{c['slice_xyxy_half_open']}` | {fmt(c['consistent_with_v11'])} |")
    md += ["", "## 3. Selector decisions", "",
           "| side | selected | fallback_required | rejection_reasons |", "|---|---|---|---|"]
    for s in SIDES:
        md.append(f"| `{s}` | **`{val[f'decision.{s}.selected_candidate']}`** | "
                  f"{val[f'decision.{s}.fallback_required']} | {fmt(val[f'decision.{s}.rejection_reasons'])} |")
    md += ["", "### Gate results", "",
           "| side | " + " | ".join(f"`{g.split('_')[0]}`" for g in _GATE_ORDER) + " |",
           "|---|" + "---|" * len(_GATE_ORDER)]
    for s in SIDES:
        md.append(f"| `{s}` | " + " | ".join(fmt(val[f"gate.{s}.{g}.pass"]) for g in _GATE_ORDER) + " |")
    md += ["", "Gate A detail:"]
    for s in SIDES:
        md.append(f"- `{s}`: pre-stage inside-ROI hits = "
                  f"{fmt(val[f'gate.{s}.A.pre_stage_inside_roi_prompt_hits'])}, "
                  f"constrained inside-ROI hits = {fmt(val[f'gate.{s}.A.constrained_inside_roi_prompt_hits'])}, "
                  f"lost = {fmt(val[f'gate.{s}.A.lost_prompt_indices'])}; "
                  f"Gate B status = `{val[f'gate.{s}.B.status']}`")
    md += ["", "## 4. Outside-ROI locality invariant (selected masks)", "",
           "| side | added | removed | changed |", "|---|---:|---:|---:|"]
    for s in SIDES:
        md.append(f"| `{s}` | {fmt(val[f'locality.{s}.outside_roi_added_pixels'])} | "
                  f"{fmt(val[f'locality.{s}.outside_roi_removed_pixels'])} | "
                  f"**{fmt(val[f'locality.{s}.outside_roi_changed_pixels'])}** |")
    md += ["", "## 5. Sleeve integrity", ""]
    for s in SIDES:
        md += [f"### `{s}`", "",
               "| result | px | prompt cov | ref IoU | lower ret | cuff ret | dominant CC | opp cross | "
               "center intr | outside garment |",
               "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for label in labels:
            md.append(f"| `{label}` | {fmt(val[f'sleeve.{label}.{s}.sleeve_pixel_count'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.prompt_coverage'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.reference_candidate_iou'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.lower_sleeve_retention_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.cuff_retention_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.dominant_component_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.opposite_side_crossing_pixels'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.torso_center_intrusion_fraction'])} | "
                      f"{fmt(val[f'sleeve.{label}.{s}.assigned_outside_garment_pixels'])} |")
        md.append("")
        for k in si["checks"][s]:
            md.append(f"- `{k}`: **{fmt(val[f'sleeve_check.{s}.{k}'])}**")
        md.append("")
    md += [f"left/right conflict pixels: **{fmt(val['sleeve.left_right_conflict_pixels'])}**", "",
           "## 6. Prompt preservation", "",
           "| side | pre-stage coverage | selected coverage | outside-ROI loss | inside-ROI lost idx |",
           "|---|---:|---:|---:|---|"]
    for s in SIDES:
        md.append(f"| `{s}` | {fmt(val[f'preservation.{s}.pre_stage_prompt_coverage'])} | "
                  f"{fmt(val[f'preservation.{s}.selected_prompt_coverage'])} | "
                  f"**{fmt(val[f'preservation.{s}.outside_roi_prompt_loss_count'])}** | "
                  f"{fmt(val[f'preservation.{s}.inside_roi_lost_prompt_indices'])} |")
    md += ["", f"`local_path_review_required` = **{val['preservation.local_path_review_required']}**", "",
           "## 7. Torso safety", "",
           "| result | torso px | area frac | dominant CC | placket | side chest | outside garment |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for label in ("v9", "v10", "v14_selected"):
        md.append(f"| `{label}` | {fmt(val[f'torso.{label}.torso_pixel_count'])} | "
                  f"{fmt(val[f'torso.{label}.torso_area_fraction_of_garment'])} | "
                  f"{fmt(val[f'torso.{label}.dominant_component_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.placket_coverage_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.side_chest_preservation_fraction'])} | "
                  f"{fmt(val[f'torso.{label}.assigned_outside_garment_pixels'])} |")
    md.append("")
    for k in ts["checks"]:
        md.append(f"- `{k}`: **{fmt(val[f'torso_check.{k}'])}**")
    md += ["", "## 8. Determinism & tests", ""]
    for s in SIDES:
        md.append(f"- `{s}` identical across 3 runs: **{fmt(val[f'determinism.{s}.identical_across_runs'])}**")
    md += [f"- all_deterministic: **{fmt(val['determinism.all_deterministic'])}**",
           f"- pytest returncode: `{val['tests.returncode']}`, all_passed: "
           f"**{fmt(val['tests.all_passed'])}**, canonical_replay_executed: "
           f"**{fmt(val['tests.canonical_replay_executed'])}**",
           f"- node counts: `{tst['counts']}`", "",
           "## 9. Source snapshot", "",
           f"- modified source files: {', '.join('`' + f + '`' for f in snap['modified_source_files'])}",
           f"- production runtime files modified: `{snap['production_runtime_files_modified']}`",
           f"- git diff stat: `{snap['git_diff_stat']}`", "",
           "## 10. Gates", ""]
    for k, v in ctx["gates"].items():
        md.append(f"- `{k}`: **{fmt(v)}**")
    md += ["", "## 11. Verdict", "", f"**`{ctx['verdict']}`**"]
    (out / "validation_summary.md").write_text("\n".join(md))

    def tab(head, rows):
        return ("<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")

    dec_rows = [[f"<code>{s}</code>", f"<b>{val[f'decision.{s}.selected_candidate']}</b>",
                 str(val[f"decision.{s}.fallback_required"]), fmt(val[f"decision.{s}.rejection_reasons"])]
                for s in SIDES]
    gate_rows = [[f"<code>{s}</code>"] + [fmt(val[f"gate.{s}.{g}.pass"]) for g in _GATE_ORDER] for s in SIDES]
    loc_rows = [[f"<code>{s}</code>", fmt(val[f"locality.{s}.outside_roi_added_pixels"]),
                 fmt(val[f"locality.{s}.outside_roi_removed_pixels"]),
                 f"<b>{fmt(val[f'locality.{s}.outside_roi_changed_pixels'])}</b>"] for s in SIDES]
    sl_rows = [[f"<code>{s}</code>", f"<code>{label}</code>"]
               + [fmt(val[f"sleeve.{label}.{s}.{k}"]) for k in
                  ("sleeve_pixel_count", "prompt_coverage", "reference_candidate_iou",
                   "lower_sleeve_retention_fraction", "cuff_retention_fraction",
                   "dominant_component_fraction", "opposite_side_crossing_pixels",
                   "torso_center_intrusion_fraction", "assigned_outside_garment_pixels")]
               for s in SIDES for label in labels]
    to_rows = [[f"<code>{label}</code>"] + [fmt(val[f"torso.{label}.{k}"]) for k in
               ("torso_pixel_count", "torso_area_fraction_of_garment", "dominant_component_fraction",
                "placket_coverage_fraction", "side_chest_preservation_fraction",
                "assigned_outside_garment_pixels")] for label in ("v9", "v10", "v14_selected")]
    pres_rows = [[f"<code>{s}</code>", fmt(val[f"preservation.{s}.pre_stage_prompt_coverage"]),
                  fmt(val[f"preservation.{s}.selected_prompt_coverage"]),
                  f"<b>{fmt(val[f'preservation.{s}.outside_roi_prompt_loss_count'])}</b>",
                  fmt(val[f"preservation.{s}.inside_roi_lost_prompt_indices"])] for s in SIDES]
    gates_rows = [[f"<code>{k}</code>", fmt(v)] for k, v in ctx["gates"].items()]
    ok = ctx["verdict"] == "OFFLINE_LOCAL_QC_FALLBACK_VALIDATED"
    html_doc = f"""<meta charset="utf-8"><title>v14 Armhole Local QC &amp; Safe Fallback</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:32px;max-width:1350px}}
table{{border-collapse:collapse;margin:14px 0;width:100%}}th,td{{border:1px solid #ccc;padding:6px 9px;font-size:13px}}
th{{background:#f2f2f2;text-align:left}}img{{max-width:100%;border:1px solid #ddd;margin:10px 0}}
.v{{font-size:20px;padding:12px;background:{'#d4edda' if ok else '#f8d7da'};
border:1px solid {'#28a745' if ok else '#dc3545'};display:inline-block}}</style>
<h1>v14 Offline Armhole Local QC &amp; Safe Fallback Selector</h1>
<p>provider_calls=<b>0</b>, sam2_inference_calls=<b>0</b>, modified_production_runtime_files=<b>[]</b>,
production_wiring_performed=<b>false</b>. Report/JSON mismatches:
<b>{consistency['report_json_mismatch_count']}</b>.</p>
<h2>Selector decisions</h2>{tab(["side", "selected", "fallback_required", "rejection_reasons"], dec_rows)}
<h2>Gates</h2>{tab(["side"] + [f"<code>{g}</code>" for g in _GATE_ORDER], gate_rows)}
<h2>Outside-ROI locality invariant (selected)</h2>
{tab(["side", "added", "removed", "changed"], loc_rows)}
<h2>Sleeve integrity</h2>
{tab(["side", "result", "px", "prompt cov", "ref IoU", "lower ret", "cuff ret", "dom CC", "opp cross",
      "center intr", "outside garment"], sl_rows)}
<h2>Prompt preservation</h2>
{tab(["side", "pre-stage cov", "selected cov", "outside-ROI loss", "inside-ROI lost idx"], pres_rows)}
<p>local_path_review_required: <b>{val['preservation.local_path_review_required']}</b></p>
<h2>Torso safety</h2>
{tab(["result", "torso px", "area frac", "dom CC", "placket", "side chest", "outside garment"], to_rows)}
<h2>Gates summary</h2>{tab(["gate", "result"], gates_rows)}
<h2>Visuals</h2>
<img src="left_candidate_comparison.png"><img src="right_candidate_comparison.png">
<img src="left_rejection_reason_overlay.png"><img src="right_rejection_reason_overlay.png">
<img src="selected_partition_overlay.png"><img src="prompt_preservation_overlay.png">
<img src="v9_v10_v13_v14_contact_sheet.png">
<h2>Verdict</h2><p class="v"><b>{htmllib.escape(ctx['verdict'])}</b></p>
"""
    (out / "validation_report.html").write_text(html_doc)
    return consistency


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / (
    "ab_out/frame_lock/stripe-projection-protected-v1/artifacts")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="artifacts root directory")
    ap.add_argument("--out", default=None, help="output directory (new, never an existing artifact dir)")
    ap.add_argument("--mode", choices=("v13", "v14"), default="v13",
                    help="v13 = locality merge replay; v14 = local QC + safe fallback replay")
    args = ap.parse_args()
    root = Path(args.root)
    if args.mode == "v14":
        out = Path(args.out) if args.out else root / "diagnostic_armhole_local_qc_fallback_v14"
        verdict = run_v14(root, out)
    else:
        out = Path(args.out) if args.out else root / "diagnostic_armhole_locality_primitive_v13"
        verdict = run(root, out)
    print("VERDICT:", verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
