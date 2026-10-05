"""상세페이지 전체를 한 번에 보는 한국어 소제목 생성기.

시그니처 사진을 제외한 AI 사진마다 ``headline`` 하나를 만든다. 모델 출력은 코드 규칙으로
먼저 검사하며, 위반하거나 누락된 항목만 한 번 더 생성한다. 두 번째 결과도 부적합하면 그
항목은 비워 상세페이지 생성을 막지 않는다.
"""

import logging
import os
import re

from ..config import Settings
from .content_roles import resolve_content_role
from .fit_axes import confirmed_copy_fit
from .prompts import _sanitize, clean_text
from .vision_llm import complete_json

log = logging.getLogger(__name__)

MAX_HEADLINE_CHARS = 20

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "copywriter_v2.txt")

_ROLE_LABELS = {
    "hero": "핵심 장점",
    "benefit": "핵심 장점",
    "coordination": "코디",
    "fit": "핏",
    "realWear": "코디",
    "productOverview": "제품 확인",
    "detail": "디테일",
    "custom": "제품 확인",
}
_CUT_LABELS = {
    "styling": "착용 코디 사진",
    "horizon": "스튜디오 착용 사진",
    "mirror": "일상 착용 사진",
    "product": "제품 단독 사진",
}
_DIRECTION_LABELS = {"front": "앞모습", "side": "옆모습", "back": "뒷모습"}
_CLOTHING_LABELS = {"top": "상의", "bottom": "하의", "outer": "아우터", "dress": "원피스"}
_FIT_LABELS = {
    "slim": "슬림핏",
    "regular": "레귤러핏",
    "semi_over": "세미 오버핏",
    "over": "오버핏",
}
_SWATCH_LABELS = {
    "white": "화이트", "gray": "그레이", "black": "블랙", "ivory": "아이보리",
    "beige": "베이지", "brown": "브라운", "purple": "퍼플", "red": "레드",
    "yellow": "옐로우", "green": "그린", "blue": "블루", "navy": "네이비",
    "pink": "핑크",
}

_BANNED_WORDS = (
    "최고", "최초", "유일", "1위", "완벽", "당신", "되어지",
    "방수", "방풍", "보온", "항균", "구김 없음", "보풀 없음",
    "체형 교정", "다이어트", "날씬해",
)
_INTERNAL_WORDS = ("호리존", "컷", "블록", "섹션", "시그니처")
_SENTENCE_ENDING = re.compile(
    r"(?:습니다|합니다|됩니다|입니다|해요|어요|아요|네요|예요|이에요|죠|다|요)[.!?]?$"
)
_POSSESSIVE_CHAIN = re.compile(r"(?:\S+의\s+){2,}\S+")
_HUNDRED_PERCENT = re.compile(r"(?<!\d)100\s*%")
_RANKING_CLAIM = re.compile(r"(?<!\d)1\s*위")
_NO_MAINTENANCE_CLAIM = re.compile(
    r"(?:구김|보풀)(?:이|가)?\s*(?:(?:잘\s*)?(?:생기지|나지)\s*않|걱정\s*없|없)"
)
_UV_CLAIM = re.compile(r"(?:UV|자외선)(?:을|를)?\s*차단", re.IGNORECASE)
_FIRST_PERSON_REVIEW = re.compile(r"제가\s*입어\s*보니")


def copy_schema() -> dict:
    """페이지 생성 모델의 strict 호환 출력 스키마."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "blockId": {"type": "string"},
                        "headline": {"type": "string"},
                    },
                    "required": ["blockId", "headline"],
                },
            },
        },
        "required": ["blocks"],
    }


def _is_signature(block: dict) -> bool:
    example_id = block.get("exampleId") or block.get("example_id") or ""
    return isinstance(example_id, str) and example_id.startswith("sig_")


def eligible_blocks(blocks: list[dict]) -> list[dict]:
    """카피 대상 AI 사진만 스토리보드 순서로 반환한다."""
    return [
        block for block in (blocks or [])
        if isinstance(block, dict)
        and block.get("source") == "ai"
        and block.get("id") is not None
        and not _is_signature(block)
    ]


def _color_label(product: dict, color_id) -> str:
    colors = product.get("colors") or []
    selected = next(
        (color for color in colors if str(color.get("id")) == str(color_id)),
        None,
    ) if color_id is not None else None
    if selected is None:
        selected = next((color for color in colors if color.get("isBase")), colors[0] if colors else {})
    return _sanitize(
        selected.get("name") or selected.get("label")
        or _SWATCH_LABELS.get(str(selected.get("swatchId") or ""))
    )


def _matching_labels(analysis: dict, match_ids: list) -> list[str]:
    candidates = analysis.get("matchClothing") or analysis.get("matchCandidates") or []
    by_id = {
        str(item.get("id")): item for item in candidates
        if isinstance(item, dict) and item.get("id") is not None
    }
    labels = []
    for match_id in match_ids or []:
        item = by_id.get(str(match_id))
        if not item:
            continue
        name = _sanitize(item.get("name"))
        color = _sanitize(item.get("colorName") or item.get("color_name"))
        if name:
            labels.append(f"{name} ({color})" if color else name)
    return labels


def _detail_label(analysis: dict, target_id) -> str:
    if not target_id:
        return ""
    recommendations = analysis.get("detailRecommendations") or {}
    for item in recommendations.get("candidates") or []:
        if isinstance(item, dict) and str(item.get("id")) == str(target_id):
            return _sanitize(item.get("label"))
    return ""


def _shot_label(block: dict, clothing_type: str) -> str:
    shot = block.get("shot")
    if shot == "full":
        return "전신"
    if shot == "medium":
        return "하반신" if clothing_type == "bottom" else "상반신"
    if shot == "detail":
        return "부위 클로즈업"
    if shot == "ghost":
        return "제품 전체"
    return "제품 확인"


def _facts_lines(product: dict, analysis: dict) -> list[str]:
    fit = confirmed_copy_fit(analysis)
    clothing_type = product.get("clothing_type") or product.get("clothingType") or ""
    materials = []
    for material in analysis.get("materials") or product.get("materials") or []:
        name = _sanitize(material.get("name")) if isinstance(material, dict) else _sanitize(material)
        if name:
            materials.append(name)
    points = [_sanitize(point) for point in analysis.get("sellingPoints") or [] if _sanitize(point)]
    lines = [
        product.get("name") and f"- 상품명: {_sanitize(product.get('name'))}",
        clothing_type and f"- 종류: {_CLOTHING_LABELS.get(str(clothing_type), '의류')}",
        fit and f"- 핏: {_FIT_LABELS.get(fit, '확인된 핏')}",
        points and f"- 셀러 강조 특징: {'; '.join(points)}",
        materials and f"- 셀러 확인 소재: {', '.join(materials)}",
    ]
    return [line for line in lines if line]


def _block_lines(block: dict, product: dict, analysis: dict) -> list[str]:
    clothing_type = str(product.get("clothing_type") or product.get("clothingType") or "")
    role = resolve_content_role(block)
    shot = _shot_label(block, clothing_type)
    direction = _DIRECTION_LABELS.get(str(block.get("direction") or ""))
    framing = ", ".join(part for part in (shot, direction) if part)
    lines = [
        f"- blockId: {_sanitize(block.get('id'))}",
        f"  역할: {_ROLE_LABELS.get(role, '제품 확인')}",
        f"  사진 종류: {_CUT_LABELS.get(str(block.get('cutType') or ''), '제품 확인 사진')}",
        f"  구도: {framing}",
    ]
    color = _color_label(product, block.get("colorId"))
    matching = _matching_labels(analysis, block.get("matchIds") or block.get("match_ids") or [])
    detail = _detail_label(analysis, block.get("detailTargetId") or block.get("detail_target_id"))
    if color:
        lines.append(f"  색상: {color}")
    if matching:
        lines.append(f"  함께 입는 옷: {', '.join(matching)}")
    if detail:
        lines.append(f"  보이는 부위: {detail}")
    return lines


def build_prompt(
    blocks: list[dict], product: dict, analysis: dict, *,
    target_ids: set[str] | None = None, used_headlines: list[str] | None = None,
) -> str:
    """확인된 값과 사람이 읽는 사진 설명만 v2 프롬프트에 넣는다."""
    with open(_PROMPT_FILE, encoding="utf-8") as prompt_file:
        template = prompt_file.read()
    targets = eligible_blocks(blocks)
    if target_ids is not None:
        targets = [block for block in targets if str(block.get("id")) in target_ids]
    facts = "\n".join(_facts_lines(product or {}, analysis or {})) or "- 확인 정보 없음"
    block_text = "\n".join(
        line
        for block in targets
        for line in _block_lines(block, product or {}, analysis or {})
    )
    prompt = f"{template}\n\n확인된 상품 정보:\n{facts}\n\n작성 대상 사진:\n{block_text}"
    if used_headlines:
        used = "\n".join(f"- {_sanitize(headline)}" for headline in used_headlines)
        prompt += f"\n\n이미 사용한 소제목입니다. 같은 메시지를 피하세요:\n{used}"
    return prompt


def headline_violations(value) -> list[str]:
    """소제목 한 줄의 결정적 규칙 위반을 반환한다."""
    if not isinstance(value, str) or not value.strip():
        return ["빈 값"]
    reasons = []
    if "\n" in value or "\r" in value:
        reasons.append("한 줄")
    text = clean_text(value)
    if len(text) > MAX_HEADLINE_CHARS:
        reasons.append("길이")
    if (
        any(word in text for word in _BANNED_WORDS)
        or _HUNDRED_PERCENT.search(text)
        or _RANKING_CLAIM.search(text)
        or _NO_MAINTENANCE_CLAIM.search(text)
        or _UV_CLAIM.search(text)
    ):
        reasons.append("금지어")
    if any(word in text for word in _INTERNAL_WORDS):
        reasons.append("내부 용어")
    if _FIRST_PERSON_REVIEW.search(text):
        reasons.append("가상 후기")
    if _POSSESSIVE_CHAIN.search(text):
        reasons.append("번역투")
    if _SENTENCE_ENDING.search(text):
        reasons.append("종결")
    return reasons


def _parse_response(raw: dict, expected_ids: list[str]) -> dict[str, str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
        raise ValueError("invalid copy response")
    expected = set(expected_ids)
    parsed = {}
    duplicates = set()
    for item in raw["blocks"]:
        if not isinstance(item, dict):
            continue
        block_id = item.get("blockId")
        headline = item.get("headline")
        if not isinstance(block_id, str) or block_id not in expected or not isinstance(headline, str):
            continue
        if block_id in parsed:
            duplicates.add(block_id)
        else:
            parsed[block_id] = headline
    for block_id in duplicates:
        parsed.pop(block_id, None)
    return parsed


def _message_key(headline: str, selling_points: list) -> str:
    compact = re.sub(r"[^0-9A-Za-z가-힣]", "", headline).lower()
    for point in selling_points or []:
        point_text = _sanitize(point)
        tokens = [token.lower() for token in re.findall(r"[0-9A-Za-z가-힣]+", point_text) if len(token) >= 2]
        if tokens and all(token in compact for token in tokens):
            return "point:" + re.sub(r"[^0-9A-Za-z가-힣]", "", point_text).lower()
    return "copy:" + compact


def validate_headlines(
    parsed: dict[str, str], block_ids: list[str], analysis: dict, *,
    accepted: dict[str, str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    valid = dict(accepted or {})
    counts = {}
    for text in valid.values():
        key = _message_key(text, analysis.get("sellingPoints") or [])
        counts[key] = counts.get(key, 0) + 1
    invalid = set()
    for block_id in block_ids:
        headline = parsed.get(block_id)
        if headline is None or headline_violations(headline):
            invalid.add(block_id)
            continue
        cleaned = clean_text(headline)
        key = _message_key(cleaned, analysis.get("sellingPoints") or [])
        if counts.get(key, 0) >= 2:
            invalid.add(block_id)
            continue
        counts[key] = counts.get(key, 0) + 1
        valid[block_id] = cleaned
    return valid, invalid


def validate_product_name(value) -> str | None:
    """기존 자동 상품명 폴백이 쓰는 짧은 한국어 이름 검사."""
    name = clean_text(value)
    if not name or name == "새 상품" or len(name) > 30 or not re.search(r"[가-힣]", name):
        return None
    return name


async def generate(
    settings: Settings, *, blocks: list[dict], product: dict, analysis: dict,
) -> list[dict]:
    """페이지 1차 생성, 실패 1회 재시도, 위반 항목 1회 재생성을 수행한다."""
    targets = eligible_blocks(blocks)
    block_ids = [str(block["id"]) for block in targets]
    if not block_ids:
        return []

    raw = None
    for attempt in range(2):
        try:
            raw, _provider = await complete_json(
                settings, build_prompt(targets, product, analysis), copy_schema())
            parsed = _parse_response(raw, block_ids)
            break
        except Exception as exc:
            log.warning("page copy generation attempt %d failed: %r", attempt + 1, exc)
    else:
        return []

    valid, invalid = validate_headlines(parsed, block_ids, analysis or {})
    if invalid:
        try:
            repair_raw, _provider = await complete_json(
                settings,
                build_prompt(
                    targets, product, analysis,
                    target_ids=invalid, used_headlines=list(valid.values()),
                ),
                copy_schema(),
            )
            repair = _parse_response(repair_raw, [block_id for block_id in block_ids if block_id in invalid])
            valid, _still_invalid = validate_headlines(
                repair,
                [block_id for block_id in block_ids if block_id in invalid],
                analysis or {},
                accepted=valid,
            )
        except Exception as exc:
            log.warning("page copy repair failed: %r", exc)

    return [
        {"blockId": block_id, "texts": [{"role": "headline", "text": valid[block_id]}]}
        for block_id in block_ids if block_id in valid
    ]
