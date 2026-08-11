"""Regression for the pattern/material blind spot — the exact candidates, the human labels.

The defect this closes: the live stripe shirt returned PASS 3/3 from the general judge. At
matched garment scale the source is a dense brown+blue PAIRED stripe (~22 pairs across the
chest) and the generated shirt is a sparse, washed pink/lilac single stripe (~13 lines). Both
are "striped", which is why the general `patternType` check answered PASS honestly.

Labels here were set by looking at source and candidate cropped to the SAME FRACTION OF THE
GARMENT'S WIDTH. That correction matters: an earlier comparison put a 5712px macro close-up
beside a full-body frame and drew a conclusion about stripe density from it, which is not a
comparison of anything.

The positive controls are identity controls — a source photograph passed in as the candidate.
A stripe cannot differ from itself, so a gate that fails one of these has become
"stripe = always fail" and is useless. They are the cheapest honest positive available:
every real mannequin cut of a striped shirt in the corpus is unfaithful, which is itself the
finding.

  cd server && .venv/bin/python -m scripts.llm_qc_pattern_regression --corpus <dir> --out <file>
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import pathlib
import sys

from scripts._env import load_env

load_env()

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.agents import garment_fidelity_qc as gfqc          # noqa: E402
from app.agents import pattern_fidelity_qc as pfq           # noqa: E402
from app.config import load_settings                         # noqa: E402
from app.services import pattern_fidelity_gates as pg        # noqa: E402

LIVE = SERVER / "ab_out/llm_qc/live_hard"
LIVE_CONTROL = SERVER / "ab_out/llm_qc/live"


def corpus_cut(corpus: pathlib.Path, project: str, version: int) -> pathlib.Path:
    hits = sorted(glob.glob(str(corpus / project / f"cut_v{version}_*.png")))
    if not hits:
        raise SystemExit(f"no cut v{version} for {project}")
    return pathlib.Path(hits[0])


def corpus_source(corpus: pathlib.Path, project: str, slot: str) -> pathlib.Path | None:
    hits = sorted(glob.glob(str(corpus / project / f"source_{slot}_*.jpg")))
    return pathlib.Path(hits[0]) if hits else None


#: (case id, source project, candidate path builder, gates, expected per gate, what we saw)
#: `expected` maps gate -> "PASS" (must not block) or "FAIL" (must block).
def build_cases(corpus: pathlib.Path) -> list[dict]:
    return [
        # ── THE REGRESSION. §8: this exact candidate must be rejected. ────────────
        {
            "case": "live-stripe-C0-regression",
            "source": "stripe-shirt",
            "candidate": LIVE / "stripe/C0.png",
            "gates": (pg.GATE_STRIPE,),
            "expected": {pg.GATE_STRIPE: "FAIL"},
            "seen": "THE BLIND SPOT. Source: brown and blue lines in tight PAIRS, about 22 "
                    "pairs across the chest, on a greenish-cream ground. Generated: about 13 "
                    "isolated stripes, no pairing, colours washed to pale pink and lilac. "
                    "The general judge returned PASS 3/3 on this candidate.",
        },
        {
            "case": "frozen-stripe-older-cut",
            "source": "stripe-shirt",
            "candidate": corpus_cut(corpus, "stripe-shirt", 1),
            "gates": (pg.GATE_STRIPE,),
            "expected": {pg.GATE_STRIPE: "FAIL"},
            "seen": "the same product generated earlier, failing the same way: roughly half "
                    "the stripe density, pairing lost, pink/lilac instead of brown/blue.",
        },
        # CORRECTED FAIL -> PASS. Measured against the flat lay's actual body extent
        # (x 0.13..0.50), not a guessed one, the generated cut holds ~30 stripes across the
        # chest exactly like the source, keeps the taupe/blue alternation and keeps both
        # colours. Contrast is softer, which is a real but lesser defect. The first label
        # came from a crop that sampled a much wider slice of the source than of the
        # candidate, which measures nothing.
        {
            "case": "positive-stripe-crop-shirt-real-cut",
            "source": "stripe-crop-shirt",
            "candidate": corpus_cut(corpus, "stripe-crop-shirt", 1),
            "gates": (pg.GATE_STRIPE,),
            "expected": {pg.GATE_STRIPE: "PASS"},
            "seen": "REAL-CUT POSITIVE: same stripe density (~30 across the chest), same "
                    "taupe/blue alternation, both colours still present. Lower contrast than "
                    "the source but not a different design.",
        },
        # ── POSITIVE CONTROLS. §9: the gate must not become 'always fail'. ────────
        {
            "case": "positive-stripe-identity",
            "source": "stripe-shirt",
            "candidate": corpus_source(corpus, "stripe-shirt", "Front"),
            "gates": (pg.GATE_STRIPE,),
            "expected": {pg.GATE_STRIPE: "PASS"},
            "seen": "identity control: the product's own front photograph as the candidate. "
                    "The stripe is the same stripe by construction.",
        },
        {
            "case": "positive-check-identity",
            "source": "check-shirt",
            "candidate": corpus_source(corpus, "check-shirt", "Front"),
            "gates": (pg.GATE_CHECK,),
            "expected": {pg.GATE_CHECK: "PASS"},
            "seen": "identity control for the check gate: the product's own front photograph.",
        },
        # CORRECTED PASS -> FAIL, and this one changes a previous conclusion. At native
        # resolution the source is a POINTELLE knit: rows of punched holes forming diamond
        # motifs. The live control candidate has only faint vertical pressed lines and no
        # holes at all. The general 13-check judge passed its `materialAppearance` 3/3 — the
        # same blind spot as the stripe, on the control product.
        {
            "case": "material-pointelle-to-plain-rib-4ff-live",
            "source": None,          # uses the live control run's own sources
            "candidate": LIVE_CONTROL / "4ff2132f/C0.png",
            "sources_dir": LIVE_CONTROL / "4ff2132f",
            "gates": (pg.GATE_SURFACE_TEXTURE,),
            "expected": {pg.GATE_SURFACE_TEXTURE: "FAIL"},
            "seen": "source is pointelle openwork — punched holes in diamond motifs, clearly "
                    "perforated; the generated control candidate shows only faint vertical "
                    "pressed ribs with no holes.",
        },
        {
            "case": "positive-texture-identity-rib",
            "source": "boatneck-rib-knit",
            "candidate": corpus_source(corpus, "boatneck-rib-knit", "Detail"),
            "gates": (pg.GATE_SURFACE_TEXTURE,),
            "expected": {pg.GATE_SURFACE_TEXTURE: "PASS"},
            "seen": "identity control for the texture gate: the product's own fabric close-up "
                    "as the candidate. Rib cannot differ from itself.",
        },
        {
            "case": "positive-texture-identity-pointelle",
            "source": None,
            "candidate": None,       # filled below from the live control's own detail photo
            "sources_dir": LIVE_CONTROL / "4ff2132f",
            "candidate_from_source": "Detail",
            "gates": (pg.GATE_SURFACE_TEXTURE,),
            "expected": {pg.GATE_SURFACE_TEXTURE: "PASS"},
            "seen": "identity control on the control product's own pointelle close-up.",
        },
        # ── MATERIAL REGRESSIONS. §10. ────────────────────────────────────────────
        {
            "case": "material-rib-to-satin",
            "source": "boatneck-rib-knit",
            "candidate": corpus_cut(corpus, "boatneck-rib-knit", 1),
            "gates": (pg.GATE_SURFACE_TEXTURE,),
            "expected": {pg.GATE_SURFACE_TEXTURE: "FAIL"},
            "seen": "source is a fine rib knit with shadowed channels; generated is flat "
                    "satin-smooth black with no rib structure at all.",
        },
        {
            "case": "material-crinkle-to-flat",
            "source": "check-shirt",
            "candidate": LIVE / "check/C0.png",
            "gates": (pg.GATE_SURFACE_TEXTURE,),
            "expected": {pg.GATE_SURFACE_TEXTURE: "FAIL"},
            "seen": "source has heavy vertical crinkled/puckered seersucker panels across the "
                    "whole front; the generated shirt is completely smooth.",
        },
        {
            "case": "material-lace-opacity",
            "source": "lace-top",
            "candidate": corpus_cut(corpus, "lace-top", 4),
            "gates": (pg.GATE_LACE_STRUCTURE, pg.GATE_OPACITY),
            "expected": {pg.GATE_OPACITY: "FAIL"},
            "seen": "source is a semi-sheer pale pink knit — the hanger and the background "
                    "read through it; the generated top is materially opaque.",
        },
    ]


def load_sources(corpus: pathlib.Path, case: dict) -> tuple[bytes | None, bytes | None]:
    if case.get("sources_dir"):
        d = pathlib.Path(case["sources_dir"])
        front = sorted(glob.glob(str(d / "source_Front_*.jpg")))
        detail = sorted(glob.glob(str(d / "source_Detail_*.jpg")))
        return (pathlib.Path(front[0]).read_bytes() if front else None,
                pathlib.Path(detail[0]).read_bytes() if detail else None)
    front = corpus_source(corpus, case["source"], "Front")
    detail = corpus_source(corpus, case["source"], "Detail")
    return (front.read_bytes() if front else None,
            detail.read_bytes() if detail else None)


async def run(corpus: pathlib.Path, out_path: pathlib.Path) -> dict:
    settings = load_settings()
    rows = []
    for case in build_cases(corpus):
        if case.get("candidate_from_source"):
            hits = sorted(glob.glob(str(pathlib.Path(case["sources_dir"])
                                        / f"source_{case['candidate_from_source']}_*.jpg")))
            case["candidate"] = hits[0]
        candidate_path = pathlib.Path(case["candidate"])
        front, detail = load_sources(corpus, case)
        generated = gfqc.as_inline(candidate_path.read_bytes())
        try:
            results, calls = await pfq.run_gates(
                settings, required=case["gates"], source_front=front,
                source_detail=detail, generated=generated)
        except Exception as exc:                       # noqa: BLE001
            rows.append({"case": case["case"], "error": f"{type(exc).__name__}: {exc}",
                         "correct": False, "seen": case["seen"]})
            print(f"{case['case']:34s} ERROR {type(exc).__name__}: {exc}")
            continue

        statuses = {g: (results.get(g) or {}).get("status", "MISSING") for g in case["gates"]}
        correct = True
        for gate, want in case["expected"].items():
            got = statuses.get(gate, "MISSING")
            if want == "FAIL":
                correct = correct and pg.gate_blocks(got)
            else:
                correct = correct and not pg.gate_blocks(got)
        rows.append({
            "case": case["case"], "candidate": str(candidate_path),
            "sourceProject": case.get("source"), "gates": list(case["gates"]),
            "expected": case["expected"], "statuses": statuses, "correct": correct,
            "providerCalls": calls, "seen": case["seen"],
            "details": {g: {k: v for k, v in (results.get(g) or {}).items()
                            if k in ("status", "sampleStatuses", "confidence", "evidence",
                                     "correctionInstruction", "properties", "evidenceCrops")}
                        for g in case["gates"]},
            "error": None,
        })
        mark = "OK " if correct else "XX "
        print(f"{mark}{case['case']:34s} expected={case['expected']} got={statuses} "
              f"calls={calls}")

    summary = {
        "cases": len(rows),
        "correct": sum(1 for r in rows if r.get("correct")),
        "wrong": [r["case"] for r in rows if not r.get("correct")],
        "errors": [r["case"] for r in rows if r.get("error")],
        "regressionRejected": next(
            (r["statuses"].get(pg.GATE_STRIPE) for r in rows
             if r["case"] == "live-stripe-C0-regression" and not r.get("error")), None),
        "positivesAccepted": [r["case"] for r in rows
                              if r["case"].startswith("positive-") and r.get("correct")],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "cases": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "cases": rows}


def closed(summary: dict) -> bool:
    """The bar: the exact live candidate is rejected AND no positive control is blocked."""
    if summary["errors"]:
        return False
    if summary["regressionRejected"] not in ("FAIL", "UNVERIFIABLE"):
        return False
    positives = [c for c in summary["positivesAccepted"]]
    return len(positives) >= 5 and not [w for w in summary["wrong"]
                                        if w.startswith("positive-")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = asyncio.run(run(pathlib.Path(args.corpus), pathlib.Path(args.out)))
    print("\n--- summary ---")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("\nPATTERN_QC_BLIND_SPOT_CLOSED" if closed(out["summary"])
          else "\nPATTERN_QC_STILL_UNRELIABLE")
    return 0 if closed(out["summary"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
