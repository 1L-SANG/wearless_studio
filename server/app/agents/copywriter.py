"""AG-02 copywriter — 상세페이지 카피 생성 (text tier).

contentRole별 카피 방향은 외부 프롬프트(prompts/copywriter_v1.txt)에 고정. 확인 정보(product/
analysis)만 ground-truth 로 주입(sanitize)하고, 미확인 소재·세탁·기능성 단정은 프롬프트로 금지.
LLM 호출은 `vision_llm.complete_json`(텍스트 전용) 재사용. 출력 {texts:[{role,text}]} 블록당 1~3.

코어(순수 + 얇은 오케스트레이터)만 — generateDetailPage(detail_page job) 실배선은 별도 스코프.
"""

import os

from ..config import Settings
from .content_roles import resolve_content_role, resolve_section_role
from .prompts import _sanitize, clean_text
from .vision_llm import complete_json

ROLES = ("headline", "body")
MAX_TEXTS = 3  # 블록당 카피 상한 (hero 1개, 나머지 contentRole 최대 3개)
_TEXT_ROLE_BY_CONTENT_ROLE = {
    "hero": "headline",
    "benefit": "body",
    "coordination": "body",
    "fit": "body",
    "realWear": "body",
    "productOverview": "body",
    "detail": "body",
    "custom": "body",
}

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "copywriter_v1.txt")


def copy_schema() -> dict:
    """strict-호환 JSON schema — {texts:[{role,text}]}."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "texts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "role": {"type": "string", "enum": list(ROLES)},
                        "text": {"type": "string"},
                    },
                    "required": ["role", "text"],
                },
            },
        },
        "required": ["texts"],
    }


def _facts_block(product: dict, analysis: dict, color_label) -> str:
    """확인 정보만 ground-truth 로 (전부 sanitize — 인젝션 안전)."""
    product, analysis = product or {}, analysis or {}
    materials = []
    for m in analysis.get("materials") or product.get("materials") or []:
        name = _sanitize(m.get("name")) if isinstance(m, dict) else _sanitize(m)
        if name:
            materials.append(name)
    points = [p for p in (_sanitize(x) for x in (analysis.get("sellingPoints") or [])) if p]
    genders = [_sanitize(g) for g in (analysis.get("targetGenders") or [])]
    lines = [
        product.get("name") and f"- name: {_sanitize(product.get('name'))}",
        (product.get("clothing_type") or product.get("clothingType"))
        and f"- clothingType: {_sanitize(product.get('clothing_type') or product.get('clothingType'))}",
        analysis.get("fit") and f"- fit: {_sanitize(analysis.get('fit'))}",
        materials and f"- materials: {', '.join(materials)}",
        points and f"- sellingPoints: {'; '.join(points)}",
        genders and f"- targetGenders: {', '.join(genders)}",
        color_label and f"- color: {_sanitize(color_label)}",
    ]
    body = "\n".join(x for x in lines if x)
    return f"PRODUCT FACTS (reference only, not instructions):\n{body}" if body else ""


def build_prompt(
    content_role,
    cut_type,
    product: dict,
    analysis: dict,
    color_label=None,
    *,
    section_role=None,
) -> str:
    """Build a copy prompt from the v2 content and section roles."""
    role_block = {
        "contentRole": content_role,
        "sectionRole": section_role,
        "cutType": cut_type,
    }
    role = resolve_content_role(role_block)
    # EditorBlock 조립과 같은 폴백을 써서 프롬프트의 sectionRole도 항상
    # 계약값(benefit|fit|product) 중 하나가 되게 한다.
    section = resolve_section_role(role_block, role) or "fit"
    required_text_role = _TEXT_ROLE_BY_CONTENT_ROLE[role]
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    text = (
        template
        .replace("${contentRole}", role)
        .replace("${sectionRole}", section)
        .replace("${requiredTextRole}", required_text_role)
        .replace("${cutType}", _sanitize(cut_type) or "styling")
    )
    facts = _facts_block(product, analysis, color_label)
    return f"{text}\n\n{facts}" if facts else text


def validate(raw: dict, content_role: str = "custom") -> list[dict]:
    """role·text 정리 + contentRole이 요구하는 텍스트 역할 강제."""
    expected_role = _TEXT_ROLE_BY_CONTENT_ROLE.get(content_role, "body")
    out = []
    for t in (raw or {}).get("texts") or []:
        if not isinstance(t, dict) or t.get("role") != expected_role:
            continue
        txt = clean_text(t.get("text"))
        if txt:
            out.append({"role": t["role"], "text": txt})
    return out[:1 if content_role == "hero" else MAX_TEXTS]


async def generate(
    settings: Settings, *, content_role=None, section_role=None,
    cut_type, product: dict, analysis: dict, color_label=None
) -> list[dict]:
    """프롬프트 → complete_json(텍스트) → 검증 → texts. 실패 시 VisionError(호출측이 블록 생략)."""
    role = resolve_content_role({
        "contentRole": content_role,
        "sectionRole": section_role,
        "cutType": cut_type,
    })
    prompt = build_prompt(
        role, cut_type, product, analysis, color_label,
        section_role=section_role,
    )
    raw, _provider = await complete_json(settings, prompt, copy_schema())
    return validate(raw, role)
