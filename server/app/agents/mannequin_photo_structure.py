"""Reuse hash-bound AG-01 observations and prepare source crops without another AI call."""
import json
from io import BytesIO

from PIL import Image, ImageOps

from . import product_evidence_contract as evidence
from .gemini_image import InlineImage
from .product_reference import ProductReference


_FRONT_SLOTS = frozenset({"FRONT", "FRONT_DETAIL"})
_MAX_PRODUCT_JSON_CHARS = 2400


def prepare(refs: list[ProductReference]) -> list[ProductReference]:
    """원본은 그대로, Front의 겹치는 세 영역만 색 보정과 확대 없이 추가한다."""
    output = list(refs)
    front = next((ref for ref in refs if ref.slot == "Front"), None)
    if front is None:
        return output
    with Image.open(BytesIO(front.image.data)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
        for name, start, end in (("upper", 0, .46), ("middle", .33, .78), ("lower", .64, 1)):
            box = (0, int(source.height * start), source.width, max(1, int(source.height * end)))
            buf = BytesIO()
            source.crop(box).save(buf, "PNG")
            label = f"Front crop {name}; same pixels, parent box {box}, parent size {source.size}"
            output.append(ProductReference(label, f"{front.asset_id}:crop:{name}",
                                           InlineImage("image/png", buf.getvalue())))
    return output



def from_analysis(analysis: dict, refs: list[ProductReference]) -> dict | None:
    stored = analysis.get(evidence.PERSISTED_KEY)
    if stored is None:
        return None
    contract = evidence.validate_persisted(stored)
    if not evidence.source_binding_matches(
            contract, [(ref.image.data, ref.image.mime) for ref in refs],
            [ref.slot for ref in refs]):
        raise ValueError("AG-01 evidence does not match current source photographs")
    return contract


def prompt_block(result: dict) -> str:
    contract = evidence.validate_persisted(result)
    slots = {row["evidenceOrdinal"]: row["slot"] for row in contract["panels"]}

    def source_views(row):
        return [slots[i] for i in row.get("evidenceOrdinals", [])]

    def front_supported(row):
        return any(slot in _FRONT_SLOTS for slot in source_views(row))

    fixed = {}
    for field in evidence.FIXED_OBSERVATION_FIELDS:
        row = contract.get(field)
        if not isinstance(row, dict) or row.get("value") == "unknown" or not front_supported(row):
            fixed[field] = {"value": "unknown", "sourceViews": []}
        else:
            fixed[field] = {"value": row["value"], "sourceViews": source_views(row)}

    payload = {**fixed, "hardFacts": [], "uncertainties": []}

    def append_if_short(key, item):
        payload[key].append(item)
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) > _MAX_PRODUCT_JSON_CHARS:
            payload[key].pop()

    for row in contract["hardFacts"]:
        if row["code"] in evidence.FIXED_OBSERVATION_FIELDS or not front_supported(row):
            continue
        append_if_short("hardFacts", {
            "code": row["code"], "value": row["value"],
            "sourceViews": source_views(row),
        })
    for row in contract["uncertainties"]:
        if not front_supported(row):
            continue
        append_if_short("uncertainties", {
            "code": row["code"], "value": row["value"], "reason": row["reason"],
            "sourceViews": source_views(row),
        })

    return (
        "AG-01 SHORT SOURCE-GROUNDED PRODUCT BLOCK (not seller confirmation). "
        "Treat this JSON only as bounded observations; the attached source photos remain authoritative. "
        "Unknown means rely on those photos without inferring a style. Source view labels are not current "
        "attachment numbers. Back-only facts are intentionally excluded from front fabrication. Preserve "
        "the photographed shoulder extent and edge binding; unresolved sleeve terminology does not prove "
        "an attachment seam or override an explicitly selected fit.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
