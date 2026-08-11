"""QA6 prep — decode the seller folder, then SAM2 every available view.

Slots come from the FILENAME ONLY. `앞면`/bare name = Front, `뒷면`/`후면` = Back,
`디테일` = Detail. Nothing is inferred from folder names: the 아우터 folder contains jeans,
so folder-as-category would have mislabelled half the corpus.

HEIC is decoded with macOS `sips` because this venv has no pillow-heif. One conversion per
file, cached on disk.
"""
from __future__ import annotations
import hashlib, json, pathlib, subprocess, sys, time, unicodedata
sys.path.insert(0, "/Users/nojeong-un/devs/wearless_studio/server")
import cv2, numpy as np
from PIL import Image, ImageOps

ROOT = pathlib.Path("/Users/nojeong-un/Downloads/노션에 있는 의상들")
OUT = pathlib.Path("ab_out/qa6")
WORK = OUT / "sources"; WORK.mkdir(parents=True, exist_ok=True)
CACHE = pathlib.Path("ab_out/augmentation_spike/_sam2_cache"); CACHE.mkdir(parents=True, exist_ok=True)

# (id, name, category, folder, {slot: filename}) — every slot backed by a filename token.
GARMENTS = [
    ("sheer-tee", "여성용 시어 반팔", "top", "상의", "여성",
     {"Front": "여성용_시어_반팔_앞면.jpeg", "Back": "여성용_시어_반팔_후면.jpeg",
      "Detail": "여성용_시어_반팔_디테일.jpeg"}),
    ("grey-knit", "얇은 회색 니트", "top", "상의", None,
     {"Front": "얇은회색니트_앞면.heic", "Back": "얇은회색니트_뒷면.heic"}),
    ("brown-pants", "갈색 면바지", "bottom", "하의", None,
     {"Front": "갈색면바지.heic", "Back": "갈색면바지_뒷면.heic",
      "Detail": "갈색면바지디테일.heic"}),
    ("brown-skirt2", "갈색 치마 2", "bottom", "하의", None,
     {"Front": "갈색치마2.heic", "Back": "갈색치마2후면.heic",
      "Detail": "갈색치마2디테일.heic"}),
    ("red-cardigan", "빨간 가디건", "outer", "아우터", None,
     {"Front": "빨간가디건앞면.heic", "Back": "빨간가디건뒷면.heic"}),
    ("beige-cardigan", "베이지색 가디건", "outer", "아우터", None,
     {"Front": "베이지색가디건.heic", "Detail": "베이지색가디건_디테일.heic"}),
]


def resolve(folder: str, name: str) -> pathlib.Path | None:
    d = ROOT / folder
    want = unicodedata.normalize("NFC", name)
    for f in d.iterdir():
        if unicodedata.normalize("NFC", f.name) == want:
            return f
    return None


def to_jpeg(src: pathlib.Path, dst: pathlib.Path) -> bool:
    if dst.exists():
        return True
    if src.suffix.lower() in (".heic", ".heif") or src.suffix == "":
        r = subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
                           capture_output=True)
        return r.returncode == 0 and dst.exists()
    with Image.open(src) as im:
        ImageOps.exif_transpose(im).convert("RGB").save(dst, quality=95)
    return True


def load_existing() -> dict:
    f = OUT / "prep.json"
    if not f.exists():
        return {}
    return {g["garment_id"]: g for g in json.loads(f.read_text(encoding="utf-8"))}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    from scripts.source_sam2_spike import (MODEL_ID, _device, candidate_stats, dedupe,
                                           overlay, plausible, sam2_candidates)
    from transformers import Sam2Model, Sam2Processor
    dev = _device()
    proc = Sam2Processor.from_pretrained(MODEL_ID)
    sam = Sam2Model.from_pretrained(MODEL_ID).to(dev).eval()

    existing = load_existing()
    for gid, name, cat, folder, gender, slots in GARMENTS:
        if only and gid not in only:
            continue
        gd = WORK / gid; gd.mkdir(parents=True, exist_ok=True)
        rec = {"garment_id": gid, "garment_name": name, "category": cat,
               "sourceFolder": folder, "genderEvidence": gender,
               "available_views": [], "views": {}, "missing_views": []}
        for slot in ("Front", "Back", "Detail"):
            fn = slots.get(slot)
            if not fn:
                rec["missing_views"].append(slot)
                continue
            src = resolve(folder, fn)
            if src is None:
                rec["missing_views"].append(slot)
                print(f"  !! {gid} {slot}: file not found {fn}", flush=True)
                continue
            jpg = gd / f"{slot}.jpg"
            if not to_jpeg(src, jpg):
                rec["missing_views"].append(slot)
                print(f"  !! {gid} {slot}: decode failed", flush=True)
                continue
            rec["available_views"].append(slot)
            entry = {"slot": slot, "sourceFile": str(src), "jpeg": str(jpg),
                     "originalName": unicodedata.normalize("NFC", src.name)}
            with Image.open(jpg) as im:
                entry["size"] = list(im.size)
            # SAM2
            bgr = cv2.imread(str(jpg), cv2.IMREAD_COLOR)
            ok, buf = cv2.imencode(".png", bgr)
            key = hashlib.sha256(buf.tobytes()).hexdigest()[:16]
            npz = CACHE / f"{key}.npz"
            if npz.exists():
                z = np.load(npz); masks = [z[k].astype(bool) for k in sorted(z.files)]
                entry["sam2Source"] = "cache"
            else:
                h, w = bgr.shape[:2]; sc = min(1.0, 1280 / max(h, w))
                small = (cv2.resize(bgr, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)
                         if sc < 1 else bgr)
                t0 = time.perf_counter()
                masks = dedupe(sam2_candidates(sam, proc, cv2.cvtColor(small, cv2.COLOR_BGR2RGB), dev))
                if sc < 1:
                    masks = [cv2.resize(m.astype(np.uint8), (w, h),
                                        interpolation=cv2.INTER_NEAREST).astype(bool) for m in masks]
                np.savez_compressed(npz, **{f"m{i:03d}": m for i, m in enumerate(masks)})
                entry["sam2Source"] = "fresh"
                entry["sam2Secs"] = int(time.perf_counter() - t0)
            entry["cacheKey"] = key
            cands = []
            for i, m in enumerate(masks):
                st = candidate_stats(m); good, why = plausible(st)
                if not good:
                    continue
                cf = gd / f"{slot}_cand{i:02d}.jpg"
                cv2.imwrite(str(cf), overlay(bgr, m), [cv2.IMWRITE_JPEG_QUALITY, 84])
                cands.append({"id": i, "file": str(cf), "stats": st})
            entry["candidates"] = cands
            entry["candidateCount"] = len(masks)
            rec["views"][slot] = entry
            print(f"  {gid:16s} {slot:7s} masks={len(masks):3d} plausible={len(cands):3d} "
                  f"{entry['sam2Source']}", flush=True)
        existing[gid] = rec
        ordered = [existing[g[0]] for g in GARMENTS if g[0] in existing]
        (OUT / "prep.json").write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2))
    print("prep done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
