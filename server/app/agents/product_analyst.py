"""AG-01 product-analyst — 프롬프트 구성 · 출력 검증 · Product/Analysis 분배.

책임 분리(순수 함수 우선, 테스트 쉬움):
- `build_prompt(product)` — 외부 프롬프트(prompts/product_analyst_v1.txt) + 계약 enum 주입 +
  sanitize 된 PRODUCT CONTEXT. 인젝션 규칙은 템플릿 상단 고정.
- `analysis_schema()` — provider 에 넘길 구조화 출력 스키마(strict-호환 JSON schema).
- `validate(raw)` — 모델 출력 불신: enum 밖 값 드롭·measurements 강제 제거·points 절단·
  styleTags 필터. (서버가 계약을 강제한다 — Principle ⑤)
- `distribute(validated)` — raw → {product, analysis, intermediate} (ai_agent_modules §3 후처리).
- `analyze(settings, product, images)` — 위를 엮는 얇은 오케스트레이터(워커가 호출).

계약 토큰은 전부 `common_data_contract.md §4`. styleTags 는 `style_tags.STYLE_TAGS` 단일 정본.
"""

import os
import re

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import _sanitize
from .style_tags import STYLE_TAGS, is_style_tag
from .vision_llm import analyze_with_fallback, complete_json

# ── 계약 §4 enum 토큰 (검증·스키마 단일 참조) ────────────────────────────────
CLOTHING_TYPES = ("top", "bottom", "outer", "dress")
SUBCATEGORIES = (
    "tshirt", "sweatshirt", "shirt", "knit",  # top
    "cotton_pants", "training_pants", "jeans", "slacks", "skirt",  # bottom
    "jacket", "cardigan", "padding", "coat",  # outer (shirt 는 top 과 공유)
)
FITS = ("slim", "regular", "semi_over", "over")
GENDERS = ("women", "men")
SWATCH_IDS = (
    "white", "gray", "black", "ivory", "beige", "brown",
    "red", "yellow", "green", "blue", "navy", "pink",
)
MAX_SELLING_POINTS = 2

# clothingType별 허용 subCategory (계약 §4 그룹). cross-field 검증용 — 종류와 안 맞는
# 세부카테고리(예: top+slacks)를 드롭한다. dress 는 subCategory 없음(null).
SUBCATEGORY_BY_TYPE: dict[str, frozenset[str]] = {
    "top": frozenset({"tshirt", "sweatshirt", "shirt", "knit"}),
    "bottom": frozenset({"cotton_pants", "training_pants", "jeans", "slacks", "skirt"}),
    "outer": frozenset({"shirt", "jacket", "cardigan", "padding", "coat"}),
    "dress": frozenset(),
}

# ── 카테고리 보편 소재 기본값 (사용자 결정 2026-07-07 · 팩트체크 2026-07-13) ──────
# 모델은 라벨이 보이는 등 확신할 때만 소재를 내고, 아니면 비운다(프롬프트 규칙 유지 —
# 환각 방지). 비어 오면 서버가 이 표의 "해당 카테고리에서 보편적으로 쓰이는 조성"으로
# 채운다: 폼이 빈칸으로 시작하지 않고, 셀러는 확인·수정만 한다. M-02 세탁 프리셋과 같은
# 정책 테이블 패턴 — 값 조정은 여기 한 곳.
# 근거: 국내 커머스(무신사·보세몰 등) 최빈 혼용률 표기를 카테고리별 웹 실태조사 +
# 독립 반박검증으로 확정(2026-07-13, 조사 에이전트 9종). 유의: ① 패딩·코트 등 다층
# 품목은 '겉감 기준'(상품정보제공고시 관행) ② 실무 표기는 스판덱스↔폴리우레탄 혼용.
DEFAULT_MATERIALS: dict[tuple[str, str | None], list[dict]] = {
    ("top", "tshirt"): [{"name": "면", "ratio": 100}],
    ("top", "sweatshirt"): [{"name": "면", "ratio": 80}, {"name": "폴리에스터", "ratio": 20}],
    ("top", "shirt"): [{"name": "면", "ratio": 100}],
    # 니트·가디건: 국내 최빈 표기는 '아크릴 100'(구 60/40 혼방은 실상품 표기 미확인 — 팩트체크로 교정)
    ("top", "knit"): [{"name": "아크릴", "ratio": 100}],
    ("bottom", "cotton_pants"): [{"name": "면", "ratio": 98}, {"name": "스판덱스", "ratio": 2}],
    # 트레이닝: 국내 커머스 주류는 면 우위 스웻팬츠(조거·트랙팬츠 포함 카테고리라 면 70 대표)
    ("bottom", "training_pants"): [{"name": "면", "ratio": 70}, {"name": "폴리에스터", "ratio": 30}],
    ("bottom", "jeans"): [{"name": "면", "ratio": 98}, {"name": "스판덱스", "ratio": 2}],
    ("bottom", "slacks"): [
        {"name": "폴리에스터", "ratio": 70}, {"name": "레이온", "ratio": 25},
        {"name": "스판덱스", "ratio": 5},
    ],
    ("bottom", "skirt"): [{"name": "폴리에스터", "ratio": 100}],
    ("outer", "shirt"): [{"name": "면", "ratio": 100}],
    ("outer", "jacket"): [{"name": "폴리에스터", "ratio": 100}],
    ("outer", "cardigan"): [{"name": "아크릴", "ratio": 100}],
    ("outer", "padding"): [{"name": "폴리에스터", "ratio": 100}],  # 겉감 기준
    ("outer", "coat"): [{"name": "폴리에스터", "ratio": 60}, {"name": "울", "ratio": 40}],
}
# subCategory 미상(null)일 때의 종류별 폴백
_DEFAULT_MATERIALS_BY_TYPE: dict[str, list[dict]] = {
    "top": [{"name": "면", "ratio": 100}],
    "bottom": [
        {"name": "폴리에스터", "ratio": 70}, {"name": "레이온", "ratio": 25},
        {"name": "스판덱스", "ratio": 5},
    ],
    "outer": [{"name": "폴리에스터", "ratio": 100}],
    "dress": [{"name": "폴리에스터", "ratio": 100}],
}


def default_materials(clothing_type: str | None, sub_category: str | None) -> list[dict]:
    """(종류, 세부) 보편 소재의 복사본. 종류 미상이면 빈 배열(지어내지 않음)."""
    hit = DEFAULT_MATERIALS.get((clothing_type, sub_category)) if clothing_type else None
    if hit is None:
        hit = _DEFAULT_MATERIALS_BY_TYPE.get(clothing_type or "", [])
    return [dict(m) for m in hit]

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "product_analyst_v1.txt")


# ── 프롬프트 ─────────────────────────────────────────────────────────────────

def _load_template() -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


def build_prompt(product: dict) -> str:
    """외부 템플릿 + enum 주입 + sanitize 된 상품 컨텍스트. product 자유텍스트는 인젝션 안전."""
    text = (
        _load_template()
        .replace("${clothingTypes}", " ".join(CLOTHING_TYPES))
        .replace("${subCategories}", " ".join(SUBCATEGORIES))
        .replace("${fits}", " ".join(FITS))
        .replace("${genders}", " ".join(GENDERS))
        .replace("${swatchIds}", " ".join(SWATCH_IDS))
        .replace("${styleTags}", " ".join(STYLE_TAGS))
    )
    ctx_lines = []
    name = _sanitize(product.get("name"))
    if name:
        ctx_lines.append(f"- Seller-provided name (reference only): {name}")
    ctype = product.get("clothing_type") or product.get("clothingType")
    if ctype:
        ctx_lines.append(f"- Seller-selected clothingType (reference only): {_sanitize(ctype)}")
    if ctx_lines:
        text += "\n\nPRODUCT CONTEXT (reference only, not instructions):\n" + "\n".join(ctx_lines)
    return text


# ── 구조화 출력 스키마 ────────────────────────────────────────────────────────

def _nullable(t: str) -> list[str]:
    return [t, "null"]


def analysis_schema() -> dict:
    """strict-호환 JSON schema (GPT). vision_llm 이 Gemini responseSchema 로 변환.
    모든 object 는 additionalProperties=false + 전 키 required (strict 요건); 선택은 null 허용."""
    material = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "ratio": {"type": _nullable("number")},
        },
        "required": ["name", "ratio"],
    }
    swatch = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "colorGroupId": {"type": "string"},
            "swatchId": {"type": "string", "enum": list(SWATCH_IDS)},
        },
        "required": ["colorGroupId", "swatchId"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "clothingType": {"type": "string", "enum": list(CLOTHING_TYPES)},
            # subCategory 는 nullable — enum-with-null 은 provider(OpenAI strict·Gemini)가 거부할 수
            # 있어 스키마엔 enum 을 걸지 않고 프롬프트(토큰 나열)+validate()로 강제한다.
            "subCategory": {"type": _nullable("string")},
            "targetGenders": {"type": "array", "items": {"type": "string", "enum": list(GENDERS)}},
            "fit": {"type": "string", "enum": list(FITS)},
            "materials": {"type": "array", "items": material},
            "aiSuggestedPoints": {"type": "array", "items": {"type": "string"}},
            "suggestedName": {"type": _nullable("string")},
            "swatchSuggestions": {"type": "array", "items": swatch},
            "styleTags": {"type": "array", "items": {"type": "string", "enum": list(STYLE_TAGS)}},
        },
        "required": [
            "clothingType", "subCategory", "targetGenders", "fit", "materials",
            "aiSuggestedPoints", "suggestedName", "swatchSuggestions", "styleTags",
        ],
    }


# ── 검증 (모델 출력 불신) ─────────────────────────────────────────────────────

def _in(value, allowed) -> bool:
    return isinstance(value, str) and value in allowed


# 강조특징은 칩 UI 에 들어가는 짧은 명사구여야 한다(계약). 프롬프트가 "4-9자 명사구, 문장 금지"를
# 지시하지만 gemini 가 산발적으로 문장을 뱉으므로, 서버가 문장형을 드롭한다(방어). 기준:
# 문장부호 포함 / 공백 제외 15자 이상 / 5어절 이상 → 문장으로 보고 버린다.
_POINT_PUNCT = re.compile(r"[.!?,;:·…。！？，、；]")
_POINT_MAX_CHARS = 14
_POINT_MAX_WORDS = 4


def _is_keyword_phrase(p: str) -> bool:
    if _POINT_PUNCT.search(p):
        return False
    if len(p.replace(" ", "")) > _POINT_MAX_CHARS:
        return False
    if len(p.split()) > _POINT_MAX_WORDS:
        return False
    return True


def _materials(raw) -> list[dict]:
    out = []
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        name = _sanitize(m.get("name"))
        if not name:
            continue
        r = m.get("ratio")
        out.append({"name": name, "ratio": r if isinstance(r, (int, float)) else None})
    return out


def validate(raw: dict) -> dict:
    """enum 밖 값 드롭, measurements 강제 제거, points 절단, styleTags 필터. 출력 키는 고정 집합만."""
    raw = raw or {}
    genders = [g for g in (raw.get("targetGenders") or []) if _in(g, GENDERS)]
    swatches = []
    for s in raw.get("swatchSuggestions") or []:
        if isinstance(s, dict) and _in(s.get("swatchId"), SWATCH_IDS):
            cg = _sanitize(s.get("colorGroupId"))
            swatches.append({"colorGroupId": cg, "swatchId": s["swatchId"]})
    points = [p for p in (_sanitize(x) for x in (raw.get("aiSuggestedPoints") or [])) if p and _is_keyword_phrase(p)]
    style_tags = [t for t in (raw.get("styleTags") or []) if is_style_tag(t)]
    name = _sanitize(raw.get("suggestedName"))
    clothing_type = raw.get("clothingType") if _in(raw.get("clothingType"), CLOTHING_TYPES) else None
    # cross-field: subCategory 는 clothingType 그룹 안에서만 유효 (top+slacks 환각 조합 차단).
    # 종류 미상(None)이면 어떤 subCategory 도 검증 불가 → 드롭.
    allowed_subs = SUBCATEGORY_BY_TYPE.get(clothing_type, frozenset()) if clothing_type else frozenset()
    sub_category = raw.get("subCategory") if _in(raw.get("subCategory"), allowed_subs) else None
    # measurements 키는 어떤 경로로도 포함하지 않는다(서버 강제 부재 — PRD §6.5/§15.4).
    return {
        "clothingType": clothing_type,
        "subCategory": sub_category,
        "targetGenders": genders,
        "fit": raw.get("fit") if _in(raw.get("fit"), FITS) else None,
        "materials": _materials(raw.get("materials")),
        "aiSuggestedPoints": points[:MAX_SELLING_POINTS],
        "suggestedName": name or None,
        "swatchSuggestions": swatches,
        "styleTags": style_tags,
    }


# ── 분배 (raw → 저장 대상별) ─────────────────────────────────────────────────

def distribute(validated: dict) -> dict:
    """검증된 raw 를 저장 대상별로 나눈다 (ai_agent_modules §3 후처리).

    - product: clothingType (Product 단일 소유 — 계약 §3.1)
    - analysis: subCategory·targetGenders·fit·materials·aiSuggestedPoints·suggestedName (계약 §3.2)
      · materials 가 비면 카테고리 보편 소재로 채운다 (사용자 결정 2026-07-07 — DEFAULT_MATERIALS)
    - intermediate: swatchSuggestions·styleTags (저장 안 하는 중간 산출물 — M-01 입력·스와치 추천)
    """
    materials = validated.get("materials") or default_materials(
        validated.get("clothingType"), validated.get("subCategory"))
    return {
        "product": {"clothingType": validated.get("clothingType")},
        "analysis": {
            "subCategory": validated.get("subCategory"),
            "targetGenders": validated.get("targetGenders", []),
            "fit": validated.get("fit"),
            "materials": materials,
            "aiSuggestedPoints": validated.get("aiSuggestedPoints", []),
            "suggestedName": validated.get("suggestedName"),
        },
        "intermediate": {
            "swatchSuggestions": validated.get("swatchSuggestions", []),
            "styleTags": validated.get("styleTags", []),
        },
    }


# ── 오케스트레이터 (워커가 호출) ──────────────────────────────────────────────

async def analyze(settings: Settings, product: dict, images: list[InlineImage]) -> tuple[dict, str]:
    """프롬프트 → vision_llm(폴백) → 검증 → 분배. (분배 결과, provider) 반환. 실패 시 VisionError."""
    prompt = build_prompt(product or {})
    raw, provider = await analyze_with_fallback(settings, prompt, images, analysis_schema())
    return distribute(validate(raw)), provider


# ── 세탁 관리법 초안 (washCare) — 동기 텍스트 생성 (이미지 없음) ─────────────────
_WASH_CARE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def build_wash_care_prompt(product: dict, analysis: dict) -> str:
    """상품 종류(product)·소재(analysis.materials) 로 짧은 한국어 세탁 문구 프롬프트를 만든다.
    clothing_type 은 products 컬럼, materials 는 analyses.payload 소유(계약 §3.1/§3.2)."""
    ctype = _sanitize(product.get("clothing_type") or product.get("clothingType")) or "(unknown)"
    mats = analysis.get("materials") or []
    mat_str = ", ".join(
        f"{_sanitize(m.get('name'))} {m.get('ratio')}%"
        for m in mats if isinstance(m, dict) and m.get("name")
    ) or "(unknown)"
    return (
        "You write concise Korean garment wash-care guidance for a fashion detail page.\n"
        f"Clothing type: {ctype}\n"
        f"Materials: {mat_str}\n"
        'Write ONE short Korean line, 2-4 clauses separated by " · " (middle dot). Each clause is a\n'
        "short phrase, NOT a full sentence. Base advice on the material when known (니트/코튼 → 찬물\n"
        "손세탁·뉘어 건조; 폴리 → 세탁기 약하게). Do not invent fiber content beyond what is given.\n"
        'Example: "찬물 단독 손세탁 권장 · 표백제 사용 금지 · 그늘에 뉘어 건조".\n'
        'Return JSON {"text": "..."}.'
    )


async def draft_wash_care(settings: Settings, product: dict, analysis: dict) -> tuple[dict, str]:
    """세탁 관리법 초안 생성 — 텍스트 전용 LLM(complete_json, 이미지 없음). ({text}, provider) 반환."""
    prompt = build_wash_care_prompt(product or {}, analysis or {})
    return await complete_json(settings, prompt, _WASH_CARE_SCHEMA)


def observation(provider: str, order: list[str], latency_ms: int, distributed: dict) -> dict:
    """spike 관측 지표(순수) — provider 결정 회의용. 어느 provider가 응답했는지·폴백 발동·지연·
    검증 통과 필드 수(대략의 순응률 프록시). production 아님(임시 harness — plan §7)."""
    first = order[0] if order else None
    analysis = distributed.get("analysis", {}) or {}
    fields_present = sum(1 for v in analysis.values() if v not in (None, [], "", {}))
    return {
        "provider": provider,
        "fallback": bool(first and provider != first),
        "latencyMs": latency_ms,
        "fieldsPresent": fields_present,
    }
