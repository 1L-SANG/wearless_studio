"""Independent main-garment QC, with bounded evidence and no provider fallback."""
import asyncio
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image, ImageOps

from . import vision_llm
from .gemini_image import InlineImage, run_cpu_bound
from .image_qc import build_declared_fit_block
from .product_reference import ProductReference
from .prompts import build_mirrored_source_block, clean_text

VERSION = "visible_garment_specialists_v1"
ROLES = ("structure", "details", "appearance")
_PROMPT = Path(__file__).resolve().parents[2] / "prompts/mannequin_specialist_qc_v1.txt"
_KINDS = {
    "structure": ("family_change", "silhouette_change", "construction_change", "other"),
    "details": ("construction_change", "design_line_change", "closure_change", "pocket_change", "trim_change", "other"),
    "appearance": ("color_shift", "material_change", "pattern_change", "opacity_change", "other"),
}
_RESPONSIBILITIES = {
    "structure": "Family, overall silhouette and fundamental garment construction only. Inspect outer shape, major functional openings and attachment morphology. Decorative seam topology, buttons, pockets, color and material belong to other roles.",
    "details": "Permanent seams/design lines, closures/buttons, pockets and trims only. Compare visible counts, shapes, placement, endpoints and connections. Do not assess sleeve/leg category, overall fit, material, color, pattern or opacity. Distinguish real edge binding from unsupported internal attachment lines.",
    "appearance": "Main color, visible material surface, pattern scale and opacity only. Allow exposure, shading, stretch and magnification differences. Do not infer fiber identity or hidden thickness. Seam topology, buttons, pockets, silhouette and sleeve/leg category belong to other roles.",
}
_CHECKLISTS = {
    "top": {
        "structure": "Inspect neckline, shoulder extent, sleeve or sleeveless construction, arm openings, body length and silhouette. Distinguish broad shoulder drape from attached sleeves; preserve real edge bindings and broad shoulders. Do not inspect the matching bottom.",
        "details": "Scan actual neckline/arm edges, chest, torso and hem joins, lines, closures and trim. Derive every count from this garment's source photos; do not assume chest curves or vertical seams on a plain garment.",
        "appearance": "Compare the main top's dominant color and visible surface across chest and torso. Compare patterns and opacity only where supported.",
    },
    "bottom": {
        "structure": "Derive subtype from the source: inspect two legs and openings for trousers, or a continuous skirt outline and hem for a skirt. Compare visible waist, applicable rise, length and silhouette. Do not demand trouser legs for a skirt or inspect shoulders, sleeves or armholes.",
        "details": "Inspect source-supported waistband, closures, pockets, panels, permanent seams and hems. Derive details from the visible bottom subtype. Do not require a fly, pockets, pleats or trim without evidence.",
        "appearance": "Compare the main bottom's dominant color, material surface and pattern scale across visible fabric. Compare opacity only where supported.",
    },
    "whole": {
        "structure": "Derive the garment family from the actual source photos. Inspect its applicable neckline, shoulder, openings, torso, waist and lower silhouette. Inspect legs only if the source proves separate legs. Do not impose inapplicable sleeve, trouser or skirt requirements.",
        "details": "Scan the actual garment's visible permanent seams, panel relationships, closures, pockets, trim and hems. Derive counts, positions and connections solely from source-supported construction.",
        "appearance": "Compare the main garment's color, visible material surface, pattern scale and supported opacity across its visible extent.",
    },
}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _string(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _array(items):
    return {"type": "array", "items": items, "maxItems": 3}


def _enum(values):
    return {"type": "string", "enum": list(values)}


_BOX = _object({key: {"type": "number", "minimum": 0, "maximum": 1}
                for key in ("left", "top", "right", "bottom")})
_EVIDENCE = _object({"imageIndex": {"type": "integer", "minimum": 1},
                     "region": _BOX, "observation": _string(110)})
SCHEMA = _object({
    "verdict": _enum(("pass", "review", "fail")), "summary": _string(180),
    "issues": _array(_object({
        "kind": _enum(dict.fromkeys(kind for kinds in _KINDS.values() for kind in kinds)),
        "materiality": _enum(("minor", "material")), "sourceEvidence": _EVIDENCE,
        "candidateEvidence": _EVIDENCE, "repairInstruction": _string(220),
    })),
    "confirmedMatches": _array(_string(180)), "visibilityLimitations": _array(_string(180)),
    "materialUncertainties": _array(_string(180)),
})


def _validate(value, schema):
    kind = schema["type"]
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float)}[kind]
    if not valid:
        raise ValueError("Invalid specialist response type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Invalid specialist response enum")
    if kind in ("integer", "number") and (not math.isfinite(value) or
            not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)):
        raise ValueError("Invalid specialist evidence coordinate")
    if kind == "string" and (not value.strip() or len(value) > schema.get("maxLength", math.inf)):
        raise ValueError("Invalid specialist response text")
    if kind == "object":
        if set(value) != set(schema["required"]):
            raise ValueError("Invalid specialist response fields")
        for key, child in value.items():
            _validate(child, schema["properties"][key])
    if kind == "array":
        if len(value) > schema["maxItems"]:
            raise ValueError("Too many specialist findings")
        for child in value:
            _validate(child, schema["items"])


def validate_response(raw, images, role):
    """Reject malformed or cross-image evidence; visibility is never absence."""
    _validate(raw, SCHEMA)
    for issue in raw["issues"]:
        if issue["kind"] not in _KINDS[role]:
            raise ValueError("Finding outside specialist responsibility")
        for key, expected in (("sourceEvidence", "source"), ("candidateEvidence", "candidate")):
            evidence = issue[key]
            index = evidence["imageIndex"]
            if index > len(images) or images[index - 1]["kind"] != expected:
                raise ValueError("Evidence cites the wrong image role")
            box = evidence["region"]
            if box["left"] >= box["right"] or box["top"] >= box["bottom"]:
                raise ValueError("Evidence box is empty or inverted")
    material = any(i["materiality"] == "material" for i in raw["issues"])
    if raw["verdict"] == "fail" and not material:
        raise ValueError("Failure needs a material evidenced issue")
    if raw["verdict"] == "pass" and (material or raw["materialUncertainties"]):
        raise ValueError("Pass cannot conceal material mismatch or uncertainty")
    return raw


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _within(outer, inner):
    x, y, right, bottom = outer
    return [round(x + inner[0] * (right-x), 5), round(y + inner[1] * (bottom-y), 5),
            round(x + inner[2] * (right-x), 5), round(y + inner[3] * (bottom-y), 5)]


def _prepare(product_refs, candidate, clothing_type, match_image, fit_profile, role, source_mirrored=False):
    family = "top" if clothing_type in ("top", "outer") else "bottom" if clothing_type in ("bottom", "pants", "skirt") else "whole"
    main_box = [0.15, 0.16, 0.85, 0.58] if family == "top" else [0.18, 0.40, 0.85, 0.96] if family == "bottom" else [0.15, 0.16, 0.85, 0.96]
    region = ({"structure": [0, 0, 1, .72], "details": [0, .10, 1, 1], "appearance": [.10, .18, .90, .92]}
              if family == "top" else {"structure": [0, 0, 1, 1], "details": [0, 0, 1, .62], "appearance": [.05, .18, .95, .90]})[role]
    if family == "whole" and role == "details":
        region = [0, .10, 1, 1]
    images, manifest = [], []

    def append(image, kind, label, **extra):
        with Image.open(BytesIO(image.data)) as decoded:
            decoded.load()
            size = list(ImageOps.exif_transpose(decoded).size)
        images.append(image)
        manifest.append({"imageIndex": len(images), "kind": kind, "label": label,
                         "sha256": _sha(image.data), "dimensions": size, **extra})
        return len(images)

    def crop(parent_index, box, label):
        parent = images[parent_index - 1]
        with Image.open(BytesIO(parent.data)) as decoded:
            decoded = ImageOps.exif_transpose(decoded)
            pixels = [round(v * n) for v, n in zip(box, (decoded.width, decoded.height) * 2)]
            if not (pixels[0] < pixels[2] and pixels[1] < pixels[3]):
                raise ValueError("Image too small for evidence crop")
            output = BytesIO()
            decoded.crop(pixels).convert("RGB").save(output, format="PNG")
        append(InlineImage("image/png", output.getvalue()), manifest[parent_index - 1]["kind"],
               label + f", parent normalized box {box}; repeated pixels, not another view",
               parentImageIndex=parent_index, parentSha256=_sha(parent.data),
               parentPixelBox=pixels, parentNormalizedBox=box, coordinateFrame="EXIF-oriented parent")

    if not product_refs:
        raise ValueError("No source photographs")
    priority = {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3, "Fit": 4}
    for ref in sorted(product_refs, key=lambda ref: priority.get(ref.slot, 5)):
        slot = clean_text(ref.slot, 48) or "Unknown"
        append(ref.image, "source", f"Source {slot} photograph", slot=slot)
    crop(1, region, f"Source {manifest[0]['slot']} generic {family} {role} region")
    if match_image is not None:
        append(match_image, "matching", "Matching garment identity reference ONLY; not a validated source")
    candidate_index = append(candidate, "candidate", "Candidate whole full-body photograph")
    crop(candidate_index, main_box, f"Candidate generic {family} main garment region")
    crop(candidate_index, _within(main_box, region), f"Candidate generic {family} {role} region")
    prompt = _PROMPT.read_text(encoding="utf-8")
    prompt += f"\nASSIGNED ROLE: {role}\n{_RESPONSIBILITIES[role]}\nAPPLICABLE CHECKLIST: {_CHECKLISTS[family][role]}\n"
    if role == "structure":
        prompt += build_declared_fit_block(fit_profile)
    if source_mirrored is True:
        prompt += (
            "\nSOURCE ORIENTATION FOR COMPARISON: Account for mirrored source photographs when "
            "comparing with the candidate. The following is the source-orientation contract, not "
            "a request to generate an image. Corrected readable lettering and restored left/right "
            "placement are expected matches, not invented changes. Apply only within your assigned role.\n"
            + build_mirrored_source_block({"sourceMirrored": True}) + "\n"
        )
    prompt += "\nEXACT ATTACHMENT ORDER:\n" + "\n".join(f"Image {m['imageIndex']}: {m['label']}" for m in manifest)
    return prompt, images, manifest


async def judge(settings, product_refs: list[ProductReference], generated_image: InlineImage,
                *, clothing_type, fit_profile=None, match_image=None, source_mirrored=False) -> dict:
    """Three independent calls. An unavailable role yields review, never approval."""
    async def assess(role):
        metadata = {}
        result = {"status": "error", "metadata": metadata}
        try:
            prompt, images, manifest = await run_cpu_bound(
                _prepare, product_refs, generated_image, clothing_type, match_image, fit_profile, role,
                source_mirrored)
            result.update(images=manifest, prompt_hash=_sha(prompt.encode()),
                          schema_hash=_sha(json.dumps(SCHEMA, sort_keys=True).encode()))
            raw = await vision_llm._call_gpt(
                settings, settings.mannequin_specialist_model, prompt, images, SCHEMA,
                settings.mannequin_specialist_timeout_seconds, reasoning_effort="medium", image_detail="high",
                max_completion_tokens=1800, metadata=metadata)
            result.update(response=validate_response(raw, manifest, role), status="ok")
        except Exception as error:
            result["error_type"] = type(error).__name__
        return result

    results = await asyncio.gather(*(assess(role) for role in ROLES))
    roles = dict(zip(ROLES, results))
    complete = all(row["status"] == "ok" for row in results)
    verdicts = [row["response"]["verdict"] for row in results if row["status"] == "ok"]
    return {"version": VERSION, "image_hash": _sha(generated_image.data), "roles": roles,
            "complete": complete, "matching_fully_validated": False,
            "verdict": "fail" if "fail" in verdicts else "pass" if complete and all(v == "pass" for v in verdicts) else "review"}


def blocking_issues(report):
    return [issue for role in ROLES for row in [(report or {}).get("roles", {}).get(role, {})]
            if row.get("status") == "ok" for issue in row["response"]["issues"]
            if issue["materiality"] == "material"]


def repair_instructions(report):
    """Only validated material corrections, preserving the model's exact wording."""
    return list(dict.fromkeys(issue["repairInstruction"] for issue in blocking_issues(report)))


def repair_accepted(report):
    rows = (report or {}).get("roles", {})
    if not ((report or {}).get("complete") is True and (report or {}).get("verdict") == "pass"
            and set(rows) == set(ROLES)):
        return False
    for row in rows.values():
        if row.get("status") != "ok":
            return False
        raw = row.get("response")
        try:
            _validate(raw, SCHEMA)
        except ValueError:
            return False
        if (raw["verdict"] != "pass" or raw["materialUncertainties"]
                or any(i["materiality"] == "material" for i in raw["issues"])):
            return False
    return True
