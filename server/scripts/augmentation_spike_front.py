"""Timeboxed spike — three Front images, masks read from cache, no new segmentation.

The nine-view run was cancelled at ~6 minutes per view once the stripe case had already
answered the interesting part: SAM2 offers the shirt as separate body and sleeve candidates,
and the automatic selector took a sleeve. Benchmarking segmentation was never the question.

Two questions are being kept apart on purpose, because the first one failing must not stop
the second from being answered:

  A. can the automatic selector find the garment?
  B. GIVEN a good mask, is deterministic source-preserving canonicalisation useful?

So every plausible candidate is rendered for inspection, the automatic pick is shown as the
automatic pick, and `--pick` re-runs canonicalisation on a HUMAN-CHOSEN candidate. That path
is a diagnostic, is labelled as one in the report, and automates nothing.

Masks come from `ab_out/augmentation_spike/_sam2_cache`, written by the cancelled run. The
selector needs the product's other views to score cross-view support, so Back and Detail
candidates are loaded from the same cache when they exist — read, never recomputed.

  .venv/bin/python -m scripts.augmentation_spike_front --out <dir>
  .venv/bin/python -m scripts.augmentation_spike_front --out <dir> --pick stripe=3,check=1
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
from scripts.augmentation_spike import (                              # noqa: E402
    BACKDROP, CANVAS, FEATHER_PX, PAD_FRACTION, canonicalise, detect_occlusion,
    fill_holes, load_bgr, verify_rgb_preserved,
)
from scripts.source_mask_selector import embed_candidates, select     # noqa: E402
from scripts.source_sam2_spike import (                               # noqa: E402
    DEDUPE_IOU, GRID, MODEL_ID, candidate_stats, overlay, plausible,
)

CORPUS = SERVER / "ab_out/llm_qc/corpus"
CACHE = SERVER / "ab_out/augmentation_spike/_sam2_cache"

PRODUCTS = [
    ("stripe", "stripe-shirt", "goldenset-stripe-shirt — the STRIPE case"),
    ("check", "check-shirt", "goldenset-check-shirt — the CHECK / puckered case"),
    ("4ff2132f", "4ff2132f-control", "4ff2132f — 소프트 골지 블라우스 레드"),
]
MAX_CANDIDATES_SHOWN = 6


def cache_key(bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", bgr)
    return hashlib.sha256(buf.tobytes()).hexdigest()[:16]


def cached_masks(bgr: np.ndarray) -> list[np.ndarray] | None:
    path = CACHE / f"{cache_key(bgr)}.npz"
    if not path.exists():
        return None
    z = np.load(path)
    return [z[k].astype(bool) for k in sorted(z.files)]


def source_path(product_dir: pathlib.Path, slot: str) -> pathlib.Path | None:
    hits = sorted(glob.glob(str(product_dir / f"source_{slot}_*.jpg")))
    return pathlib.Path(hits[0]) if hits else None


def emit_canonical(bgr, mask_bool, out_dir: pathlib.Path, stem: str) -> dict:
    """Mask -> the deterministic pipeline. Shared by the automatic and the human-picked path."""
    started = time.perf_counter()
    mask = fill_holes(mask_bool.astype(np.uint8) * 255)
    overlay_path = out_dir / f"{stem}_overlay.jpg"
    mask_path = out_dir / f"{stem}_mask.png"
    cv2.imwrite(str(overlay_path), overlay(bgr, mask > 0), [cv2.IMWRITE_JPEG_QUALITY, 88])
    cv2.imwrite(str(mask_path), mask)
    out = {"maskCoverage": round(float((mask > 0).mean()), 5),
           "overlayFile": str(overlay_path), "maskFile": str(mask_path),
           "occlusion": detect_occlusion(bgr, mask)}
    try:
        canonical, rgba, prov = canonicalise(bgr, mask)
    except ValueError as exc:
        out.update({"error": str(exc)})
        return out
    canonical_path = out_dir / f"{stem}_canonical.png"
    rgba_path = out_dir / f"{stem}_canonical_rgba.png"
    cv2.imwrite(str(canonical_path), canonical)
    cv2.imwrite(str(rgba_path), rgba)
    out.update({"canonicalFile": str(canonical_path), "rgbaFile": str(rgba_path),
                "canonicalSize": [CANVAS, CANVAS], "provenance": prov,
                "preservation": verify_rgb_preserved(bgr, mask, canonical, prov),
                "durationMs": int((time.perf_counter() - started) * 1000)})
    return out


def run(out_root: pathlib.Path, picks: dict[str, int]) -> dict:
    settings = load_settings()
    model_id = settings.embed_image_model
    products = []

    for key, dirname, label in PRODUCTS:
        product_dir = CORPUS / dirname
        out_dir = out_root / key
        out_dir.mkdir(parents=True, exist_ok=True)

        front_path = source_path(product_dir, "Front")
        front_bgr, exif = load_bgr(front_path)
        front_masks = cached_masks(front_bgr)
        record = {"key": key, "label": label, "slot": "Front",
                  "sourceFile": str(front_path),
                  "sourceSize": [front_bgr.shape[1], front_bgr.shape[0]], "exif": exif,
                  "sam2": {"model": MODEL_ID, "grid": GRID, "dedupeIoU": DEDUPE_IOU,
                           "maskSource": "cache" if front_masks is not None else "MISSING"}}
        if front_masks is None:
            record.update({"maskAvailable": False, "status": "UNUSABLE",
                           "statusReasons": ["no cached SAM2 mask for this image"]})
            products.append(record)
            print(f"{key:10s} NO CACHED MASK")
            continue

        # Other views feed cross-view support only. Read from cache; never segmented here.
        pool: list[dict] = []
        support_slots = []
        for slot in ("Front", "Back", "Detail"):
            p = source_path(product_dir, slot)
            if p is None:
                continue
            bgr = front_bgr if slot == "Front" else load_bgr(p)[0]
            masks = front_masks if slot == "Front" else cached_masks(bgr)
            if masks is None:
                continue
            if slot != "Front":
                support_slots.append(slot)
            for i, m in enumerate(masks):
                st = candidate_stats(m)
                ok, why = plausible(st)
                pool.append({"slot": slot, "id": i, "mask": m, "bgr": bgr, "stats": st,
                             "plausible": ok, "reject": why or None})

        embed_candidates(pool, model_id)
        selection = select({"candidates": pool}, model_id, target_slot="Front")
        front_pool = [c for c in pool if c["slot"] == "Front"]
        plausible_front = [c for c in front_pool if c["plausible"]]
        rank_by_id = {r["id"]: r for r in selection["ranking"]}
        shown = sorted(plausible_front,
                       key=lambda c: -(rank_by_id.get(c["id"], {}).get("score") or -9))
        shown = shown[:MAX_CANDIDATES_SHOWN]

        candidate_files = []
        for c in shown:
            fp = out_dir / f"Front_cand{c['id']:02d}.jpg"
            cv2.imwrite(str(fp), overlay(front_bgr, c["mask"]), [cv2.IMWRITE_JPEG_QUALITY, 86])
            r = rank_by_id.get(c["id"], {})
            candidate_files.append({
                "id": c["id"], "file": str(fp), "stats": c["stats"],
                "score": r.get("score"), "crossViewSim": r.get("crossViewSim"),
                "autoSelected": c["id"] == selection.get("selectedId"),
                "humanPicked": picks.get(key) == c["id"]})

        record.update({
            "maskAvailable": True,
            "candidateCount": len(front_pool),
            "plausibleCount": len(plausible_front),
            "supportSlots": support_slots,
            "candidates": candidate_files,
            "selection": {k: v for k, v in selection.items() if k != "ranking"},
            "ranking": selection["ranking"][:6],
        })

        auto = next((c for c in front_pool if c["id"] == selection.get("selectedId")), None)
        if auto is not None:
            record["auto"] = {"candidateId": auto["id"],
                              **emit_canonical(front_bgr, auto["mask"], out_dir, "Front_auto")}
        else:
            record["auto"] = None

        picked_id = picks.get(key)
        if picked_id is not None:
            picked = next((c for c in front_pool if c["id"] == picked_id), None)
            if picked is None:
                record["humanPicked"] = {"error": f"candidate {picked_id} not in pool"}
            else:
                record["humanPicked"] = {
                    "candidateId": picked_id,
                    **emit_canonical(front_bgr, picked["mask"], out_dir, "Front_picked")}

        print(f"{key:10s} candidates={len(front_pool)} plausible={len(plausible_front)} "
              f"selector={selection['state']} auto=#{selection.get('selectedId')} "
              f"margin={selection.get('margin')} support={support_slots} "
              f"picked={picked_id}")
        products.append(record)

    payload = {
        "spike": "source_preserving_augmentation_front_only",
        "generativeApiCalls": 0,
        "note": "masks read from the cancelled run's cache; no segmentation performed here",
        "segmentation": {"model": MODEL_ID, "grid": GRID, "dedupeIoU": DEDUPE_IOU,
                         "restoredFrom": "66eef6d scripts/source_sam2_spike.py",
                         "selectorRestoredFrom": "5977e71 scripts/source_mask_selector.py",
                         "selectorEmbedModel": model_id},
        "canonicalisation": {"canvas": CANVAS, "padFraction": PAD_FRACTION,
                             "backdrop": list(BACKDROP), "featherPx": FEATHER_PX},
        "products": products,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "front_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=lambda o: None),
        encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pick", default="",
                    help="human-chosen candidate ids, e.g. stripe=3,check=1")
    args = ap.parse_args()
    picks = {}
    for part in [p for p in args.pick.split(",") if p.strip()]:
        k, _, v = part.partition("=")
        picks[k.strip()] = int(v)
    run(pathlib.Path(args.out), picks)
    print(f"\nresults -> {args.out}/front_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
