"""Replay sealed local wearshot evidence through the existing application adapters.

Default dry-run decodes evidence and makes no requests or output writes. Generate and
QC are separate explicit modes. Each case/mode has an immutable started receipt;
even a crash or error requires human reconciliation, never an automatic resubmit.
"""

import argparse
import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from ..agents import cut_output_qc
from ..agents import vision_llm
from ..agents.gemini_image import GeminiImageClient, InlineImage, validate_openai_output_size
from ..config import load_settings


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class _LocalVisionLogFilter(logging.Filter):
    """Keep the shared adapter's body-bearing error logs out of local replay logs."""

    def __init__(self):
        super().__init__()
        self.errors = contextvars.ContextVar("replay_provider_errors", default=None)

    def filter(self, record):
        errors = self.errors.get()
        if errors is not None and record.msg.startswith("vision_llm provider failed:"):
            args = record.args
            match = re.match(r"^(?:OpenAI|Gemini) ([1-5][0-9]{2}):", str(args[3]))
            info = {"provider": args[0], "type": args[2],
                    "httpStatus": int(match[1]) if match else None}
            errors.append(info)
            record.msg = "Local QC provider failed: provider=%s type=%s httpStatus=%s"
            record.args = (info["provider"], info["type"], info["httpStatus"])
            record.exc_info = None
            record.exc_text = None
        return True


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError("Evidence file cannot be read") from exc


def _path(base, value):
    if not isinstance(value, str) or not value:
        raise ValueError("Evidence path must be a nonempty string")
    return (base / value).resolve()


def _decode(data, declared_mime=None):
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            mime = _MIMES.get(image.format)
            if not mime or (declared_mime is not None and mime != declared_mime):
                raise ValueError("Unsupported image format or declared MIME mismatch")
            return {"mime": mime, "width": image.width, "height": image.height,
                    "sha256": _sha(data), "bytes": len(data)}
    except Exception as exc:
        raise ValueError("Evidence must decode as PNG, JPEG, or WEBP with matching MIME") from exc


def _image(base, row):
    if not isinstance(row, dict) or row.get("mime") not in _MIMES.values():
        raise ValueError("Image row requires a supported explicit MIME")
    path = _path(base, row.get("path"))
    data = _read(path)
    info = {"path": str(path), **_decode(data, row["mime"]), "normalization": "none"}
    return InlineImage(info["mime"], data), info


def _output_paths(out, case_id, mode):
    return {"started": out / f"{case_id}.{mode}.started.json",
            "receipt": out / f"{case_id}.{mode}.receipt.json",
            "image": out / f"{case_id}.png", "prompt": out / f"{case_id}.prompt.txt"}


def _no_existing(paths, mode):
    keys = ("started", "receipt", "image", "prompt") if mode == "generate" else ("started", "receipt")
    if any(paths[key].exists() or paths[key].is_symlink() for key in keys):
        raise ValueError("Case artifacts already exist; reconcile previous attempt before any new call")


def _exclusive(path, data):
    # O_EXCL also rejects existing symlinks; fsync both content and directory entry
    # before the caller can submit the request.
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_error(exc):
    # Adapter exceptions may contain provider bodies/credentials. Never serialize
    # their message; only recognize a bounded status prefix and cause class.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    match = re.match(r"^(?:OpenAI|Gemini) ([1-5][0-9]{2}):", str(exc))
    if status is None and match:
        status = int(match[1])
    return {"type": type(exc).__name__, "httpStatus": status,
            "causeType": type(exc.__cause__).__name__ if exc.__cause__ else None,
            "billable": getattr(exc, "billable", None)}


def _prepare(manifest_path, out, mode, case):
    raw = _read(manifest_path)
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 1:
        raise ValueError("Manifest schemaVersion must be 1")
    if manifest.get("generationModel") != "gpt-image-2":
        raise ValueError("Replay generationModel must be gpt-image-2")
    if not isinstance(manifest.get("qcModel"), str) or not manifest["qcModel"].strip():
        raise ValueError("An explicit qcModel is required")
    rows = manifest.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest cases must be a nonempty list")
    ids = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _ID.fullmatch(row["id"]):
            raise ValueError("Unsafe case ID")
        ids.append(row["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate case IDs")
    if case is not None and case not in ids:
        raise ValueError("Selected case does not exist")
    prepared = []
    for row in rows:
        if case is not None and row["id"] != case:
            continue
        if mode == "generate" and "generation" not in row:
            continue
        base = manifest_path.parent
        item = {"caseId": row["id"], "manifestSha256": _sha(raw)}
        generation = row.get("generation")
        if generation is not None and mode in {"dry-run", "generate"}:
            if not isinstance(generation, dict):
                raise ValueError("generation must be an object")
            validate_openai_output_size(generation.get("outputSize"))
            if not isinstance(generation.get("imageSize"), str) or not generation["imageSize"]:
                raise ValueError("generation.imageSize is required")
            if not isinstance(generation.get("aspectRatio"), str) or not generation["aspectRatio"]:
                raise ValueError("generation.aspectRatio is required")
            inputs = generation.get("inputs")
            if not isinstance(inputs, list) or not inputs:
                raise ValueError("generation.inputs must be nonempty")
            prompt_path = _path(base, generation.get("promptPath"))
            prompt_bytes = _read(prompt_path)
            try:
                prompt = prompt_bytes.decode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Prompt must be UTF-8") from exc
            if not prompt.strip():
                raise ValueError("Prompt is empty")
            pairs = [_image(base, image) for image in inputs]
            item.update({"promptBytes": prompt_bytes, "promptText": prompt,
                         "images": [pair[0] for pair in pairs],
                         "generation": {"inputs": [pair[1] for pair in pairs],
                                        "outputSize": generation["outputSize"],
                                        "imageSize": generation["imageSize"], "aspectRatio": generation["aspectRatio"]},
                         "prompt": {"path": str(prompt_path), "sha256": _sha(prompt_bytes)}})
        qc = row.get("qc")
        if not isinstance(qc, dict) or not isinstance(qc.get("plan"), dict) or not isinstance(qc.get("references"), list):
            raise ValueError("Every case requires qc.plan and qc.references")
        if mode in {"dry-run", "qc"}:
            refs, infos = [], []
            for reference in qc["references"]:
                if not isinstance(reference, dict) or reference.get("role") not in cut_output_qc.REFERENCE_ROLES:
                    raise ValueError("Unknown QC reference role")
                image, info = _image(base, reference)
                refs.append(cut_output_qc.LabeledReference(reference["role"], image))
                infos.append({"role": reference["role"], **info})
            candidate = None
            info = None
            source_generation = None
            if qc.get("candidatePath") is not None:
                candidate_path = _path(base, qc["candidatePath"])
                data = _read(candidate_path)
                info = {"path": str(candidate_path), **_decode(data)}
                candidate = InlineImage(info["mime"], data)
            elif mode == "qc":
                paths = _output_paths(out, row["id"], "generate")
                try:
                    receipt_bytes = _read(paths["receipt"])
                    receipt = json.loads(receipt_bytes)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("Generated candidate requires a successful generation receipt") from exc
                data = _read(paths["image"])
                info = {"path": str(paths["image"]), **_decode(data)}
                if (receipt.get("caseId") != row["id"] or receipt.get("status") != "success"
                        or receipt.get("model") != "gpt-image-2"
                        or receipt.get("output", {}).get("sha256") != info["sha256"]):
                    raise ValueError("Generated candidate does not match its case receipt")
                inputs = receipt.get("inputs")
                prompt_info = receipt.get("prompt")
                if (not _is_sha256(receipt.get("manifestSha256"))
                        or not isinstance(prompt_info, dict) or not _is_sha256(prompt_info.get("sha256"))
                        or not isinstance(inputs, list) or not inputs
                        or not all(isinstance(input_info, dict)
                                   and input_info.get("mime") in _MIMES.values()
                                   and _is_sha256(input_info.get("sha256"))
                                   for input_info in inputs)
                        or any(not isinstance(receipt.get(key), str) or not receipt[key]
                               for key in ("outputSize", "imageSize", "aspectRatio"))):
                    raise ValueError("Generated candidate does not match its case receipt")
                source_generation = {
                    "receipt": {"path": str(paths["receipt"]), "sha256": _sha(receipt_bytes)},
                    "manifestSha256": receipt["manifestSha256"],
                    "model": receipt["model"],
                    "prompt": {"sha256": receipt["prompt"]["sha256"]},
                    "inputs": [{"mime": input_info["mime"], "sha256": input_info["sha256"]}
                               for input_info in inputs],
                    "output": {"sha256": receipt["output"]["sha256"]},
                    "outputSize": receipt["outputSize"],
                    "imageSize": receipt["imageSize"],
                    "aspectRatio": receipt["aspectRatio"],
                }
                candidate = InlineImage(info["mime"], data)
            elif generation is None:
                raise ValueError("QC case requires candidatePath or generation")
            contract = cut_output_qc.normalize_plan(qc["plan"])
            # Same deterministic evidence checks that verdict will apply. Missing
            # roles stay UNJUDGEABLE; no substitution or hidden gate exceptions.
            preflight_image = candidate or InlineImage("image/png", b"pending-generation")
            item.update({"references": refs, "candidateImage": candidate,
                         "qc": {"plan": qc["plan"], "references": infos, "candidate": info,
                                "deterministicPreflight": cut_output_qc._forced_preflight(contract, refs, preflight_image),
                                **({"sourceGeneration": source_generation} if source_generation else {})}})
        if mode != "dry-run":
            _no_existing(_output_paths(out, row["id"], mode), mode)
        prepared.append(item)
    if mode == "generate" and len(prepared) > 10:
        raise ValueError("A generation batch may contain at most ten cases")
    return manifest, prepared


async def run_manifest(manifest_path, output_dir, *, mode="dry-run", case=None, settings=None):
    """Validate the entire selected batch before invoking any paid boundary (cap 2)."""
    if mode not in {"dry-run", "generate", "qc"}:
        raise ValueError("Unknown replay mode")
    out = Path(output_dir).resolve()
    manifest, prepared = _prepare(Path(manifest_path).resolve(), out, mode, case)
    if mode == "dry-run":
        return [{key: value for key, value in {**item, "status": "dry-run"}.items()
                 if key not in {"promptBytes", "promptText", "images", "references", "candidateImage"}}
                for item in prepared]
    settings = settings or load_settings()
    qc_settings = replace(settings, analysis_model_order="gpt", model_text=manifest["qcModel"], analysis_timeout_seconds=120)
    out.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(2)
    client = GeminiImageClient(settings)
    vision_filter = _LocalVisionLogFilter()

    async def execute(item):
        async with semaphore:
            paths = _output_paths(out, item["caseId"], mode)
            _no_existing(paths, mode)
            receipt = {"schemaVersion": 1, "caseId": item["caseId"], "mode": mode,
                       "manifestSha256": item["manifestSha256"], "status": "started",
                       "startedAt": datetime.now(timezone.utc).isoformat(),
                       "model": manifest["generationModel"] if mode == "generate" else manifest["qcModel"]}
            if mode == "generate":
                receipt.update({**item["generation"], "prompt": item["prompt"], "provider": "openai"})
            else:
                receipt.update(item["qc"])
                receipt["provider"] = None
                receipt["analysisModelOrder"] = "gpt"
                receipt["providerErrors"] = []
                vision_filter.errors.set(receipt["providerErrors"])
            _exclusive(paths["started"], _json_bytes(receipt))
            try:
                if mode == "generate":
                    _exclusive(paths["prompt"], item["promptBytes"])
                    result = await client.generate_content_image(
                        model=manifest["generationModel"], prompt=item["promptText"], images=item["images"],
                        image_size=item["generation"]["imageSize"], aspect_ratio=item["generation"]["aspectRatio"],
                        openai_output_size=item["generation"]["outputSize"], openai_preserve_input_bytes=True,
                        timeout=300)
                    receipt.update({"usage": result.usage, "latencyMs": result.latency_ms})
                    info = _decode(result.image, result.mime)
                    if info["mime"] != "image/png":
                        raise ValueError("OpenAI returned an unexpected output format")
                    _exclusive(paths["image"], result.image)
                    receipt["output"] = {"path": str(paths["image"]), **info}
                else:
                    result = await cut_output_qc.verdict(qc_settings, item["qc"]["plan"], item["references"], item["candidateImage"])
                    receipt.update({"verdict": result, "provider": result.get("provider")})
                    if result.get("provider") not in {"gpt", None}:
                        raise ValueError("QC returned a non-GPT provider")
                receipt["status"] = "success"
            except Exception as exc:
                receipt.update({"status": "error", "error": _safe_error(exc)})
            receipt["finishedAt"] = datetime.now(timezone.utc).isoformat()
            _exclusive(paths["receipt"], _json_bytes(receipt))
            return receipt

    vision_llm.logger.addFilter(vision_filter)
    try:
        return await asyncio.gather(*(execute(item) for item in prepared))
    finally:
        vision_llm.logger.removeFilter(vision_filter)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "generate", "qc"), default="dry-run")
    parser.add_argument("--case")
    args = parser.parse_args(argv)
    try:
        results = asyncio.run(run_manifest(args.manifest, args.output_dir, mode=args.mode, case=args.case))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": _safe_error(exc)}))
        return 1
    # Keep CLI output small and avoid reproducing prompts, plans or image payloads.
    print(json.dumps([{"caseId": result["caseId"], "status": result["status"]} for result in results]))
    return int(any(result["status"] == "error" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
