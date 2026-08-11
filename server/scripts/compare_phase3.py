#!/usr/bin/env python3
"""Per-panel QC comparison for Phase 3: carrier vs legacy vs replay vs recolor.
Produces numeric_comparison_phase3.json, contact sheets, zoom crops, and a short HTML report.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.hybrid_composite import deterministic_qc as hc_qc
from app.services.hybrid_composite import panel_map as hc_panel
from app.services.hybrid_composite import stripe_model as hc_stripe
from app.services.hybrid_composite import scale_anchor as hc_scale
import importlib.util
replay_path = Path(__file__).resolve().parents[1] / 'scripts' / 'replay_stripe_projection.py'
spec = importlib.util.spec_from_file_location('replay_module', str(replay_path))
replay = importlib.util.module_from_spec(spec)
if spec.loader:
    spec.loader.exec_module(replay)
else:
    raise SystemExit('could not load replay_stripe_projection')
from app.services.hybrid_composite.color import bgr_to_lab, ciede2000


def load_img(p: Path):
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"can't read {p}")
    return img


def median_delta_e_lab(a_bgr, b_bgr, mask):
    if mask is None:
        return None
    sel = mask > 0
    if not sel.any():
        return None
    a_lab = bgr_to_lab(a_bgr).astype(np.float32)
    b_lab = bgr_to_lab(b_bgr).astype(np.float32)
    vals = [float(ciede2000(a_lab[i], b_lab[i])) for i in zip(*np.nonzero(sel))]
    if not vals:
        return None
    return float(np.median(vals))


def make_contact_sheet(images, names, outpath, thumb_h=600):
    thumbs = []
    for img in images:
        h, w = img.shape[:2]
        scale = thumb_h / h
        tw = int(w * scale)
        th = thumb_h
        thumbs.append(cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA))
    # pad to same height
    total_w = sum(t.shape[1] for t in thumbs) + (len(thumbs) - 1) * 10
    sheet = np.full((thumb_h + 50, total_w, 3), 255, dtype=np.uint8)
    x = 0
    for name, t in zip(names, thumbs):
        sheet[0:t.shape[0], x:x + t.shape[1]] = t
        cv2.putText(sheet, name, (x + 6, thumb_h + 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (0, 0, 0), 2, cv2.LINE_AA)
        x += t.shape[1] + 10
    cv2.imwrite(str(outpath), sheet)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('dataset', help='dataset dir (with artifacts)')
    p.add_argument('--out', help='output dir', default=None)
    args = p.parse_args()
    ds = Path(args.dataset)
    # support passing either dataset root or its artifacts/ directory
    if (ds / 'artifacts').exists():
        art = ds / 'artifacts'
        dataset_root = ds
    else:
        art = ds
        dataset_root = ds.parent
    out = Path(args.out) if args.out else art / 'diagnostic_phase3_compare'
    out.mkdir(parents=True, exist_ok=True)

    # load geometry + provenance
    # replay.load_geometry expects dataset root (where geometry.json lives)
    geo, prov = replay.load_geometry(art, load_img(art / 'carrier.png'))

    # load source and build stripe model (same as replay)
    src = load_img(art / 'source_front.png')
    roi = geo.get('stripe_model', {}).get('source_roi')
    if roi:
        x0, y0, x1, y1 = [int(v) for v in roi]
        src_roi = src[y0:y1, x0:x1]
    else:
        src_roi = src
    model = hc_stripe.extract_stripe_model_scan(src_roi,
                                                source_asset_id='replay',
                                                source_sha256=geo.get('stripe_model', {}).get('source_sha256', '0'),
                                                source_roi=tuple(roi) if roi else None)
    # load carrier and garment mask
    carrier = load_img(art / 'carrier.png')
    garment_mask = cv2.imread(str(art / 'garment_mask.png'), cv2.IMREAD_GRAYSCALE)
    # reconstruct landmarks from mask if missing
    landmarks = geo.get('carrier_landmarks') or replay._landmarks_from_mask(garment_mask)

    # construct simple panels from landmarks (avoid full build_panel_map which may fail strict checks)
    from app.services.hybrid_composite.panel_map import Panel
    panels = []
    def px(pt):
        return (int(pt[0] * carrier.shape[1]), int(pt[1] * carrier.shape[0]))
    # torso quad
    try:
        sl = landmarks['shoulder_l']; sr = landmarks['shoulder_r']; hl = landmarks['hem_l']; hr = landmarks['hem_r']
        torso_q = np.array([px(sl), px(sr), px(hr), px(hl)], dtype=np.float32)
        panels.append(Panel('torso', 'stripe', torso_q))
    except Exception:
        pass
    # sleeves
    for side, key in (('l', 'sleeve_l_end'), ('r', 'sleeve_r_end')):
        if key in landmarks:
            end = np.array(px(landmarks[key]), dtype=np.float32)
            top = np.array(px(landmarks['shoulder_l'] if side == 'l' else landmarks['shoulder_r']), dtype=np.float32)
            d = end - top
            norm = np.array([-d[1], d[0]], np.float32)
            nlen = float(np.linalg.norm(norm))
            if nlen >= 1e-3:
                norm = norm / nlen * max(8.0, float(np.linalg.norm(d)) * 0.28)
                q = np.array([top - norm * 0.4, top + norm * 0.6, end + norm * 0.6, end - norm * 0.4], dtype=np.float32)
                panels.append(Panel(f'sleeve_{side}', 'stripe', q if float(0.5) else q))
    # collar / placket boxes from geometry if present
    car_boxes = geo.get('carrier_component_boxes_norm') or {}
    def denorm_box(box):
        if not box:
            return None
        w = carrier.shape[1]; h = carrier.shape[0]
        pts = [[int(x*w), int(y*h)] for x,y in box]
        return np.array(pts, dtype=np.float32)
    collar_box = denorm_box(car_boxes.get('collar_box'))
    if collar_box is not None:
        panels.append(Panel('collar', 'decal', collar_box))
    placket_box = denorm_box(car_boxes.get('placket_box'))
    if placket_box is not None:
        panels.append(Panel('placket', 'decal', placket_box))

    # build a minimal panel map-like holder with garment_mask and panels
    class SimplePM:
        def __init__(self, garment_mask, panels):
            self.garment_mask = garment_mask
            self.panels = panels
            self.protected = cv2.imread(str(art / 'protected.png'), cv2.IMREAD_GRAYSCALE) if (art / 'protected.png').exists() else np.zeros_like(garment_mask)

    pm = SimplePM(garment_mask, panels)

    target_period_px = float(geo.get('target_period_px')) if geo.get('target_period_px') else None
    axis = geo.get('garment_axis') or model.axis

    # images to compare
    imgs = {
        'source': load_img(art / 'source_front.png'),
        'carrier': carrier,
        'legacy': load_img(art / 'composite.png'),
        'replay': load_img(art / 'replay_composite.png'),
        'recolor': load_img(art / 'diagnostic_phase3' / 'recolor_composite.png')
    }

    # masks
    painted = None
    painted_path = art / 'painted.png'
    if painted_path.exists():
        painted = cv2.imread(str(painted_path), cv2.IMREAD_GRAYSCALE)
    recolor_mask = None
    recolor_mask_path = art / 'diagnostic_phase3' / 'recolor_mask.png'
    if recolor_mask_path.exists():
        recolor_mask = cv2.imread(str(recolor_mask_path), cv2.IMREAD_GRAYSCALE)
    preserved_mask = None
    preserved_mask_path = art / 'diagnostic_phase3' / 'preserved_mask.png'
    if preserved_mask_path.exists():
        preserved_mask = cv2.imread(str(preserved_mask_path), cv2.IMREAD_GRAYSCALE)

    # per-panel measurements
    panels = {p.name: p for p in pm.panels}
    desired_panels = ['torso', 'sleeve_l', 'sleeve_r', 'collar', 'placket', 'cuff_l', 'cuff_r']

    results = {
        'provider_calls': 0,
        'per_panel': {},
    }

    for pname in desired_panels:
        panel = panels.get(pname)
        if panel is None:
            results['per_panel'][pname] = {'available': False, 'reason': 'no_panel_in_map'}
            continue
        results['per_panel'][pname] = {'available': True}
        carrier_meas = None
        try:
            carrier_meas, _ = hc_qc._measure_panel_local(carrier, panel, model,
                                                          target_period_px=target_period_px,
                                                          target_axis=axis,
                                                          garment_mask=pm.garment_mask)
        except Exception as e:
            carrier_meas = {'error': str(e)}
        results['per_panel'][pname]['carrier'] = carrier_meas

        for key, img in imgs.items():
            if key == 'source':
                # source front is not comparable in panel space — record null
                results['per_panel'][pname][key] = None if key == 'source' else None
                if key == 'source':
                    results['per_panel'][pname]['source_reason'] = 'source_not_in_carrier_space'
                continue
            # choose painted/exclude masks
            pmask = None
            if key == 'legacy' and painted is not None:
                pmask = painted
            if key == 'recolor' and recolor_mask is not None:
                pmask = recolor_mask
            if key == 'recolor' and preserved_mask is not None:
                # recolor uses preserved mask separately — pass painted as recolor_mask
                pmask = recolor_mask
            try:
                meas, failures = hc_qc._measure_panel_local(img, panel, model,
                                                            target_period_px=target_period_px,
                                                            target_axis=axis,
                                                            garment_mask=pm.garment_mask,
                                                            exclude_mask=(preserved_mask if key=='recolor' and preserved_mask is not None else None))
            except Exception as e:
                meas = {'error': str(e)}
                failures = []
            results['per_panel'][pname][key] = {'metrics': meas, 'failures': failures}

        # boundary chroma and seam for legacy/recolor/replay if painted exists
        boundary_metrics = {}
        for key in ['legacy', 'replay', 'recolor']:
            out_img = imgs[key]
            pmask = painted if key in ('legacy', 'replay') else recolor_mask
            try:
                bc = hc_qc._boundary_chroma(out_img, pmask, pm.garment_mask, band_px=int(max(3, target_period_px)))
            except Exception:
                bc = {}
            try:
                alpha = None
                # no alpha available in artifacts; pass None
                seam = hc_qc._interface_seam(None, pmask, pm.garment_mask, band_px=max(2, int(target_period_px)))
            except Exception:
                seam = {}
            boundary_metrics[key] = {'boundary_chroma': bc, 'seam': seam}
        results['per_panel'][pname]['boundary'] = boundary_metrics

        # protected region drift
        try:
            prot = pm.protected if hasattr(pm, 'protected') else None
            drift = median_delta_e_lab(carrier, imgs['recolor'], prot)
        except Exception:
            drift = None
        results['per_panel'][pname]['protected_region_drift'] = drift

    # low-frequency shading correlation (drape) for each image vs carrier
    drape = {}
    for key in ['legacy', 'replay', 'recolor']:
        try:
            dr = hc_qc._drape_preservation(imgs[key], carrier, pm.garment_mask)
        except Exception:
            dr = {'drape_measurable': False}
        drape[key] = dr
    results['drape'] = drape

    # aggregate recolored/preserved fractions from phase3 metrics if present
    phase3_metrics_path = art / 'diagnostic_phase3' / 'diagnostic_metrics_phase3.json'
    if phase3_metrics_path.exists():
        results['phase3_metrics'] = json.loads(phase3_metrics_path.read_text())

    # write numeric JSON
    out_json = out / 'numeric_comparison_phase3.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))

    # contact sheet
    names = ['source', 'carrier', 'legacy', 'replay', 'recolor']
    make_contact_sheet([imgs[n] for n in names], names, out / 'contact_sheet.png')

    # crops: torso upper/mid/waist and placket/button area and painted/preserved boundary
    # get torso quad and compute crop boxes
    torso = panels.get('torso')
    if torso:
        q = torso.quad
        xs = q[:, 0]; ys = q[:, 1]
        minx, maxx = int(xs.min()), int(xs.max())
        miny, maxy = int(ys.min()), int(ys.max())
        h = maxy - miny
        crops = {
            'upper_torso': (minx, miny, maxx, miny + int(h * 0.25)),
            'mid_torso': (minx, miny + int(h * 0.35), maxx, miny + int(h * 0.65)),
            'waist': (minx, miny + int(h * 0.7), maxx, maxy),
        }
        for cname, (x0, y0, x1, y1) in crops.items():
            strip = []
            for n in ['carrier', 'legacy', 'replay', 'recolor']:
                img = imgs[n]
                crop = img[y0:y1, x0:x1]
                if crop.size == 0:
                    crop = np.full((100, 100, 3), 255, np.uint8)
                strip.append(cv2.resize(crop, (600, int(600 * crop.shape[0] / max(1, crop.shape[1]))), interpolation=cv2.INTER_AREA))
            # horizontal concat
            total_w = sum(s.shape[1] for s in strip) + 3*10
            H = max(s.shape[0] for s in strip)
            canvas = np.full((H + 30, total_w, 3), 255, np.uint8)
            x = 0
            for s, label in zip(strip, ['carrier','legacy','replay','recolor']):
                canvas[0:s.shape[0], x:x + s.shape[1]] = s
                cv2.putText(canvas, label, (x + 6, H + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,0),2)
                x += s.shape[1] + 10
            cv2.imwrite(str(out / f'crop_{cname}.png'), canvas)

    # simple HTML report
    report = out / 'comparison_report_phase3.html'
    lines = [
        '<html><head><meta charset="utf-8"><title>Phase3 Comparison</title></head><body>',
        '<h1>Phase3 Per-panel Comparison</h1>',
        '<h2>Contact sheet</h2>',
        f'<img src="{Path("contact_sheet.png").name}" style="max-width:100%;height:auto;">',
        '<h2>Numeric results</h2>',
        '<pre>' + json.dumps(results, indent=2) + '</pre>',
        '</body></html>'
    ]
    report.write_text('\n'.join(lines))
    print('wrote', out_json, 'and report to', out)
