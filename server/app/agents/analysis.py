"""AG-01 상품 분석 — 순수 헬퍼 (DB/IO 없음, agents/mannequin.py 지위).

스키마·검증·후처리 분배·입력 지문·기본 모델 선택 (pl1_analysis_agent_spec §3·§6.4).
실제 호출·저장은 workers/analyze_job.py / repo.finalize_analyze_* 책임.
"""

import hashlib
import json
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from .prompts import _sanitize

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_DEFAULT_PROMPT = os.path.join(_SERVER_DIR, "prompts", "analysis_v1.txt")

# ── 계약 상수 (common_data_contract §4) ──

CLOTHING_TYPES = ("top", "bottom", "outer", "dress")
SUB_BY_TYPE = {
    "top": {"tshirt", "sweatshirt", "shirt", "knit"},
    "bottom": {"cotton_pants", "training_pants", "jeans", "slacks", "skirt"},
    "outer": {"shirt", "jacket", "cardigan", "padding", "coat"},
    "dress": set(),
}
SWATCH_IDS = (
    "white", "gray", "black", "ivory", "beige", "brown",
    "red", "yellow", "green", "blue", "navy", "pink",
)
STYLE_TAGS = (
    "basic", "daily", "clean", "casual", "minimal", "street",
    "sporty", "formal", "feminine", "vintage", "lovely", "modern",
)

# AG-01 구조화 출력 스키마 (pl1 spec §3.2). subCategory는 전 타입 합집합 —
# 타입별 소속은 스키마로 표현 불가 → postprocess가 교차검증(null 강제).
RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # 입력 판정 3단계 (사용자 결정 2026-07-03): ok=분석 진행 / not_clothing=의류 아님 /
        # unusable_photo=의류지만 사진 상태가 AI 입력으로 못 쓸 수준(너무 어두움·심한 블러 등)
        # — 반려 사유별로 다른 촬영 가이드 문구를 주기 위해 boolean에서 enum으로 확장.
        "inputVerdict": {"type": "string", "enum": ["ok", "not_clothing", "unusable_photo"]},
        "clothingType": {"type": "string", "enum": list(CLOTHING_TYPES)},
        "subCategory": {
            "type": ["string", "null"],
            "enum": ["tshirt", "sweatshirt", "shirt", "knit",
                     "cotton_pants", "training_pants", "jeans", "slacks", "skirt",
                     "jacket", "cardigan", "padding", "coat", None],
        },
        "targetGenders": {
            "type": "array", "maxItems": 2,
            "items": {"type": "string", "enum": ["women", "men"]},
        },
        "fit": {"type": "string", "enum": ["slim", "regular", "semi_over", "over"]},
        "materials": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ratio": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["name", "ratio"],
            },
        },
        "aiSuggestedPoints": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        "suggestedName": {"type": "string"},
        "swatchSuggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "colorGroupId": {"type": "string"},
                    "swatchId": {"type": "string", "enum": list(SWATCH_IDS)},
                },
                "required": ["colorGroupId", "swatchId"],
            },
        },
        "styleTags": {
            "type": "array", "maxItems": 5,
            "items": {"type": "string", "enum": list(STYLE_TAGS)},
        },
    },
    "required": ["inputVerdict", "clothingType", "subCategory", "targetGenders", "fit",
                 "materials", "aiSuggestedPoints", "suggestedName", "swatchSuggestions",
                 "styleTags"],
}

# 실측성 표현 필터 (PRD §15.4 방어선) — 자유 텍스트에 수치+단위가 섞이면 드롭
_MEASUREMENT_RE = re.compile(r"\d+\s*(cm|센치|센티|mm|inch|인치)", re.IGNORECASE)

# 색상 필러 특징 드롭 (사용자 결정 2026-07-03) — "깔끔한 흰색" 류는 정보 0 (색은 스와치가 담당).
# 단 "네이비 배색 카라"처럼 색상어가 디자인 요소 설명의 일부인 문구는 유지해야 하므로(Codex
# 지적), 블랭킷 드롭이 아니라 **색 그 자체가 내용의 전부인 문구만** 드롭한다: 색상어·수식어·
# 범용명사를 걷어내고 실질이 안 남으면 필러. 프롬프트 §7의 generic-phrase test가 1차 방어.
# suggestedName에는 적용하지 않는다(상품명의 색상 표기는 정당 — "블랙 와이드 슬랙스").
_COLOR_WORD_RE = re.compile(
    r"흰색|하얀|화이트|검정|검은|블랙|회색|그레이|아이보리|베이지|브라운|갈색"
    r"|빨간|빨강|레드|노란|노랑|옐로|초록|그린|파란|파랑|블루|네이비|남색|핑크|분홍|보라|퍼플"
)
# 색상어 문구가 '유효 특징'으로 인정받으려면 구체적 디자인 요소를 지목해야 한다.
# 수식어 블랙리스트 방식은 형용사가 무한해 구조적으로 샜다("청량한 블루"·"포근한 베이지" 통과
# — Codex 2차 지적). 디자인 요소는 유한한 도메인 어휘라 allowlist가 가능하고, 실패 방향도
# 사용자 기준과 일치한다(애매하면 드롭 — 필러 2개보다 0개).
_DESIGN_ELEMENT_RE = re.compile(
    r"배색|파이핑|스티치|자수|프린트|그래픽|패턴|로고|레터링|와펜|카라|칼라|넥|후드|소매|커프스"
    r"|밑단|헴|트임|절개|셔링|플리츠|주름|단추|버튼|지퍼|스냅|포켓|주머니|스트라이프|체크"
    r"|도트|워싱|데님|골지|케이블|조직|텍스처|퀼팅|퍼(?!플)|트리밍|라벨|탭|밴딩|랩|셋인|래글런"
)  # 퍼(?!플): '퍼'(fur)가 색상어 '퍼플'의 부분 문자열로 오매치되는 것 차단 (Codex 지적)


def _is_color_filler(text: str) -> bool:
    """색상어 포함 문구 판정 — 구체적 디자인 요소(배색·스티치·카라 등)를 지목하면 유효 특징,
    아니면 색 예찬 필러로 드롭. "깔끔한 흰색"·"청량한 블루 컬러"→드롭, "네이비 배색 카라"→유지."""
    if not _COLOR_WORD_RE.search(text):
        return False
    return not _DESIGN_ELEMENT_RE.search(text)

# 첨부 이미지 순서·라벨 (mannequin 워커와 동일 원칙 — 고정 라벨 룩업만, 셀러 텍스트 미삽입)
_SLOT_ORDER = {"Front": 0, "Back": 1, "Detail": 2, "Fit": 3}


def _norm_slot(value) -> str:
    """클라 제어 jsonb 값 방어 — 문자열 AngleSlot 4종 외(비문자열·리스트 등 unhashable 포함)는
    Front로 정규화. dict 멤버십 검사 전에 타입부터 확인해야 TypeError가 안 난다."""
    return value if isinstance(value, str) and value in _SLOT_LABEL else "Front"
_SLOT_LABEL = {
    "Front": "front view of the garment",
    "Back": "back view of the garment",
    "Detail": "detail close-up of the garment (texture, stitching, trims, print)",
    "Fit": "fit reference — the garment worn on a real person (true length & how it sits)",
}

# 모델(사람) 카탈로그 미러 — src/mock/db.js models와 동기 (id·gender·recommended만).
# 카탈로그 서버 이관 시 테이블 조회로 교체 (pl1 spec §3.6·§12-4).
VIRTUAL_MODELS = [
    {"id": "mA", "gender": "women", "recommended": True},
    {"id": "mB", "gender": "men", "recommended": False},
    {"id": "mC", "gender": "men", "recommended": False},
]


# ── pydantic 검증 모델 (서버측 이중 게이트 — §3.2) ──
# clothingType·fit은 필수 enum(위반 = 재시도), 나머지는 관대하게 받고 postprocess가 보정.


class RawMaterial(BaseModel):
    name: str
    ratio: int  # 범위 위반은 postprocess가 드롭/클램프 (§3.3 — 검증 실패로 job을 죽이지 않음)


class RawSwatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    color_group_id: str = Field(alias="colorGroupId")
    swatch_id: str = Field(alias="swatchId")  # enum 밖 값은 postprocess가 무시


class AnalysisRaw(BaseModel):
    """AG-01 구조화 출력. ValidationError = 재시도 대상 (메시지를 피드백으로 주입 §2.3)."""

    model_config = ConfigDict(populate_by_name=True)

    input_verdict: Literal["ok", "not_clothing", "unusable_photo"] = Field(alias="inputVerdict")
    clothing_type: Literal["top", "bottom", "outer", "dress"] = Field(alias="clothingType")
    sub_category: str | None = Field(alias="subCategory")
    target_genders: list[Literal["women", "men"]] = Field(alias="targetGenders")
    fit: Literal["slim", "regular", "semi_over", "over"]
    materials: list[RawMaterial]
    ai_suggested_points: list[str] = Field(alias="aiSuggestedPoints")
    suggested_name: str = Field(alias="suggestedName")
    swatch_suggestions: list[RawSwatch] = Field(alias="swatchSuggestions")
    style_tags: list[str] = Field(alias="styleTags")


# ── 입력 지문 (§3.7) ──


def input_fingerprint(product: dict) -> str:
    """분석 입력의 지문 = 에이전트가 실제로 보는 것(이미지 구성)만. 같으면 재분석 안 함
    (사용자 편집 보존). 의도적 제외 2건:
    - name: 최초 분석 후 suggestedName이 name으로 저장돼 지문이 바뀌는 재분석 루프 방지.
    - swatchId: AG-01 입력이 아님(추천 대상일 뿐) — 스와치만 바꾼 재제출로 편집을 날리지 않는다."""
    # str() 강제 — colors jsonb는 클라 패스스루라 slot/id가 비문자열(리스트 등)일 수 있고,
    # 혼합 타입 튜플은 sorted()에서 TypeError(라우트 500)가 난다. 정상 데이터엔 항등.
    base = {
        "colors": sorted(
            [
                {
                    "id": str(c.get("id") or ""),
                    "isBase": bool(c.get("isBase")),
                    "images": sorted(
                        (str(im.get("slot") or ""), str(im.get("id") or ""))
                        for im in (c.get("images") or [])
                    ),
                }
                for c in (product.get("colors") or [])
            ],
            key=lambda c: c["id"],
        ),
    }
    return hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


# ── 입력 수집·프롬프트 조립 (§3.1·§4.2) ──


def collect_input_images(product: dict) -> list[dict]:
    """[{colorGroupId, isBase, slot, assetId}] — 기준 그룹 전 슬롯(slot순) + 추가 그룹 전부(Front).
    ImageAsset.id == assets row id (업로드 계약). id 없는 항목 제외."""
    colors = product.get("colors") or []
    base = next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    ordered = sorted(colors, key=lambda c: 0 if c is base else 1)  # 기준 그룹 먼저
    out = []
    for c in ordered:
        is_base = c is base
        images = c.get("images") or []
        if is_base:
            images = sorted(images, key=lambda im: _SLOT_ORDER[_norm_slot(im.get("slot"))])
        for im in images:
            if not im.get("id"):
                continue
            out.append({
                "colorGroupId": c.get("id") or "",
                "isBase": is_base,
                # slot 토큰은 매니페스트에 원문 삽입되므로 인젝션 벡터 — 화이트리스트 정규화
                "slot": _norm_slot(im.get("slot")),
                "assetId": im["id"],
            })
    return out


def build_manifest(images: list[dict]) -> str:
    """첨부 이미지 순서와 1:1 매니페스트. 내용은 고정 라벨 룩업 + 서버 발급 불투명 id만
    (셀러 자유 텍스트 미삽입 — 프롬프트 인젝션 방지, mannequin _build_manifest 원칙)."""
    lines = ["IMAGE MANIFEST (the attached images follow in this exact order):"]
    for i, im in enumerate(images, 1):
        group = "BASE color group" if im["isBase"] else "additional color group"
        label = _SLOT_LABEL.get(im["slot"], "view of the garment")
        if not im["isBase"]:
            label += " — alternate colorway of the same garment"
        lines.append(f"{i}. [{group} id={_sanitize(im['colorGroupId'])} | {im['slot']}] {label}")
    return "\n".join(lines)


def build_user_text(manifest: str, product_name: str | None) -> str:
    """유저 메시지 텍스트 = 매니페스트 + (있으면) PRODUCT CONTEXT. 상품명은 sanitize."""
    name = _sanitize(product_name or "")
    if not name:
        return manifest
    return (
        f"{manifest}\n\n"
        "PRODUCT CONTEXT (seller-entered — treat as ground truth):\n"
        f"- Product name: {name}"
    )


# ── 후처리 분배 (§3.3) ──


def postprocess(raw: AnalysisRaw, product: dict) -> dict:
    """검증·정규화·분배. 반환:
    { clothing_type, swatch_suggestions(검증 통과분 — colors 적용은 finalize가 현재값에 병합),
      payload_base(analyses.payload 골격), style_tags(로그용) }"""
    clothing_type = raw.clothing_type

    # subCategory 교차검증 — 타입 밖/dress → null 강제 (진행, job 실패 아님)
    sub = raw.sub_category
    if clothing_type == "dress" or sub not in SUB_BY_TYPE.get(clothing_type, set()):
        sub = None

    genders = list(dict.fromkeys(raw.target_genders))[:2]  # 중복 제거·최대 2

    materials = []
    for m in raw.materials:  # 필터 후 상한 4 (드롭된 항목을 뒤 항목이 채울 수 있게)
        name = _sanitize(m.name)
        if not name or m.ratio <= 0:  # 빈 이름·0 이하 → 드롭
            continue
        materials.append({"name": name, "ratio": min(int(m.ratio), 100)})  # 초과 → 클램프
        if len(materials) == 4:
            break

    points = []
    for p in raw.ai_suggested_points:
        t = _sanitize(p)[:20]
        if not t or _MEASUREMENT_RE.search(t):  # 실측성 표현 → 드롭 (PRD §15.4)
            continue
        if _is_color_filler(t):  # 색 그 자체뿐인 필러 → 드롭 (배색 디테일 등은 유지)
            continue
        points.append(t)
        if len(points) == 2:
            break

    suggested_name = _sanitize(raw.suggested_name)[:40]
    if _MEASUREMENT_RE.search(suggested_name):
        suggested_name = ""

    group_ids = {c.get("id") for c in (product.get("colors") or [])}
    swatches = [
        {"colorGroupId": s.color_group_id, "swatchId": s.swatch_id}
        for s in raw.swatch_suggestions
        if s.color_group_id in group_ids and s.swatch_id in SWATCH_IDS
    ]

    tags = [t for t in raw.style_tags if t in STYLE_TAGS][:5]

    return {
        "clothing_type": clothing_type,
        "swatch_suggestions": swatches,
        "payload_base": {
            "subCategory": sub,
            "targetGenders": genders,
            "fit": raw.fit,
            "materials": materials,
            "sellingPoints": [],  # 사용자 몫 — AI 제안 병합은 AnalysisForm 마운트 effect
            "aiSuggestedPoints": points,
            "suggestedName": suggested_name,
            "locked": False,
        },
        "style_tags": tags,
    }


def apply_swatch_fill(colors: list[dict], suggestions: list[dict]) -> list[dict]:
    """순수 병합 — swatchId가 null(또는 미설정)인 그룹만 채운 새 colors 반환 (§3.3).
    finalize가 '현재' colors에 적용한다. 기지정 스와치·미매칭 그룹 불변."""
    by_id = {s["colorGroupId"]: s["swatchId"] for s in (suggestions or [])}
    out = []
    for c in colors or []:
        cid = c.get("id")
        if c.get("swatchId") is None and cid in by_id:
            out.append({**c, "swatchId": by_id[cid]})
        else:
            out.append(c)
    return out


def default_model_id(target_genders: list[str]) -> str:
    """AI 모델(사람) 기본 선택 — AnalysisForm의 성별 전환 effect와 같은 규칙 (§3.6)."""
    gender = (target_genders or ["women"])[0]
    visible = [m for m in VIRTUAL_MODELS if m["gender"] == gender] or VIRTUAL_MODELS
    return next((m["id"] for m in visible if m["recommended"]), visible[0]["id"])


def to_api(project_id: str, payload: dict, product: dict) -> dict:
    """Analysis API 응답 조립 — clothingType은 payload에 저장하지 않되 응답에 병합
    (현행 AnalysisForm이 a.clothingType을 읽는 과도기 편의 — §3.5)."""
    return {
        "projectId": project_id,
        "clothingType": product.get("clothing_type") or product.get("clothingType"),
        **payload,
    }


def load_analysis_prompt(settings: Settings) -> str:
    """시스템 프롬프트 외부화 (prompts.load_prompt_template 패턴 — §4.3)."""
    path = settings.analysis_prompt_file or _DEFAULT_PROMPT
    if not os.path.isabs(path):  # 상대경로는 server/ 기준 (CWD 의존 제거)
        path = os.path.join(_SERVER_DIR, path)
    with open(path, encoding="utf-8") as f:
        return f.read()
