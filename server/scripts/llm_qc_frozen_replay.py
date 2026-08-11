"""Phase C — does the vision QC tell obvious PASS from obvious FAIL, on artifacts we have seen?

The expectations here were set by LOOKING at the images, not by reading an OpenCV score. That
distinction is the whole point of the exercise: the deterministic pattern/colour metrics are
what we are trying to replace as the semantic authority, so using them as labels would only
measure agreement with the thing under suspicion.

Every case is a real generated cut and its own product's real Front/Back/Detail photographs,
downloaded by `llm_qc_corpus_fetch`. One case is deliberately mismatched — a real cut judged
against a DIFFERENT product's photographs — because a judge that says PASS to everything also
scores well on a corpus of near-misses.

  cd server && .venv/bin/python -m scripts.llm_qc_frozen_replay --corpus <dir> --out <file>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from scripts._env import load_env

load_env()

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.agents import garment_fidelity_qc as gfqc     # noqa: E402
from app.config import load_settings                    # noqa: E402
from app.services import garment_fidelity_authority as gfa   # noqa: E402

#: (case id, source project, generated project, cut version, expected, what we saw)
#: `expected` is the human verdict on the images.
#:
#: Three labels were CORRECTED after the first run, and it matters how. The first pass was
#: read off 520px-tall contact sheets, where a fine two-colour stripe averages to cream, a
#: tonal flap pocket disappears into the body, and a flat jersey and a rib knit look alike.
#: The judge disagreed on exactly those three; going back to the full-resolution pixels
#: showed the judge was right and the sheet-scale reading was wrong. The corrections are
#: recorded here with what the pixels actually show, so the change is auditable and is not
#: "the label moved until the score improved". No prompt, threshold or gate was touched.
CASES = [
    ("faithful-eyelet-tee", "eyelet-puff-square-neck-tee", "eyelet-puff-square-neck-tee", 1,
     "PASS",
     "same dusty-mauve pointelle top: round scoop neck, curved yoke with gathering below, "
     "short cap sleeves without a cuff. Fit is closer to the body than the flat-lay but the "
     "product is not redesigned."),
    ("faithful-henley-pocket-tee", "sheer-top", "sheer-top", 4, "PASS",
     "pink knit tee with the 5-button henley placket AND the flap pocket at the lower right "
     "both present, colour unchanged."),
    ("faithful-lace-top", "lace-top", "lace-top", 4, "PASS",
     "pale pink sheer knit long sleeve: lettuce/scalloped edges and the empire-seam bow are "
     "both still there."),
    # CORRECTED PASS -> FAIL. The product is "보트넥 골지 슬림 니트" — a RIB knit — and the
    # Detail photo is a fine rib close-up. At full resolution the generated top is a flat,
    # satin-smooth black with no knit structure at all. That is the "rib knit -> satin
    # appearance" failure, and the first label missed it because rib does not survive a
    # 520px downscale.
    ("material-loss-boatneck-knit", "boatneck-rib-knit", "boatneck-rib-knit", 1, "FAIL",
     "the source is a fine rib knit; the generated black top is flat satin-smooth jersey "
     "with no rib structure. Note the Detail photo is a grey colourway close-up, so a judge "
     "could also have false-failed baseColor here — it did not."),
    ("redesign-stripe-shirt", "stripe-shirt", "stripe-shirt", 1, "FAIL",
     "the blue/beige stripe is gone: the generated shirt is a near-solid pale pink."),
    # CORRECTED FAIL -> PASS. At 3392x5056 the blue and beige vertical stripes are plainly
    # there, with the collar, placket, buttons and cuffs intact. The first label came from a
    # thumbnail in which the stripe had averaged into cream.
    ("faithful-stripe-crop-shirt", "stripe-crop-shirt", "stripe-crop-shirt", 1, "PASS",
     "the blue/beige vertical stripe IS preserved at full resolution, along with the collar, "
     "placket, buttons and cuffs."),
    ("redesign-check-shirt-texture", "check-shirt", "check-shirt", 1, "FAIL",
     "the windowpane check survives but the heavy vertical crinkle/ruched panels that cover "
     "the whole front of the source are gone — the generated shirt is smooth."),
    ("redesign-4ff-blouse-construction", "red-rib-blouse-goldenset",
     "red-rib-blouse-goldenset", 1, "FAIL",
     "the 4ff2132f blouse, construction changed: the round scoop neck became a SQUARE neck, "
     "the loose cap sleeves gained an elasticated ruffle cuff, and the flared hem became "
     "straight and boxy."),
    # CORRECTED FAIL -> PASS. Cropping the lower torso shows the buttoned flap pocket and the
    # henley placket both present. The first label was read from a full-body thumbnail in
    # which a tonal pocket on a tonal body is invisible.
    ("faithful-pocket-tee", "button-pocket-tee", "button-pocket-tee", 1, "PASS",
     "the buttoned flap pocket at the lower hem and the henley placket are both present in "
     "the generated tee."),
    # the mismatch control: a real cut, judged against a different product entirely
    ("negative-control-wrong-product", "stripe-shirt", "red-rib-blouse-goldenset", 1, "FAIL",
     "a mauve knit top judged against a striped button shirt's photographs. Nothing about "
     "this is the same product; a judge that passes it is not discriminating."),
]


def load_corpus(root: pathlib.Path) -> dict:
    manifest = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    return {p["name"]: p for p in manifest["projects"]}


def sources_for(project: dict) -> list[gfqc.SourceRef]:
    refs = []
    seen_detail = 0
    for src in project["sources"]:
        slot = src["slot"]
        if slot == "Detail":
            seen_detail += 1
            if seen_detail > 1:        # one close-up is evidence; five is a slideshow
                continue
        data = pathlib.Path(src["path"]).read_bytes()
        refs.append(gfqc.SourceRef(slot=slot, image=gfqc.as_inline(data, src.get("mime")),
                                   asset_id=src["assetId"]))
    return list(gfqc.order_sources(refs))


def generated_for(project: dict, version: int):
    for gen in project["generated"]:
        if gen["version"] == version:
            return gen
    raise SystemExit(f"cut v{version} not in {project['name']}")


async def run(corpus_root: pathlib.Path, out_path: pathlib.Path) -> dict:
    settings = load_settings()
    projects = load_corpus(corpus_root)
    results = []
    for case_id, source_project, gen_project, version, expected, seen in CASES:
        src = projects[source_project]
        gen_proj = projects[gen_project]
        gen = generated_for(gen_proj, version)
        refs = sources_for(src)
        generated = gfqc.as_inline(pathlib.Path(gen["path"]).read_bytes(), gen.get("mime"))
        try:
            verdict, provider = await gfqc.judge(settings, sources=refs, generated=generated)
            decision = gfa.decide(verdict)
            row = {
                "case": case_id, "expected": expected,
                "sourceProject": source_project, "generatedProject": gen_project,
                "cutVersion": version, "generatedAssetId": gen["assetId"],
                "sourceAssetIds": [r.asset_id for r in refs],
                "whatWeSaw": seen,
                "provider": provider,
                "qcModel": (settings.model_text_gemini if provider == "gemini"
                            else settings.model_text),
                "qcPromptVersion": verdict.get("promptVersion"),
                "decision": decision.decision,
                "modelDecision": decision.model_decision,
                "modelAgreed": decision.model_agreed,
                "failedGates": list(decision.failed_checks),
                "unverifiableGates": list(decision.unverifiable_checks),
                "softIssues": list(decision.soft_issues),
                "failureReasons": list(decision.failure_reasons),
                "confidence": decision.confidence,
                "samples": verdict.get("samples"),
                "sampleDecisions": verdict.get("sampleDecisions"),
                "sampleAgreement": verdict.get("sampleAgreement"),
                "checks": verdict.get("checks"),
                "correctionInstruction": gfa.correction_instruction(verdict, decision),
                "modelCorrectionInstruction": verdict.get("correctionInstruction"),
                "correct": decision.decision == expected
                           or (expected == "FAIL" and decision.decision != "PASS"),
                "error": None,
            }
        except Exception as exc:                      # noqa: BLE001
            row = {"case": case_id, "expected": expected, "decision": "ERROR",
                   "error": f"{type(exc).__name__}: {exc}", "correct": False,
                   "whatWeSaw": seen}
        results.append(row)
        print(f"{case_id:38s} EXPECTED={row['expected']:12s} "
              f"LLM={row['decision']:13s} "
              f"GATES={','.join(row.get('failedGates') or []) or '-'} "
              f"UNVERIFIABLE={','.join(row.get('unverifiableGates') or []) or '-'} "
              f"CONF={row.get('confidence')} "
              f"SAMPLES={row.get('sampleDecisions')}")

    summary = summarise(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "cases": results},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "cases": results}


def summarise(results: list[dict]) -> dict:
    passes = [r for r in results if r["expected"] == "PASS"]
    fails = [r for r in results if r["expected"] == "FAIL"]
    accepted = [r for r in passes if r["decision"] == "PASS"]
    rejected = [r for r in fails if r["decision"] != "PASS"]
    return {
        "expectedPass": len(passes),
        "expectedFail": len(fails),
        "correctlyAccepted": len(accepted),
        "correctlyRejected": len(rejected),
        "falseRejections": [r["case"] for r in passes if r["decision"] != "PASS"],
        "falseAcceptances": [r["case"] for r in fails if r["decision"] == "PASS"],
        "errors": [r["case"] for r in results if r["decision"] == "ERROR"],
        # the gate exists to stop redesigns; passing a different garment entirely is
        # disqualifying on its own, whatever the aggregate looks like
        "negativeControlRejected": next(
            (r["decision"] != "PASS" for r in results
             if r["case"] == "negative-control-wrong-product"), None),
    }


def discriminates(summary: dict) -> bool:
    """The bar for spending image-generation calls.

    Not "high accuracy" — the question Phase C asks is narrower: can it tell an obvious
    redesign from an obvious faithful render? So: no false acceptance at all, the mismatch
    control rejected, and at least most of the faithful cases surviving. A judge that rejects
    everything discriminates nothing, which is why the accepted count has a floor too.
    """
    if summary["errors"]:
        return False
    if summary["falseAcceptances"]:
        return False
    if summary["negativeControlRejected"] is not True:
        return False
    return summary["correctlyAccepted"] >= max(1, summary["expectedPass"] - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = asyncio.run(run(pathlib.Path(args.corpus), pathlib.Path(args.out)))
    s = out["summary"]
    print("\n--- summary ---")
    print(json.dumps(s, ensure_ascii=False, indent=2))
    if discriminates(s):
        print("\nLLM_QC_DISCRIMINATES")
        return 0
    print("\nLLM_QC_FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
