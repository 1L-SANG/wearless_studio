"""Local-only neck-band/blob evaluation; never uploads full photographs or writes a DB."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
import sys
import unicodedata

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import image_usage
from app.agents import face_identity as fi, face_seam_repair as seam, face_mask_lock as ml
from app.config import load_settings


def normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def keyed_pngs(directory: Path, suffix: str = "") -> dict[str, Path]:
    result = {}
    for path in sorted(directory.glob(f"*{suffix}.png")):
        name = normalized(path.stem.removesuffix(suffix)) if suffix else normalized(path.stem)
        if name in result:
            raise ValueError("duplicate_normalized_filename")
        result[name] = path
    return result


def load_api_env(path: Path | None) -> None:
    # Allowlist deliberately excludes database, storage and provider endpoints.
    if path is None:
        return
    allowed = {"OPENAI_API_KEY"}
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def save_pair(path: Path, before: Image.Image, after: Image.Image, box) -> None:
    x, y, side = box
    bounds = (x, y, x + side, y + side)
    pair = Image.new("RGB", (side * 2, side))
    pair.paste(before.crop(bounds), (0, 0))
    pair.paste(after.crop(bounds), (side, 0))
    pair.save(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--inputs", type=Path, required=True, help="Qwen *__face.png directory")
    result.add_argument("--originals", type=Path, required=True, help="matching original *.png directory")
    result.add_argument("--references", type=Path, required=True, help="local identity-reference PNG directory")
    result.add_argument("--model-dir", type=Path, required=True, help="existing local YuNet/SFace models")
    result.add_argument("--output-dir", type=Path, required=True, help="local, Git-ignored result directory")
    result.add_argument("--env-file", type=Path, help="read only the OpenAI API key")
    result.add_argument("--masks-dir", type=Path, help="optional captured *__m0.npz masks; missing masks are explicitly reconstructed")
    result.add_argument("--tone", action="store_true", help="evaluate tone_v4 with +12/-4/2 caps")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--plan-only", action="store_true", help="vision planning only; no image edits")
    result.add_argument("--plans-dir", type=Path, help="reuse verified plans with identical current/base neck crops")
    mode.add_argument("--responses-dir", type=Path, help="reuse prior live neck inputs/responses for local composition; no API calls")
    return result


def prepare_inputs(args):
    faces = keyed_pngs(args.inputs, "__face")
    originals = keyed_pngs(args.originals)
    if not faces or not faces.keys() <= originals.keys():
        raise ValueError("missing_matching_originals")
    refs = fi.reference_embeddings(
        [p.read_bytes() for p in sorted(args.references.glob("*.png"))],
        model_dir=str(args.model_dir),
    )
    if not refs:
        raise ValueError("identity_references_unavailable")
    masks = {normalized(p.stem.removesuffix("__m0")): p for p in args.masks_dir.glob("*__m0.npz")} if args.masks_dir else {}
    inputs = {}
    for name, path in faces.items():
        current = Image.open(path).convert("RGB")
        original = Image.open(originals[name]).convert("RGB")
        base, plan, meta = fi.prepare_image(original, str(args.model_dir), pad_crop=True)
        if plan is None or meta.get("skipped_reason"):
            raise ValueError("original_face_plan_unavailable")
        if fi.unpad_edges(base, meta.get("crop_pad")).size != current.size:
            raise ValueError("original_qwen_dimensions_mismatch")
        if name in masks:
            with np.load(masks[name], allow_pickle=False) as saved:
                gen_mask = saved["mask"]
            mask_source = "captured_m0"
        else:
            gen_mask = ml.gen_mask(np.asarray(fi.crop_1024(base, plan), np.float32), plan)
            mask_source = "reconstructed_from_base"
        padded_current = base.copy()
        pl, pt, *_ = seam._pad_ltrb(meta.get("crop_pad"))
        padded_current.paste(current, (pl, pt))
        context = seam.FaceSeamContext(**seam.capture_repair_context(
            base, plan, gen_mask=gen_mask, current=padded_current, tone_enabled=args.tone,
            crop_pad=meta.get("crop_pad"), references=refs,
            model_dir=str(args.model_dir), neck_offset=fi.neck_offset_meta(base, plan),
        ))
        inputs[name] = (current, context, mask_source)
    return inputs


def validate_output_dir(path: Path) -> Path:
    """Refuse image artifacts in a non-ignored directory inside this repository."""
    import subprocess

    path = path.expanduser().resolve()
    root = Path(__file__).resolve().parents[2]
    if path.is_relative_to(root):
        checked = subprocess.run(
            ["git", "check-ignore", "-q", str(path / "neck-result.png")],
            cwd=root, capture_output=True, check=False,
        )
        if checked.returncode != 0:
            raise ValueError("output_must_be_outside_git_or_ignored")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def validate_cache_paths(output: Path, *sources: Path | None) -> None:
    output = output.expanduser().resolve()
    for source in sources:
        if source is None:
            continue
        source = source.expanduser().resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("output_overlaps_cache")


def measure_result(current, repaired, context, meta):
    before = fi.identity_score(current, context.references, model_dir=context.model_dir)
    after = fi.identity_score(repaired, context.references, model_dir=context.model_dir)
    buf = BytesIO()
    repaired.save(buf, "PNG")
    decoded = Image.open(BytesIO(buf.getvalue())).convert("RGB")
    exact_png = np.array_equal(np.asarray(decoded), np.asarray(repaired))
    finite = before is not None and after is not None and np.isfinite([before, after]).all()
    drop = round(before - after, 6) if finite else None
    return {
        **meta, "identity_before": before if finite else None, "identity_after": after if finite else None,
        "identity_drop": drop, "png_roundtrip_exact": exact_png,
        "numeric_checks_passed": bool(exact_png and meta.get("outside_changed") == 0 and drop is not None and drop <= .02),
    }


def check_cached_pixels(directory, crop, prefix):
    for filename, expected in ((f"{prefix}-input.png", crop.current), (f"{prefix}-base.png", crop.base)):
        cached = Image.open(directory / filename).convert("RGB")
        if expected is None or not np.array_equal(np.asarray(cached), np.asarray(expected)):
            raise ValueError("cached_input_mismatch")


async def evaluate(args):
    from app.agents.gemini_image import run_cpu_bound
    from app.agents.image_cost import estimate_cost
    from app.agents.face_seam_vision import plan_repair, verify_repair

    if args.plans_dir and args.responses_dir:
        raise ValueError("conflicting_plan_response_caches")
    validate_cache_paths(args.output_dir, args.plans_dir, args.responses_dir)
    output = validate_output_dir(args.output_dir)
    load_api_env(args.env_file)
    settings = replace(load_settings(), face_seam_repair="on", face_tone_fix="on" if args.tone else "off", fm_face_qc_dir=str(args.model_dir))
    image_usage.configure(pool=None, persist=False)
    inputs = await run_cpu_bound(prepare_inputs, args)
    report = {"evaluation": "blob_v8_tone_v4", "phase": "prepare" if args.prepare_only else "plan" if args.plan_only else "live" if args.live else "recompose",
              "image_model": seam.SUNBURST_MODEL, "tone": args.tone, "rows": [], "human_review": "not_performed"}
    semaphore = asyncio.Semaphore(3)

    async def one(name, current, context, mask_source):
        async with semaphore:
            row = {"cut": name, "vision_calls": 0, "image_edit_calls": 0, "neck_offset": context.neck_offset, "mask_source": mask_source}
            case = output / name
            case.mkdir(exist_ok=True, mode=0o700)
            try:
                if (case / "repair-plan.json").exists() or (case / "sunburst-response.png").exists():
                    raise ValueError("output_exists_use_new_directory")
                look = await run_cpu_bound(seam.prepare_repair_crop, current, context)
                cache = args.plans_dir or args.responses_dir
                if cache:
                    check_cached_pixels(cache / name, look, "look")
                    repair = seam.RepairPlan(**json.loads((cache / name / "repair-plan.json").read_text()))
                elif not args.prepare_only:
                    row["vision_calls"] += 1
                    row["planning"] = {}
                    repair = await plan_repair(settings, look, metadata=row["planning"])
                look.current.save(case / "look-input.png")
                look.base.save(case / "look-base.png")
                if args.prepare_only:
                    row.update(status="prepared", crop=list(look.box))
                    return
                crop = await run_cpu_bound(seam.prepare_repair_crop, current, context, gap=repair.neck_collar_gap)
                if args.responses_dir:
                    check_cached_pixels(args.responses_dir / name, crop, "neck")
                crop.current.save(case / "neck-input.png")
                crop.base.save(case / "neck-base.png")
                prompt = seam.PROMPT_HEAD + repair.instruction.strip() + " " + seam.PROMPT_TAIL
                save_json(case / "repair-plan.json", asdict(repair))
                row.update(crop=list(crop.box), look_crop=list(look.box), neck_collar_gap=repair.neck_collar_gap,
                           observations=repair.observations, instruction=repair.instruction,
                           prompt_sha=hashlib.sha256(prompt.encode()).hexdigest()[:16])
                if args.plan_only:
                    row["status"] = "planned"
                    return
                if args.responses_dir:
                    generated = Image.open(args.responses_dir / name / "sunburst-response.png").convert("RGB")
                    call = json.loads((args.responses_dir / name / "image-call.json").read_text())
                    row["cached_image_call"] = call
                else:
                    row["image_edit_calls"] = 1
                    response = await seam.call_sunburst_edit(settings, prompt, crop)
                    generated = response.image
                    generated.save(case / "sunburst-response.png")
                    call = {"latency_ms": response.latency_ms, "usage": response.usage,
                            "cost": asdict(estimate_cost(seam.SUNBURST_MODEL, "1024x1024", response.usage))}
                    row["image_call"] = call
                    save_json(case / "image-call.json", call)
                repaired, meta, support = await run_cpu_bound(seam.finish_repair, current, context, crop, repair, generated, tone_enabled=args.tone)
                repaired.save(case / "candidate.png")
                Image.fromarray(support.astype(np.uint8)*255).save(case / "support.png")
                save_pair(case / "before-after-native.png", current, repaired, crop.box)
                row.update(await run_cpu_bound(measure_result, current, repaired, context, meta))
                row["status"] = "numeric_pass" if row["numeric_checks_passed"] else "numeric_reject"
                (repaired if row["numeric_checks_passed"] else current).save(case / "adopted.png")
                if args.live:
                    row["vision_calls"] += 1
                    row["visual_check"] = {}
                    x, y, side = crop.box
                    try:
                        row["visual_check"]["passed"] = await verify_repair(settings, crop, repaired.crop((x, y, x+side, y+side)), metadata=row["visual_check"])
                    except Exception:
                        row["visual_check"]["reason"] = "unavailable"
            except Exception as exc:
                row.update(status="failed", error_type=type(exc).__name__)
                if isinstance(exc, seam.SeamRepairUnavailable):
                    reason = str(exc)
                    if reason.isascii() and reason.replace("_", "").isalnum() and len(reason) < 60:
                        row["reason"] = reason
            finally:
                report["rows"].append(row)
                report["rows"].sort(key=lambda value: value["cut"])
                save_json(output / "report.json", report)
                print(json.dumps({"cut": name, "status": row["status"], "image_edit_calls": row["image_edit_calls"]}, ensure_ascii=False), flush=True)

    await asyncio.gather(*(one(name, *data) for name, data in sorted(inputs.items())))
    report.update(vision_calls=sum(r["vision_calls"] for r in report["rows"]), image_edit_calls=sum(r["image_edit_calls"] for r in report["rows"]))
    save_json(output / "report.json", report)
    return 1 if any(r["status"] in {"failed", "numeric_reject"} for r in report["rows"]) else 0


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        raise SystemExit(asyncio.run(evaluate(parser().parse_args())))
    except Exception as exc:
        print(json.dumps({"evaluation_failed": type(exc).__name__}), flush=True)
        raise SystemExit(1)
