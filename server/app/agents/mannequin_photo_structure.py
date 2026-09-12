"""사진의 물리적 구조를 상품명과 분리한다. 요청 내에서만 보관하며 DB를 쓰지 않는다."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import vision_llm
from .gemini_image import InlineImage, run_cpu_bound
from .product_reference import ProductReference


class Attribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["attachedSleeves", "shoulderCoverage", "armOpening", "sleeveLength",
                  "legOrSkirtStructure", "waistConstruction", "primaryOpening"]
    value: str = Field(max_length=110)
    certainty: Literal["high", "medium", "low", "unknown"]
    evidenceIndex: StrictInt = Field(ge=1)
    observation: str = Field(min_length=1, max_length=140)


class Structure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: Literal["top", "bottom", "outer", "one_piece", "unknown"]
    attributes: list[Attribute] = Field(max_length=7)
    uncertainties: list[str] = Field(max_length=3)


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


def validate(raw: dict, image_count: int) -> dict:
    value = Structure.model_validate(raw).model_dump()
    seen = set()
    for attribute in value["attributes"]:
        if attribute["evidenceIndex"] > image_count or attribute["name"] in seen:
            raise ValueError("구조의 사진 근거나 항목이 잘못됐어요.")
        seen.add(attribute["name"])
    if any(len(item) > 180 for item in value["uncertainties"]):
        raise ValueError("구조의 불확실성 설명이 너무 길어요.")
    return value


def prompt_block(result: dict) -> str:
    # 판매자가 확인한 사실로 승격하지 않는다. 저확신 가설은 생성 지시에서 제외한다.
    attributes = [{"attribute": item["name"], "observed": item["value"]}
                  for item in result.get("attributes", []) if item["certainty"] == "high"]
    return ("PHOTO-DERIVED STRUCTURE OBSERVATIONS (not seller confirmation). "
            "Photographs remain authoritative. Do not infer sleeves from a product name or "
            "upper-arm coverage. Do not add an unobserved seam or guess an unresolved attribute.\n"
            + json.dumps(attributes, ensure_ascii=False))


async def analyze(settings, refs: list[ProductReference]) -> dict:
    if not refs:
        raise ValueError("구조 분석에 상품 사진이 필요해요.")
    prepared = await run_cpu_bound(prepare, refs)
    prompt = Path(__file__).parents[2].joinpath("prompts/mannequin_photo_structure_v1.txt").read_text()
    prompt += "\nATTACHMENT ORDER:\n" + "\n".join(
        f"Image {index}: {ref.slot}" for index, ref in enumerate(prepared, 1))
    metadata = {}
    raw = await vision_llm._call_gpt(
        settings, settings.mannequin_specialist_model, prompt,
        [ref.image for ref in prepared], Structure.model_json_schema(), settings.mannequin_specialist_timeout_seconds,
        reasoning_effort="medium", image_detail="high", max_completion_tokens=1800,
        metadata=metadata)
    result = validate(raw, len(prepared))
    result["source_hash"] = hashlib.sha256(json.dumps(
        [(ref.slot, hashlib.sha256(ref.image.data).hexdigest()) for ref in refs]).encode()).hexdigest()
    result["metadata"] = metadata
    return result
