"""프롬프트 외부화 (spike `--prompt-file` 의 production화).

시스템 프롬프트는 코드 하드코딩 금지 — 파일(MANNEQUIN_PROMPT_FILE 또는 기본 경로)에서
읽고, ${토큰}만 치환한다. 분석 정보(상품명·색·핏·소재·강조특징)는 끝에 ground-truth로
자동 주입한다 (ai_agent_modules §3 AG-04 입력 · spike productBlock()).
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from . import knowledge as knowledge_kb
from .fit_axes import build_fit_profile_block
from .materials import material_guidance
from .selling_points import canonicalize

logger = logging.getLogger(__name__)

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_DEFAULT_PROMPT = os.path.join(_SERVER_DIR, "prompts", "mannequin_generate_v1.txt")


def _sanitize(value: Any) -> str:
    """프롬프트 인젝션·블록 이탈 방지 — 개행/제어문자 제거 + 길이 제한."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:200]


def clean_text(value: Any, limit: int = 300) -> str:
    """모델 출력 텍스트 정리 — 개행/제어문자 접기 + 길이 상한. 입력 sanitize(_sanitize, 200자·인젝션
    방지)보다 관대(표시용). AG-02 카피·AG-03 검수·AG-P2 mismatch 등 출력 정리 공용."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


@dataclass(frozen=True)
class MannequinPromptContext:
    clothing_type: str
    product_count: int
    base_gender: str
    image_manifest: str = ""  # 첨부 이미지 순서·역할 목록 (워커가 실제 슬롯으로 구성)
    fit_profile: dict | None = None


def load_prompt_template(settings: Settings) -> str:
    path = settings.mannequin_prompt_file or _DEFAULT_PROMPT
    if not os.path.isabs(path):  # 상대경로는 server/ 기준 (CWD 의존 제거)
        path = os.path.join(_SERVER_DIR, path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _product_block(
    product: dict,
    analysis: dict,
    seller_canon: str = "off",
    knowledge: str = "off",
    *,
    include_legacy_fit: bool = True,
) -> str:
    """분석 정보를 ground-truth 블록으로. 값 없는 항목은 생략, 값은 sanitize.
    materials는 [{name,ratio}] — name sanitize + ratio% 표기, 그 다음 소재 렌더링 가이드(영문) 첨부."""
    raw_mats = analysis.get("materials") or []
    mat_strs = []
    clothing_type = product.get("clothing_type") or product.get("clothingType")
    clothing_type_str = str(clothing_type or "")
    sub_category_str = str(analysis.get("subCategory") or "")
    for m in raw_mats:  # [{name,ratio}] (name=자유텍스트 → sanitize). 레거시 문자열도 수용
        if isinstance(m, dict):
            name = _sanitize(m.get("name", ""))
            if not name:
                continue
            r = m.get("ratio")
            mat_strs.append(f"{name} {int(r)}%" if isinstance(r, (int, float)) and r else name)
        elif m:
            mat_strs.append(_sanitize(m))
    material_entry = None
    if mat_strs:  # Material 줄 + (소재 인식) 렌더링 가이드 블록 (materials.py, §2.6)
        material_entry = f"- Material: {', '.join(mat_strs)}"
        guidance = material_guidance(raw_mats, clothing_type_str, sub_category_str)
        if guidance:
            material_entry += "\n" + guidance
    # 강조특징 정규화 (FR-D1): off=원문 그대로 / shadow=원문+매핑 로그 / enforce=canonical 큐만
    raw_points = list(analysis.get("sellingPoints") or []) + list(analysis.get("aiSuggestedPoints") or [])
    points = [_sanitize(p) for p in raw_points]
    key_features_line = None
    normalized_block = None
    if seller_canon == "enforce":
        matched, unmatched = canonicalize(raw_points)
        if unmatched:
            logger.info("seller_text_canonicalize", extra={"mode": "enforce", "dropped": len(unmatched)})
        if matched:  # PRODUCT CONTEXT 밖 별도 파생 블록 (FR-D1a — ground-truth 라벨 보존)
            normalized_block = (
                "NORMALIZED STYLING CUES (derived from seller input — styling direction only, "
                "not literal product claims):\n" + "\n".join(f"- {c}" for c in matched)
            )
        # key_features_line=None → PRODUCT CONTEXT에서 'Key features' 줄 제외
    else:
        if seller_canon == "shadow":
            matched, unmatched = canonicalize(raw_points)
            logger.info(
                "seller_text_canonicalize",
                extra={"mode": "shadow", "matched": len(matched), "unmatched": len(unmatched)},
            )
        key_features_line = f"- Key features: {'; '.join(points)}" if points else None
    # 정적 지식블록 (feature 2a): off=미적용 / static=category+styleTags 결정적 선택.
    # category는 clothing_type 우선, 없으면 subCategory로 보강(위 material_guidance 호출과 동일 관례).
    knowledge_block = None
    if knowledge == "static":
        kb_category = clothing_type_str or sub_category_str or None
        style_tags = [_sanitize(t) for t in (analysis.get("styleTags") or []) if t]
        kb_blocks = knowledge_kb.select(kb_category, style_tags)
        if kb_blocks:  # PRODUCT CONTEXT 밖 별도 섹션 (D1의 NORMALIZED STYLING CUES와 동일 구조)
            knowledge_block = (
                "COMPOSITION GUIDANCE (curated styling reference — not product facts):\n"
                + "\n".join(f"- {b}" for b in kb_blocks)
            )
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
        include_legacy_fit and analysis.get("fit") and f"- Fit: {_sanitize(analysis.get('fit'))}",
        material_entry,
        key_features_line,
    ]
    body = "\n".join(x for x in lines if x)
    context = ""
    if body:
        context = (
            "PRODUCT CONTEXT (seller-confirmed analysis — treat as ground truth, never "
            "contradict it; use it to keep the garment's color, fit, and any logo faithful):\n"
            + body
        )
    if normalized_block:  # PRODUCT CONTEXT 밖 별도 섹션 (FR-D1a)
        context = f"{context}\n\n{normalized_block}" if context else normalized_block
    if knowledge_block:  # PRODUCT CONTEXT 밖 별도 섹션 (feature 2a — D1과 동일 구조, 공존 가능)
        context = f"{context}\n\n{knowledge_block}" if context else knowledge_block
    return context


def render_mannequin_prompt(
    template: str,
    ctx: MannequinPromptContext,
    product: dict,
    analysis: dict,
    seller_canon: str = "off",
    knowledge: str = "off",
) -> str:
    """템플릿 ${토큰} 치환 + 분석 정보 자동 주입."""
    text = (
        template.replace("${clothingType}", _sanitize(ctx.clothing_type))
        .replace("${productCount}", str(ctx.product_count))
        .replace("${baseGender}", ctx.base_gender)
        .replace("${imageManifest}", ctx.image_manifest)  # 멀티라인 — 마지막에 치환
    )
    leftover = re.findall(r"\$\{[a-zA-Z_]+\}", text)  # 템플릿의 오타·미해결 토큰 검출
    if leftover:
        raise ValueError(f"프롬프트 템플릿에 해결되지 않은 토큰: {sorted(set(leftover))}")
    fit_profile = ctx.fit_profile if ctx.fit_profile is not None else analysis.get("fitProfile")
    fit_block = build_fit_profile_block(fit_profile)
    product_block = _product_block(
        product, analysis, seller_canon, knowledge, include_legacy_fit=fit_profile is None
    )
    blocks = [text, fit_block, product_block]
    return "\n\n".join(block for block in blocks if block)


def prompt_version(settings: Settings) -> str:
    return settings.mannequin_prompt_version
