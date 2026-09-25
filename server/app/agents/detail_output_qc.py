"""Source-owned detail QC. Dedicated core + fact specialist, no provider fallback.

Display text and judge prose never become correction instructions. Unknown evidence,
an incomplete envelope or an invalid contract cannot authorize publication or repair.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from . import vision_llm
from .gemini_image import InlineImage


POLICY_VERSION = "detail-output-qc-v4"
GATES = (
    "targetFraming", "constructionDesign", "colorMaterial", "permanentMarkings",
    "temporaryArtifacts", "photographicPreparation",
)
CORE_GATES = ("targetFraming", "constructionDesign", "colorMaterial", "photographicPreparation")
SPECIALIST_GATES = ("permanentMarkings", "temporaryArtifacts")
_KINDS = frozenset({"neckline", "closure", "pocket", "waist", "cuff", "hem",
                    "construction", "surface", "fabric", "label"})
_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
_PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "detail_output_qc_v1.txt"
_MARKING_FIELDS = {
    "sourcePresence": ("present", "absent", "uncertain"),
    "sourceReadability": ("readable", "unreadable", "no_text", "not_applicable"),
    "candidateRegion": ("in_frame", "out_of_frame", "uncertain"),
    "candidatePresence": ("present", "absent", "uncertain", "not_applicable"),
    "candidateReadability": ("readable", "unreadable", "no_text", "not_applicable"),
    "textMatch": ("match", "mismatch", "not_comparable", "not_applicable"),
}
_ARTIFACT_FIELDS = {
    "sourcePresence": ("present", "absent", "uncertain"),
    "sourceKind": ("sales_tag", "hanger_or_clip", "hand_or_body", "background_clutter", "none", "uncertain"),
    "candidatePresence": ("present", "absent", "uncertain"),
    "candidateKind": ("sales_tag", "hanger_or_clip", "hand_or_body", "background_clutter", "none", "uncertain"),
}
_REPAIRS = {
    "targetFraming": "Photograph the requested detail and its useful connecting garment context; exclude a full-garment overview and preserve the requested side.",
    "constructionDesign": "Restore the original garment's visible construction, motif family, intentional pleats or gathers, and hardware relationships. Keep permissible steaming and attractive re-laying.",
    "colorMaterial": "Restore the original garment's color family and supported material, pattern, openwork, texture and sheen without inventing finer fibers or changing the fabric.",
    "permanentMarkings": "Preserve permanent sewn labels, logos and markings where their original location is inside this crop. Preserve presence separately from lettering; never invent unreadable source text or expose an outside-frame label.",
    "temporaryArtifacts": "Remove temporary sales tags, tag fasteners, hangers, clips, hands and distracting photographic artifacts. Keep real garment hardware, permanent sewn labels and intentional threads.",
    "photographicPreparation": "Prepare a coherent fashion-product detail photograph with soft neutral light and natural cloth depth. Smooth incidental wrinkles and re-lay the garment while preserving intentional texture, lace, pleats and construction.",
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def _enum_fields(fields):
    return _object({key: {"type": "string", "enum": list(values)} for key, values in fields.items()})


def _gate_schema(codes):
    return {"type": "array", "items": _object({
            "code": {"type": "string", "enum": list(codes)},
            "status": {"type": "string", "enum": sorted(_STATUSES)},
            "evidence": {"type": "string"},
        })}


def response_schema():
    return _object({"gates": _gate_schema(CORE_GATES)})


def specialist_schema():
    return _object({
        "gates": _gate_schema(SPECIALIST_GATES),
        "permanentMarkings": _enum_fields(_MARKING_FIELDS),
        "temporaryArtifacts": _enum_fields(_ARTIFACT_FIELDS),
    })


def is_detail(spec):
    return isinstance(spec, dict) and spec.get("cutType") == "product" and spec.get("shot") == "detail"


def _target(target, source_hashes, direction):
    if direction not in {"front", "back"}:
        raise ValueError("invalid_direction")
    if target is None:
        return None
    if not isinstance(target, dict) or target.get("kind") not in _KINDS:
        raise ValueError("invalid_target")
    if target.get("direction", direction) != direction:
        raise ValueError("target_direction_mismatch")
    # sourceIndex is the original analysis index, NOT the runtime's reordered list.
    source_hash = target.get("sourceSha256")
    if source_hash not in source_hashes:
        raise ValueError("target_source_mismatch")
    bound = {"kind": target["kind"], "direction": direction,
             "sourceOrdinal": source_hashes.index(source_hash) + 1}
    color_transfer = "colorTransfer" in target or "colorSourceSha256" in target
    if color_transfer:
        if target.get("colorTransfer") is not True or target.get("colorSourceSha256") not in source_hashes:
            raise ValueError("invalid_color_authority")
        bound.update(colorTransfer=True, colorSourceOrdinal=source_hashes.index(target["colorSourceSha256"]) + 1)
    region = target.get("region")
    if region is None and color_transfer and target["kind"] == "construction":
        return bound
    if not isinstance(region, dict) or set(region) != {"x", "y", "w", "h"}:
        raise ValueError("invalid_target_region")
    if any(type(n) not in (float, int) or not math.isfinite(n) for n in region.values()):
        raise ValueError("invalid_target_region")
    x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
        raise ValueError("invalid_target_region")
    return {**bound, "region": region}


def build_prompt(source_count, target, direction, *, has_crop=False, specialist=False):
    manifest = [f"{i + 1}. SOURCE {i + 1}: actual seller garment evidence." for i in range(source_count)]
    if has_crop:
        manifest.append(f"{len(manifest) + 1}. TARGET CROP: deterministic crop of SOURCE {target['sourceOrdinal']}; same evidence, not a different product or generated authority.")
    if specialist:
        source_ordinal = target["sourceOrdinal"] if target else 1
        for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
            manifest.append(f"{len(manifest) + 1}. SOURCE DETAIL TILE {corner}: pixel crop of the {'target crop' if has_crop else f'SOURCE {source_ordinal}'}. Inspect small permanent/temporary objects, not new evidence.")
    manifest.append(f"{len(manifest) + 1}. CANDIDATE: the ONLY image being evaluated.")
    if specialist:
        for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
            manifest.append(f"{len(manifest) + 1}. CANDIDATE DETAIL TILE {corner}: pixel crop of the candidate above. Inspect all four tiles for small residual objects; these are NOT additional source photos.")
    primary_template, separator, specialist_template = _PROMPT.read_text(encoding="utf-8").partition("\n---SPECIALIST---\n")
    if specialist and not separator:
        raise ValueError("specialist_prompt_missing")
    return ((specialist_template if specialist else primary_template) + "\n\nIMAGE MANIFEST\n" + "\n".join(manifest)
            + "\nREQUESTED SIDE: " + direction + "\nSERVER TARGET: " + _canonical(target))


def _prepare(sources, candidate, target):
    """Decode/EXIF-normalize and downsize only; crop never creates visual evidence."""
    def decode(item):
        if not isinstance(item, InlineImage) or item.mime not in {"image/png", "image/jpeg", "image/webp"} or not item.data:
            raise ValueError("invalid_image")
        with Image.open(BytesIO(item.data)) as image:
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")

    def encode(image):
        image = image.copy()
        image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
        buf = BytesIO()
        image.save(buf, format="PNG")
        return InlineImage("image/png", buf.getvalue())

    images = [decode(item) for item in sources]
    crop = None
    if target is not None and "region" in target:
        image = images[target["sourceOrdinal"] - 1]
        r = target["region"]
        crop = image.crop((math.floor(r["x"] * image.width), math.floor(r["y"] * image.height),
                           math.ceil((r["x"] + r["w"]) * image.width), math.ceil((r["y"] + r["h"]) * image.height)))
    def tiles(image):
        mid_x, mid_y = image.width // 2, image.height // 2
        if min(mid_x, mid_y) < 1:
            raise ValueError("image_too_small")
        return [encode(image.crop(box)) for box in (
            (0, 0, mid_x, mid_y), (mid_x, 0, image.width, mid_y),
            (0, mid_y, mid_x, image.height), (mid_x, mid_y, image.width, image.height))]

    focus = crop if crop is not None else images[(target["sourceOrdinal"] - 1) if target else 0]
    candidate_image = decode(candidate)
    return [*[encode(im) for im in images], *([encode(crop)] if crop is not None else []),
            *tiles(focus), encode(candidate_image), *tiles(candidate_image)]


def _validate_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("invalid_judge_contract")
    if any(not isinstance(value[key], str) or value[key] not in choices for key, choices in fields.items()):
        raise ValueError("invalid_judge_contract")


def _marking_status(m):
    for side in ("source", "candidate"):
        presence, readability = m[side + "Presence"], m[side + "Readability"]
        if presence in {"absent", "not_applicable"} and readability != "not_applicable":
            raise ValueError("inconsistent_marking_evidence")
        if presence == "present" and readability == "not_applicable":
            raise ValueError("inconsistent_marking_evidence")
    if m["textMatch"] == "match" and m["sourceReadability"] != m["candidateReadability"]:
        raise ValueError("unsupported_text_match")
    if m["sourcePresence"] == m["candidatePresence"] == "absent" and m["textMatch"] != "not_applicable":
        raise ValueError("unsupported_text_match")
    if m["candidateRegion"] == "out_of_frame":
        if (m["candidatePresence"], m["candidateReadability"], m["textMatch"]) != ("not_applicable",) * 3:
            raise ValueError("inconsistent_marking_evidence")
        return "PASS"
    if m["candidateRegion"] == "uncertain" or "uncertain" in (m["sourcePresence"], m["candidatePresence"]):
        return "UNKNOWN"
    if m["candidatePresence"] == "not_applicable":
        raise ValueError("inconsistent_marking_evidence")
    if m["sourcePresence"] == "absent":
        if m["sourceReadability"] != "not_applicable":
            raise ValueError("inconsistent_marking_evidence")
        return "FAIL" if m["candidatePresence"] == "present" else "PASS"
    if m["candidatePresence"] == "absent":
        return "FAIL"
    if m["sourceReadability"] == "unreadable":
        if m["candidateReadability"] == "unreadable" and m["textMatch"] != "not_comparable":
            raise ValueError("inconsistent_marking_evidence")
        return {"readable": "FAIL", "unreadable": "PASS"}.get(m["candidateReadability"], "UNKNOWN")
    if m["sourceReadability"] in {"readable", "no_text"}:
        return {"match": "PASS", "mismatch": "FAIL"}.get(m["textMatch"], "UNKNOWN")
    raise ValueError("inconsistent_marking_evidence")


def _validate_gates(gates, required):
    if not isinstance(gates, list) or len(gates) != len(required):
        raise ValueError("invalid_judge_contract")
    seen = {}
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) != {"code", "status", "evidence"}:
            raise ValueError("invalid_judge_contract")
        code, status, evidence = gate["code"], gate["status"], gate["evidence"]
        if not isinstance(code, str) or code not in required or code in seen or not isinstance(status, str) or status not in _STATUSES:
            raise ValueError("invalid_judge_contract")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 480 or any(ord(c) < 32 for c in evidence):
            raise ValueError("invalid_judge_contract")
        seen[code] = dict(gate)
    return seen


def _normalize(raw):
    if not isinstance(raw, dict) or set(raw) != {"gates", "permanentMarkings", "temporaryArtifacts"}:
        raise ValueError("invalid_judge_contract")
    seen = _validate_gates(raw["gates"], GATES)
    markings, artifacts = dict(raw["permanentMarkings"]) if isinstance(raw["permanentMarkings"], dict) else raw["permanentMarkings"], raw["temporaryArtifacts"]
    _validate_fields(markings, _MARKING_FIELDS)
    _validate_fields(artifacts, _ARTIFACT_FIELDS)
    # Known semantic alias, not a guessed fact: an absent mark has no text to read.
    # Keep rawJudge unchanged for audit. Absent + readable/unreadable stays contradictory.
    for side in ("source", "candidate"):
        if markings[side + "Presence"] in {"absent", "not_applicable"} and markings[side + "Readability"] == "no_text":
            markings[side + "Readability"] = "not_applicable"
    if markings["candidateRegion"] == "out_of_frame" and markings["candidatePresence"] == "absent":
        markings["candidatePresence"] = "not_applicable"
    marking_status = _marking_status(markings)
    if markings["candidateRegion"] == "out_of_frame" and seen["permanentMarkings"]["status"] == "FAIL":
        raise ValueError("outside_frame_marking_failure")
    for side in ("source", "candidate"):
        presence, kind = artifacts[side + "Presence"], artifacts[side + "Kind"]
        if presence == "absent" and kind != "none":
            raise ValueError("inconsistent_artifact_evidence")
        if presence == "present" and kind in {"none", "uncertain"}:
            raise ValueError("inconsistent_artifact_evidence")
    artifact_status = {"absent": "PASS", "present": "FAIL", "uncertain": "UNKNOWN"}[artifacts["candidatePresence"]]
    for code, derived in (("permanentMarkings", marking_status), ("temporaryArtifacts", artifact_status)):
        states = {seen[code]["status"], derived}
        seen[code]["status"] = "UNKNOWN" if "UNKNOWN" in states else "FAIL" if "FAIL" in states else "PASS"
    # Unknown evidence also forbids spending an automatic correction attempt.
    statuses = {gate["status"] for gate in seen.values()}
    decision = "UNKNOWN" if "UNKNOWN" in statuses else "FAIL" if "FAIL" in statuses else "PASS"
    return {"passed": decision == "PASS", "decision": decision,
            "gates": [seen[code] for code in GATES], "markings": dict(markings),
            "temporaryArtifacts": dict(artifacts)}


def _unknown(out, code):
    return {**out, "passed": False, "decision": "UNKNOWN", "errorCode": code,
            "gates": [{"code": gate, "status": "UNKNOWN", "evidence": "QC could not establish a complete valid judgment."} for gate in GATES]}


async def verdict(settings, sources: list[InlineImage], candidate: InlineImage, *, target=None, direction="front"):
    out = {"version": 1, "policyVersion": POLICY_VERSION, "passed": False, "decision": "UNKNOWN",
           "gates": [], "markings": None, "temporaryArtifacts": None, "provider": None,
           "requestedModel": None, "returnedModel": None, "promptHash": None,
           "sourceHashes": [], "sourceHash": None, "resultHash": None, "targetHash": None,
           "judgeImageHashes": [], "rawJudge": None, "errorCode": None, "usage": None,
           "specialist": {"status": "NOT_RUN", "provider": "gpt", "requestedModel": None,
                          "returnedModel": None, "promptHash": None, "judgeImageHashes": [],
                          "rawJudge": None, "usage": None, "errorCode": None}}
    try:
        if not isinstance(sources, list) or not sources:
            raise ValueError("source_images_required")
        if any(not isinstance(im, InlineImage) or not isinstance(im.data, bytes) for im in [*sources, candidate]):
            raise ValueError("invalid_image")
        out["sourceHashes"] = [_hash(im.data) for im in sources]
        out["sourceHash"] = _hash(_canonical([{"mime": im.mime, "sha256": sha} for im, sha in zip(sources, out["sourceHashes"])]).encode())
        out["resultHash"] = _hash(candidate.data)
        out["targetHash"] = _hash(_canonical({"target": target, "direction": direction}).encode())
        bound = _target(target, out["sourceHashes"], direction)
        prompt = build_prompt(len(sources), bound, direction, has_crop=bound is not None and "region" in bound)
        out["promptHash"] = _hash(prompt.encode())
        all_images = await asyncio.to_thread(_prepare, sources, candidate, bound)
        has_crop = bound is not None and "region" in bound
        images = [*all_images[:len(sources) + int(has_crop)], all_images[-5]]
        out["judgeImageHashes"] = [_hash(im.data) for im in images]
    except (ValueError, TypeError, OSError):
        return _unknown(out, "invalid_input")
    out["provider"] = "gpt"
    out["requestedModel"] = getattr(settings, "model_detail_core", None)
    key = getattr(settings, "openai_api_key", None)
    if not key or not isinstance(out["requestedModel"], str) or not out["requestedModel"].strip():
        return _unknown(out, "primary_judge_unavailable")
    metadata = {}
    try:
        timeout = float(getattr(settings, "detail_core_timeout_seconds", 90))
        if not math.isfinite(timeout) or timeout <= 0:
            return _unknown(out, "invalid_judge_timeout")
        args = (settings, out["requestedModel"], prompt, images, response_schema(), min(timeout, 120.0))
        raw = await asyncio.wait_for(vision_llm._call_gpt(
            *args, reasoning_effort="high", image_detail="high", max_completion_tokens=7000,
            metadata=metadata), timeout=min(timeout, 120.0))
        out.update(returnedModel=metadata.get("returned_model"), usage=metadata.get("usage"))
        out["rawJudge"] = raw  # audit-only; repair_instructions never reads this field
        if not isinstance(raw, dict) or set(raw) != {"gates"}:
            return _unknown(out, "invalid_primary_contract")
        try:
            primary = _validate_gates(raw["gates"], CORE_GATES)
        except ValueError as exc:
            return _unknown(out, str(exc))
        states = {gate["status"] for gate in primary.values()}
        if states != {"PASS"}:
            decision = "UNKNOWN" if "UNKNOWN" in states else "FAIL"
            gates = [primary.get(code, {"code": code, "status": "UNKNOWN", "evidence": "NOT_RUN: primary core did not pass; no specialist call spent."}) for code in GATES]
            return {**out, "decision": decision, "gates": gates}
    except Exception:
        # Do not log provider exception text: it can include sensitive request data.
        out.update(returnedModel=metadata.get("returned_model"), usage=metadata.get("usage"))
        return _unknown(out, "judge_unavailable_or_invalid")
    specialist = out["specialist"]
    specialist["requestedModel"] = getattr(settings, "model_detail_specialist", None)
    if not getattr(settings, "openai_api_key", None) or not specialist["requestedModel"]:
        specialist.update(status="UNAVAILABLE", errorCode="specialist_unavailable")
        return _unknown(out, "specialist_unavailable")
    specialist_metadata = {}
    try:
        specialist_timeout = float(getattr(settings, "detail_specialist_timeout_seconds", 90))
        if not math.isfinite(specialist_timeout) or specialist_timeout <= 0:
            raise ValueError("invalid_specialist_timeout")
        specialist_prompt = build_prompt(len(sources), bound, direction, has_crop=has_crop, specialist=True)
        specialist.update(status="RUN", promptHash=_hash(specialist_prompt.encode()),
                          judgeImageHashes=[_hash(im.data) for im in all_images])
        specialist_raw = await asyncio.wait_for(vision_llm._call_gpt(
            settings, specialist["requestedModel"], specialist_prompt, all_images, specialist_schema(),
            min(specialist_timeout, 120), reasoning_effort="high", image_detail="high",
            max_completion_tokens=7000, metadata=specialist_metadata), timeout=min(specialist_timeout, 120))
        specialist.update(status="COMPLETE", rawJudge=specialist_raw, returnedModel=specialist_metadata.get("returned_model"),
                          usage=specialist_metadata.get("usage"))
        if not isinstance(specialist_raw, dict) or set(specialist_raw) != {"gates", "permanentMarkings", "temporaryArtifacts"}:
            raise ValueError("invalid_specialist_contract")
        _validate_gates(specialist_raw["gates"], SPECIALIST_GATES)
        normalized = _normalize({**specialist_raw, "gates": [*primary.values(), *specialist_raw["gates"]]})
        return {**out, **normalized}
    except Exception as exc:
        specialist.update(status="UNKNOWN", returnedModel=specialist_metadata.get("returned_model"),
                          usage=specialist_metadata.get("usage"), errorCode=type(exc).__name__)
        return _unknown(out, "specialist_unavailable_or_invalid")


def repair_instructions(verdict):
    if not isinstance(verdict, dict) or verdict.get("passed") is not False or verdict.get("decision") != "FAIL":
        return []
    gates = verdict.get("gates")
    if not isinstance(gates, list):
        return []
    failed = {g.get("code") for g in gates if isinstance(g, dict) and isinstance(g.get("code"), str) and g.get("status") == "FAIL"}
    return [_REPAIRS[code] for code in GATES if code in failed]
