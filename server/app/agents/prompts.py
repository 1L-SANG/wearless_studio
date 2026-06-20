"""프롬프트 외부화 (spike `--prompt-file` 의 production화).

시스템 프롬프트는 코드 하드코딩 금지 — 파일(MANNEQUIN_PROMPT_FILE 또는 기본 경로)에서
읽고, ${토큰}만 치환한다. 분석 정보(상품명·색·핏·소재·강조특징)는 끝에 ground-truth로
자동 주입한다 (ai_agent_modules §3 AG-04 입력 · spike productBlock()).
"""

import os
import re
from dataclasses import dataclass

from ..config import Settings

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_DEFAULT_PROMPT = os.path.join(_SERVER_DIR, "prompts", "mannequin_generate_v1.txt")


def _sanitize(value: str) -> str:
    """프롬프트 인젝션·블록 이탈 방지 — 개행/제어문자 제거 + 길이 제한."""
    return re.sub(r"\s+", " ", str(value)).strip()[:200]


@dataclass(frozen=True)
class MannequinPromptContext:
    clothing_type: str
    product_count: int
    candidate: str
    base_fit: str
    base_gender: str


def load_prompt_template(settings: Settings) -> str:
    path = settings.mannequin_prompt_file or _DEFAULT_PROMPT
    if not os.path.isabs(path):  # 상대경로는 server/ 기준 (CWD 의존 제거)
        path = os.path.join(_SERVER_DIR, path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _product_block(product: dict, analysis: dict) -> str:
    """분석 정보를 ground-truth 블록으로. 값 없는 항목은 생략, 값은 sanitize."""
    materials = [_sanitize(m) for m in (analysis.get("materials") or [])]
    points = [_sanitize(p) for p in (analysis.get("sellingPoints") or []) + (analysis.get("aiSuggestedPoints") or [])]
    genders = [_sanitize(g) for g in (analysis.get("targetGenders") or [])]
    category = " / ".join(
        _sanitize(x)
        for x in (product.get("clothing_type") or product.get("clothingType"), analysis.get("subCategory"))
        if x
    )
    lines = [
        product.get("name") and f"- Product name: {_sanitize(product.get('name'))}",
        category and f"- Category: {category}",
        genders and f"- Target gender: {', '.join(genders)}",
        analysis.get("fit") and f"- Fit: {_sanitize(analysis.get('fit'))}",
        materials and f"- Material: {', '.join(materials)}",
        points and f"- Key features: {'; '.join(points)}",
    ]
    body = "\n".join(x for x in lines if x)
    if not body:
        return ""
    return (
        "PRODUCT CONTEXT (seller-confirmed analysis — treat as ground truth, never "
        "contradict it; use it to keep the garment's color, fit, and any logo faithful):\n"
        + body
    )


def render_mannequin_prompt(
    template: str, ctx: MannequinPromptContext, product: dict, analysis: dict
) -> str:
    """템플릿 ${토큰} 치환 + 분석 정보 자동 주입."""
    text = (
        template.replace("${clothingType}", _sanitize(ctx.clothing_type))
        .replace("${productCount}", str(ctx.product_count))
        .replace("${candidate}", ctx.candidate)
        .replace("${baseFit}", _sanitize(ctx.base_fit))
        .replace("${baseGender}", ctx.base_gender)
    )
    leftover = re.findall(r"\$\{[a-zA-Z_]+\}", text)  # 템플릿의 오타·미해결 토큰 검출
    if leftover:
        raise ValueError(f"프롬프트 템플릿에 해결되지 않은 토큰: {sorted(set(leftover))}")
    block = _product_block(product, analysis)
    return f"{text}\n\n{block}" if block else text


def prompt_version(settings: Settings) -> str:
    return settings.mannequin_prompt_version
