"""QA6 — three input strategies over the same six garments, one AI QC for all of them.

Experiment harness, not production code. Everything is read-only except this run's own output
directory: these garments are loose files in a Downloads folder, so there is no project row,
no Product Truth package and no credit anywhere in the loop.

The three arms:

* `baseline` — the production template with the production manifest and the raw seller photos.
  Deliberately unchanged, including the fact that it says nothing about which photo is
  authoritative for what.
* `sam2` — the same raw photos PLUS a background-free cutout per available view, and an
  explicit authority block: Front is front geometry, Back is rear geometry, Detail is material
  and component evidence ONLY and may never change the garment's shape or the pattern's scale.
* `stage3d` — two calls. First a garment-only board (front / three-quarter / back, no body)
  built from the same evidence; then the mannequin, dressed from that board with the original
  photographs still the final authority.

The authority block is part of what arms 2 and 3 ARE, so it is a confound against the
baseline: those arms differ from it by evidence AND by wording. Recorded in the report rather
than pretended away — separating the two would need a fourth arm and twice the calls.

QC is the same for all three: `agents.garment_fidelity_qc`, thirteen checks, three samples at
temperature zero, merged fail-closed, judged against the RAW photographs only. The cutouts are
never shown to the judge — they are an input to generation, not evidence of what the product
is. The Pillow verdict is recorded beside it as a deterministic reference signal, never as the
decision.
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

from app.agents import garment_fidelity_qc as gfqc                     # noqa: E402
from app.agents import mannequin                                       # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage     # noqa: E402
from app.agents.model_routing import resolve_model                     # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings                                   # noqa: E402
from app.r2 import R2Client                                            # noqa: E402
from app.services import garment_fidelity_authority as gfa             # noqa: E402
from app.services import qc as pillow_qc                               # noqa: E402
from app.workers.mannequin_job import _build_manifest                  # noqa: E402

OUT = SERVER / "ab_out/qa6"

#: Slot authority, stated once and reused by arms 2 and 3.
AUTHORITY = """EVIDENCE AUTHORITY — read this before anything else.

FRONT photograph: PRIMARY FRONT GEOMETRY evidence. It decides the front silhouette, the
garment length, the neckline and collar, the placket, buttons, pockets, front seams, and where
the pattern sits on the front.

BACK photograph: PRIMARY REAR GEOMETRY evidence. It decides the rear silhouette, the rear
length, the yoke and back seams, rear sleeve construction, and how the pattern continues
around the back.

DETAIL photograph: MATERIAL / PATTERN / COMPONENT evidence ONLY. It is a macro close-up, so
everything in it is magnified many times. Read the weave, the knit structure, the stripe or
check scale RELATIVE TO THE WHOLE GARMENT SEEN IN THE FRONT PHOTO, ribbing, pucker, lace,
stitching and the look of the buttons. A Detail photograph must NEVER change the garment's
geometry and must NEVER make you draw the pattern larger than it appears in the FRONT photo.
If the Detail and the Front seem to disagree about pattern scale, the FRONT is right.

ISOLATED GARMENT CUTOUT: the same photograph with its background removed. Its pixels are the
original photograph, not a redrawing. Use it as the clearest view of the garment's outline and
proportions for the slot it belongs to."""

PRESERVE = """PRESERVE THE REAL PRODUCT — this is not a redesign.

Do not invent or remove buttons, pockets, zips or trims. Do not change the neckline, the
sleeve type, the sleeve length class or the garment's length class. Do not change the
pattern's orientation or widen a fine pattern. Do not flatten a defining textile structure
such as rib, knit, pucker or lace. Do not add decorative details.

Natural three-dimensional folds and drape are wanted. Product redesign is not."""

STAGE1_TAIL = """WHAT TO OUTPUT
One image containing exactly three views of THE SAME garment, left to right: FRONT,
THREE-QUARTER, BACK. Render it as a clean ghost-mannequin / invisible-form product shot: the
garment holds a natural filled three-dimensional shape as if worn, with realistic folds, but
there is NO body inside it.

ABSOLUTELY NOT IN THE IMAGE: no person, no mannequin, no head, no arms, no hands, no legs, no
skin, no hanger, no shop, no floor, no props, no text, no labels. Plain neutral light-grey
studio background, soft even studio lighting.

The three views must be unmistakably the SAME product: same colour, same pattern at the same
scale, same construction, same proportions."""

STAGE2_HIERARCHY = """INSTRUCTION HIERARCHY — apply in this order when anything disagrees:
1. The ORIGINAL PRODUCT PHOTOGRAPHS are the final authority on what the product IS.
2. The GARMENT RECONSTRUCTION BOARD is an aid for reading three-dimensional shape and drape.
   It is an interpretation, not evidence. Where it disagrees with the original photographs
   about any product fact, THE ORIGINAL PHOTOGRAPHS WIN.
3. The BASE MANNEQUIN in IMAGE 1 is the sole authority for body, pose, camera, framing, crop,
   background and lighting. Keep them identical.

Your task is narrow: put THIS already-reconstructed garment onto THIS mannequin. Do not
reinterpret the garment from scratch."""

CLOTHING_LABEL = {"top": "상의", "bottom": "하의", "outer": "아우터"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_inline(path: str | pathlib.Path) -> InlineImage:
    data = pathlib.Path(path).read_bytes()
    return InlineImage("image/png" if str(path).endswith(".png") else "image/jpeg", data)


def evidence_manifest(slots, cutout_slots, *, base_first=True, board_last=False) -> str:
    lines = []
    i = 1
    if base_first:
        lines.append(f"{i}. BASE MANNEQUIN — the canvas to dress. Keep it identical."); i += 1
    role = {"Front": "PRIMARY FRONT GEOMETRY evidence",
            "Back": "PRIMARY REAR GEOMETRY evidence",
            "Detail": "MATERIAL / PATTERN / COMPONENT evidence only — never geometry"}
    for s in slots:
        lines.append(f"{i}. ORIGINAL {s.upper()} photograph — {role[s]}"); i += 1
    for s in cutout_slots:
        lines.append(f"{i}. ISOLATED {s.upper()} GARMENT CUTOUT — background removed, "
                     f"same pixels as the {s} photograph"); i += 1
    if board_last:
        lines.append(f"{i}. GARMENT RECONSTRUCTION BOARD — front, three-quarter and back views "
                     f"of this same garment rendered without a body. A shape aid only.")
    return "ATTACHED IMAGES, in order:\n" + "\n".join(lines)


async def judge(settings, raw_refs, image: InlineImage) -> dict:
    try:
        verdict, provider = await gfqc.judge(settings, sources=raw_refs, generated=image)
    except Exception as exc:                        # noqa: BLE001
        return {"errored": True, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "outcome": "UNVERIFIABLE", "allowed": False}
    d = gfa.decide(verdict)
    return {"errored": False, "provider": provider,
            "outcome": d.decision, "allowed": d.allowed,
            "modelDecision": d.model_decision, "modelAgreed": d.model_agreed,
            "failedChecks": list(d.failed_checks),
            "unverifiableChecks": list(d.unverifiable_checks),
            "failureReasons": list(d.failure_reasons), "confidence": d.confidence,
            "sampleDecisions": verdict.get("sampleDecisions"),
            "checks": verdict.get("checks"),
            "qcPromptVersion": verdict.get("promptVersion"),
            "correctionInstruction": gfa.correction_instruction(verdict, d)}


async def run_garment(g, picks, *, settings, gemini, base_img, out_root,
                      base_gender="women") -> dict:
    gid = g["garment_id"]
    gd = out_root / gid
    gd.mkdir(parents=True, exist_ok=True)
    slots = [s for s in ("Front", "Back", "Detail") if s in g["views"]]
    raw_imgs = [load_inline(g["views"][s]["jpeg"]) for s in slots]
    raw_refs = [gfqc.SourceRef(slot=s, image=im, asset_id=f"{gid}-{s}")
                for s, im in zip(slots, raw_imgs)]

    cut_slots, cut_imgs, cut_files = [], [], {}
    for s in slots:
        f = OUT / "runs" / gid / f"cutout_{s}.png"
        if f.exists():
            cut_slots.append(s); cut_imgs.append(load_inline(f)); cut_files[s] = str(f)

    product = {"name": g["garment_name"],
               "clothing_type": CLOTHING_LABEL.get(g["category"], g["category"])}
    analysis: dict = {}
    gender = base_gender
    model = resolve_model(settings, settings.mannequin_tier)
    size = settings.mannequin_image_size
    template = load_prompt_template(settings)
    prod_assets = [{"slot": s, "id": f"{gid}-{s}"} for s in slots]

    rec = {**{k: v for k, v in g.items() if k != "views"},
           "chosen_views": slots, "cutoutViews": cut_slots,
           "cutoutPicks": picks.get(gid, {}),
           "baseGender": gender,
           "genderBasis": (f"base mannequin forced to {gender} for this run; "
                           f"filename gender evidence: {g.get('genderEvidence') or 'none'}"),
           "generationModel": model, "imageSize": size,
           "viewAuthority": {s: ("PRIMARY FRONT GEOMETRY" if s == "Front" else
                                 "PRIMARY REAR GEOMETRY" if s == "Back" else
                                 "MATERIAL / PATTERN / COMPONENT ONLY") for s in slots},
           "sourceFiles": {s: g["views"][s]["jpeg"] for s in slots},
           "cutoutFiles": cut_files,
           "method_results": {}}

    async def generate(prompt, images, aspect, label):
        t0 = time.monotonic()
        try:
            res = await gemini.generate_content_image(model, prompt, images, size,
                                                      aspect_ratio=aspect)
        except Exception as exc:                    # noqa: BLE001
            return None, {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "label": label}
        p = gd / f"{label}.png"
        p.write_bytes(res.image)
        return res, {"file": str(p), "latencyMs": int((time.monotonic() - t0) * 1000),
                     "promptSha256": sha(prompt.encode()), "imageCalls": 1}

    # ── 1. baseline: production manifest, production template, raw photos only ──
    ctx = mannequin.prompt_context(
        clothing_type=product["clothing_type"], product_count=len(raw_imgs),
        base_gender=gender, image_manifest=_build_manifest(prod_assets, False,
                                                           product["clothing_type"]))
    p_base = render_mannequin_prompt(template, ctx, product, analysis)
    res, meta = await generate(p_base, [base_img] + raw_imgs,
                               settings.mannequin_aspect_ratio, "baseline")
    if res:
        meta["qc"] = await judge(settings, raw_refs, InlineImage(res.mime, res.image))
        meta["pillow"] = _pillow(res.image)
    meta["inputs"] = ["base"] + slots
    rec["method_results"]["baseline"] = meta

    # ── 2. sam2: raw photos + cutouts + explicit authority ─────────────────────
    if cut_imgs:
        man = evidence_manifest(slots, cut_slots)
        p_sam = (f"{man}\n\n{AUTHORITY}\n\n{PRESERVE}\n\n{p_base}")
        res2, meta2 = await generate(p_sam, [base_img] + raw_imgs + cut_imgs,
                                     settings.mannequin_aspect_ratio, "sam2")
        if res2:
            meta2["qc"] = await judge(settings, raw_refs, InlineImage(res2.mime, res2.image))
            meta2["pillow"] = _pillow(res2.image)
        meta2["inputs"] = ["base"] + slots + [f"cutout:{s}" for s in cut_slots]
    else:
        meta2 = {"skipped": True,
                 "reason": "no usable SAM2 cutout was selected for any view of this garment"}
    rec["method_results"]["sam2"] = meta2

    # ── 3. stage3d: garment board, then dress ──────────────────────────────────
    if cut_imgs:
        man1 = evidence_manifest(slots, cut_slots, base_first=False)
        p1 = (f"Produce a GARMENT-ONLY product visualisation board of the single garment "
              f"shown in the attached photographs.\n\n{man1}\n\n{AUTHORITY}\n\n"
              f"{STAGE1_TAIL}\n\n{PRESERVE}")
        r1, m1 = await generate(p1, raw_imgs + cut_imgs, "16:9", "stage3d_board")
        if r1 is None:
            rec["method_results"]["stage3d"] = {"skipped": True,
                                                "reason": f"stage 1 failed: {m1.get('error')}",
                                                "stage1": m1}
        else:
            board = InlineImage(r1.mime, r1.image)
            man2 = evidence_manifest(slots, [], board_last=True)
            p2 = (f"Dress the mannequin in IMAGE 1 with the garment shown in the attached "
                  f"photographs. The garment has already been reconstructed for you.\n\n"
                  f"{man2}\n\n{AUTHORITY}\n\n{STAGE2_HIERARCHY}\n\n{PRESERVE}\n\n"
                  f"Output ONE photorealistic studio photograph: the mannequin from IMAGE 1 "
                  f"wearing this garment, full body, head to feet, nothing cropped, portrait "
                  f"orientation, plain studio background, barefoot. No grid, no text.")
            r2, m2 = await generate(p2, [base_img] + raw_imgs + [board],
                                    settings.mannequin_aspect_ratio, "stage3d")
            if r2:
                m2["qc"] = await judge(settings, raw_refs, InlineImage(r2.mime, r2.image))
                m2["pillow"] = _pillow(r2.image)
            m2["stage1"] = m1
            m2["inputs"] = ["base"] + slots + ["stage1Board"]
            m2["imageCalls"] = 2
            rec["method_results"]["stage3d"] = m2
    else:
        rec["method_results"]["stage3d"] = {
            "skipped": True, "reason": "no usable SAM2 cutout — stage 1 has nothing to add "
                                       "over the baseline evidence"}
    return rec


def _pillow(image_bytes: bytes) -> dict:
    """Deterministic reference signal only. Never the decision."""
    try:
        v = pillow_qc.evaluate_mannequin_qc(image_bytes)
        return {"verdict": v.verdict, "reasons": list(v.reasons)}
    except Exception as exc:                        # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def main_async(args):
    import psycopg
    from psycopg.rows import dict_row
    settings = load_settings()
    r2 = R2Client(settings)
    gemini = GeminiImageClient(settings)
    tag = args.tag or ("" if args.gender == "women" else f"_{args.gender}")
    results_name = f"results{tag}.json"
    runs_dir = OUT / f"runs{tag}"
    prep = json.loads((OUT / "prep.json").read_text(encoding="utf-8"))
    picks = json.loads(pathlib.Path(args.picks).read_text(encoding="utf-8"))
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    if only:
        prep = [g for g in prep if g["garment_id"] in only]

    async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            base_asset_id = (settings.base_mannequin_men_asset_id if args.gender == "men"
                             else settings.base_mannequin_women_asset_id)
            await cur.execute("select r2_key, mime_type from assets where id = %s",
                              (base_asset_id,))
            row = await cur.fetchone()
    base_img = InlineImage(row["mime_type"],
                           await asyncio.to_thread(r2.get_bytes, row["r2_key"]))

    existing = {}
    rf = OUT / results_name
    if rf.exists():
        existing = {g["garment_id"]: g
                    for g in json.loads(rf.read_text(encoding="utf-8"))["garments"]}
    order = [g["garment_id"] for g in json.loads((OUT / "prep.json").read_text(encoding="utf-8"))]
    results = []
    for g in prep:
        print(f"\n=== {g['garment_id']} ({g['category']}) views={list(g['views'])}", flush=True)
        rec = await run_garment(g, picks, settings=settings, gemini=gemini,
                                base_img=base_img, out_root=runs_dir,
                                base_gender=args.gender)
        for m, r in rec["method_results"].items():
            if r.get("skipped"):
                print(f"   {m:9s} SKIPPED — {r['reason']}", flush=True)
            elif r.get("error"):
                print(f"   {m:9s} ERROR {r['error']}", flush=True)
            else:
                q = r.get("qc") or {}
                print(f"   {m:9s} qc={q.get('outcome')} failed={q.get('failedChecks')}",
                      flush=True)
        results.append(rec)
        existing[rec["garment_id"]] = rec
        merged = [existing[k] for k in order if k in existing]
        (OUT / results_name).write_text(
            json.dumps({"garments": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    calls = sum(r.get("imageCalls", 0) for g in results
                for r in g["method_results"].values() if isinstance(r, dict))
    print(f"\nimage calls: {calls}", flush=True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--gender", default="women", choices=["women", "men"])
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
