"""Prompt/profile A/B with the complete six-reference service contract.

The current production prompt, its optional directing-profile arm, and the
experiment-only structured candidate all receive the same image bytes in the
same order. Existing current-production outputs are reused only after verifying
that their stored prompt is byte-identical to the prompt rendered today.

No DB or R2 write occurs. Outputs stay under gitignored ``server/ab_out``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from app.agents import cut_generator  # noqa: E402
from app.agents.cut_prompt_v2 import build_candidate_prompt  # noqa: E402
from app.agents.gemini_image import GeminiImageClient  # noqa: E402
from app.config import load_settings  # noqa: E402
from scripts import experiment_runtime_cut_pipeline as base  # noqa: E402
from scripts import experiment_runtime_cut_pipeline_diverse as diverse  # noqa: E402


OUT = base.OUT / "prompt-fair"
ARMS = {"production_v1", "production_profile_v1", "candidate_v2"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prompt(name: str, arm: str, manifest: str) -> tuple[str, dict | None]:
    fixture = diverse.FIXTURES[name]
    profile = diverse.LEAN_PROFILE if arm == "production_profile_v1" else None
    if arm == "candidate_v2":
        prompt = build_candidate_prompt(
            diverse._spec(fixture), fixture["product"],
            analysis=fixture["analysis"], manifest=manifest,
        )
    else:
        prompt = cut_generator.build_prompt(
            diverse._spec(fixture), fixture["product"],
            analysis=fixture["analysis"], manifest=manifest,
            directing_profile=profile,
        )
    return prompt, profile


def prepare(names: list[str], arms: list[str]) -> list[dict]:
    prompt_dir = OUT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for name in names:
        fixture = diverse.FIXTURES[name]
        images, manifest, paths = diverse._inputs(fixture)
        for arm in arms:
            prompt, profile = _prompt(name, arm, manifest)
            prompt_path = prompt_dir / f"{name}__{arm}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            prior_output = None
            if arm == "production_v1":
                prior_prompt = diverse.OUT / "prompts" / f"{name}__final_truth_v2__r1.txt"
                candidates = list(
                    (diverse.OUT / "gemini-pro").glob(f"{name}__final_truth_v2__r1.*")
                )
                if not prior_prompt.exists() or prior_prompt.read_text(encoding="utf-8") != prompt:
                    raise RuntimeError(f"current production prompt changed for {name}")
                if len(candidates) != 1:
                    raise RuntimeError(f"missing unique current production output for {name}")
                prior_output = str(candidates[0])
            records.append({
                "id": f"{name}__{arm}", "fixture": name, "arm": arm,
                "prompt": str(prompt_path), "promptSha256": _sha(prompt.encode()),
                "directingProfile": profile, "inputs": paths,
                "inputMimes": [image.mime for image in images],
                "manifest": manifest, "referenceCount": len(paths),
                "reusedVerifiedOutput": prior_output,
            })
    (OUT / "experiment_manifest.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


async def run(records: list[dict], *, image_size: str, force: bool) -> list[dict]:
    output_dir = OUT / "gemini-pro"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(load_settings(), mannequin_image_size=image_size)
    client = GeminiImageClient(settings)
    results: list[dict] = []
    for record in records:
        if record["arm"] == "production_v1":
            results.append({
                **record, "status": "reused_prompt_verified",
                "output": record["reusedVerifiedOutput"],
                "provider": "gemini", "model": settings.model_image_high,
                "imageSize": image_size,
            })
            continue
        existing = list(output_dir.glob(f"{record['id']}.*"))
        if existing and not force:
            results.append({**record, "status": "skipped_existing", "output": str(existing[0])})
            continue
        fixture = diverse.FIXTURES[record["fixture"]]
        images, _manifest, _paths = diverse._inputs(fixture)
        prompt = Path(record["prompt"]).read_text(encoding="utf-8")
        started = time.perf_counter()
        try:
            result = await client.generate_content_image(
                settings.model_image_high, prompt, images, image_size,
                aspect_ratio=settings.mannequin_aspect_ratio,
            )
            output = output_dir / f"{record['id']}{base._suffix(result.mime)}"
            output.write_bytes(result.image)
            results.append({
                **record, "status": "generated", "output": str(output),
                "provider": "gemini", "model": settings.model_image_high,
                "imageSize": image_size, "outputMime": result.mime,
                "outputSha256": _sha(result.image),
                "latencyMs": round((time.perf_counter() - started) * 1000),
                "usage": result.usage,
            })
        except Exception as exc:
            results.append({
                **record, "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
        await asyncio.sleep(1)
    (output_dir / "run_audit.json").write_text(
        json.dumps({"records": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument(
        "--fixtures",
        default=(
            "striped_polo_top,washed_denim_bottom,"
            "charcoal_blazer_outer_outfit_anchor,black_fit_flare_dress"
        ),
    )
    parser.add_argument(
        "--arms", default="production_v1,production_profile_v1,candidate_v2"
    )
    parser.add_argument("--image-size", choices=("1K", "2K", "4K"), default="1K")
    parser.add_argument("--max-new-calls", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.gemini):
        parser.error("choose --prepare and/or --gemini")
    names = [part.strip() for part in args.fixtures.split(",") if part.strip()]
    arms = [part.strip() for part in args.arms.split(",") if part.strip()]
    if any(name not in diverse.FIXTURES for name in names):
        parser.error("unknown fixture")
    if any(arm not in ARMS for arm in arms):
        parser.error("unknown arm")
    records = prepare(names, arms)
    print(f"prepared {len(records)} prompt-fair records in {OUT}", flush=True)
    if args.gemini:
        new_calls = sum(record["arm"] != "production_v1" for record in records)
        if new_calls > args.max_new_calls:
            raise SystemExit(
                f"planned new Gemini calls {new_calls} exceed --max-new-calls {args.max_new_calls}"
            )
        results = await run(records, image_size=args.image_size, force=args.force)
        ok = sum(item["status"] in {
            "generated", "skipped_existing", "reused_prompt_verified"
        } for item in results)
        print(f"Gemini completed {ok}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
