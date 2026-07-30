"""T3 골드셋 — 의류종류별 **구조 견고성** 심판 (blinded structural QC).

목적: 축 조정(T2)이 아니라, out-of-box 초기생성이 **종류별로 구조적으로 온전한가**를 잰다.
실패 taxonomy: 종류 오인식 · 하반신 누락 · 비율 깨짐 · garment 정체성 훼손.

codex 2라운드 반영:
- **블라인드 종류 판정**: 기대 종류를 프롬프트에 숨긴다 → judge 는 "보이는 종류(typeSeen)"만 자유
  기술, 정답 비교는 외부 `classify_type`(순수, judge 밖)이 기계적으로 한다(확증편향 차단).
- **length 축 없음**: garment-only 상품컷은 worn-length 기준(Fit 슬롯)이 없어 착장 기장 식별 불가.
  gross 기장오류(코트→크롭 등)는 종류불일치(type_misrecognition)로 잡힌다.
- **강제 boolean 폐기**: 각 축은 관찰 가능할 때만 판정, 아니면 `notVisible`(fail 아님 = unjudgeable).
- **identity 분리**: garmentFidelity(옷 vs source) ↔ mannequinBasePreserved(베이스 마네킹) 별도.
- **provider 고정**: `_call_gemini` 직접(폴백 순서 무시 — 평가자 재현성). 캘리브 게이트 통과 후에만 스코어 사용.

순수 함수(_FAMILY_KEYWORDS·_family_of·classify_type·aggregate·schema·validate·build_prompt_blind)는
DB·네트워크 없음 → 유닛 테스트 대상.
"""

import re

from .gemini_image import InlineImage
from .vision_llm import VisionError, _call_gemini

# ── 종류 family (구조 견고성 관점의 최상위 묶음) ──────────────────────────────
# 기대 family 는 arm 정의가 준다("top"/"pants"/"skirt"/"dress"/"outer").
# judge 의 자유기술 typeSeen 을 아래 키워드로 family 에 매핑해 기계 비교한다.
# 순서 주의: outer→dress→skirt→pants→top (겹치는 토큰은 더 구체적인 쪽이 이김,
#   예: "denim jacket" 은 jacket(outer)이 denim(pants)을 이겨야 함).
_FAMILY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("outer", ("coat", "jacket", "blazer", "parka", "padding", "puffer", "trench",
               "overcoat", "windbreaker", "cardigan", "outerwear", "outer")),
    ("dress", ("dress", "gown", "one-piece", "onepiece", "one piece")),
    ("skirt", ("skirt",)),
    ("pants", ("pants", "pant", "trousers", "trouser", "jeans", "jean", "denim",
               "slacks", "slack", "chinos", "chino", "leggings", "legging", "shorts",
               "joggers", "jogger", "sweatpants", "sweatpant", "bottoms", "bottom")),
    ("top", ("t-shirt", "tshirt", "tee", "shirts", "shirt", "top", "knit", "sweater",
             "jumper", "hoodie", "hood", "sweatshirt", "blouse", "polo", "henley",
             "turtleneck", "crewneck", "pullover")),
]

_CORE_AXES: dict[str, tuple[str, str, str]] = {
    # key: (ok_state, fail_state, failure_mode)
    "lowerBody": ("present", "cropped", "missing_lower_body"),
    "proportions": ("ok", "distorted", "broken_proportions"),
    "garmentFidelity": ("preserved", "altered", "garment_identity_altered"),
}


def _family_of(type_seen: str) -> str | None:
    """자유기술 종류 → family. 미지/공백이면 None(=판정불가)."""
    t = (type_seen or "").strip().lower()
    if not t:
        return None
    for family, kws in _FAMILY_KEYWORDS:
        if any(re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", t) for kw in kws):
            return family
    return None


def classify_type(type_seen: str, expected_family: str) -> dict:
    """블라인드 typeSeen 을 기대 family 와 **기계 비교**(judge 밖, 순수). judge 편향 차단.
    match: True/False, 또는 None(family 미분류 = 판정불가). seenFamily: 매핑 결과."""
    fam = _family_of(type_seen)
    if fam is None:
        return {"match": None, "seenFamily": None}
    return {"match": fam == expected_family, "seenFamily": fam}


def aggregate(verdict: dict, expected_family: str) -> dict:
    """judge verdict + 기대 family → 구조 판정(순수).
    overallPass 3-state: True(전 축 확인 통과) / False(구조 실패) / None(핵심축 판정불가).
    notVisible 는 fail 이 아니라 unjudgeable(codex P1-6). base 마네킹 훼손은 advisory(overall 미게이팅)."""
    modes: list[str] = []
    unjudge: list[str] = []
    any_fail = False
    any_unjudge = False

    cls = classify_type(verdict.get("typeSeen", ""), expected_family)
    if cls["match"] is None:
        unjudge.append("type"); any_unjudge = True
    elif cls["match"] is False:
        modes.append("type_misrecognition"); any_fail = True

    for key, (ok_v, fail_v, mode) in _CORE_AXES.items():
        st = (verdict.get(key) or {}).get("state")
        if st == fail_v:
            modes.append(mode); any_fail = True
        elif st == ok_v:
            continue
        else:  # notVisible 또는 미지 상태 → unjudgeable(fail 아님)
            unjudge.append(key); any_unjudge = True

    # base 마네킹 보존 — garment fidelity 와 분리(advisory, overall 미게이팅)
    base_st = (verdict.get("mannequinBasePreserved") or {}).get("state")
    if base_st == "altered":
        modes.append("base_mannequin_altered")

    if any_fail:
        overall = False
    elif any_unjudge:
        overall = None
    else:
        overall = True
    return {"overallPass": overall, "failureModes": modes,
            "unjudgeable": unjudge, "typeSeenFamily": cls["seenFamily"]}


def _axis_schema(states: list[str]) -> dict:
    return {"type": "object",
            "properties": {"state": {"type": "string", "enum": states},
                           "landmark": {"type": "string"}},
            "required": ["state", "landmark"]}


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "typeSeen": {"type": "string"},
            "lowerBody": _axis_schema(["present", "cropped", "notVisible"]),
            "proportions": _axis_schema(["ok", "distorted", "notVisible"]),
            "garmentFidelity": _axis_schema(["preserved", "altered", "notVisible"]),
            "mannequinBasePreserved": _axis_schema(["preserved", "altered", "notVisible"]),
            "notes": {"type": "string"},
        },
        "required": ["typeSeen", "lowerBody", "proportions",
                     "garmentFidelity", "mannequinBasePreserved", "notes"],
    }


_AXIS_STATES = {
    "lowerBody": {"present", "cropped", "notVisible"},
    "proportions": {"ok", "distorted", "notVisible"},
    "garmentFidelity": {"preserved", "altered", "notVisible"},
    "mannequinBasePreserved": {"preserved", "altered", "notVisible"},
}


def validate(raw: dict) -> dict:
    """모델 출력 불신 — 축 state 가 enum 밖이면 notVisible 로 강등(임의 boolean 조작 방지)."""
    if not isinstance(raw, dict):
        raise VisionError(f"structure_qc: dict 아님 {type(raw)}")
    out: dict = {"typeSeen": str(raw.get("typeSeen") or "")[:120]}
    for key, allowed in _AXIS_STATES.items():
        node = raw.get(key) if isinstance(raw.get(key), dict) else {}
        st = node.get("state")
        if st not in allowed:
            st = "notVisible"
        out[key] = {"state": st, "landmark": str(node.get("landmark") or "")[:200]}
    out["notes"] = str(raw.get("notes") or "")[:300]
    return out


def build_prompt_blind() -> str:
    """구조 QC 프롬프트 — **기대 종류 미노출**(블라인드). 관찰 근거만, 안 보이면 notVisible."""
    return (
        "You are a strict visual QA judge for an e-commerce garment-on-mannequin generation.\n"
        "Images in order: first the SOURCE product photo(s) of a garment (garment only — may be flat, "
        "ghost, or laid out), then the BASE mannequin (the blank canvas), and the LAST image is the "
        "GENERATED result (the garment dressed on the mannequin).\n"
        "Step 1 — WITHOUT any hint or assumption, state in `typeSeen` what garment category the GENERATED "
        "mannequin appears to be wearing, using a plain noun phrase (e.g. \"t-shirt\", \"knit sweater\", "
        "\"hoodie\", \"shirt\", \"jeans\", \"slacks\", \"skirt\", \"dress\", \"coat\", \"jacket\").\n"
        "Step 2 — judge ONLY visible evidence on the GENERATED image. If a required landmark is cropped, "
        "hidden, or ambiguous, answer state = \"notVisible\" — never guess.\n"
        "- lowerBody: are the mannequin's legs AND feet fully within the frame (present) or cut off / "
        "cropped away (cropped)? The mannequin is barefoot; never require footwear.\n"
        "- proportions: are human body proportions plausible (ok) or distorted / warped / anatomically "
        "broken (distorted)? cite the landmark (head-to-body ratio, limb continuity, shoulder width).\n"
        "- garmentFidelity: does the GENERATED garment preserve the SOURCE garment's color, pattern, "
        "fabric appearance, construction, neckline, and trims (preserved), or is it materially different "
        "(altered)?\n"
        "- mannequinBasePreserved: is the BASE mannequin's identity kept (preserved) or replaced / "
        "distorted into a different figure (altered)?\n"
        "Return JSON only, matching the schema exactly. Describe each landmark factually; do not reward "
        "photorealism when a landmark is missed."
    )


def _sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    return "image/png"


async def judge(
    settings, gen_bytes: bytes, src_bytes: list[bytes], base_bytes: bytes, *,
    model: str | None = None, timeout: float = 60.0,
) -> dict:
    """SOURCE(s) + BASE + GENERATED(마지막)를 Gemini(고정)로 구조 판정 → validate 된 verdict.
    provider 고정: analysis_model_order 무시하고 Gemini 만 호출(평가자 재현성, codex P1)."""
    images = [InlineImage(_sniff_mime(b), b) for b in src_bytes]
    images.append(InlineImage(_sniff_mime(base_bytes), base_bytes))
    images.append(InlineImage(_sniff_mime(gen_bytes), gen_bytes))
    model = model or settings.model_text_gemini
    raw = await _call_gemini(settings, model, build_prompt_blind(), images,
                             schema(), timeout, thinking_level="low")
    return validate(raw)
