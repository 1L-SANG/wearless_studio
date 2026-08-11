"""Phase D/G — real generation, real vision QC, real targeted correction. Isolated QA mode.

What is REAL here: the Gemini image model and the prompt production would send it, the vision
QC and its prompt, the correction call, the image budget rule, the cut-authority predicate and
the billing predicate. All of them are the production objects, imported, not reimplemented.

What is ISOLATED: nothing is written. The product, its analysis and its source bytes are READ
from the production record; the Product Truth package is built in memory with the production
builder when the project has no approved revision; the image budget lives in a dict for the
duration of the run; no job row, no cut row, no credit ledger entry and no R2 object is
created. `wouldChargeCredit` is the real `resolve_billable_charge` answer to the real
`qc_scores` this candidate would have been saved with — computed, not applied.

Budget: the production `image_budget` module, unmodified. Three slots exist; this run uses
BASE for C0 and TARGETED_CORRECTION for C1 and never takes FULL_REGENERATION, so a failed
candidate is never re-rolled behind the correction's back.

  cd server && .venv/bin/python -m scripts.llm_qc_live_e2e --out <dir> --products 4ff2132f
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import pathlib
import sys
import time
import uuid

from scripts._env import load_env

load_env()

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from PIL import Image                                          # noqa: E402

from app.agents import garment_fidelity_qc as gfqc             # noqa: E402
from app.agents import pattern_fidelity_qc as pfq              # noqa: E402
from app.agents import mannequin                                # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage   # noqa: E402
from app.agents.model_routing import resolve_model             # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings                           # noqa: E402
from app.r2 import R2Client                                    # noqa: E402
from app.services import garment_fidelity_authority as gfa     # noqa: E402
from app.services import garment_fidelity_pass as gf_pass      # noqa: E402
from app.services import image_budget as ib                    # noqa: E402
from app.services import pattern_evidence as pe                # noqa: E402
from app.services import pattern_fidelity_gates as pg          # noqa: E402
from app.services import product_truth as pt                   # noqa: E402
from app.services.mannequin_cut_authority import (             # noqa: E402
    evaluate_mannequin_cut_authority, resolve_billable_charge)
from app.workers.mannequin_job import (                        # noqa: E402
    _build_manifest, effective_image_size, tier_for_job)

#: The run's roster. The control is first and the run stops there if it cannot generate.
#: Four products, no more: the point is a controlled comparison, not corpus coverage.
PRODUCTS = {
    # THE CONTROL. Its own photographs, its own Product Truth. This project has never had a
    # cut generated for it, so C0 here is genuinely new output and not a replay.
    "4ff2132f": {
        "projectId": "4ff2132f-039b-49a4-a34e-8703df85f0df",
        "label": "4ff2132f — 소프트 골지 블라우스 레드 (CONTROL)",
        "why": "the control product: dusty-mauve pointelle knit, round scoop neck, curved "
               "yoke with gathering, short cap sleeves, flared hem.",
    },
    "stripe": {
        "projectId": "c7f00166-92a1-4be2-8d47-338808fc4eca",
        "label": "goldenset-stripe-shirt (STRIPE)",
        "why": "STRIPE. Approved Product Truth says STRIPE, and this product is in the "
               "previous QA batches, so the result is comparable.",
    },
    "check": {
        "projectId": "96610dbd-7bb5-4133-a703-3630276fa66e",
        "label": "goldenset-check-shirt (CHECK)",
        "why": "CHECK. Approved Product Truth says CHECK, previously QA'd. Its source also "
               "carries heavy vertical crinkle panels, so it tests texture as well as grid.",
    },
    "structural": {
        "projectId": "0db50de3-ab1f-490c-8cc2-c0dff8686a3e",
        "label": "goldenset-lace-top (STRUCTURAL)",
        "why": "structural/trim: openwork knit with lettuce-scalloped edges and an "
               "empire-seam bow. Chosen over the shirring blouse because it has an approved "
               "Product Truth and appears in the previous QA batches, which the brief asks "
               "for so the comparison means something.",
    },
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── read-only loading ────────────────────────────────────────────────────────

async def load_product(conn, project_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id, user_id, title from projects where id = %s", (project_id,))
        project = await cur.fetchone()
        await cur.execute(
            "select id, project_id, name, clothing_type, colors, measurements, "
            "measurements_unknown from products where project_id = %s", (project_id,))
        product = await cur.fetchone()
        await cur.execute(
            "select payload from analyses where project_id = %s order by created_at desc "
            "limit 1", (project_id,))
        analysis_row = await cur.fetchone()
        await cur.execute(
            "select id, version, status, garment_spec, color_spec, pattern_spec, "
            "protected_details, source_fingerprint from product_truth_packages "
            "where project_id = %s and status = 'approved' order by version desc limit 1",
            (project_id,))
        truth_row = await cur.fetchone()
    # psycopg hands back UUID objects; the repo layer stringifies before the domain sees
    # them, and `product_truth` canonicalises with a strict JSON encoder that rejects
    # anything it was not told about. Stringify here so the truth builder gets what
    # production gives it.
    product = {k: (str(v) if isinstance(v, uuid.UUID) else v)
               for k, v in dict(product).items()}
    product["projectId"] = str(product["project_id"])
    product["clothingType"] = product.get("clothing_type")
    analysis = dict((analysis_row or {}).get("payload") or {})
    return {"project": dict(project), "product": product, "analysis": analysis,
            "truthRow": dict(truth_row) if truth_row else None}


async def load_asset_bytes(conn, r2: R2Client, asset_id: str) -> tuple[bytes, str] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select r2_key, mime_type from assets where id = %s", (asset_id,))
        row = await cur.fetchone()
    if not row:
        return None
    return await asyncio.to_thread(r2.get_bytes, row["r2_key"]), row["mime_type"]


def build_truth(product: dict, analysis: dict, truth_row: dict | None) -> tuple[dict, str]:
    """The approved Product Truth for this product, or the same thing built in memory.

    Building it is not a shortcut around approval: `approve_snapshot` runs the real
    `validation_issues` gate, so a product that could not be approved in the app cannot be
    approved here either — it raises.
    """
    if truth_row:
        return {
            "id": str(truth_row["id"]), "version": truth_row["version"],
            "status": "approved",
            "garmentSpec": truth_row["garment_spec"] or {},
            "colorSpec": truth_row["color_spec"] or {},
            "patternSpec": truth_row["pattern_spec"] or {},
            "protectedDetails": truth_row["protected_details"] or {},
            "sourceFingerprint": truth_row["source_fingerprint"],
        }, "approved_revision_from_db"
    draft = pt.build_truth_draft(product, analysis)
    approved = pt.approve_snapshot(draft, actor_id="qa-isolated")
    approved["id"] = "in-memory"
    return approved, "built_in_memory_with_production_builder"


# ── deterministic, non-semantic validation ───────────────────────────────────

def technical_validation(image_bytes: bytes, mime: str, *, aspect_ratio: str) -> dict:
    """The part that stays deterministic: is this a decodable image of the right shape.

    Nothing here judges the garment. That separation is the whole architecture — pixels can
    answer "is this a 2:3 PNG that decodes" and cannot answer "is this the same blouse".
    """
    out = {"decodable": False, "width": None, "height": None, "bytes": len(image_bytes),
           "mime": mime, "aspectRatioOk": None, "errors": []}
    if not image_bytes:
        out["errors"].append("empty_provider_response")
        return out
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            im.load()
            out.update({"decodable": True, "width": im.width, "height": im.height})
    except Exception as exc:                       # noqa: BLE001
        out["errors"].append(f"decode_failed:{type(exc).__name__}")
        return out
    try:
        w_ratio, h_ratio = (int(x) for x in aspect_ratio.split(":"))
        want = w_ratio / h_ratio
        got = out["width"] / out["height"]
        out["aspectRatioOk"] = abs(got - want) / want <= 0.02
        if not out["aspectRatioOk"]:
            out["errors"].append(f"aspect_ratio:{got:.3f}!={want:.3f}")
    except Exception:                              # noqa: BLE001
        out["aspectRatioOk"] = None
    return out


# ── the run ──────────────────────────────────────────────────────────────────

class Budget:
    """The production budget rule, with the ledger in a dict instead of on a job row.

    `image_budget.plan` is the real function; what a job row supplies is memory across a
    restart, which a single-process QA run does not need. Reservations are still taken in the
    instruction before the provider call, and a denial still ends the attempt.
    """

    def __init__(self):
        self.state = ib.empty_budget()
        self.log: list[dict] = []

    def reserve(self, *, request: str, operation: str, candidate=None, attempt=None):
        decision = ib.plan(self.state, request=request, operation=operation,
                           candidate=candidate, attempt=attempt)
        if decision.allowed:
            self.state = decision.budget_after
        self.log.append(decision.as_event())
        return decision

    def summary(self) -> dict:
        used = self.state["used"]
        return {"baseCalls": used[ib.KIND_BASE],
                "targetedCalls": used[ib.KIND_TARGETED_CORRECTION],
                "fullRegenCalls": used[ib.KIND_FULL_REGENERATION],
                "imageCalls": self.state["total"],
                "maxTotal": ib.MAX_TOTAL,
                "budgetRemaining": ib.remaining(self.state)}


async def run_product(key: str, spec: dict, *, conn, settings, r2, gemini,
                      out_dir: pathlib.Path) -> dict:
    project_id = spec["projectId"]
    loaded = await load_product(conn, project_id)
    product, analysis = loaded["product"], loaded["analysis"]
    truth, truth_origin = build_truth(product, analysis, loaded["truthRow"])

    clothing_type = product.get("clothing_type") or "top"
    gender = mannequin.select_base_gender(analysis, clothing_type)
    base_asset_id = (settings.base_mannequin_men_asset_id if gender == "men"
                     else settings.base_mannequin_women_asset_id)
    base = await load_asset_bytes(conn, r2, base_asset_id)
    if base is None:
        return {"productKey": key, "error": "base_mannequin_missing"}
    base_img = InlineImage(base[1], base[0])

    # Source references, exactly as the worker resolves them: the truth's own assets when it
    # has them, the base colour's images otherwise, deduplicated, in slot order.
    truth_role_to_slot = {"FRONT": "Front", "BACK": "Back", "FIT": "Fit"}
    truth_inputs = [(truth_role_to_slot.get(a.get("role"), "Detail"), a.get("assetId"))
                    for a in (truth.get("sourceAssets") or []) if a.get("assetId")]
    source_inputs = truth_inputs or mannequin.base_color_images(product)
    prod_assets, prod_refs, seen = [], [], set()
    for slot, asset_id in source_inputs:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        loaded_asset = await load_asset_bytes(conn, r2, asset_id)
        if loaded_asset is None:
            continue
        prod_assets.append({"slot": slot, "id": asset_id})
        prod_refs.append(gfqc.SourceRef(slot=slot, asset_id=asset_id,
                                        image=InlineImage(loaded_asset[1], loaded_asset[0])))
    if not prod_refs:
        return {"productKey": key, "error": "no_product_images"}

    manifest = _build_manifest(prod_assets, False, clothing_type)
    ctx = mannequin.prompt_context(
        clothing_type=clothing_type, product_count=len(prod_refs), base_gender=gender,
        image_manifest=manifest,
        fit_profile=mannequin.effective_fit_profile(analysis, False))
    prompt = render_mannequin_prompt(
        load_prompt_template(settings), ctx, product, analysis,
        seller_canon=settings.seller_text_canonicalize,
        knowledge=settings.retrieval_knowledge, product_truth=truth)
    image_size = effective_image_size(settings, product, analysis, truth)
    model = resolve_model(settings, tier_for_job(settings, None))

    case_dir = out_dir / key
    case_dir.mkdir(parents=True, exist_ok=True)
    for ref in prod_refs:
        (case_dir / f"source_{ref.slot}_{ref.asset_id[:8]}.jpg").write_bytes(ref.image.data)

    budget = Budget()
    record = {
        "productKey": key, "label": spec["label"], "why": spec["why"],
        "projectId": project_id, "productId": str(product["id"]),
        "productName": product.get("name"), "clothingType": clothing_type,
        "truthOrigin": truth_origin,
        "truthPatternType": (truth.get("patternSpec") or {}).get("type"),
        "sourceAssetIds": [r.asset_id for r in prod_refs],
        "sourceSlots": [r.slot for r in prod_refs],
        "sourceFiles": [str((case_dir / f"source_{r.slot}_{r.asset_id[:8]}.jpg"))
                        for r in prod_refs],
        "generationModel": model, "imageSize": image_size,
        "aspectRatio": settings.mannequin_aspect_ratio,
        "promptSha256": sha(prompt.encode()),
        "promptVersion": settings.mannequin_prompt_version,
        "qcModel": settings.model_text_gemini,
        "qcPromptVersion": gfqc.PROMPT_VERSION,
        "qcSamplesPerJudgement": gfqc.DEFAULT_SAMPLES,
        "qcTemperature": gfqc.JUDGE_TEMPERATURE,
        "candidates": [], "error": None,
    }

    # ── C0: BASE slot, then the provider ─────────────────────────────────────
    reservation = budget.reserve(request=ib.REQUEST_GENERATION, operation="generate",
                                 candidate="A", attempt=1)
    if not reservation.allowed:
        record["error"] = f"budget_denied:{reservation.reason}"
        record["imageBudget"] = budget.summary()
        return record
    t0 = time.monotonic()
    try:
        c0 = await gemini.generate_content_image(
            model, prompt, [base_img] + [r.image for r in prod_refs], image_size,
            aspect_ratio=settings.mannequin_aspect_ratio)
    except Exception as exc:                        # noqa: BLE001
        record["error"] = f"generation_failed:{type(exc).__name__}: {str(exc)[:200]}"
        record["imageBudget"] = budget.summary()
        return record
    record["generationLatencyMs"] = int((time.monotonic() - t0) * 1000)

    corrected_result = {"res": None}
    required_gates = pg.required_gates(truth, product=product)
    gate_calls = {"provider": 0}
    front_bytes = next((r.image.data for r in prod_refs if r.slot == "Front"), None)
    detail_bytes = next((r.image.data for r in prod_refs if r.slot == "Detail"), None)
    record["requiredGates"] = list(required_gates)

    async def judge_fn(image):
        return await gfqc.judge(settings, sources=prod_refs, generated=image)

    async def specialized_fn(image):
        results, calls = await pfq.run_gates(
            settings, required=required_gates, source_front=front_bytes,
            source_detail=detail_bytes, generated=image)
        gate_calls["provider"] += calls
        return results

    async def correct_fn(instruction, image):
        decision = budget.reserve(request=ib.REQUEST_TARGETED_CORRECTION,
                                  operation="garment_fidelity_correction",
                                  candidate="A", attempt=1)
        if not decision.allowed:
            return None
        correction_prompt = gf_pass.render_correction_prompt(
            instruction, gf_pass.build_correction_manifest([r.slot for r in prod_refs]))
        record["correctionPromptSha256"] = sha(correction_prompt.encode())
        (case_dir / "correction_instruction.txt").write_text(instruction, encoding="utf-8")
        try:
            out = await gemini.generate_content_image(
                resolve_model(settings, "image_high"), correction_prompt,
                [image] + [r.image for r in prod_refs], image_size,
                aspect_ratio=settings.mannequin_aspect_ratio)
        except Exception as exc:                    # noqa: BLE001
            record["correctionError"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            return None
        corrected_result["res"] = out
        return InlineImage(out.mime, out.image)

    outcome = await gf_pass.run_fidelity_pass(
        mode=gf_pass.MODE_ENFORCE, candidate=InlineImage(c0.mime, c0.image),
        judge_fn=judge_fn, correct_fn=correct_fn,
        required_gates=required_gates, specialized_fn=specialized_fn)

    raw_by_label = {gf_pass.LABEL_BASE: c0, gf_pass.LABEL_CORRECTED: corrected_result["res"]}
    for attempt in outcome.attempts:
        raw = raw_by_label.get(attempt.label)
        image_bytes = attempt.image.data
        path = case_dir / f"{attempt.label}.png"
        path.write_bytes(image_bytes)
        tech = technical_validation(image_bytes, attempt.image.mime,
                                    aspect_ratio=settings.mannequin_aspect_ratio)
        crop_files = []
        for crop in pe.generated_crops(image_bytes):
            crop_path = case_dir / f"{attempt.label}_{crop.name}.jpg"
            crop_path.write_bytes(crop.data)
            crop_files.append({**crop.as_dict(), "file": str(crop_path)})
        entry = attempt.as_dict()
        entry.update({
            "file": str(path), "sha256": sha(image_bytes),
            "qcCrops": crop_files,
            "technicalValidation": tech,
            "providerLatencyMs": getattr(raw, "latency_ms", None),
            "samples": (attempt.verdict or {}).get("samples"),
            "sampleDecisions": (attempt.verdict or {}).get("sampleDecisions"),
            "sampleAgreement": (attempt.verdict or {}).get("sampleAgreement"),
        })
        record["candidates"].append(entry)

    fidelity_scores = outcome.as_scores()
    # The qc_scores this candidate WOULD have been saved with, and therefore the input the
    # real authority predicate reads. Nothing about it is invented for the report.
    qc_scores = {"outcome": "auto_pass", "garmentFidelityQc": fidelity_scores}
    final_tech = (record["candidates"][-1]["technicalValidation"]
                  if record["candidates"] else {"decodable": False, "errors": ["no_candidate"]})
    technically_valid = bool(final_tech.get("decodable")) and not final_tech.get("errors")
    if not technically_valid:
        # a file that does not decode is not a product, whatever the judge said
        qc_scores["imageQcErrored"] = True
    authority = evaluate_mannequin_cut_authority(qc_scores)
    charge = resolve_billable_charge([{"qc_scores": qc_scores}], 2)

    record.update({
        "garmentFidelityQc": fidelity_scores,
        "qcScoresAsSaved": qc_scores,
        "technicalValidationPassed": technically_valid,
        "authorityAllowed": authority.allowed,
        "authorityReason": authority.reason,
        "wouldChargeCredit": charge.charge,
        "billingReason": charge.reason,
        "imageBudget": budget.summary(),
        "budgetLog": budget.log,
        "visionCallCount": outcome.vision_provider_calls + gate_calls["provider"],
        "generalVisionCalls": outcome.vision_provider_calls,
        "patternGateVisionCalls": gate_calls["provider"],
        "visionJudgements": outcome.vision_calls,
        "finalCandidate": (record["candidates"][-1]["label"] if authority.allowed
                           and record["candidates"] else None),
        "readyState": "READY" if authority.allowed else "BLOCKED",
    })
    return record


async def main_async(args) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    settings = load_settings()
    r2 = R2Client(settings)
    gemini = GeminiImageClient(settings)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    keys = [k.strip() for k in args.products.split(",") if k.strip()]
    unknown = [k for k in keys if k not in PRODUCTS]
    if unknown:
        raise SystemExit(f"unknown products: {unknown}")
    if len(keys) > 4:
        raise SystemExit("this run is capped at four unique products")

    results = []
    async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        await conn.set_read_only(True)     # the isolation is enforced, not just intended
        for key in keys:
            print(f"\n=== {key}: {PRODUCTS[key]['label']}")
            record = await run_product(key, PRODUCTS[key], conn=conn, settings=settings,
                                       r2=r2, gemini=gemini, out_dir=out_dir)
            results.append(record)
            if record.get("error"):
                print(f"  ERROR {record['error']}")
                if key == keys[0]:
                    print("  the control run failed technically — stopping")
                    break
                continue
            print(f"  gates required: {record.get('requiredGates')}")
            for c in record["candidates"]:
                print(f"  {c['label']}: {c['decision']:13s} "
                      f"failed={c['failedChecks']} unverifiable={c['unverifiableChecks']} "
                      f"conf={c['confidence']} samples={c['sampleDecisions']} "
                      f"gates={c.get('gateStatuses')}")
            print(f"  authority={record['readyState']} ({record['authorityReason']}) "
                  f"charge={record['wouldChargeCredit']} budget={record['imageBudget']}")

    payload = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "mode": "isolated-qa-no-db-writes", "products": results}
    (out_dir / "llm_qc_live_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--products", default="4ff2132f")
    args = ap.parse_args()
    payload = asyncio.run(main_async(args))
    print(f"\nresults -> {args.out}/llm_qc_live_results.json")
    return 0 if any(not p.get("error") for p in payload["products"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
