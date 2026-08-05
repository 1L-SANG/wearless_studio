"""Provider-fair runtime cut experiment (local outputs only).

Gemini Pro receives the exact same five reference images, order, manifest and
rendered production prompt prepared for the built-in imagegen arm.  This avoids
calling a six-reference production arm a provider comparison when imagegen can
accept only five references.

No DB or R2 write occurs.  Outputs stay below the gitignored ``server/ab_out``.
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
from app.agents.gemini_image import GeminiImageClient  # noqa: E402
from app.config import load_settings  # noqa: E402
from scripts import experiment_runtime_cut_pipeline as base  # noqa: E402
from scripts import experiment_runtime_cut_pipeline_diverse as diverse  # noqa: E402


OUT = base.OUT / "provider-fair"
ARMS = {
    "production_v1",
    "production_profile_v1",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prompt_for_arm(name: str, arm: str, manifest: str) -> tuple[str, dict | None]:
    fixture = diverse.FIXTURES[name]
    profile = diverse.LEAN_PROFILE if arm == "production_profile_v1" else None
    prompt = cut_generator.build_prompt(
        diverse._spec(fixture),
        fixture["product"],
        analysis=fixture["analysis"],
        manifest=manifest,
        directing_profile=profile,
    )
    return prompt, profile


def prepare(names: list[str], arms: list[str]) -> list[dict]:
    prompt_dir = OUT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for name in names:
        fixture = diverse.FIXTURES[name]
        images, manifest, paths = diverse._imagegen_inputs(fixture)
        for arm in arms:
            prompt, profile = _prompt_for_arm(name, arm, manifest)
            if arm == "production_v1":
                imagegen_prompt = (
                    diverse.OUT
                    / "imagegen-prompts"
                    / f"{name}__final_truth_v2__imagegen_r1.txt"
                )
                if not imagegen_prompt.exists():
                    raise RuntimeError(f"missing prepared imagegen prompt for {name}")
                prepared = imagegen_prompt.read_text(encoding="utf-8")
                if prompt != prepared:
                    raise RuntimeError(f"provider prompt mismatch for {name}")
            prompt_path = prompt_dir / f"{name}__{arm}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            records.append(
                {
                    "id": f"{name}__{arm}",
                    "fixture": name,
                    "arm": arm,
                    "prompt": str(prompt_path),
                    "promptSha256": _sha(prompt.encode()),
                    "directingProfile": profile,
                    "inputs": paths,
                    "inputMimes": [image.mime for image in images],
                    "manifest": manifest,
                    "referenceCount": len(paths),
                }
            )
    (OUT / "experiment_manifest.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


async def run(records: list[dict], *, force: bool, image_size: str) -> list[dict]:
    output_dir = OUT / "gemini-pro"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(load_settings(), mannequin_image_size=image_size)
    client = GeminiImageClient(settings)
    results: list[dict] = []
    for record in records:
        existing = list(output_dir.glob(f"{record['id']}.*"))
        if existing and not force:
            output = existing[0]
            payload = output.read_bytes()
            results.append({
                **record,
                "status": "skipped_existing",
                "output": str(output),
                "provider": "gemini",
                "model": settings.model_image_high,
                "imageSize": image_size,
                "outputMime": (
                    "image/png" if output.suffix.lower() == ".png" else "image/jpeg"
                ),
                "outputSha256": _sha(payload),
            })
            continue
        fixture = diverse.FIXTURES[record["fixture"]]
        images, manifest, _paths = diverse._imagegen_inputs(fixture)
        prompt = Path(record["prompt"]).read_text(encoding="utf-8")
        started = time.perf_counter()
        try:
            result = await client.generate_content_image(
                settings.model_image_high,
                prompt,
                images,
                image_size,
                aspect_ratio=settings.mannequin_aspect_ratio,
            )
            output = output_dir / f"{record['id']}{base._suffix(result.mime)}"
            output.write_bytes(result.image)
            results.append(
                {
                    **record,
                    "status": "generated",
                    "output": str(output),
                    "provider": "gemini",
                    "model": settings.model_image_high,
                    "imageSize": image_size,
                    "outputMime": result.mime,
                    "outputSha256": _sha(result.image),
                    "latencyMs": round((time.perf_counter() - started) * 1000),
                    "usage": result.usage,
                }
            )
        except Exception as exc:
            results.append(
                {**record, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
            )
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
        "--refresh-audit",
        action="store_true",
        help="rebuild the Gemini audit from the frozen manifest and existing outputs only",
    )
    parser.add_argument(
        "--fixtures",
        default=(
            "striped_polo_top,washed_denim_bottom,"
            "charcoal_blazer_outer_outfit_anchor,black_fit_flare_dress"
        ),
    )
    parser.add_argument("--image-size", choices=("1K", "2K", "4K"), default="1K")
    parser.add_argument("--arms", default="production_v1")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.gemini or args.refresh_audit):
        parser.error("choose --prepare, --gemini, and/or --refresh-audit")
    names = [value.strip() for value in args.fixtures.split(",") if value.strip()]
    arms = [value.strip() for value in args.arms.split(",") if value.strip()]
    if any(name not in diverse.FIXTURES for name in names):
        parser.error("unknown fixture")
    if any(arm not in ARMS for arm in arms):
        parser.error("unknown arm")
    if args.refresh_audit:
        frozen = json.loads((OUT / "experiment_manifest.json").read_text(encoding="utf-8"))
        frozen_records = frozen.get("records") or []
        if not isinstance(frozen_records, list) or not frozen_records:
            raise SystemExit("frozen provider-fair manifest has no records")
        records = [
            record
            for record in frozen_records
            if isinstance(record, dict) and record.get("arm") in ARMS
        ]
        if not records:
            raise SystemExit("frozen provider-fair manifest has no current arms")
        print(f"loaded {len(records)} frozen provider-fair arms in {OUT}", flush=True)
    else:
        records = prepare(names, arms)
        print(f"prepared {len(records)} provider-fair arms in {OUT}", flush=True)
    if args.gemini or args.refresh_audit:
        if len(records) > args.max_calls:
            raise SystemExit(
                f"planned Gemini calls {len(records)} exceed --max-calls {args.max_calls}"
            )
        results = await run(records, force=args.force, image_size=args.image_size)
        ok = sum(item["status"] in {"generated", "skipped_existing"} for item in results)
        print(f"Gemini completed {ok}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
