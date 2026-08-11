"""Hybrid experiment — generate two branches per garment, let QC pick the winner.

Reuses, unchanged: the production prompt template and `render_mannequin_prompt`, the six-
garment prep and the SAM cutouts from `ab_out/qa6`, `services.qc` for the deterministic pass,
`agents.garment_fidelity_qc` for the vision pass (3 samples, temperature 0, fail-closed),
`services.garment_fidelity_authority` for turning checks into a decision and for building the
correction text. New here: the branch prompts (`hybrid_branches`) and the winner-pick below.

Two rules the selection encodes.

**Deterministic QC never decides quality.** It only removes candidates that are not images:
undecodable, wrong shape, blank. Everything semantic is the vision judge's. A candidate that
fails the deterministic pass is out before a single vision call is spent on it.

**A correction runs at most once, and only when the failure is recoverable.** Recoverable
means the failures are about how the garment SITS — integration, silhouette, proportion —
which an edit can plausibly fix without redrawing the product. A candidate that failed
`garmentIdentity` or `patternType` is the wrong garment; editing it is polishing the wrong
answer, so it is left failed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import time

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from scripts._env import load_env                                      # noqa: E402

load_env()

from PIL import Image                                                  # noqa: E402

from app.agents import garment_fidelity_qc as gfqc                     # noqa: E402
from app.agents import mannequin                                       # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage     # noqa: E402
from app.agents.model_routing import resolve_model                     # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings                                   # noqa: E402
from app.r2 import R2Client                                            # noqa: E402
from app.services import garment_fidelity_authority as gfa             # noqa: E402
from app.services import garment_fidelity_pass as gf_pass              # noqa: E402
from app.agents import pattern_fidelity_qc as pfq                      # noqa: E402
from app.services import pattern_fidelity_gates as pg                  # noqa: E402
from app.services import qc as det_qc                                  # noqa: E402
from app.workers.mannequin_job import _build_manifest                  # noqa: E402
from scripts import hybrid_branches as hb                              # noqa: E402
from scripts import hybrid_legacy as hlg                               # noqa: E402

QA6 = SERVER / "ab_out/qa6"
OUT = SERVER / "ab_out/hybrid"
CLOTHING_LABEL = {"top": "상의", "bottom": "하의", "outer": "아우터"}

#: Failures an edit can plausibly repair without redrawing the product.
RECOVERABLE = {"garmentBodyIntegration", "silhouette", "lengthAndProportion",
               "generationArtifacts", "patternLayout",
               # category gates an edit can plausibly repair without redrawing the product
               pg.GATE_BODY_INTEGRATION_LOWER, pg.GATE_WAIST_PLACEMENT, pg.GATE_HEM_SHAPE,
               pg.GATE_FRONT_PANEL_SYMMETRY, pg.GATE_LEG_SILHOUETTE,
               pg.GATE_CLOSURE_STATE, pg.GATE_FRONT_OPENING, pg.GATE_ZIPPER_ALIGNMENT}

#: Category routing. TOP compares both arms on QC alone. BOTTOM prefers SAM when the two are
#: close — the brief's default, applied only as a TIE-BREAK so a clearly better baseline still
#: wins. OUTER has no branch preference; its closed-front gates do the work.
PREFERRED_BRANCH = {"bottom": "sam2"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def inline(path) -> InlineImage:
    data = pathlib.Path(path).read_bytes()
    return InlineImage("image/png" if str(path).endswith(".png") else "image/jpeg", data)


def deterministic_qc(image_bytes: bytes, *, aspect: str) -> dict:
    """Stage 1. Mechanical only: is this an image of the right shape, and is it not blank."""
    out = {"passed": False, "failures": [], "width": None, "height": None,
           "bytes": len(image_bytes)}
    if not image_bytes:
        out["failures"].append("empty_provider_response")
        return out
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            im.load()
            out["width"], out["height"] = im.width, im.height
            grey = im.convert("L")
            extrema = grey.getextrema()
            hist = grey.histogram()
            total = sum(hist) or 1
            out["dynamicRange"] = extrema[1] - extrema[0]
            out["dominantToneShare"] = round(max(hist) / total, 4)
    except Exception as exc:                        # noqa: BLE001
        out["failures"].append(f"decode_failed:{type(exc).__name__}")
        return out
    if out["width"] < 512 or out["height"] < 512:
        out["failures"].append(f"resolution_too_small:{out['width']}x{out['height']}")
    try:
        wr, hr = (int(x) for x in aspect.split(":"))
        want, got = wr / hr, out["width"] / out["height"]
        if abs(got - want) / want > 0.02:
            out["failures"].append(f"aspect_ratio:{got:.3f}!={want:.3f}")
    except Exception:                               # noqa: BLE001
        pass
    if out["dynamicRange"] < 24:
        out["failures"].append("blank_or_near_empty")
    if out["dominantToneShare"] > 0.97:
        out["failures"].append("single_tone_image")
    # the repo's own Pillow heuristics, recorded as reference (framing/crop signals)
    try:
        v = det_qc.evaluate_mannequin_qc(image_bytes)
        out["pillow"] = {"verdict": v.verdict, "reasons": list(v.reasons)}
    except Exception as exc:                        # noqa: BLE001
        out["pillow"] = {"error": f"{type(exc).__name__}"}
    out["passed"] = not out["failures"]
    return out


async def category_gates(settings, category, raw_bytes, image: InlineImage) -> dict:
    """Bottom / outer hard gates. Same judge machinery as the pattern gates, own prompts.

    Returns {} for TOP, which owes no category gate — the general fourteen checks already
    cover a top, and inventing gates for it would be policy without evidence.
    """
    required = pg.required_gates(None, category=category)
    if not required:
        return {"required": [], "results": {}, "providerCalls": 0}
    results, calls = await pfq.run_gates(
        settings, required=required, source_front=raw_bytes.get("Front"),
        source_detail=raw_bytes.get("Detail") or raw_bytes.get("Back"), generated=image)
    statuses = {g: (results.get(g) or {}).get("status", "UNVERIFIABLE") for g in required}
    failed = [g for g, s in statuses.items() if pg.gate_blocks(s)]
    return {"required": list(required), "statuses": statuses, "failed": failed,
            "results": results, "providerCalls": calls,
            "allowed": not failed}


async def vision_qc(settings, raw_refs, image: InlineImage) -> dict:
    try:
        verdict, provider = await gfqc.judge(settings, sources=raw_refs, generated=image)
    except Exception as exc:                        # noqa: BLE001
        return {"errored": True, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "outcome": "UNVERIFIABLE", "allowed": False, "failedChecks": [],
                "unverifiableChecks": sorted(gfa.HARD_CHECKS)}
    d = gfa.decide(verdict)
    return {"errored": False, "provider": provider, "outcome": d.decision,
            "allowed": d.allowed, "modelDecision": d.model_decision,
            "modelAgreed": d.model_agreed, "failedChecks": list(d.failed_checks),
            "unverifiableChecks": list(d.unverifiable_checks),
            "softIssues": list(d.soft_issues), "failureReasons": list(d.failure_reasons),
            "confidence": d.confidence, "sampleDecisions": verdict.get("sampleDecisions"),
            "checks": verdict.get("checks"),
            "correctionInstruction": gfa.correction_instruction(verdict, d)}


def blocking(qc: dict, cat: dict | None = None) -> list[str]:
    out = list(qc.get("failedChecks") or []) + list(qc.get("unverifiableChecks") or [])
    out += list((cat or {}).get("failed") or [])
    return out


def recoverable(qc: dict, cat: dict | None = None) -> bool:
    bad = set(blocking(qc, cat))
    return bool(bad) and bad.issubset(RECOVERABLE)


def pick_winner(candidates: dict, category: str = "") -> dict:
    """Winner among candidates that cleared BOTH stages, with the brief's tie-breaks.

    Order: fewest blocking checks, then garmentBodyIntegration, then identity-family checks,
    then soft issues, then confidence. Branch order is the last resort so the result is
    deterministic rather than dict-ordered.
    """
    IDENTITY = ("garmentIdentity", "protectedComponents", "patternType", "materialAppearance")
    ORDER = ["baseline", "sam2", "stage3d"]
    # a candidate is eligible only if it cleared stage 1, the general vision QC AND every
    # category gate it owed. The closed-front rule is enforced here: an outer candidate whose
    # closureState failed cannot be chosen, however good the rest of it looks.
    eligible = [(k, c) for k, c in candidates.items()
                if c.get("deterministic", {}).get("passed")
                and (c.get("vision") or {}).get("allowed")
                and (c.get("categoryGates") or {}).get("allowed", True)]
    if not eligible:
        return {"winner": None, "state": "BLOCKED",
                "reason": "no candidate cleared both the deterministic and the vision gates"}
    if len(eligible) == 1:
        k, c = eligible[0]
        return {"winner": k, "state": "SELECTED",
                "reason": f"only {k} cleared both gates"}

    preferred = PREFERRED_BRANCH.get((category or "").strip().lower())

    def score(item):
        k, c = item
        v = c["vision"]
        cat = c.get("categoryGates") or {}
        checks = v.get("checks") or {}
        integration = checks.get("garmentBodyIntegration", {}).get("status")
        identity_ok = sum(1 for n in IDENTITY
                          if checks.get(n, {}).get("status") == "PASS")
        return (len(blocking(v, cat)),
                0 if integration == "PASS" else 1,
                -identity_ok,
                len(v.get("softIssues") or []),
                -(v.get("confidence") or 0.0),
                # category preference is the LAST discriminator before branch order: it breaks
                # ties, it never overrules a candidate that scored better on real evidence.
                0 if (preferred and k.startswith(preferred)) else 1,
                ORDER.index(k) if k in ORDER else 99)

    ranked = sorted(eligible, key=score)
    best, runner = ranked[0], ranked[1]
    bk, bc = best
    rk = runner[0]
    bs, rs = score(best), score(runner)
    if bs[:5] == rs[:5] and bs[5] != rs[5]:
        reason = (f"{bk} and {rk} are indistinguishable on QC evidence; {bk} preferred because "
                  f"the {category} routing policy prefers {preferred}")
    elif bs[:6] == rs[:6]:
        reason = (f"{bk} and {rk} are indistinguishable on every tie-break "
                  f"(same blocking checks, same body integration, same identity checks, "
                  f"same soft issues, same confidence); {bk} chosen by fixed branch order")
    else:
        parts = []
        if bs[1] != rs[1]:
            parts.append("better garmentBodyIntegration")
        if bs[2] != rs[2]:
            parts.append("more identity checks passing")
        if bs[3] != rs[3]:
            parts.append("fewer soft issues")
        if bs[4] != rs[4]:
            parts.append("higher confidence")
        reason = f"{bk} beats {rk} on " + ", ".join(parts or ["fewer blocking checks"])
    return {"winner": bk, "state": "SELECTED", "reason": reason,
            "ranking": [k for k, _ in ranked]}


_REPEAT_SUFFIX = re.compile(r"_r(\d{2})$")


def base_branch(name: str) -> str:
    """`baseline_r03` -> `baseline`. Repeat suffixes are bookkeeping, not a different arm."""
    return _REPEAT_SUFFIX.sub("", name)


def repeat_index(name: str) -> int | None:
    m = _REPEAT_SUFFIX.search(name)
    return int(m.group(1)) if m else None


def image_dimensions(data: bytes) -> dict | None:
    """[w, h] plus the real container format.

    The harness writes every candidate as `.png`, but the provider actually returns JPEG —
    so this reads the bytes, not the filename.
    """
    try:
        im = Image.open(io.BytesIO(data))
        return {"width": im.width, "height": im.height, "format": im.format}
    except Exception:                               # noqa: BLE001
        return None


async def run_garment(g, *, settings, gemini, base_img, allow_correction=True,
                      contact_ab=False, legacy_ab=False, repeat=1,
                      only_branches: frozenset = frozenset()) -> dict:
    gid = g["garment_id"]
    gd = OUT / gid
    gd.mkdir(parents=True, exist_ok=True)
    slots = [s for s in ("Front", "Back", "Detail") if s in g["views"]]
    raw_imgs = [inline(g["views"][s]["jpeg"]) for s in slots]
    raw_refs = [gfqc.SourceRef(slot=s, image=im, asset_id=f"{gid}-{s}")
                for s, im in zip(slots, raw_imgs)]
    raw_bytes = {s: im.data for s, im in zip(slots, raw_imgs)}

    cut_slots, cut_imgs, cut_files = [], [], {}
    for s in ("Front", "Back"):                     # cutouts only where one was selected
        f = QA6 / "runs" / gid / f"cutout_{s}.png"
        if f.exists():
            cut_slots.append(s); cut_imgs.append(inline(f)); cut_files[s] = str(f)

    product = {"name": g["garment_name"],
               "clothing_type": CLOTHING_LABEL.get(g["category"], g["category"])}
    gender = "men" if (g.get("genderEvidence") or "") == "남성" else "women"
    model = resolve_model(settings, settings.mannequin_tier)
    size = settings.mannequin_image_size
    aspect = settings.mannequin_aspect_ratio
    template = load_prompt_template(settings)
    prod_assets = [{"slot": s, "id": f"{gid}-{s}"} for s in slots]

    # The harness used to pass `analysis={}`, which meant `_product_block` emitted no
    # `- Material:` line and `material_guidance` never ran — so NO arm carried the knit
    # guidance and a knit A/B would have measured nothing. Feed whatever material metadata
    # prep.json has, exactly the way production feeds it from the analysis record.
    analysis = {"materials": g.get("materials") or [],
                "subCategory": g.get("subCategory") or ""}
    ctx = mannequin.prompt_context(
        clothing_type=product["clothing_type"], product_count=len(raw_imgs),
        base_gender=gender,
        image_manifest=_build_manifest(prod_assets, False, product["clothing_type"]))
    production_prompt = render_mannequin_prompt(template, ctx, product, analysis)

    rec = {"garment_id": gid, "garment_name": g["garment_name"], "category": g["category"],
           "genderEvidence": g.get("genderEvidence"),
           "available_views": g["available_views"], "missing_views": g.get("missing_views", []),
           "chosen_views": slots, "cutoutViews": cut_slots, "cutoutFiles": cut_files,
           "sourceFiles": {s: g["views"][s]["jpeg"] for s in slots},
           "viewAuthority": {s: hb.SLOT_ROLE[s] for s in slots},
           "cutoutAuthority": hb.CUTOUT_ROLE,
           "generationModel": model, "imageSize": size, "aspectRatio": aspect,
           "candidates": {}, "imageCalls": 0}

    # Two different controls, and they answer different questions.
    #   --contact-ab : `_nocontact` arms strip ONLY the contact block. Incremental effect of
    #                  the block on top of the new knit guidance and the new cutout wording.
    #   --legacy-ab  : `legacy_*` arms restore the contact block AND the knit guidance AND
    #                  (for SAM) the cutout wording. Whole-change effect, pre vs post.
    # Without either flag the run is exactly the two production branches it always was.
    control_prompt = hb.strip_contact_block(production_prompt) if contact_ab else None
    legacy_applied = None
    if legacy_ab:
        legacy_pr, legacy_applied = hlg.legacy_prompt(production_prompt)
    rec["contactBlock"] = {"present": hb.has_contact_block(production_prompt),
                           "contactAbEnabled": bool(contact_ab),
                           "legacyAbEnabled": bool(legacy_ab),
                           "legacyReversionsApplied": legacy_applied}

    raw_in = ["base"] + slots
    branches = {
        "baseline": (hb.baseline_prompt(production_prompt), [base_img] + raw_imgs, raw_in),
    }
    if contact_ab:
        branches["baseline_nocontact"] = (hb.baseline_prompt(control_prompt),
                                          [base_img] + raw_imgs, raw_in)
    if legacy_ab:
        branches["legacy_baseline"] = (hb.baseline_prompt(legacy_pr),
                                       [base_img] + raw_imgs, raw_in)
    if cut_imgs:
        sam_imgs, sam_in = ([base_img] + raw_imgs + cut_imgs,
                            raw_in + [f"cutout:{s}" for s in cut_slots])
        branches["sam2"] = (
            hb.sam_augmented_prompt(production_prompt, slots, cut_slots, g["category"]),
            sam_imgs, sam_in)
        if contact_ab:
            branches["sam2_nocontact"] = (
                hb.sam_augmented_prompt(control_prompt, slots, cut_slots, g["category"]),
                sam_imgs, sam_in)
        if legacy_ab:
            branches["legacy_sam2"] = (
                hb.sam_augmented_prompt(legacy_pr, slots, cut_slots, g["category"],
                                        legacy_cutout=True),
                sam_imgs, sam_in)
    else:
        skip = {"skipped": True,
                "reason": "no SAM cutout was selected for any view of this garment"}
        rec["candidates"]["sam2"] = skip
        if contact_ab:
            rec["candidates"]["sam2_nocontact"] = dict(skip)
        if legacy_ab:
            rec["candidates"]["legacy_sam2"] = dict(skip)

    # Repeat mode. Generation is stochastic, so a single image per arm cannot separate "the
    # prompt fixed it" from "that seed happened not to fail". Each repeat is its own provider
    # call with its own file; nothing is reused between them.
    if only_branches:
        missing = only_branches - set(branches) - set(rec["candidates"])
        if missing:
            raise SystemExit(f"--branches names not produced by this mode: {sorted(missing)}")
        branches = {k: v for k, v in branches.items() if k in only_branches}
    if repeat > 1:
        branches = {f"{k}_r{i:02d}": v
                    for k, v in branches.items() for i in range(1, repeat + 1)}

    rec["wordingProfile"] = {name: hlg.wording_profile(base_branch(name),
                                                       applied=legacy_applied)
                             for name in list(branches) + list(rec["candidates"])}
    rec["materials"] = analysis["materials"]
    rec["subCategory"] = analysis["subCategory"]
    rec["materialsSource"] = g.get("materialsSource")
    rec["repeat"] = repeat

    for name, (prompt, images, inputs) in branches.items():
        t0 = time.monotonic()
        try:
            res = await gemini.generate_content_image(model, prompt, images, size,
                                                      aspect_ratio=aspect)
        except Exception as exc:                    # noqa: BLE001
            rec["candidates"][name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}",
                                       "inputs": inputs}
            print(f"   {name:9s} PROVIDER ERROR", flush=True)
            continue
        rec["imageCalls"] += 1
        path = gd / f"{name}.png"
        path.write_bytes(res.image)
        cand = {"file": str(path), "inputs": inputs, "imageCalls": 1,
                "branch": base_branch(name), "repeatIndex": repeat_index(name),
                "latencyMs": int((time.monotonic() - t0) * 1000),
                "promptSha256": sha(prompt.encode()), "sha256": sha(res.image),
                "imageDimensions": image_dimensions(res.image),
                "materials": analysis["materials"], "subCategory": analysis["subCategory"],
                "wordingProfile": rec["wordingProfile"][name]}
        cand["deterministic"] = deterministic_qc(res.image, aspect=aspect)
        if not cand["deterministic"]["passed"]:
            cand["vision"] = {"skipped": True,
                              "reason": "deterministic QC failed — no vision call spent"}
            rec["candidates"][name] = cand
            print(f"   {name:9s} DET-FAIL {cand['deterministic']['failures']}", flush=True)
            continue
        img = InlineImage(res.mime, res.image)
        cand["vision"] = await vision_qc(settings, raw_refs, img)
        cand["categoryGates"] = await category_gates(settings, g["category"], raw_bytes, img)
        rec["candidates"][name] = cand
        v, cg = cand["vision"], cand["categoryGates"]
        print(f"   {name:9s} {v['outcome']:13s} failed={v.get('failedChecks')} "
              f"catGates={cg.get('failed') if cg.get('required') else '—'}", flush=True)

    # ── at most one targeted correction, only for recoverable failures ────────
    if allow_correction:
        for name in list(rec["candidates"]):
            c = rec["candidates"][name]
            if c.get("skipped") or c.get("error") or c.get("corrected"):
                continue
            v = c.get("vision") or {}
            cg = c.get("categoryGates") or {}
            if v.get("skipped") or v.get("errored"):
                continue
            if v.get("allowed") and cg.get("allowed", True):
                continue
            if not recoverable(v, cg):
                c["correction"] = {"attempted": False,
                                   "reason": "failures are not recoverable by an edit: "
                                             + ", ".join(blocking(v, cg))}
                continue
            instruction = v.get("correctionInstruction") or ""
            for gate in (cg.get("failed") or []):
                node = (cg.get("results") or {}).get(gate) or {}
                extra = (node.get("correctionInstruction") or "").strip()
                instruction += f"\n- restore the source {gate}"
                if extra and not gfa.is_vague(extra):
                    instruction += f" ({extra[:220]})"
            if "garmentBodyIntegration" in blocking(v, cg):
                instruction += "\n\n" + hb.BODY_INTEGRATION_CORRECTION
            manifest = gf_pass.build_correction_manifest(slots)
            prompt = gf_pass.render_correction_prompt(instruction, manifest)
            t0 = time.monotonic()
            try:
                res = await gemini.generate_content_image(
                    model, prompt, [inline(c["file"])] + raw_imgs, size, aspect_ratio=aspect)
            except Exception as exc:                # noqa: BLE001
                c["correction"] = {"attempted": True,
                                   "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                continue
            rec["imageCalls"] += 1
            p = gd / f"{name}_corrected.png"
            p.write_bytes(res.image)
            corr = {"file": str(p), "imageCalls": 1, "parent": name,
                    "latencyMs": int((time.monotonic() - t0) * 1000),
                    "instruction": instruction,
                    "deterministic": deterministic_qc(res.image, aspect=aspect)}
            if corr["deterministic"]["passed"]:
                cimg = InlineImage(res.mime, res.image)
                corr["vision"] = await vision_qc(settings, raw_refs, cimg)
                corr["categoryGates"] = await category_gates(
                    settings, g["category"], raw_bytes, cimg)
            else:
                corr["vision"] = {"skipped": True, "reason": "deterministic QC failed"}
                corr["categoryGates"] = {"required": [], "results": {}}
            c["correction"] = {"attempted": True, **corr}
            rec["candidates"][f"{name}+corr"] = {
                "file": corr["file"], "inputs": c["inputs"] + ["correction"],
                "imageCalls": 1, "deterministic": corr["deterministic"],
                "vision": corr["vision"],
                "categoryGates": corr.get("categoryGates", {}), "derivedFrom": name}
            cv = corr["vision"]
            print(f"   {name}+corr {str(cv.get('outcome')):13s} "
                  f"failed={cv.get('failedChecks')}", flush=True)

    rec["categoryGatesRequired"] = list(pg.required_gates(None, category=g["category"]))
    rec["routing"] = {"category": g["category"],
                      "preferredBranch": PREFERRED_BRANCH.get(g["category"]),
                      "policy": ("TOP: QC decides between both arms"
                                 if g["category"] == "top" else
                                 "BOTTOM: prefer SAM-AUGMENTED when candidates are close"
                                 if g["category"] == "bottom" else
                                 "OUTER: closed-front gates must pass; no branch preference")}
    # Control arms exist to be measured, not to be shipped — the routing policy only ever
    # chooses between the arms production actually has. In repeat mode there is no single
    # production candidate to choose, and reporting one would read as a verdict this run
    # cannot support.
    if repeat > 1 or only_branches:
        rec["selection"] = {"winner": None, "state": "N/A",
                            "reason": "repeatability study — arms are measured, not ranked"}
    else:
        rec["selection"] = pick_winner(
            {k: v for k, v in rec["candidates"].items() if k in PRODUCTION_BRANCHES},
            g["category"])
    # The named-arm tables assume one candidate per arm; under --repeat the per-repeat rows in
    # `candidates` are the record, and a 4-row table would just show blanks.
    if repeat == 1:
        if contact_ab:
            rec["contactAB"] = _arm_table(rec["candidates"], CONTACT_AB_ARMS)
        if legacy_ab:
            rec["legacyAB"] = _arm_table(rec["candidates"], LEGACY_AB_ARMS)
    return rec


#: Everything the routing policy is allowed to choose between. An experiment arm is never a
#: shippable candidate, no matter how well it scores.
PRODUCTION_BRANCHES = frozenset({"baseline", "sam2", "baseline+corr", "sam2+corr", "stage3d"})

#: Incremental ablation — contact block ON vs OFF, everything else new in every arm.
CONTACT_AB_ARMS = {"A1": "baseline_nocontact", "A2": "baseline",
                   "B1": "sam2_nocontact", "B2": "sam2"}

#: Whole-change comparison — production before vs production after.
LEGACY_AB_ARMS = {"LEGACY_BASELINE": "legacy_baseline", "NEW_BASELINE": "baseline",
                  "LEGACY_SAM": "legacy_sam2", "NEW_SAM": "sam2"}


def _arm_table(candidates: dict, arms: dict) -> dict:
    """Named arms side by side. Reports what each arm's QC said — it does not pick."""
    out = {}
    for arm, branch in arms.items():
        c = candidates.get(branch) or {}
        v, cg = c.get("vision") or {}, c.get("categoryGates") or {}
        integration = ((v.get("checks") or {}).get("garmentBodyIntegration") or {}).get("status")
        contact, knit, cutout = hlg.BRANCH_WORDING[branch]
        out[arm] = {"branch": branch,
                    "sam": branch.endswith("sam2"),
                    "contactBlock": contact == "new",
                    "knitGuidance": knit,
                    "cutoutWording": cutout,
                    "skipped": c.get("skipped", False),
                    "error": c.get("error"),
                    "file": c.get("file"),
                    "outcome": v.get("outcome"),
                    "garmentBodyIntegration": integration,
                    "failedChecks": v.get("failedChecks"),
                    "categoryGatesFailed": cg.get("failed") if cg.get("required") else None}
    return out


async def main_async(args):
    import psycopg
    from psycopg.rows import dict_row
    settings = load_settings()
    r2 = R2Client(settings)
    gemini = GeminiImageClient(settings)
    OUT.mkdir(parents=True, exist_ok=True)
    prep = json.loads((QA6 / "prep.json").read_text(encoding="utf-8"))
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    if only:
        prep = [g for g in prep if g["garment_id"] in only]

    async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            await cur.execute("select r2_key, mime_type from assets where id = %s",
                              (settings.base_mannequin_women_asset_id,))
            row = await cur.fetchone()
    base_img = InlineImage(row["mime_type"],
                           await asyncio.to_thread(r2.get_bytes, row["r2_key"]))

    rf = OUT / "results.json"
    existing = ({g["garment_id"]: g for g in
                 json.loads(rf.read_text(encoding="utf-8"))["garments"]}
                if rf.exists() else {})
    order = [g["garment_id"] for g in
             json.loads((QA6 / "prep.json").read_text(encoding="utf-8"))]
    # A correction rewrites the prompt with body-integration language of its own, which is the
    # very thing under test — so the A/B measures first-pass output only.
    ab = args.contact_ab or args.legacy_ab
    only_branches = frozenset(b.strip() for b in args.branches.split(",") if b.strip())
    repeat = max(1, args.repeat)
    allow_correction = not args.no_correction and not ab and repeat == 1
    if args.legacy_ab:
        hlg.assert_in_sync()        # fail before spending a single image call
    if ab or repeat > 1:
        arms = 2 + 2 * args.contact_ab + 2 * args.legacy_ab
        n = len(only_branches) if only_branches else arms
        print(f"A/B mode: {n} arm(s) x {repeat} repeat(s) per garment, correction disabled "
              f"(contact={args.contact_ab}, legacy={args.legacy_ab}, "
              f"branches={sorted(only_branches) or 'all'})", flush=True)
    for g in prep:
        print(f"\n=== {g['garment_id']} ({g['category']})", flush=True)
        rec = await run_garment(g, settings=settings, gemini=gemini, base_img=base_img,
                                allow_correction=allow_correction,
                                contact_ab=args.contact_ab, legacy_ab=args.legacy_ab,
                                repeat=repeat, only_branches=only_branches)
        print(f"   WINNER: {rec['selection']['winner']} — {rec['selection']['reason']}",
              flush=True)
        for label, table in (("contact", rec.get("contactAB")),
                             ("legacy", rec.get("legacyAB"))):
            for arm, row in (table or {}).items():
                print(f"   [{label}] {arm:15s} {row['branch']:17s} "
                      f"{str(row['outcome']):13s} "
                      f"integration={row['garmentBodyIntegration']} "
                      f"failed={row['failedChecks']}", flush=True)
        existing[rec["garment_id"]] = rec
        merged = [existing[k] for k in order if k in existing]
        rf.write_text(json.dumps({"garments": merged}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    total = sum(g.get("imageCalls", 0) for g in existing.values())
    print(f"\ntotal image calls (all garments in results.json): {total}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-correction", action="store_true")
    ap.add_argument("--contact-ab", action="store_true",
                    help="INCREMENTAL ablation — A1 raw/no-contact, A2 raw/contact, "
                         "B1 SAM/no-contact, B2 SAM/contact. All four keep the new knit "
                         "guidance and the new cutout wording, so this isolates the contact "
                         "block alone. Disables correction so the arms measure the prompt.")
    ap.add_argument("--repeat", type=int, default=1,
                    help="generate each selected branch N times as independent provider "
                         "calls, written as <branch>_rNN.png. Disables correction and "
                         "selection — this measures how often an arm fails, not which wins.")
    ap.add_argument("--branches", default="",
                    help="comma-separated branch names to run, e.g. "
                         "'legacy_baseline,baseline'. Errors if a name is not produced by "
                         "the current mode rather than silently running fewer arms.")
    ap.add_argument("--legacy-ab", action="store_true",
                    help="WHOLE-CHANGE comparison — LEGACY_BASELINE / NEW_BASELINE / "
                         "LEGACY_SAM / NEW_SAM. The legacy arms restore the pre-change "
                         "contact, knit and cutout wording at runtime. Production is never "
                         "modified. Disables correction.")
    args = ap.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
