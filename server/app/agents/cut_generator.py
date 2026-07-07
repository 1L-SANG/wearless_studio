"""AG-06 cut-generator — 컷 생성 (스타일링·호리존·제품). ai_agent_modules §3 AG-06.

cutType별 프롬프트는 독립 파일(prompts/cuts/{cutType}_v1.txt)로 분리하고, 입출력 계약·
생성 호출 등 배관은 이 모듈 1벌로 공유한다(§3 AG-06 구현 구조 결정, 2026-06-20). tier는
셋 다 image_high 공유(컷별 모델 분리는 보류).

이 모듈은 코어(프롬프트 조립 + 생성 호출)만 제공한다. PL-4 상세페이지 생성(detail_page_job)이
source='ai' 블록별로 이 generate()를 호출한다.
"""

import os

from ..config import Settings
from .gemini_image import GeminiImageClient, InlineImage
from .mannequin import base_color_images
from .model_routing import resolve_model
from .prompts import _sanitize

CUT_TYPES = ("styling", "horizon", "product")

# 첨부 이미지 슬롯 → 모델용 설명. base_color_images 순서와 동일하게 매니페스트를 만든다
# (detail_page_job이 그 순서로 이미지를 attach하므로 순서·의미가 일치한다).
_SLOT_DESC = {
    "Front": "front view of the product",
    "Back": "back view of the product",
    "Detail": "detail close-up of the product (texture/stitching/print)",
    "Fit": "fit reference — the product worn on a person",
}

_DEFAULT_CUT_TYPE = "styling"  # unknown cutType 은 styling 으로 안전 폴백 (AG-07과 동일 관례)

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_TEMPLATE_FILE = {
    "styling": os.path.join(_SERVER_DIR, "prompts", "cuts", "styling_v1.txt"),
    "horizon": os.path.join(_SERVER_DIR, "prompts", "cuts", "horizon_v1.txt"),
    "product": os.path.join(_SERVER_DIR, "prompts", "cuts", "product_v1.txt"),
}


def _template_path(cut_type: str) -> str:
    return _TEMPLATE_FILE.get(cut_type, _TEMPLATE_FILE[_DEFAULT_CUT_TYPE])


def _load_template(cut_type: str) -> str:
    with open(_template_path(cut_type), encoding="utf-8") as f:
        return f.read()


def _product_block(product: dict) -> str:
    """상품 ground-truth 블록 (name/clothingType, sanitize) — 셀러 원문을 지시문으로 절대 주입 안 함."""
    name = _sanitize(product.get("name"))
    clothing_type = _sanitize(product.get("clothing_type") or product.get("clothingType"))
    lines = [
        name and f"- Product name: {name}",
        clothing_type and f"- Garment type: {clothing_type}",
    ]
    body = "\n".join(x for x in lines if x)
    if not body:
        return ""
    return (
        "PRODUCT CONTEXT (ground truth — never contradict it; use it to keep the garment's "
        "identity faithful, generate the SAME garment as the product photos):\n" + body
    )


def build_prompt(cut_spec: dict, product: dict) -> str:
    """cutType 템플릿 로드 + sanitized product/direction/shot 주입. 미상 cutType→styling 폴백.
    셀러 원문(product 자유 텍스트)은 절대 지시문으로 직접 주입하지 않고 _sanitize를 거친다."""
    cut_type = cut_spec.get("cutType")
    template = _load_template(cut_type)
    direction = _sanitize(cut_spec.get("direction") or "front")
    shot = _sanitize(cut_spec.get("shot") or "full")
    text = (
        template
        .replace("${direction}", direction)
        .replace("${shot}", shot)
        .replace("${imageManifest}", _manifest(product))
    )
    block = _product_block(product)
    return f"{text}\n\n{block}" if block else text


def _manifest(product: dict) -> str:
    """첨부 이미지 순서·의미 목록 — base_color_images 순서와 일치(detail_page_job이 그 순서로 attach).
    비면 일반 문구. (템플릿 ${imageManifest} 치환 — 미치환 리터럴 토큰 유출 방지)"""
    lines = [
        f"{i}. {_SLOT_DESC.get(slot, 'view of the product')}"
        for i, (slot, _id) in enumerate(base_color_images(product), 1)
    ]
    return "\n".join(lines) or "(the seller's product photos — treat as ground truth)"


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    cut_spec: dict,
    product: dict,
    images: list[InlineImage],
) -> tuple[bytes, str]:
    """컷 1개 생성. 실패 시 GeminiError 를 그대로 전파(호출자가 빈 슬롯 등으로 처리)."""
    model = resolve_model(settings, "image_high")
    prompt = build_prompt(cut_spec, product)
    res = await gemini.generate_content_image(
        model, prompt, images, settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    return res.image, res.mime
