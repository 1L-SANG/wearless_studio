#!/usr/bin/env python3
"""Refine torso core mask and detect local bands inside garment-local geometry.
Produces corrected_torso_core_mask.png, overlays, scanline overlays, local confidence map,
mask pixel counts, and an experiment recolor that restricts original recolor to new mask.
Offline only; uses artifacts in dataset. No production changes.
"""
from __future__ import annotations
from pathlib import Path
import json
import sys
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
from importlib.machinery import SourceFileLoader
# load replay_stripe_projection.py directly
replay_path = Path(__file__).resolve().parents[1] / 'scripts' / 'replay_stripe_projection.py'
spec = importlib.util.spec_from_file_location('replay_module', str(replay_path))
replay = importlib.util.module_from_spec(spec)
if spec.loader:
    spec.loader.exec_module(replay)
else:
    raise SystemExit('could not load replay_stripe_projection')


def load_img(p: Path):
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"can't read {p}")
    return img


def load_gray(p: Path):
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise SystemExit(f"can't read {p}")
    return img


def poly_to_mask(poly, shape):
    mask = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(mask, [poly.astype(np.int32)], 255)
    return mask


def keep_largest_component(mask, seed_point=None):
    n, labels = cv2.connectedComponents((mask>0).astype('uint8'))
    if n <= 1:
        return mask
    areas = [(labels==i).sum() for i in range(n)]
    # if seed provided, choose component containing seed
    if seed_point is not None:
        x,y = seed_point
        lab = labels[y,x] if 0<=y<labels.shape[0] and 0<=x<labels.shape[1] else 0
        if lab!=0:
            return (labels==lab).astype(np.uint8)*255
    # else choose largest non-background
    best = np.argmax(areas[1:])+1
    return (labels==best).astype(np.uint8)*255


def shrink_mask(mask, pixels):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pixels*2+1, pixels*2+1))
    return cv2.erode(mask, k)


def detect_local_bands(carrier_bgr, torso_mask, model=None, n_scan=21):
    # compute luminance and bandpass
    gray = cv2.cvtColor(carrier_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # bandpass for stripe-scale features
    band = cv2.GaussianBlur(gray, (0,0), 1.0) - cv2.GaussianBlur(gray, (0,0), 4.0)
    energy = np.abs(band)
    h,w = gray.shape
    ys = np.linspace(int(h*0.15), int(h*0.85), n_scan).astype(int)
    scan_results = []
    edge_map = np.zeros_like(gray, dtype=np.uint8)
    conf_map = np.zeros_like(gray, dtype=np.float32)
    for y in ys:
        # find contiguous segments inside torso mask at this y
        row_mask = torso_mask[y]>0
        if not row_mask.any():
            scan_results.append({'y':int(y),'edges':[]})
            continue
        # restrict columns where mask is True
        cols = np.where(row_mask)[0]
        x0,x1 = int(cols.min()), int(cols.max())+1
        profile = gray[y, x0:x1]
        prof_energy = energy[y, x0:x1]
        # smooth profile
        prof_s = cv2.GaussianBlur(profile.reshape(1,-1), (1,9), 0).ravel()
        grad = np.abs(np.gradient(prof_s))
        # detect peaks in gradient as candidate edges
        # set threshold relative to local median energy
        thr = max(5.0, np.median(grad)*3.0)
        peaks = np.where(grad > thr)[0]
        edges = [int(x0+p) for p in peaks]
        scan_results.append({'y':int(y),'edges':edges})
        for ex in edges:
            if 0<=ex<w:
                edge_map[y-1:y+2, max(0,ex-1):min(w,ex+2)] = 255
                # confidence = normalized gradient * local energy
                idx = ex - x0
                c = float(grad[idx]) * (prof_energy[idx] if idx>=0 and idx<len(prof_energy) else 1.0)
                conf_map[y-2:y+3, max(0,ex-2):min(w,ex+3)] = max(conf_map[y-2:y+3, max(0,ex-2):min(w,ex+3)].max(), c)
    # normalize conf_map to 0-1
    if conf_map.max() > 0:
        conf_map = conf_map / float(conf_map.max())
    # zero out outside torso_mask
    conf_map[torso_mask==0] = 0.0
    edge_map[torso_mask==0] = 0
    return edge_map, conf_map, scan_results


def overlay_masks(base_bgr, old_mask, new_mask):
    over = base_bgr.copy()
    # old red, new green
    over[old_mask>0] = (0.8*over[old_mask>0] + np.array([0,0,255])*0.2).astype(np.uint8)
    # green tint where new_mask
    over[new_mask>0] = (0.8*over[new_mask>0] + np.array([0,255,0])*0.2).astype(np.uint8)
    # yellow where both
    both = (old_mask>0) & (new_mask>0)
    over[both] = (0.6*over[both] + np.array([0,255,255])*0.4).astype(np.uint8)
    return over


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('artdir')
    p.add_argument('--out', default=None)
    args = p.parse_args()
    art = Path(args.artdir)
    out = Path(args.out) if args.out else art / 'diagnostic_phase3_refine'
    out.mkdir(parents=True, exist_ok=True)

    carrier = load_img(art / 'carrier.png')
    garment_mask = load_gray(art / 'garment_mask.png')
    protected = (cv2.imread(str(art / 'protected.png'), cv2.IMREAD_GRAYSCALE) if (art / 'protected.png').exists() else np.zeros_like(garment_mask))
    recolor_mask_path = art / 'diagnostic_phase3' / 'recolor_mask.png'
    recolor_mask = (cv2.imread(str(recolor_mask_path), cv2.IMREAD_GRAYSCALE) if recolor_mask_path.exists() else np.zeros_like(garment_mask))
    preserved_mask_path = art / 'diagnostic_phase3' / 'preserved_mask.png'
    preserved_mask = (cv2.imread(str(preserved_mask_path), cv2.IMREAD_GRAYSCALE) if preserved_mask_path.exists() else np.zeros_like(garment_mask))
    painted_path = art / 'painted.png'
    painted = (cv2.imread(str(painted_path), cv2.IMREAD_GRAYSCALE) if painted_path.exists() else None)

    # load geometry and landmarks
    try:
        geo, prov = replay.load_geometry(art.parent, carrier)
    except SystemExit:
        # geometry.json missing — reconstruct minimal geometry from garment_mask
        geo = {}
        prov = {'landmarks': 'reconstructed_from_mask', 'carrier_preflight': 'missing'}
        lm = replay._landmarks_from_mask(garment_mask)
    else:
        if geo.get('carrier_landmarks'):
            lm = geo['carrier_landmarks']
        else:
            lm = replay._landmarks_from_mask(garment_mask)
    h,w = carrier.shape[:2]
    def px(pt):
        return (int(pt[0]*w), int(pt[1]*h))
    sl = px(lm['shoulder_l']); sr = px(lm['shoulder_r']); hl = px(lm['hem_l']); hr = px(lm['hem_r'])
    torso_poly = np.array([sl, sr, hr, hl], np.int32)
    torso_poly_mask = poly_to_mask(torso_poly, carrier.shape)

    # initial torso core = torso_poly_mask & garment_mask
    torso_core = ((torso_poly_mask>0) & (garment_mask>0)).astype(np.uint8)*255
    # remove protected & component boxes (placket/collar)
    # placket/collar boxes from geometry
    car_boxes = geo.get('carrier_component_boxes_norm') or {}
    def denorm(box):
        if not box: return None
        return np.array([[int(x*w), int(y*h)] for x,y in box], np.int32)
    collar_box = denorm(car_boxes.get('collar_box'))
    placket_box = denorm(car_boxes.get('placket_box'))
    if collar_box is not None:
        cmask = poly_to_mask(collar_box, carrier.shape)
        torso_core[cmask>0]=0
    if placket_box is not None:
        pmask = poly_to_mask(placket_box, carrier.shape)
        torso_core[pmask>0]=0
    # erode edges to avoid feather link to sleeves
    torso_core = shrink_mask(torso_core, max(3, int(min(h,w)*0.005)))
    # keep largest component containing torso center
    torso_center = ((sl[0]+sr[0])//2, (sl[1]+hl[1])//2)
    torso_core = keep_largest_component(torso_core, seed_point=torso_center)

    # old vs new overlay
    old_torso_mask = torso_poly_mask & garment_mask
    overlay = overlay_masks(carrier, old_torso_mask, torso_core)
    cv2.imwrite(str(out / 'corrected_torso_core_mask.png'), torso_core)
    cv2.imwrite(str(out / 'old_vs_new_mask_overlay.png'), overlay)

    # band detection inside new torso_core
    edge_map, conf_map, scans = detect_local_bands(carrier, torso_core)
    # colorize edge_map and conf_map
    edge_color = cv2.cvtColor(edge_map, cv2.COLOR_GRAY2BGR)
    edge_color[edge_map>0] = [0,0,255]
    conf_vis = (conf_map*255).astype(np.uint8)
    conf_color = cv2.applyColorMap(conf_vis, cv2.COLORMAP_JET)
    # overlay edges on carrier
    band_overlay = carrier.copy()
    band_overlay[edge_map>0] = (0.6*band_overlay[edge_map>0] + np.array([0,0,255])*0.4).astype(np.uint8)
    cv2.imwrite(str(out / 'local_band_overlay.png'), band_overlay)
    cv2.imwrite(str(out / 'local_confidence_map.png'), conf_color)

    # scanline overlay: draw sampled lines and edges
    scan_vis = carrier.copy()
    for s in scans:
        y = s['y']
        cv2.line(scan_vis, (0,y),(w-1,y),(200,200,200),1)
        for ex in s['edges']:
            cv2.circle(scan_vis, (ex,y), 3, (0,255,255), -1)
    cv2.imwrite(str(out / 'scanline_overlay.png'), scan_vis)

    # mask-outside detection zero image: show edge_map masked outside
    outside_detect = edge_map.copy()
    outside_detect[torso_core>0]=0
    outside_vis = carrier.copy()
    outside_vis[outside_detect>0] = [0,0,255]
    cv2.imwrite(str(out / 'mask_outside_detection.png'), outside_vis)

    # pixel counts
    counts = {
        'garment_mask_pixels': int((garment_mask>0).sum()),
        'old_torso_mask_pixels': int((old_torso_mask>0).sum()),
        'corrected_torso_core_pixels': int((torso_core>0).sum()),
        'protected_pixels': int((protected>0).sum()),
        'recolor_mask_pixels': int((recolor_mask>0).sum()),
        'preserved_mask_pixels': int((preserved_mask>0).sum()),
        'painted_pixels': int((painted>0).sum()) if painted is not None else None,
    }
    (out / 'mask_pixel_counts.json').write_text(json.dumps(counts, indent=2))

    # experiment: restrict existing recolor to corrected torso_core (remove recolor outside)
    recolor_img = load_img(art / 'diagnostic_phase3' / 'recolor_composite.png')
    carrier_img = carrier
    recolor2 = recolor_img.copy()
    # where torso_core==0, use carrier pixels
    recolor2[torso_core==0] = carrier_img[torso_core==0]
    cv2.imwrite(str(out / 'recolor_restricted_to_corrected_mask.png'), recolor2)
    # also save old recolor copy path
    cv2.imwrite(str(out / 'recolor_original_copy.png'), recolor_img)

    # record provider_calls
    meta = {'provider_calls': 0}
    (out / 'meta.json').write_text(json.dumps(meta))

    print('wrote outputs to', out)

if __name__=='__main__':
    main()
