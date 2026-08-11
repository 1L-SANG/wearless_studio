"""Two-stage spike: reconstruct the garment alone, then dress the fixed mannequin with it.

Throwaway glue. Four image calls, no QC, no correction, no retries, nothing written outside
this run's directory.

The hypothesis is that the current single call asks for too much at once — read messy shop
photos, infer 3D structure, keep construction, keep pattern, fit a body, invent drape. Stage 1
removes the body from the problem; Stage 2 removes the interpretation from it.

Authority is stated explicitly in both prompts because it is the thing most likely to go
wrong: a Detail close-up is a texture reference, and a model that treats it as geometry
enlarges the stripe until the shirt is a different product. Stage 2 is told, in as many words,
that the original photographs outrank the Stage-1 board whenever they disagree — the board is
an interpretation aid, not evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import os
import pathlib
import sys
import time

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from scripts._env import load_env                                      # noqa: E402

load_env()

from app.agents import mannequin                                       # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage     # noqa: E402
from app.agents.model_routing import resolve_model                     # noqa: E402
from app.config import load_settings                                   # noqa: E402
from app.r2 import R2Client                                            # noqa: E402
from app.workers.mannequin_job import effective_image_size, tier_for_job  # noqa: E402
from scripts.llm_qc_live_e2e import build_truth, load_asset_bytes, load_product  # noqa: E402

CANON = SERVER / "ab_out/augmentation_front"

PRODUCTS = [
    ("stripe", "c7f00166-92a1-4be2-8d47-338808fc4eca",
     "STRIPE shirt — cropped/boxy, collar, front placket, buttons, fine muted vertical stripe"),
    ("4ff2132f", "4ff2132f-039b-49a4-a34e-8703df85f0df",
     "4ff2132f — round scoop neck, curved yoke seam with gathering, short cap sleeves, "
     "dusty-mauve pointelle knit, flared hem"),
]

_AUTHORITY = """AUTHORITY OF EACH ATTACHED PHOTOGRAPH — read this before anything else.

FRONT photo: the authority for front silhouette, garment length, neckline and collar, placket,
buttons, pockets, front seams and where the pattern sits on the front.

BACK photo: the authority for rear silhouette, rear length, yoke and back seams, rear sleeve
construction and how the pattern continues around the back.

DETAIL photo(s): a TEXTURE REFERENCE ONLY. They are macro close-ups, so everything in them is
magnified many times. Use them ONLY to read the fabric's weave, the stripe or check scale
RELATIVE TO THE GARMENT SEEN IN THE FRONT PHOTO, ribbing, lace, puckering, stitching and the
look of the buttons. A Detail photo must NEVER change the garment's geometry, and must NEVER
make you draw the pattern larger than it appears in the FRONT photo. If a Detail photo and the
Front photo seem to disagree about how big the pattern is, the FRONT photo is right.

ISOLATED GARMENT CUTOUT: the same product with its background removed. Its pixels are the
original photograph, not a redrawing. It is the clearest view of the garment's outline and
proportions."""

_PRESERVE = """PRESERVE THE REAL PRODUCT. This is not a redesign.

Do NOT invent buttons. Do NOT remove buttons. Do NOT invent pockets. Do NOT change the
neckline. Do NOT change the sleeve type or length class. Do NOT change the garment's length
class. Do NOT change the stripe or check orientation. Do NOT broaden a fine stripe. Do NOT
flatten a defining textile structure such as rib, pointelle, pucker or lace. Do NOT add
decorative details of any kind.

Natural three-dimensional folds and drape ARE allowed and wanted. Product redesign is not."""


def stage1_prompt(manifest: str, must_keep: str) -> str:
    return f"""Produce a GARMENT-ONLY product visualisation board of the single garment shown
in the attached photographs.

{manifest}

{_AUTHORITY}

WHAT TO OUTPUT
One image containing exactly three views of THE SAME garment, left to right:
1. FRONT view
2. THREE-QUARTER view
3. BACK view

Render it as a clean ghost-mannequin / invisible-form product shot: the garment holds a
natural filled three-dimensional shape as if worn, with realistic folds and drape, but there
is NO body inside it.

ABSOLUTELY NOT IN THE IMAGE: no person, no mannequin, no head, no arms, no hands, no legs, no
skin, no hanger, no hook, no shop, no shelves, no floor, no props, no text, no labels, no
watermark. Plain neutral light-grey studio background, soft even studio lighting, a soft
contact shadow at most.

The three views must be unmistakably the SAME product: same colour, same pattern at the same
scale, same construction, same proportions.

{_PRESERVE}

For this specific product, preserve especially: {must_keep}"""


def stage2_prompt(manifest: str, must_keep: str) -> str:
    return f"""Dress the mannequin in IMAGE 1 with the garment shown in the attached
photographs. The garment has already been reconstructed for you.

{manifest}

{_AUTHORITY}

INSTRUCTION HIERARCHY — apply in this order when anything disagrees:
1. The ORIGINAL PRODUCT PHOTOGRAPHS are the final authority on what the product IS: its
   colour, pattern, construction, components and proportions.
2. The GARMENT RECONSTRUCTION BOARD is an aid for reading the garment's three-dimensional
   shape and how it hangs. It is an interpretation, not evidence. Where it disagrees with the
   original photographs about any product fact, the ORIGINAL PHOTOGRAPHS WIN.
3. The BASE MANNEQUIN in IMAGE 1 is the sole authority for the body, the pose, the camera, the
   framing, the crop, the background and the lighting. Keep them identical.

YOUR TASK IS NARROW: put THIS already-reconstructed garment onto THIS mannequin. Do not
reinterpret the garment from scratch, and do not restyle it.

Preserve the garment's identity, silhouette and length, neckline and collar, sleeve
construction, buttons, placket and pockets, its recognisable pattern at the same scale, its
defining material appearance, and its major seams.

Generate natural drape, folds, body interaction, occlusion and shading appropriate to the
fabric.

{_PRESERVE}

For this specific product, preserve especially: {must_keep}

Output ONE photorealistic studio photograph: the mannequin from IMAGE 1, wearing this garment,
full body, head to feet, nothing cropped, portrait orientation, plain studio background,
barefoot. No grid, no collage, no text."""


MUST_KEEP = {
    "stripe": ("the cropped, boxy body length; the shirt collar; the full front button "
               "placket and every button; the sleeve construction; the FINE, MUTED vertical "
               "stripe — thin alternating taupe and blue lines on a cream ground, at the "
               "density seen in the FRONT photo, never widened; the base colour"),
    "4ff2132f": ("the ROUND SCOOP neckline — it is not square; the curved yoke seam across "
                 "the bust with gathering below it; the short cap sleeves with no cuff band; "
                 "the dusty-mauve base colour, which is muted and not rust or orange; the "
                 "pointelle openwork knit surface; the slightly flared hem"),
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def run_one(key, pid, label, *, conn, settings, r2, gemini, out_root):
    loaded = await load_product(conn, pid)
    product, analysis = loaded["product"], loaded["analysis"]
    truth, _origin = build_truth(product, analysis, loaded["truthRow"])
    clothing_type = product.get("clothing_type") or "top"
    gender = mannequin.select_base_gender(analysis, clothing_type)
    base_id = (settings.base_mannequin_men_asset_id if gender == "men"
               else settings.base_mannequin_women_asset_id)
    base = await load_asset_bytes(conn, r2, base_id)
    base_img = InlineImage(base[1], base[0])

    role_slot = {"FRONT": "Front", "BACK": "Back", "FIT": "Fit"}
    truth_inputs = [(role_slot.get(a.get("role"), "Detail"), a.get("assetId"))
                    for a in (truth.get("sourceAssets") or []) if a.get("assetId")]
    source_inputs = truth_inputs or mannequin.base_color_images(product)
    slots, imgs, seen = [], [], set()
    for slot, aid in source_inputs:
        if aid in seen:
            continue
        seen.add(aid)
        got = await load_asset_bytes(conn, r2, aid)
        if got:
            slots.append(slot)
            imgs.append(InlineImage(got[1], got[0]))

    out_dir = out_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    for slot, im in zip(slots, imgs):
        (out_dir / f"source_{slot}_{sha(im.data)[:8]}.jpg").write_bytes(im.data)

    cutout_path = CANON / key / "Front_picked_canonical.png"
    cutout = InlineImage("image/png", cutout_path.read_bytes())
    # Back cutout only when the SOURCE can establish rear geometry. On the stripe shirt the
    # Back photograph is a tight crop of a crumpled garment — no silhouette, no hem, sleeves
    # out of frame — so no cutout is fabricated from it and the raw Back photo is still sent
    # as supporting evidence. On 4ff2132f the Back photograph is a complete rear view, so it
    # was segmented once and its cutout is attached.
    back_path = CANON / key / "Back_picked_canonical.png"
    back_cutout = (InlineImage("image/png", back_path.read_bytes())
                   if back_path.exists() else None)

    model = resolve_model(settings, tier_for_job(settings, None))
    image_size = effective_image_size(settings, product, analysis, truth)
    must_keep = MUST_KEEP[key]
    record = {"key": key, "label": label, "projectId": pid,
              "productName": product.get("name"), "generationModel": model,
              "imageSize": image_size, "sourceSlots": slots,
              "sourceFiles": [str(out_dir / f"source_{s}_{sha(i.data)[:8]}.jpg")
                              for s, i in zip(slots, imgs)],
              "cutoutFile": str(cutout_path),
              "cutoutProvenance": "HUMAN_SELECTED_EXPERIMENTAL_MASK",
              "backCutout": str(back_path) if back_cutout else None,
              "backCutoutNote": (
                  "usable rear source — segmented once, cutout attached as rear geometry "
                  "authority" if back_cutout else
                  "BACK_CUTOUT_UNAVAILABLE_SOURCE_LIMITATION — the Back photograph is a tight "
                  "crop of the crumpled garment: no full rear silhouette, hem out of frame, "
                  "sleeves cut off. No cutout was fabricated from it; the raw Back photograph "
                  "is still supplied to Stage 1 as supporting product evidence."),
              "stages": {}}

    # ── Stage 1 ─────────────────────────────────────────────────────────────
    s1_extra = [("ISOLATED FRONT GARMENT CUTOUT of the same product (background removed)",
                 cutout)]
    if back_cutout is not None:
        s1_extra.append(("ISOLATED BACK GARMENT CUTOUT of the same product (background "
                         "removed) — the authority for rear silhouette, rear length and rear "
                         "sleeve construction", back_cutout))
    s1_manifest = "ATTACHED IMAGES, in order:\n" + "\n".join(
        f"{i + 1}. ORIGINAL {s.upper()} photograph of the product" for i, s in enumerate(slots)
    ) + "\n" + "\n".join(f"{len(slots) + 1 + i}. {lab}"
                          for i, (lab, _im) in enumerate(s1_extra))
    if back_cutout is None:
        s1_manifest += ("\nNOTE: no isolated BACK cutout exists for this product because the "
                        "Back photograph does not show a complete rear view. Read the rear "
                        "from the ORIGINAL BACK photograph, and do not invent rear features "
                        "it does not show.")
    p1 = stage1_prompt(s1_manifest, must_keep)
    t0 = time.monotonic()
    try:
        r1 = await gemini.generate_content_image(
            model, p1, imgs + [im for _lab, im in s1_extra], image_size,
            aspect_ratio="16:9")
    except Exception as exc:                       # noqa: BLE001
        record["stages"]["stage1"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        print(f"  {key} stage1 FAILED {type(exc).__name__}")
        return record
    board_path = out_dir / "stage1_board.png"
    board_path.write_bytes(r1.image)
    record["stages"]["stage1"] = {"file": str(board_path), "imageCalls": 1,
                                  "promptSha256": sha(p1.encode()), "aspectRatio": "16:9",
                                  "latencyMs": int((time.monotonic() - t0) * 1000)}
    print(f"  {key} stage1 ok {record['stages']['stage1']['latencyMs']}ms")

    # ── Stage 2 ─────────────────────────────────────────────────────────────
    board = InlineImage(r1.mime, r1.image)
    s2_manifest = ("ATTACHED IMAGES, in order:\n"
                   "1. BASE MANNEQUIN — the canvas to dress. Authority for body, pose, camera, "
                   "framing, background and lighting. Keep it identical.\n"
                   + "\n".join(f"{i + 2}. ORIGINAL {s.upper()} photograph of the product"
                               for i, s in enumerate(slots))
                   + f"\n{len(slots) + 2}. GARMENT RECONSTRUCTION BOARD — front, three-quarter "
                     "and back views of this same garment, rendered without a body. A shape "
                     "aid only.")
    p2 = stage2_prompt(s2_manifest, must_keep)
    t0 = time.monotonic()
    try:
        r2img = await gemini.generate_content_image(
            model, p2, [base_img] + imgs + [board], image_size,
            aspect_ratio=settings.mannequin_aspect_ratio)
    except Exception as exc:                       # noqa: BLE001
        record["stages"]["stage2"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        print(f"  {key} stage2 FAILED {type(exc).__name__}")
        return record
    final_path = out_dir / "stage2_mannequin.png"
    final_path.write_bytes(r2img.image)
    record["stages"]["stage2"] = {"file": str(final_path), "imageCalls": 1,
                                  "promptSha256": sha(p2.encode()),
                                  "aspectRatio": settings.mannequin_aspect_ratio,
                                  "latencyMs": int((time.monotonic() - t0) * 1000)}
    print(f"  {key} stage2 ok {record['stages']['stage2']['latencyMs']}ms")
    return record


async def main_async(out_root: pathlib.Path):
    import psycopg
    from psycopg.rows import dict_row
    settings = load_settings()
    r2 = R2Client(settings)
    gemini = GeminiImageClient(settings)
    out_root.mkdir(parents=True, exist_ok=True)
    results = []
    async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        await conn.set_read_only(True)
        for key, pid, label in PRODUCTS:
            print(f"\n=== {key}")
            results.append(await run_one(key, pid, label, conn=conn, settings=settings,
                                         r2=r2, gemini=gemini, out_root=out_root))
    calls = sum(1 for r in results for s in r["stages"].values() if "file" in s)
    fails = sum(1 for r in results for s in r["stages"].values() if "error" in s)
    payload = {"experiment": "two_stage_garment_mannequin", "imageCalls": calls,
               "providerFailures": fails, "newSam2Runs": 0, "products": results}
    (out_root / "two_stage_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ncalls={calls}/4 failures={fails}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    global PRODUCTS
    if a.only:
        keys = {k.strip() for k in a.only.split(",") if k.strip()}
        PRODUCTS = [p for p in PRODUCTS if p[0] in keys]
    asyncio.run(main_async(pathlib.Path(a.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
