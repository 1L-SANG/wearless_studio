"""Spike — can a messy seller photo become a clean Product Truth input WITHOUT redrawing it?

Deliberately not a pipeline. One script, no provider call of any kind: the whole point is to
find out whether SOURCE PIXELS alone can be made presentable, so anything that could invent a
stripe is excluded by construction rather than by policy.

Segmentation is the prior spike's, restored and reused rather than rewritten:

* `scripts/source_sam2_spike` (commit 66eef6d) — pretrained SAM2 `facebook/sam2.1-hiera-tiny`
  through transformers, prompted by a uniform 8x8 point grid so no click or hand-drawn box is
  needed, candidates deduped at IoU 0.80, then filtered by the conservative `plausible()`
  rules (too small, near-full-frame, border-dominated, impossible aspect). Thresholds are
  untouched.
* `scripts/source_mask_selector` (commit 5977e71) — ranks candidates by SigLIP similarity to
  the same product's OTHER views, comparing masked crops on neutral grey so the shop backdrop
  cannot win on its own. Size, centre and candidate index are deliberately absent from the
  score: on a flat-laid shirt the largest plausible candidate is the backdrop. Its one change
  here is a `target_slot` argument, because this spike needs a mask for Front, Back and Detail
  rather than Front alone; the scoring is byte-identical.

An earlier draft of this file used a classical background-difference mask and claimed SAM2 was
unavailable. Both were wrong. SAM2 loads offline from the local HF cache in 0.1s, and the
classical path reproduced, exactly, the failure 66eef6d's own commit message already records:
the border band of a shop photo is the shop, so nearly the whole frame reads as foreground.
That path is gone; it is not a fallback, because a fallback that is known to fail is a way of
reporting success.

Garment pixels are copied, never filtered. The only operations inside the garment are a crop,
an optional pure downscale, and alpha at the very edge. Nothing sharpens, smooths, recolours
or reconstructs, and `rgbPreserved` is a MEASURED claim: the canonical image is compared back
to the source inside the eroded mask.

  cd server && .venv/bin/python -m scripts.augmentation_spike --out <dir>
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import pathlib
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageOps

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from scripts._env import load_env                                     # noqa: E402

load_env()

from app.config import load_settings                                  # noqa: E402
from scripts.source_mask_selector import embed_candidates, select     # noqa: E402
from scripts.source_sam2_spike import (                               # noqa: E402
    DEDUPE_IOU, GRID, MODEL_ID, _device, candidate_stats, dedupe, overlay,
    plausible, sam2_candidates,
)

CORPUS = SERVER / "ab_out/llm_qc/corpus"
MASK_CACHE = SERVER / "ab_out/augmentation_spike/_sam2_cache"

#: The three products the brief names, mapped to the corpus directories already on disk.
PRODUCTS = [
    ("stripe", "stripe-shirt", "goldenset-stripe-shirt — the STRIPE case"),
    ("check", "check-shirt", "goldenset-check-shirt — the CHECK / puckered case"),
    ("4ff2132f", "4ff2132f-control", "4ff2132f — 소프트 골지 블라우스 레드"),
]

#: Canonical canvas. Square so every view lands on one geometry, and large enough that a fine
#: stripe survives; a crop smaller than this is centred at native scale rather than upscaled,
#: because upscaling invents detail and this spike may not.
CANVAS = 2048
PAD_FRACTION = 0.06
#: Neutral studio grey. Not white: a white garment on white loses its silhouette, and the
#: check shirt in this very corpus is white.
BACKDROP = (242, 242, 242)
#: Alpha-only feather at the mask edge, applied to alpha alone — the RGB underneath is
#: untouched, so the garment is composited, not blended into.
FEATHER_PX = 2
#: SAM2 runs on a downscaled copy. A 5712x4284 photo through 64 point prompts is minutes of
#: MPS time for a mask whose boundary is then resampled anyway; the mask is computed at this
#: long side and NEAREST-upscaled to full resolution, so the garment pixels that get cropped
#: are always the originals.
SEG_LONG_SIDE = 1280
#: Candidate overlays written per view for the report. Enough to see what SAM2 offered without
#: writing sixty files per photograph.
MAX_CANDIDATE_IMAGES = 10


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def load_bgr(path: pathlib.Path) -> tuple[np.ndarray, dict]:
    """EXIF-corrected load. Rotation metadata is the one transform a phone photo always needs."""
    with Image.open(path) as im:
        before = im.size
        orientation = (im.getexif() or {}).get(274)
        fixed = ImageOps.exif_transpose(im).convert("RGB")
        after = fixed.size
        arr = np.asarray(fixed)
    return (cv2.cvtColor(arr, cv2.COLOR_RGB2BGR),
            {"exifOrientation": orientation, "sizeBefore": list(before),
             "sizeAfter": list(after), "exifApplied": before != after})


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Close interior holes without touching the silhouette.

    A flood fill marks background that reaches the frame edge; anything left is interior — a
    gap the mask opened between a sleeve and the body, or a button that matched the backdrop.

    The fill starts from a ONE-PIXEL BORDER added around the image, not from pixel (0,0). With
    a single corner seed, background that the mask cuts off from that corner is unreachable and
    therefore reads as a hole: on the check shirt that inflated the mask from 67% of the frame
    to 82%, and on 4ff2132f from 57% to 91%, swallowing the shop back into the garment. The
    padded border touches every edge, so every edge-connected background region is reached
    whatever shape the mask is.
    """
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), np.uint8)
    padded[1:-1, 1:-1] = (mask == 0).astype(np.uint8) * 255
    padded[0, :] = padded[-1, :] = padded[:, 0] = padded[:, -1] = 255
    flood = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(padded, flood, (0, 0), 128)
    holes = (padded[1:-1, 1:-1] == 255).astype(np.uint8) * 255
    return cv2.bitwise_or(mask, holes)


def segment_candidates(sam, processor, device, bgr: np.ndarray, key: str) -> list[np.ndarray]:
    """SAM2 candidates for one view, cached by content hash.

    Cached because the selector and the report get iterated on and SAM2 does not need to run
    again for that; the cache key is the EXIF-corrected bytes, so a different orientation is a
    different entry.
    """
    MASK_CACHE.mkdir(parents=True, exist_ok=True)
    path = MASK_CACHE / f"{key}.npz"
    if path.exists():
        z = np.load(path)
        return [z[k].astype(bool) for k in sorted(z.files)]
    h, w = bgr.shape[:2]
    scale = min(1.0, SEG_LONG_SIDE / max(h, w))
    small = (cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
             if scale < 1.0 else bgr)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    masks = dedupe(sam2_candidates(sam, processor, rgb, device))
    if scale < 1.0:
        masks = [cv2.resize(m.astype(np.uint8), (w, h),
                            interpolation=cv2.INTER_NEAREST).astype(bool) for m in masks]
    np.savez_compressed(path, **{f"m{i:03d}": m for i, m in enumerate(masks)})
    return masks


def detect_occlusion(image_bgr: np.ndarray, mask: np.ndarray) -> dict:
    """Look for a hand or a hanger touching the garment. Report only — nothing is repaired.

    This is the line the brief draws and it is the right one: removing a backdrop is a
    statement about pixels that are not the garment, while removing a hand means deciding what
    the garment looks like underneath it. That is invention, so the answer here is a label.
    """
    ycrcb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycrcb[:, :, 1].astype(np.int16), ycrcb[:, :, 2].astype(np.int16)
    skin = ((cr > 135) & (cr < 180) & (cb > 85) & (cb < 135)).astype(np.uint8) * 255
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    inside = cv2.bitwise_and(skin, mask)
    skin_ratio = float((inside > 0).sum()) / max(1.0, float((mask > 0).sum()))

    h = mask.shape[0]
    rows = (mask > 0).sum(axis=1)
    body = rows.max() if rows.size else 0
    top_band = rows[: int(h * 0.10)]
    hook = bool(body and top_band.size and 0 < top_band.max() < 0.12 * body)

    flags = []
    if skin_ratio > 0.004:
        flags.append("hand_overlaps_garment")
    if hook:
        flags.append("hanger_hook_in_frame")
    return {"skinInsideMaskRatio": round(skin_ratio, 5), "hangerHook": hook, "flags": flags}


def canonicalise(image_bgr: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Crop to the garment, pad, place on one canvas. Downscale only, never up.

    Returns (BGR on the backdrop, BGRA with real alpha, provenance). The garment's pixels are
    moved and possibly reduced; they are never filtered.
    """
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        raise ValueError("empty mask")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    pad = int(PAD_FRACTION * max(y1 - y0, x1 - x0))
    h, w = mask.shape
    cy0, cy1 = max(0, y0 - pad), min(h, y1 + 1 + pad)
    cx0, cx1 = max(0, x0 - pad), min(w, x1 + 1 + pad)
    crop = image_bgr[cy0:cy1, cx0:cx1]
    crop_mask = mask[cy0:cy1, cx0:cx1]

    longest = max(crop.shape[:2])
    scale = min(1.0, (CANVAS - 2 * FEATHER_PX) / longest)
    if scale < 1.0:
        target = (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale)))
        # AREA for the RGB: it averages, which is what a downscale IS, and it invents no edge.
        crop = cv2.resize(crop, target, interpolation=cv2.INTER_AREA)
        crop_mask = cv2.resize(crop_mask, target, interpolation=cv2.INTER_NEAREST)

    alpha = crop_mask.copy()
    if FEATHER_PX:
        alpha = cv2.GaussianBlur(alpha, (2 * FEATHER_PX + 1, 2 * FEATHER_PX + 1), 0)

    canvas = np.full((CANVAS, CANVAS, 3), BACKDROP[::-1], np.uint8)   # BACKDROP is RGB
    canvas_alpha = np.zeros((CANVAS, CANVAS), np.uint8)
    oy = (CANVAS - crop.shape[0]) // 2
    ox = (CANVAS - crop.shape[1]) // 2
    a = (alpha.astype(np.float32) / 255.0)[..., None]
    region = canvas[oy:oy + crop.shape[0], ox:ox + crop.shape[1]]
    canvas[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = (
        crop.astype(np.float32) * a + region.astype(np.float32) * (1 - a)).astype(np.uint8)
    canvas_alpha[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = alpha

    rgba = np.dstack([canvas, canvas_alpha])
    provenance = {
        "sourceBBox": [x0, y0, x1, y1],
        "cropBox": [cx0, cy0, cx1, cy1],
        "padPx": pad,
        "scale": round(float(scale), 5),
        "resampled": scale < 1.0,
        "resample": "INTER_AREA (downscale only)" if scale < 1.0 else "none",
        "canvas": [CANVAS, CANVAS],
        "placement": [ox, oy],
        "featherPx": FEATHER_PX,
        "backdrop": list(BACKDROP),
        "cropSize": [int(crop.shape[1]), int(crop.shape[0])],
    }
    return canvas, rgba, provenance


def verify_rgb_preserved(source_bgr, mask, canonical_bgr, prov) -> dict:
    """Measure, do not assert. Compare canonical back to the source inside the eroded mask.

    Eroded because the feather band is a deliberate alpha blend against the backdrop and would
    read as a difference; the interior is where the claim matters. When the crop was
    downscaled the comparison is made against an identically downscaled source, so what is
    being tested is "did anything other than the documented resize happen".
    """
    cx0, cy0, cx1, cy1 = prov["cropBox"]
    crop = source_bgr[cy0:cy1, cx0:cx1]
    crop_mask = mask[cy0:cy1, cx0:cx1]
    if prov["resampled"]:
        target = tuple(prov["cropSize"])
        crop = cv2.resize(crop, target, interpolation=cv2.INTER_AREA)
        crop_mask = cv2.resize(crop_mask, target, interpolation=cv2.INTER_NEAREST)
    inner = cv2.erode(crop_mask, np.ones((2 * FEATHER_PX + 3, 2 * FEATHER_PX + 3), np.uint8))
    ox, oy = prov["placement"]
    placed = canonical_bgr[oy:oy + crop.shape[0], ox:ox + crop.shape[1]]
    sel = inner > 0
    if not sel.any():
        return {"rgbPreserved": None, "rgbPreservedPct": None, "comparedPixels": 0}
    diff = np.abs(placed.astype(np.int16) - crop.astype(np.int16))[sel]
    identical = int((diff.max(axis=1) == 0).sum())
    return {"rgbPreserved": bool(diff.max() == 0),
            "rgbPreservedPct": round(100.0 * identical / max(1, sel.sum()), 4),
            "comparedPixels": int(sel.sum()), "maxAbsDiff": int(diff.max()),
            "meanAbsDiff": round(float(diff.mean()), 4)}


def propose_status(view: dict) -> tuple[str, list[str]]:
    """A deterministic FIRST GUESS, confirmed or overridden by looking at the pictures.

    Not a score and not a judge. The brief asks for a human visual call; this exists to sort
    the outputs and to say out loud which signal made a view suspicious.
    """
    reasons = []
    sel = view.get("selection") or {}
    if sel.get("state") != "SELECTED":
        reasons.append(f"selector state {sel.get('state')}")
    coverage = view.get("maskCoverage") or 0.0
    if coverage < 0.04:
        reasons.append("selected mask covers <4% of the frame")
    if coverage > 0.75:
        reasons.append("selected mask covers >75% of the frame")
    margin = sel.get("margin")
    if margin is not None and margin < 0.05:
        reasons.append(f"selection margin only {margin}")
    if not view.get("plausibleCount"):
        reasons.append("no plausible SAM2 candidate")
    reasons.extend((view.get("occlusion") or {}).get("flags", []))

    if (sel.get("state") != "SELECTED" or not view.get("plausibleCount")
            or coverage < 0.04 or coverage > 0.75):
        return "UNUSABLE", reasons
    return ("PARTIAL" if reasons else "CLEAN"), reasons


def gather_views(corpus_dir: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Front, Back and the FIRST Detail. One product's own slots, never mixed."""
    views = []
    for slot in ("Front", "Back", "Detail"):
        hits = sorted(glob.glob(str(corpus_dir / f"source_{slot}_*.jpg")))
        for i, hit in enumerate(hits):
            if slot == "Detail" and i > 0:
                break
            views.append((slot, pathlib.Path(hit)))
    return views


def run_product(sam, processor, device, model_id, key, dirname, label,
                corpus: pathlib.Path, out_root: pathlib.Path) -> dict:
    product_dir = corpus / dirname
    out_dir = out_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    views = gather_views(product_dir)
    print(f"\n=== {key}: {len(views)} views")

    # Segment every view first: the selector scores a candidate by how well it matches the
    # SAME product's other views, so all of them have to exist before anything is chosen.
    loaded, candidates = [], []
    for slot, path in views:
        t0 = time.perf_counter()
        bgr, exif = load_bgr(path)
        ok, buf = cv2.imencode(".png", bgr)
        raw_masks = segment_candidates(sam, processor, device, bgr,
                                       sha16(buf.tobytes() if ok else path.read_bytes()))
        entries = []
        for i, m in enumerate(raw_masks):
            st = candidate_stats(m)
            good, why = plausible(st)
            entry = {"slot": slot, "id": i, "mask": m, "bgr": bgr, "stats": st,
                     "plausible": good, "reject": why or None}
            entries.append(entry)
            candidates.append(entry)
        loaded.append({"slot": slot, "path": path, "bgr": bgr, "exif": exif,
                       "entries": entries, "segMs": int((time.perf_counter() - t0) * 1000)})
        print(f"  {slot:7s} candidates={len(raw_masks):3d} "
              f"plausible={sum(1 for e in entries if e['plausible']):3d} "
              f"{loaded[-1]['segMs']}ms")

    embed_candidates(candidates, model_id)

    records = []
    for item in loaded:
        slot, bgr, path = item["slot"], item["bgr"], item["path"]
        started = time.perf_counter()
        selection = select({"candidates": candidates}, model_id, target_slot=slot)
        plausible_entries = [e for e in item["entries"] if e["plausible"]]

        stem = f"{slot}_{path.stem[-8:]}"
        candidate_files = []
        for e in plausible_entries[:MAX_CANDIDATE_IMAGES]:
            fp = out_dir / f"{stem}_cand{e['id']:02d}.jpg"
            cv2.imwrite(str(fp), overlay(bgr, e["mask"]), [cv2.IMWRITE_JPEG_QUALITY, 85])
            rank = next((r for r in selection["ranking"] if r["id"] == e["id"]), None)
            candidate_files.append({
                "id": e["id"], "file": str(fp), "stats": e["stats"],
                "score": (rank or {}).get("score"),
                "crossViewSim": (rank or {}).get("crossViewSim"),
                "selected": e["id"] == selection.get("selectedId")})

        record = {
            "slot": slot, "sourceFile": str(path),
            "sourceSize": [bgr.shape[1], bgr.shape[0]], "exif": item["exif"],
            "sam2": {"model": MODEL_ID, "grid": GRID, "dedupeIoU": DEDUPE_IOU,
                     "segMs": item["segMs"], "segLongSide": SEG_LONG_SIDE},
            "candidateCount": len(item["entries"]),
            "plausibleCount": len(plausible_entries),
            "candidates": candidate_files,
            "selection": {k: v for k, v in selection.items() if k != "ranking"},
            "ranking": selection["ranking"][:6],
        }

        chosen = next((e for e in item["entries"]
                       if e["id"] == selection.get("selectedId")), None)
        if chosen is None:
            record.update({"maskCoverage": 0.0, "occlusion": {"flags": []}})
            status, reasons = propose_status(record)
            record.update({"status": status, "statusReasons": reasons,
                           "durationMs": int((time.perf_counter() - started) * 1000)})
            records.append(record)
            print(f"  {slot:7s} {status:9s} selector={selection['state']}")
            continue

        mask = fill_holes((chosen["mask"].astype(np.uint8)) * 255)
        coverage = float((mask > 0).mean())
        occ = detect_occlusion(bgr, mask)
        mask_path = out_dir / f"{stem}_mask.png"
        cv2.imwrite(str(mask_path), mask)
        sel_path = out_dir / f"{stem}_selected.jpg"
        cv2.imwrite(str(sel_path), overlay(bgr, mask > 0), [cv2.IMWRITE_JPEG_QUALITY, 88])
        record.update({"maskCoverage": round(coverage, 5), "occlusion": occ,
                       "maskFile": str(mask_path), "selectedOverlayFile": str(sel_path),
                       "selectedCandidateId": chosen["id"]})

        try:
            canonical, rgba, prov = canonicalise(bgr, mask)
        except ValueError as exc:
            record.update({"error": str(exc), "status": "UNUSABLE",
                           "statusReasons": ["empty mask"],
                           "durationMs": int((time.perf_counter() - started) * 1000)})
            records.append(record)
            continue

        canonical_path = out_dir / f"{stem}_canonical.png"
        rgba_path = out_dir / f"{stem}_canonical_rgba.png"
        cv2.imwrite(str(canonical_path), canonical)
        cv2.imwrite(str(rgba_path), rgba)
        record.update({
            "canonicalFile": str(canonical_path), "rgbaFile": str(rgba_path),
            "canonicalSize": [CANVAS, CANVAS], "provenance": prov,
            "preservation": verify_rgb_preserved(bgr, mask, canonical, prov),
        })
        status, reasons = propose_status(record)
        record.update({"status": status, "statusReasons": reasons,
                       "durationMs": int((time.perf_counter() - started) * 1000)})
        records.append(record)
        print(f"  {slot:7s} {status:9s} cand#{chosen['id']} coverage={coverage:.3f} "
              f"margin={selection['margin']} "
              f"rgb={record['preservation']['rgbPreservedPct']}% {reasons}")

    return {"key": key, "label": label, "corpusDir": str(product_dir), "views": records}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus", default=str(CORPUS))
    args = ap.parse_args()
    corpus = pathlib.Path(args.corpus)
    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    model_id = settings.embed_image_model
    from transformers import Sam2Model, Sam2Processor
    device = _device()
    print(f"[sam2] device={device} model={MODEL_ID} selector-embed={model_id}", flush=True)
    processor = Sam2Processor.from_pretrained(MODEL_ID)
    sam = Sam2Model.from_pretrained(MODEL_ID).to(device).eval()

    products = [run_product(sam, processor, device, model_id, key, dirname, label,
                            corpus, out_root)
                for key, dirname, label in PRODUCTS]

    payload = {
        "spike": "source_preserving_augmentation_v2_sam2",
        "generativeApiCalls": 0,
        "segmentation": {
            "model": MODEL_ID, "grid": GRID, "dedupeIoU": DEDUPE_IOU,
            "restoredFrom": "66eef6d scripts/source_sam2_spike.py",
            "selectorRestoredFrom": "5977e71 scripts/source_mask_selector.py",
            "selectorEmbedModel": model_id,
        },
        "products": products,
    }
    (out_root / "augmentation_spike_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2,
                   default=lambda o: None), encoding="utf-8")
    print(f"\nresults -> {out_root}/augmentation_spike_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
