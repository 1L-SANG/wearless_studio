"""A/B — does an extra source-preserving garment reference help Gemini keep the product?

Throwaway experiment code on purpose. Six generations, no QC, no correction, no retries for
quality, nothing written anywhere but this run's own directory.

The only variable is B's extra attachment. Same model, same base mannequin, same product
photos in the same order, same image size, same aspect ratio, same prompt template. B appends
one image and one manifest line describing it; A's prompt is byte-for-byte what production
sends today. Nothing in either prompt names a defect, so the comparison cannot be steered.

The canonical Front comes from `ab_out/augmentation_front`, built from a HUMAN-SELECTED SAM2
candidate. That is not production automation and is labelled as such in the report — the
automatic selector failed on all three, and this experiment is about the mask's usefulness,
not about who chose it.
"""
from __future__ import annotations

import argparse
import asyncio
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
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings                                   # noqa: E402
from app.r2 import R2Client                                            # noqa: E402
from app.workers.mannequin_job import (                                # noqa: E402
    _build_manifest, effective_image_size, tier_for_job)
from scripts.llm_qc_live_e2e import build_truth, load_asset_bytes, load_product  # noqa: E402

CANON = SERVER / "ab_out/augmentation_front"

PRODUCTS = [
    ("stripe", "c7f00166-92a1-4be2-8d47-338808fc4eca", "STRIPE shirt (goldenset)"),
    ("check", "96610dbd-7bb5-4133-a703-3630276fa66e", "CHECK / puckered shirt (goldenset)"),
    ("4ff2132f", "4ff2132f-039b-49a4-a34e-8703df85f0df", "4ff2132f — 소프트 골지 블라우스 레드"),
    ("collar-stripe-shirt", "8201ae5c-5631-4d07-ac11-a45e00be5ad5",
     "스트라이프 카라 긴팔 셔츠 — SHIRT (collar + full placket)"),
    ("lace-top", "0db50de3-ab1f-490c-8cc2-c0dff8686a3e",
     "goldenset-lace-top — BLOUSE (sheer, empire seam, lettuce edge)"),
    ("tie-pintuck-blouse", "19886d56-bd35-40c1-b029-fa8f43893261",
     "타이 핀턱 셔링 블라우스 — BLOUSE"),
]

#: `--only` narrows this list; the first three already have A/B outputs and are reused.

#: The one line B adds. Describes what the attachment IS, not what to fix — naming a defect
#: here would make B win on instructions rather than on evidence.
CANON_LINE = ("clean source-preserving garment reference — the SAME product with its "
              "background removed. Its pixels are the original product photograph, not a "
              "redrawing. Use it as the clearest available view of the garment's shape, "
              "pattern, colour and construction.")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def one_product(key, project_id, label, *, conn, settings, r2, gemini,
                      out_root: pathlib.Path) -> dict:
    loaded = await load_product(conn, project_id)
    product, analysis = loaded["product"], loaded["analysis"]
    truth, truth_origin = build_truth(product, analysis, loaded["truthRow"])

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
    prod_assets, prod_imgs, seen = [], [], set()
    for slot, asset_id in source_inputs:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        got = await load_asset_bytes(conn, r2, asset_id)
        if not got:
            continue
        prod_assets.append({"slot": slot, "id": asset_id})
        prod_imgs.append(InlineImage(got[1], got[0]))

    out_dir = out_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    for a, im in zip(prod_assets, prod_imgs):
        (out_dir / f"raw_{a['slot']}_{a['id'][:8]}.jpg").write_bytes(im.data)

    canonical_path = CANON / key / "Front_picked_canonical.png"
    canonical = InlineImage("image/png", canonical_path.read_bytes())

    image_size = effective_image_size(settings, product, analysis, truth)
    model = resolve_model(settings, tier_for_job(settings, None))
    template = load_prompt_template(settings)
    fit_profile = mannequin.effective_fit_profile(analysis, False)

    def build(manifest_extra: str | None):
        manifest = _build_manifest(prod_assets, False, clothing_type)
        if manifest_extra:
            manifest += f"\n{len(prod_assets) + 2}. {manifest_extra}"
        ctx = mannequin.prompt_context(
            clothing_type=clothing_type, product_count=len(prod_imgs) + (1 if manifest_extra else 0),
            base_gender=gender, image_manifest=manifest, fit_profile=fit_profile)
        return render_mannequin_prompt(
            template, ctx, product, analysis,
            seller_canon=settings.seller_text_canonicalize,
            knowledge=settings.retrieval_knowledge, product_truth=truth)

    arms = {
        "A": {"prompt": build(None), "images": [base_img] + prod_imgs,
              "inputs": ["base"] + [a["slot"] for a in prod_assets]},
        "B": {"prompt": build(CANON_LINE), "images": [base_img] + prod_imgs + [canonical],
              "inputs": ["base"] + [a["slot"] for a in prod_assets] + ["CanonicalFront"]},
    }

    record = {
        "key": key, "label": label, "projectId": project_id,
        "productName": product.get("name"), "truthOrigin": truth_origin,
        "generationModel": model, "imageSize": image_size,
        "aspectRatio": settings.mannequin_aspect_ratio,
        "promptVersion": settings.mannequin_prompt_version,
        "rawFiles": [str(out_dir / f"raw_{a['slot']}_{a['id'][:8]}.jpg") for a in prod_assets],
        "rawSlots": [a["slot"] for a in prod_assets],
        "canonicalFile": str(canonical_path),
        "garmentRgbModified": False,
        "arms": {},
    }

    for arm, cfg in arms.items():
        t0 = time.monotonic()
        try:
            res = await gemini.generate_content_image(
                model, cfg["prompt"], cfg["images"], image_size,
                aspect_ratio=settings.mannequin_aspect_ratio)
        except Exception as exc:                     # noqa: BLE001
            record["arms"][arm] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}",
                                   "inputs": cfg["inputs"]}
            print(f"  {key} {arm}: FAILED {type(exc).__name__}")
            continue
        path = out_dir / f"{arm}.png"
        path.write_bytes(res.image)
        record["arms"][arm] = {
            "file": str(path), "inputs": cfg["inputs"],
            "promptSha256": sha(cfg["prompt"].encode()),
            "imageCalls": 1, "latencyMs": int((time.monotonic() - t0) * 1000),
            "sha256": sha(res.image)}
        print(f"  {key} {arm}: ok {record['arms'][arm]['latencyMs']}ms "
              f"inputs={cfg['inputs']}")
    return record


async def main_async(out_root: pathlib.Path) -> dict:
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
            results.append(await one_product(key, pid, label, conn=conn, settings=settings,
                                             r2=r2, gemini=gemini, out_root=out_root))
    calls = sum(1 for r in results for a in r["arms"].values() if "file" in a)
    fails = sum(1 for r in results for a in r["arms"].values() if "error" in a)
    payload = {"experiment": "augmentation_mannequin_ab", "imageCalls": calls,
               "providerFailures": fails, "newSam2Runs": 0,
               "maskProvenance": "HUMAN_SELECTED_EXPERIMENTAL_MASK",
               "products": results}
    (out_root / "ab_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ncalls={calls}/6 failures={fails} -> {out_root}/ab_results.json")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    global PRODUCTS
    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        PRODUCTS = [p for p in PRODUCTS if p[0] in keys]
    asyncio.run(main_async(pathlib.Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
