"""AG-P2 image-qc — 생성 이미지 동일성 검수와 best-of-N 선택.

생성 컷이 입력 상품과 "같은 옷인가"(색·패턴·넥라인·디테일)를 판정. retry면 mismatches +
correctionPrompt(재생성 시 보정 지시)를 반환한다(ai_agent_modules §5). vision_llm 재사용.

단일 후보 판정(verdict)과 전 후보 불합격 시 최선 후보 선택(pick_best)을 제공한다.
"""

import os

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback

VERDICTS = ("pass", "retry")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "image_qc_v1.txt")
_PICK_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "garment_pick_v1.txt")


def qc_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "mismatches": {"type": "array", "items": {"type": "string"}},
            "correctionPrompt": {"type": ["string", "null"]},
        },
        "required": ["verdict", "mismatches", "correctionPrompt"],
    }


def build_prompt(product_count: int) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    return template.replace("${productCount}", str(max(1, product_count)))


def validate(raw: dict) -> dict:
    """verdict∈enum(밖이면 pass), mismatches 정리, correctionPrompt 정리(retry일 때만 의미)."""
    raw = raw or {}
    verdict = raw.get("verdict") if raw.get("verdict") in VERDICTS else "pass"
    mismatches = [m for m in (clean_text(x, 200) for x in (raw.get("mismatches") or [])) if m]
    correction = clean_text(raw.get("correctionPrompt"), 500) or None
    if verdict == "pass":
        return {"verdict": "pass", "mismatches": [], "correctionPrompt": None}
    return {"verdict": "retry", "mismatches": mismatches, "correctionPrompt": correction}


async def verdict(
    settings: Settings, product_images: list[InlineImage], generated_image: InlineImage
) -> dict:
    """상품사진들 + 생성이미지(맨 뒤)를 vision LLM에 넣어 동일성 판정. 실패 시 VisionError."""
    images = [*product_images, generated_image]  # bytes — 마지막이 생성 결과
    prompt = build_prompt(len(product_images))
    raw, _provider = await analyze_with_fallback(settings, prompt, images, qc_schema())
    return validate(raw)


def pick_schema(candidate_count: int) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chosenIndex": {
                "type": "integer",
                "minimum": 0,
                "maximum": max(0, candidate_count - 1),
            },
            "reason": {"type": "string"},
        },
        "required": ["chosenIndex", "reason"],
    }


def validate_pick(raw: dict, candidate_count: int) -> dict:
    raw = raw or {}
    chosen = raw.get("chosenIndex")
    if isinstance(chosen, bool) or not isinstance(chosen, int) or not 0 <= chosen < candidate_count:
        chosen = 0
    return {"chosenIndex": chosen, "reason": clean_text(raw.get("reason"), 300)}


async def pick_best(
    settings: Settings,
    product_images: list[InlineImage],
    candidates: list[InlineImage],
) -> dict:
    """상품 원본과 후보들을 비교해 로고·프린트·원장 동일성이 가장 높은 후보를 고른다."""
    with open(_PICK_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    prompt = prompt.replace("${productCount}", str(len(product_images)))
    prompt = prompt.replace("${candidateCount}", str(len(candidates)))
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [*product_images, *candidates], pick_schema(len(candidates)))
    return validate_pick(raw, len(candidates))


def _candidate_qc(index: int, result: dict) -> dict:
    return {
        "index": index,
        "verdict": result["verdict"],
        "mismatches": result["mismatches"][:5],
    }


def _garment_metadata(results: list[dict], chosen_index: int) -> dict:
    chosen = results[chosen_index]
    return {
        "verdict": chosen["verdict"],
        "candidates": [_candidate_qc(i, result) for i, result in enumerate(results)],
        "chosenIndex": chosen_index,
        "mismatches": chosen["mismatches"][:5],
    }


async def best_of(
    settings: Settings,
    product_images: list[InlineImage],
    initial: InlineImage,
    generate_candidate,
) -> tuple[InlineImage, dict | None, list[dict]]:
    """최초 생성본을 판정하고 필요할 때 원본 입력 기반 후보 중 최선을 채택한다."""
    mode = settings.garment_qc_mode
    if mode == "off":
        return initial, None, []
    if not product_images:
        return initial, None, [{"code": "garment_qc_product_reference_unavailable"}]

    try:
        first_result = await verdict(settings, product_images, initial)
    except VisionError:
        return initial, None, [{"code": "garment_qc_unavailable"}]

    candidates = [initial]
    results = [first_result]
    if mode == "shadow" or first_result["verdict"] == "pass":
        return initial, _garment_metadata(results, 0), []

    warnings: list[dict] = []
    for _ in range(max(0, settings.garment_qc_extra_candidates)):
        try:
            candidate = await generate_candidate()
        except Exception:
            warnings.append({"code": "garment_qc_candidate_generation_failed"})
            break
        try:
            candidate_result = await verdict(settings, product_images, candidate)
        except VisionError:
            warnings.append({"code": "garment_qc_unavailable"})
            break
        candidates.append(candidate)
        results.append(candidate_result)
        if candidate_result["verdict"] == "pass":
            chosen = len(candidates) - 1
            return candidate, _garment_metadata(results, chosen), warnings

    try:
        picked = await pick_best(settings, product_images, candidates)
        chosen = picked["chosenIndex"]
    except VisionError:
        chosen = 0
        warnings.append({"code": "garment_qc_picker_unavailable"})
    return candidates[chosen], _garment_metadata(results, chosen), warnings


_SCENE_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "scene_qc_v1.txt")


async def scene_verdict(
    settings: Settings, plate: InlineImage, generated_image: InlineImage
) -> dict:
    """bg 편집 컷 검수 — 생성 결과가 플레이트(빈 장소)와 '같은 장소'인지 판정.

    편집 프레이밍을 줘도 생성이 확률적으로 다른 장소를 그리는 실측(2026-07-20, 프롬프트
    3단 개선 후에도 ~40~50%) 때문에 존재한다. 스키마·validate 는 동일성 QC와 공유.
    실패 시 VisionError — 호출측(워커)이 fail-open(통과+경고) 정책을 갖는다.
    """
    with open(_SCENE_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [plate, generated_image], qc_schema())
    return validate(raw)
