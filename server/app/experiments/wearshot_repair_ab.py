"""Frozen local v2 repair pairs. Default dry-run performs no calls or writes.

Explicit qc-base, repair (one arm), and qc-repair stages create exclusive,
fsynced attempt receipts. Existing attempts require human reconciliation.
This tool does not establish ownership/approval of local reference images.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from ..agents import cut_generator, cut_identity_review, gemini_image, vision_llm
from ..agents import wearshot_contract as core, wearshot_prompt, wearshot_qc, wearshot_runtime
from ..agents.gemini_image import GeminiImageClient, InlineImage
from ..config import load_settings
from . import wearshot_replay as legacy


MODELS = {"image2": "gpt-image-2", "sunburst": "gpt-image-2.5-sunburst-2026-09-08"}
VERSION = "wearshot_repair_ab_v1"


def _hash(value):
    return legacy._sha(legacy._json_bytes(value))


def _code_versions():
    modules = (core, wearshot_prompt, wearshot_qc, wearshot_runtime, cut_generator,
               cut_identity_review, gemini_image, vision_llm, legacy)
    return {**{m.__name__: legacy._sha(legacy._read(Path(m.__file__))) for m in modules},
            "harness": legacy._sha(legacy._read(Path(__file__)))}


def _binding(row):
    return core.GarmentBinding(row["garmentId"], row["mannequinKey"], tuple(row["sellerKeys"]),
        tuple(core.EssentialDetail(d["code"], d["value"], tuple(d["evidenceKeys"]), d["visible"])
              for d in row["essentials"]), tuple(row["outOfFrameAxes"]), row.get("approvedLengthKey"))


def _contract(metadata, reference_paths, directory):
    if set(reference_paths) != {r["key"] for r in metadata["references"]}:
        raise ValueError("Reference paths must match exact contract keys")
    refs = []
    for row in metadata["references"]:
        image, _ = legacy._image(directory, dict(path=reference_paths[row["key"]], mime=row["mime"]))
        refs.append(core.BoundReference(row["key"], row["role"], image, row["garmentId"],
                                        row["evidenceOrdinal"], row["assetId"]))
    frame = metadata["frameLock"]
    contract = core.bind_contract(target=_binding(metadata["target"]),
        matching=tuple(_binding(g) for g in metadata["matching"]),
        expected_matching_ids=tuple(metadata["expectedMatchingIds"]), references=tuple(refs),
        example_key=metadata["exampleKey"], model_face_key=metadata["modelFaceKey"],
        frame_lock=core.FrameLock(frame["shot"], frame["faceVisibility"], frame["description"]),
        model_body_key=metadata["modelBodyKey"], capture_key=metadata["captureKey"],
        variation_axis=metadata["variationAxis"], capture_profile=metadata["captureProfile"],
        directing_mode=metadata.get("directingMode"))
    if contract.to_dict() != metadata:
        raise ValueError("Frozen contract metadata or reference bytes mismatch")
    return contract


def _feedback(value, contract):
    if (not isinstance(value, dict) or set(value) != {"failedAxes", "protectedAxes", "evidence"}
            or not isinstance(value["evidence"], str) or not value["evidence"].strip()
            or len(value["evidence"]) > 1200):
        raise ValueError("Human feedback requires explicit axes and bounded evidence provenance")
    for key in ("failedAxes", "protectedAxes"):
        axes = value[key]
        if (not isinstance(axes, list) or not all(isinstance(a, str) for a in axes)
                or len(set(axes)) != len(axes) or not set(axes) <= set(contract.attribute_keys)):
            raise ValueError("Human feedback axes must be unique and applicable")
    failed = set(value["failedAxes"])
    if "variation" in failed:
        failed.add(contract.variation_axis)
    if failed & set(value["protectedAxes"]):
        raise ValueError("Human failed and protected axes conflict")
    return value


def _prepare(path, case):
    raw = legacy._read(path)
    try:
        manifest = json.loads(raw)
        if (set(manifest) != {"schemaVersion", "models", "qcModel", "qcTimeoutSeconds", "quality", "imageSize", "cases"}
                or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
                or manifest["models"] != MODELS or manifest["quality"] != "medium"
                or manifest["imageSize"] not in {"1K", "2K", "4K"}
                or not isinstance(manifest["qcModel"], str) or not manifest["qcModel"].startswith("gpt-")):
            raise ValueError("Manifest requires exact repair models, medium quality, and GPT QC")
        timeout = manifest["qcTimeoutSeconds"]
        if type(timeout) not in (int, float) or not 1 <= timeout <= 600 or not math.isfinite(timeout):
            raise ValueError("Manifest qcTimeoutSeconds must be a finite number from 1 to 600")
        rows = manifest["cases"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("Cases must be nonempty")
        ids = [r["id"] for r in rows]
        if (not all(isinstance(i, str) and legacy._ID.fullmatch(i) for i in ids)
                or len(set(ids)) != len(ids) or (case is not None and case not in ids)):
            raise ValueError("Unsafe, duplicated, or missing case ID")
        prepared = []
        code = _code_versions()
        for row in rows:
            if case is not None and row["id"] != case:
                continue
            if set(row) - {"humanFeedback"} != {"id", "repairDeclared", "contract", "referencePaths", "base", "outputSize"} or row["repairDeclared"] is not True:
                raise ValueError("Case must explicitly declare repair and frozen evidence")
            contract = _contract(row["contract"], row["referencePaths"], path.parent)
            base, base_info = legacy._image(path.parent, row["base"])
            if set(row["base"]) != {"path", "mime", "sha256"} or row["base"]["sha256"] != base_info["sha256"]:
                raise ValueError("Frozen base bytes mismatch")
            size = row["outputSize"]
            wearshot_runtime.validate_output_size(base, size)
            example = next(ref.image for ref in contract.references if ref.key == contract.example_key)
            wearshot_runtime.validate_output_size(example, size)
            request = dict(harnessVersion=VERSION, caseId=row["id"], manifestSha256=legacy._sha(raw),
                           codeVersions=code, contract=contract.to_dict(), base=base_info,
                           outputSize=size, imageSize=manifest["imageSize"], quality="medium", outputFormat="png",
                           qcModel=manifest["qcModel"], qcTimeoutSeconds=timeout, models=MODELS)
            if "humanFeedback" in row:
                request["humanFeedback"] = _feedback(row["humanFeedback"], contract)
            prepared.append(dict(contract=contract, base=base, request=request))
        return manifest, prepared
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed frozen manifest") from exc


def _paths(out, case, mode, arm=None):
    stem = f"{case}.{mode}" + (f".{arm}" if arm else "")
    return {"started": out / f"{stem}.started.json", "receipt": out / f"{stem}.receipt.json",
            "image": out / f"{stem}.png", "prompt": out / f"{stem}.prompt.txt"}


def _unattempted(paths):
    if any(p.exists() or p.is_symlink() for p in paths.values()):
        raise ValueError("Attempt artifacts exist; reconcile without resubmitting")


def _read_receipt(paths, expected):
    raw = legacy._read(paths["receipt"])
    try:
        receipt = json.loads(raw)
        started = json.loads(legacy._read(paths["started"]))
        digest = receipt.pop("receiptSha256")
        if (digest != _hash(receipt) or receipt.get("status") != "completed"
                or receipt.get("request") != expected or started.get("request") != expected
                or started.get("status") != "started"
                or receipt.get("requestSha256") != _hash(expected)
                or started.get("requestSha256") != _hash(expected)):
            raise ValueError("Receipt provenance mismatch or incomplete attempt")
        receipt["receiptSha256"] = digest
        return receipt, {"path": str(paths["receipt"]), "sha256": legacy._sha(raw)}
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed receipt") from exc


def _base_request(item):
    return {**item["request"], "mode": "qc-base", "model": item["request"]["qcModel"],
            "candidateSha256": core.image_sha256(item["base"])}


def _repair_request(item, out, arm):
    contract, base = item["contract"], item["base"]
    receipt, source = _read_receipt(_paths(out, item["request"]["caseId"], "qc-base"), _base_request(item))
    verdict = receipt["verdict"]
    checked = wearshot_qc.validate(verdict.get("observations"), contract, base)
    if not checked["valid"] or any(verdict.get(k) != v for k, v in checked.items()):
        raise ValueError("Base QC requires exact valid v2 observations and binding")
    feedback = item["request"].get("humanFeedback")
    if feedback is None:
        plan = wearshot_runtime.derive_repair_plan(contract, base, verdict)
    else:
        if wearshot_runtime.release_allowed(verdict, contract, base):
            failed, passed = set(), set(checked["passedAttributes"])
        else:
            machine = wearshot_runtime.derive_repair_plan(contract, base, verdict)
            failed, passed = set(machine.failed_axes), set(machine.approved_axes)
        failed.update(feedback["failedAxes"])
        if "variation" in failed:
            failed.add(contract.variation_axis)
        desired = set(feedback["protectedAxes"])
        if not desired <= passed or desired & failed:
            raise ValueError("Human protection must already PASS machine and focused review without conflict")
        plan = core.make_repair_plan(contract, base, tuple(sorted(failed)), tuple(sorted(passed - failed)))
    rendered = wearshot_prompt.render_repair(contract, plan)
    common = {**item["request"], "mode": "repair", "sourceBaseQc": source,
              "repairPlan": plan.to_dict(), "promptSha256": legacy._sha(rendered.prompt.encode("utf-8")),
              "inputs": [{"key": key, **legacy._decode(image.data, image.mime)}
                         for key, image in zip(rendered.reference_keys, rendered.images)]}
    return {**common, "pairSha256": _hash(common), "model": MODELS[arm]}, plan, rendered


def _verify_pair(out, case, request, *, create=False):
    path = out / f"{case}.pair.json"
    pair = {k: v for k, v in request.items() if k != "model"}
    if create:
        try:
            legacy._exclusive(path, legacy._json_bytes(pair))
            return
        except FileExistsError:
            pass
    if json.loads(legacy._read(path)) != pair:
        raise ValueError("Paired arm provenance changed")


def _actual_request(request):
    return {key: request[key] for key in ("model", "promptSha256", "inputs", "outputSize", "imageSize", "quality", "outputFormat")}


class _BoundClient:
    """Observe and check the shared generator's adapter boundary before submission."""
    def __init__(self, settings, request, rendered, receipt):
        self.client = GeminiImageClient(settings)
        self.request, self.rendered, self.receipt = request, rendered, receipt
        self.called = False

    async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
        if (self.called or model != self.request["model"] or prompt != self.rendered.prompt
                or tuple(images) != self.rendered.images or image_size != self.request["imageSize"]
                or kwargs != {"openai_preserve_input_bytes": True, "openai_output_size": self.request["outputSize"]}):
            raise ValueError("Production repair request differs from frozen pair")
        self.called = True
        self.receipt["actualRequest"] = _actual_request(self.request)
        result = await self.client.generate_content_image(model, prompt, images, image_size, **kwargs)
        self.receipt.update(usage=result.usage, latencyMs=result.latency_ms)
        return result


async def run_manifest(manifest_path, output_dir, *, mode="dry-run", case=None, arm=None, settings=None):
    if mode not in {"dry-run", "qc-base", "repair", "qc-repair"}:
        raise ValueError("Unknown mode")
    if mode != "dry-run" and case is None:
        raise ValueError("Live stage requires one explicit case")
    if (mode in {"repair", "qc-repair"} and arm not in MODELS
            or mode in {"dry-run", "qc-base"} and arm is not None):
        raise ValueError("Repair and repair QC require one explicit arm")
    manifest, items = _prepare(Path(manifest_path).resolve(), case)
    if mode == "dry-run":
        return [{"caseId": i["request"]["caseId"], "status": "dry-run", "request": i["request"]} for i in items]
    out = Path(output_dir).resolve()
    item = items[0]
    contract, candidate = item["contract"], item["base"]
    paths = _paths(out, case, mode, arm)
    _unattempted(paths)
    plan = rendered = None
    if mode == "qc-base":
        request = _base_request(item)
    else:
        generation_request, plan, rendered = _repair_request(item, out, arm)
        if mode == "repair":
            request = generation_request
        else:
            _verify_pair(out, case, generation_request)
            generation_paths = _paths(out, case, "repair", arm)
            generation, source = _read_receipt(generation_paths, generation_request)
            data = legacy._read(generation_paths["image"])
            output = {"path": str(generation_paths["image"]), **legacy._decode(data)}
            if (generation.get("output") != output or generation.get("actualRequest") != _actual_request(generation_request)
                    or legacy._sha(legacy._read(generation_paths["prompt"])) != generation_request["promptSha256"]
                    or f"{output['width']}x{output['height']}" != generation_request["outputSize"]):
                raise ValueError("Repair candidate or actual request differs from owning receipt")
            candidate = InlineImage(output["mime"], data)
            request = {**generation_request, "mode": "qc-repair", "model": manifest["qcModel"],
                       "sourceGeneration": source, "candidateSha256": output["sha256"]}
    # A copied local configuration freezes both judges and image-size settings.
    configured = replace(settings or load_settings(), wearshot_qc_model=manifest["qcModel"],
                         wearshot_qc_timeout_seconds=manifest["qcTimeoutSeconds"],
                         cut_identity_review_model=manifest["qcModel"], analysis_model_order="gpt",
                         model_text=manifest["qcModel"], detail_cut_image_size=manifest["imageSize"])
    out.mkdir(parents=True, exist_ok=True)
    if mode == "repair":
        _verify_pair(out, case, request, create=True)
    receipt = dict(caseId=case, status="started", request=request, requestSha256=_hash(request),
                   startedAt=datetime.now(timezone.utc).isoformat())
    legacy._exclusive(paths["started"], legacy._json_bytes(receipt))
    log_filter = legacy._LocalVisionLogFilter()
    receipt["providerErrors"] = []
    log_filter.errors.set(receipt["providerErrors"])
    vision_llm.logger.addFilter(log_filter)
    try:
        if mode == "repair":
            legacy._exclusive(paths["prompt"], rendered.prompt.encode("utf-8"))
            client = _BoundClient(configured, request, rendered, receipt)
            data, mime = await cut_generator.repair(configured, client, {}, {}, candidate,
                wearshot_contract=contract, repair_plan=plan, repair_model=MODELS[arm], output_size=request["outputSize"])
            info = legacy._decode(data, mime)
            receipt["output"] = {"path": str(paths["image"]), **info}
            if mime != "image/png" or f"{info['width']}x{info['height']}" != request["outputSize"]:
                raise ValueError("Returned image dimensions or format differ from request")
            legacy._exclusive(paths["image"], data)
        else:
            result = await wearshot_runtime.review_candidate(configured, contract, candidate, repair_plan=plan)
            receipt["verdict"] = result
            receipt["releaseAllowed"] = wearshot_runtime.release_allowed(result, contract, candidate, repair_plan=plan)
            if not result.get("valid") or result.get("provider") != "gpt" or result.get("model") != manifest["qcModel"]:
                raise ValueError("Shared v2 judge unavailable or incorrectly bound")
        receipt["status"] = "completed"
    except Exception as exc:
        receipt.update(status="failed", error=legacy._safe_error(exc))
    finally:
        vision_llm.logger.removeFilter(log_filter)
    receipt["finishedAt"] = datetime.now(timezone.utc).isoformat()
    receipt["receiptSha256"] = _hash(receipt)
    legacy._exclusive(paths["receipt"], legacy._json_bytes(receipt))
    return [receipt]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "qc-base", "repair", "qc-repair"), default="dry-run")
    parser.add_argument("--case")
    parser.add_argument("--arm", choices=tuple(MODELS))
    args = parser.parse_args(argv)
    try:
        results = asyncio.run(run_manifest(args.manifest, args.output_dir, mode=args.mode, case=args.case, arm=args.arm))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": legacy._safe_error(exc)}))
        return 1
    print(json.dumps([{k: r[k] for k in ("caseId", "status")} for r in results]))
    return int(any(r["status"] == "failed" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
