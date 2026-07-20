"""AG-P2 image-qc — 생성 이미지 동일성 검수 (vision LLM, bytes 입력).

생성 컷이 입력 상품과 "같은 옷인가"(색·패턴·넥라인·디테일)를 판정. retry면 mismatches +
correctionPrompt(재생성 시 보정 지시)를 반환한다(ai_agent_modules §5). vision_llm 재사용.

지금은 **shadow 한정**(판정 로그만) — enforce(재시도 게이트)·크레딧/상한 정책은 별도 결정.
코어(순수 + 얇은 오케스트레이터)만.
"""

import os

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import analyze_with_fallback

VERDICTS = ("pass", "retry")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "image_qc_v1.txt")


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
