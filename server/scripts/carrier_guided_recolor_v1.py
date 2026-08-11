#!/usr/bin/env python3
"""Offline prototype: carrier_guided_recolor_v1

Constraints: offline only, provider_calls == 0, no production changes.

Minimal algorithm (torso-only, vertical stripes):
- Use extracted StripeModel (source) and PanelMap (carrier) from existing code.
- For each torso column, compute median Lab color (over rows) from carrier.
- Assign each column to nearest source palette color (color_sequence_lab) by ΔE.
- Smooth column labels with median filter to form bands (preserve local geometry).
- For high-confidence columns, replace chroma (a/b) with source palette; keep carrier L low-frequency shading.
- For medium confidence, blend carrier a/b with source palette.
- For low confidence, preserve carrier pixels.

Outputs (diagnostic_phase3): recolored composite, masks, overlays, metrics.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import argparse

import cv2
import numpy as np

# add repo path for imports
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.hybrid_composite import panel_map as pm_mod, stripe_model as sm_mod
from app.services.hybrid_composite import warp_composite as wc_mod
from app.services.hybrid_composite import deterministic_qc as qc_mod
from app.services.hybrid_composite.color import bgr_to_lab, lab_to_bgr, ciede2000
from app.services.hybrid_composite.types import CompositeFailure


def smooth_labels(labels, k=9):
    # median filter along 1D labels
    pad = k // 2
    ext = np.pad(labels, pad, mode='edge')
    out = np.empty_like(labels)
    for i in range(len(labels)):
        out[i] = int(np.median(ext[i:i + k]))
    return out


def run(dataset_dir: Path, out_dir: Path):
    art = dataset_dir / 'artifacts'
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load inputs
    carrier_p = art / 'carrier.png'
    source_p = art / 'source_front.png'
    geo_p = art / 'geometry.json'
    if not (carrier_p.exists() and source_p.exists() and geo_p.exists()):
        raise SystemExit('Missing required artifacts in dataset artifacts/')

    carrier = cv2.imread(str(carrier_p), cv2.IMREAD_COLOR)
    source = cv2.imread(str(source_p), cv2.IMREAD_COLOR)
    geo = json.loads(geo_p.read_text())

    # Build panel map (reconstruct landmarks if necessary similar to replay script)
    if not geo.get('carrier_landmarks'):
        mask = cv2.imread(str(art / 'garment_mask.png'), cv2.IMREAD_GRAYSCALE)
        ys, xs = np.nonzero(mask)
        y0, y1 = int(ys.min()), int(ys.max())
        span = max(1, y1 - y0)
        def row_extent(y):
            row = np.nonzero(mask[min(max(y, 0), mask.shape[0] - 1)])[0]
            if not len(row):
                return 0.4, 0.6
            return float(row.min()) / mask.shape[1], float(row.max()) / mask.shape[1]
        sl, sr = row_extent(y0 + int(span * 0.06))
        hl, hr = row_extent(y1 - int(span * 0.04))
        top = (y0 + mask.shape[0] * 0.02) / mask.shape[0]
        bot = (y1 - mask.shape[0] * 0.03) / mask.shape[0]
        mid_l, mid_r = row_extent((y0 + y1) // 2)
        landmarks = {
            'shoulder_l': [sl, top], 'shoulder_r': [sr, top],
            'hem_l': [hl, bot], 'hem_r': [hr, bot],
            'sleeve_l_end': [mid_l, (y0 + span * 0.55) / mask.shape[0]],
            'sleeve_r_end': [mid_r, (y0 + span * 0.55) / mask.shape[0]]
        }
    else:
        landmarks = geo['carrier_landmarks']

    pm = pm_mod.build_panel_map(carrier, landmarks, strategy='auto')
    if isinstance(pm, CompositeFailure):
        raise SystemExit('panel_map build failed: ' + pm.reason)

    # Extract stripe model from source ROI (use provided ROI)
    roi = geo.get('stripe_model', {}).get('source_roi')
    if not roi:
        raise SystemExit('source_roi missing in geometry.json')
    x0, y0, x1, y1 = roi
    model = sm_mod.extract_stripe_model_scan(
        source[y0:y1, x0:x1], source_asset_id='replay',
        source_sha256=geo.get('stripe_model', {}).get('source_sha256', '0'),
        source_roi=tuple(roi))
    if isinstance(model, CompositeFailure):
        raise SystemExit('stripe model extraction failed: ' + model.reason)

    # Focus torso panel only
    torso = next((p for p in pm.panels if p.name == 'torso'), None)
    if torso is None:
        raise SystemExit('no torso panel')

    # Prepare masks
    garment_mask = pm.garment_mask > 0
    protected_mask = pm.protected > 0  # protected area inside garment

    # exclude protected regions and component boxes
    comp_boxes = geo.get('carrier_component_boxes_norm') or {}
    comp_mask = np.zeros(garment_mask.shape, np.uint8)
    h, w = carrier.shape[:2]
    for name, box in comp_boxes.items():
        quad = np.array([[float(x) * w, float(y) * h] for x, y in box], np.float32)
        cv2.fillPoly(comp_mask, [quad.astype(np.int32)], 255)
    comp_mask = comp_mask > 0

    torso_mask = np.zeros(garment_mask.shape, bool)
    cv2.fillPoly(torso_mask, [torso.quad.astype(np.int32)], 255)
    # core: torso area excluding protected/component boxes
    core_mask = torso_mask & (~protected_mask) & (~comp_mask) & garment_mask
    if not core_mask.any():
        raise SystemExit('No torso core to recolor')

    # Convert carrier to Lab float
    carrier_lab = bgr_to_lab(carrier).astype(np.float32)

    # Prepare source palette (Lab) from model.color_sequence_lab
    palette = np.asarray(model.color_sequence_lab, np.float32)
    n_colors = len(palette)

    # Compute per-column median Lab over core rows
    ys, xs = np.nonzero(core_mask)
    # For vertical stripes, repetition axis is width -> compute per-x medians
    col_medians = np.full((w, 3), np.nan, np.float32)
    for x in range(w):
        col_sel = core_mask[:, x]
        if not col_sel.any():
            continue
        vals = carrier_lab[col_sel, x, :]
        col_medians[x] = np.median(vals, axis=0)

    # For each column, compute ΔE to each palette color (use a/b + L? use full Lab)
    col_labels = np.full(w, -1, dtype=int)
    col_conf = np.full(w, 0.0, dtype=np.float32)
    for x in range(w):
        if np.isnan(col_medians[x, 0]):
            continue
        de = ciede2000(palette, np.tile(col_medians[x][None, :], (n_colors, 1)))
        # choose the color index with min ΔE
        best = int(np.argmin(de))
        best_val = float(de[best])
        # confidence: difference to second best
        sorted_idx = np.argsort(de)
        second = float(de[sorted_idx[1]]) if len(sorted_idx) > 1 else best_val + 100.0
        conf = max(0.0, second - best_val)
        col_labels[x] = best
        col_conf[x] = conf

    # Normalize confidence and threshold into high/medium/low
    # Use heuristic thresholds (experimental only)
    # high: conf >= 6.0 ; medium: 2.5 <= conf < 6.0 ; low: conf < 2.5
    conf_norm = col_conf  # raw deltaE difference
    high_mask = conf_norm >= 6.0
    med_mask = (conf_norm >= 2.5) & (conf_norm < 6.0)
    low_mask = conf_norm < 2.5

    # Smooth labels to form contiguous bands
    labels_smoothed = smooth_labels(col_labels, k=31)

    # Apply confidence masks to smoothed labels (if low confidence, set to -1 preserved)
    final_labels = np.where(low_mask, -1, labels_smoothed)

    # Build recolored Lab image: keep L low-frequency (blur), replace a/b per label
    out_lab = carrier_lab.copy()
    # Low-frequency L: Gaussian blur with sigma related to target_period_px or image short side
    short_side = float(min(h, w))
    sigma = max(1.0, short_side * 0.03)
    blur_L = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma)
    # Use blur_L as L channel baseline, but keep carrier fine detail by adding residual small-scale L
    residual_L = carrier_lab[..., 0] - cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=max(1.0, short_side*0.005))
    L_new = np.clip(blur_L + 0.6 * residual_L, 0.0, 100.0)
    out_lab[..., 0] = L_new

    # apply a/b replacements per column for final_labels == idx
    recolored_mask = np.zeros_like(core_mask, dtype=bool)
    preserved_mask = np.zeros_like(core_mask, dtype=bool)
    confidence_map = np.zeros((h, w), np.float32)

    for x in range(w):
        lbl = int(final_labels[x])
        if lbl < 0:
            # preserved
            preserved_mask[:, x] = core_mask[:, x]
            confidence_map[:, x] = 0.0
            continue
        # high/med detection by original conf_norm
        if conf_norm[x] >= 6.0:
            alpha = 1.0
        elif conf_norm[x] >= 2.5:
            alpha = 0.5
        else:
            alpha = 0.0
        # target ab from palette
        target_ab = palette[lbl, 1:3]
        # apply to core rows in this column
        rows = np.where(core_mask[:, x])[0]
        if rows.size == 0:
            continue
        recolored_mask[rows, x] = alpha > 0.0
        preserved_mask[rows, x] = alpha == 0.0
        # blend a/b channel
        for r in rows:
            car_ab = carrier_lab[r, x, 1:3]
            new_ab = (1.0 - alpha) * car_ab + alpha * target_ab
            out_lab[r, x, 1:3] = new_ab
            confidence_map[r, x] = alpha

    # combine back into BGR
    recolored_bgr = lab_to_bgr(out_lab).astype(np.uint8)

    # integrate recolored torso into carrier: only in core_mask; protected/component preserved
    final = carrier.copy()
    sel = core_mask
    final[sel] = recolored_bgr[sel]

    # Save outputs
    cv2.imwrite(str(out_dir / 'recolor_composite.png'), final)
    cv2.imwrite(str(out_dir / 'recolor_mask.png'), (recolored_mask.astype(np.uint8) * 255))
    cv2.imwrite(str(out_dir / 'preserved_mask.png'), (preserved_mask.astype(np.uint8) * 255))
    # confidence map scaled
    cm_vis = (np.clip(confidence_map, 0.0, 1.0) * 255).astype(np.uint8)
    cv2.imwrite(str(out_dir / 'confidence_map.png'), cm_vis)

    # overlays: detected band edges (column label changes)
    edges = np.zeros((h, w, 3), np.uint8)
    lbl_change = np.zeros(w, dtype=np.uint8)
    lbl_change[1:] = (final_labels[1:] != final_labels[:-1]).astype(np.uint8)
    for x in range(w):
        if lbl_change[x]:
            edges[:, x, :] = (0, 0, 255)
    overlay = cv2.addWeighted(carrier, 0.6, edges, 0.4, 0)
    cv2.imwrite(str(out_dir / 'detected_band_overlay.png'), overlay)

    # painted/preserved boundary overlay
    painted = cv2.imread(str(art / 'painted.png'), cv2.IMREAD_GRAYSCALE) if (art / 'painted.png').exists() else None
    if painted is not None:
        painted_mask = painted > 0
        boundary = (painted_mask & ~preserved_mask) | (~painted_mask & preserved_mask)
        bvis = final.copy()
        bvis[boundary] = [0, 255, 255]
        cv2.imwrite(str(out_dir / 'painted_preserved_boundary_overlay.png'), bvis)

    # Metrics
    total_core = int(core_mask.sum())
    recolored_frac = float(recolored_mask.sum()) / max(1, total_core)
    preserved_frac = float(preserved_mask.sum()) / max(1, total_core)

    # local period preservation error: measure torso period before and after using QC helper
    pm_before, _ = qc_mod._measure_panel_local(carrier, torso, model, target_period_px=float(geo.get('target_period_px', 10.67)), target_axis=geo.get('garment_axis', model.axis), garment_mask=pm.garment_mask)
    pm_after, _ = qc_mod._measure_panel_local(final, torso, model, target_period_px=float(geo.get('target_period_px', 10.67)), target_axis=geo.get('garment_axis', model.axis), garment_mask=pm.garment_mask)
    before_period = pm_before.get('measured_period_px')
    after_period = pm_after.get('measured_period_px')
    if before_period is None or after_period is None:
        local_period_preservation_error = None
    else:
        local_period_preservation_error = abs(after_period - before_period) / max(1e-6, before_period)

    # orientation similarity: not implemented precisely — placeholder null
    local_orientation_similarity = None

    # painted/preserved boundary period jump & orientation jump: compute median period on small bands near boundary
    boundary_period_jump = None
    boundary_orientation_jump = None

    # low-frequency shading correlation between carrier and recolor (L channel lowpass)
    sigma_shade = max(1.0, short_side * 0.03)
    l_car = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma_shade)
    l_rec = cv2.GaussianBlur(bgr_to_lab(final).astype(np.float32)[..., 0], (0, 0), sigmaX=sigma_shade)
    sel_flat = core_mask
    if sel_flat.sum() > 100:
        corr = np.corrcoef(l_car[sel_flat].ravel(), l_rec[sel_flat].ravel())[0, 1]
    else:
        corr = None

    # source_palette_delta_e: for each palette color, median ΔE to recolored pixels labeled that color
    palette_deltas = []
    for idx in range(n_colors):
        cols = np.where(final_labels == idx)[0]
        if cols.size == 0:
            palette_deltas.append(None)
            continue
        sel_pixels = np.zeros(core_mask.shape, bool)
        for x in cols:
            sel_pixels[:, x] |= core_mask[:, x]
        if sel_pixels.sum() < 16:
            palette_deltas.append(None)
            continue
        lab_pixels = bgr_to_lab(final)[sel_pixels]
        med = np.median(lab_pixels, axis=0)
        de = float(np.median(ciede2000(np.asarray([model.color_sequence_lab[idx]]), np.asarray([med]))))
        palette_deltas.append(de)

    metrics = {
        'provider_calls': 0,
        'recolored_fraction': recolored_frac,
        'preserved_fraction': preserved_frac,
        'unsupported_regions': [],
        'local_period_preservation_error': local_period_preservation_error,
        'local_orientation_similarity': local_orientation_similarity,
        'painted_preserved_boundary_period_jump': boundary_period_jump,
        'boundary_orientation_jump': boundary_orientation_jump,
        'low_frequency_shading_correlation': None if corr is None else float(corr),
        'source_palette_delta_e': palette_deltas,
        'protected_region_drift': 0.0,
    }

    with open(out_dir / 'diagnostic_metrics_phase3.json', 'w') as f:
        json.dump(metrics, f, indent=2)

    # A/B gallery: copy images into out_dir
    # source_front, carrier, legacy composite, replay composite, recolor composite
    for name in ('source_front.png', 'carrier.png', 'composite.png', 'replay_composite.png'):
        src = art / name
        if src.exists():
            dst = out_dir / name
            if not dst.exists():
                with open(src, 'rb') as fr, open(dst, 'wb') as fw:
                    fw.write(fr.read())
    print('phase3 outputs written to', out_dir)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('dataset', help='dataset dir (containing artifacts/)')
    p.add_argument('--out', help='output dir', default='server/ab_out/frame_lock/stripe-projection-protected-v1/artifacts/diagnostic_phase3')
    args = p.parse_args()
    run(Path(args.dataset), Path(args.out))
