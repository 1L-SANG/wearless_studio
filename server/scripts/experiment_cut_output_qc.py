"""Run the independent one-cut QC on four provider-fair Gemini outputs.

This exercises the real compiled cut contract and the exact labelled generation
references. No generation, DB or R2 write occurs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from app.agents import cut_generator, cut_output_qc  # noqa: E402
from app.agents.cut_plan import compile_cut_plan  # noqa: E402
from app.config import load_settings  # noqa: E402
from scripts import experiment_runtime_cut_pipeline_diverse as diverse  # noqa: E402


BASE = SERVER / "ab_out/runtime_cut_pipeline_20260804"
OUT = BASE / "cut-qc"
NAMES = (
    "striped_polo_top",
    "washed_denim_bottom",
    "charcoal_blazer_outer_outfit_anchor",
    "black_fit_flare_dress",
)


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    records = []
    for name in NAMES:
        fixture = diverse.FIXTURES[name]
        images, manifest, paths = diverse._imagegen_inputs(fixture)
        spec = cut_generator.normalize_spec(
            diverse._spec(fixture), clothing_type=fixture["product"]["clothingType"]
        )
        spec = cut_generator.apply_reference_compatibility(spec)
        plan = compile_cut_plan(
            spec,
            fixture["product"]["clothingType"],
            fit_profile=fixture["analysis"].get("fitProfile"),
        )
        output = (
            BASE / "provider-fair/gemini-pro" / f"{name}__production_v1.jpg"
        )
        references = cut_output_qc.references_from_manifest(manifest, images)
        result = await cut_output_qc.verdict(
            settings, plan, references, diverse.base._load(output)
        )
        records.append({
            "fixture": name,
            "manifest": manifest,
            "inputs": paths,
            "output": str(output),
            "result": result,
        })
    destination = OUT / "run_audit.json"
    destination.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
