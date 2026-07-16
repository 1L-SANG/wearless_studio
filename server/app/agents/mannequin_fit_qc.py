"""P1 축 인지 QC — 생성 컷이 선언된 핏 축을 실제로 반영했는지 판정 + 편집 교정 정책 (순수 코어).

fidelity 캠페인(§G)·편집 재시도 스파이크(§H) 실증 기반. 선언 축(profile.axes)만 판정하고
(카탈로그 허용목록 — 셀러 자유텍스트·판정문 생성 텍스트는 프롬프트에 절대 미주입),
실패 시 워커가 "실패 이미지 1장 + 결정적 편집 지시"의 편집 호출 1회로 교정한다(retry-as-edit).
오케스트레이션(발화·예산·fail-open)은 workers/mannequin_job.py 소관.
"""

import os

from ..config import Settings
from .fit_axes import AXIS_OBSERVABLES, FIT_AXES
from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_fit_qc_v1.txt")

# 스파이크 §H 3/3 회복을 만든 불변조항 원문 — 편집 지시는 항상 이 꼬리로 끝난다.
EDIT_TAIL = (
    " Change NOTHING else — keep the same garment color, fabric, buttons, lapels and details, "
    "the same mannequin, pose, camera framing, plain background, and bare feet. "
    "Output ONE photorealistic image."
)

# (category, axis) → 편집 문장 템플릿. {observable}은 AXIS_OBSERVABLES 고정 문구만 —
# 판정기가 쓴 문장·셀러 텍스트는 절대 넣지 않는다(인젝션·비결정 방지).
_EDIT_TEMPLATES = {
    ("top", "fit"): "Re-tailor only the top's fit in this photo until {observable}.",
    ("outer", "fit"): ("Re-tailor only the outerwear fit in this photo: reposition the shoulder "
                       "seams and add or remove body and sleeve ease until {observable}."),
    ("top", "length"): ("Re-tailor only the top's body length in this photo until {observable}; "
                        "keep the entire hem untucked and visible."),
    ("outer", "length"): ("Proportionally re-tailor only the outerwear's front and back body-panel "
                          "length until {observable}; keep the entire hem untucked and unobscured "
                          "by any matching bottom."),
    ("pants", "cut"): ("Re-cut only the pants leg shape in this photo, reshaping both legs from "
                       "hip to hem until {observable}."),
    ("pants", "length"): ("Change only both pants-leg lengths in this photo, moving both hems "
                          "until {observable}; keep the mannequin barefoot and never add footwear."),
    ("skirt", "length"): ("Change only the skirt length in this photo, moving the entire hem "
                          "until {observable}."),
    ("dress", "length"): ("Change only the dress length in this photo, moving the entire hem "
                          "until {observable}."),
    ("skirt", "silhouette"): ("Re-cut only the skirt silhouette in this photo, reshaping its side "
                              "outline from waist and hip to hem until {observable}."),
    ("dress", "silhouette"): ("Re-cut only the dress silhouette in this photo, reshaping its outer "
                              "outline and waist-to-hem volume until {observable}."),
}


def declared_axis_spec(profile: dict | None) -> list[dict]:
    """정규화 프로필의 선언 축만 카탈로그 순서로 → [{category,axis,value,observableTarget}].

    matchCut(최상위 키)·미선언 축은 제외. 관측 문구가 없는 (category,axis,value)는
    허용목록 밖이므로 버린다 — 판정 프롬프트에는 여기서 나온 고정 문구만 들어간다.
    """
    if not isinstance(profile, dict):
        return []
    category = profile.get("category")
    axes = profile.get("axes")
    if category not in FIT_AXES or not isinstance(axes, dict):
        return []
    spec = []
    for axis in FIT_AXES[category]:  # 카탈로그 축 순서
        value = axes.get(axis)
        if not isinstance(value, str):
            continue
        obs = AXIS_OBSERVABLES.get((category, axis, value))
        if obs:
            spec.append({"category": category, "axis": axis, "value": value,
                         "observableTarget": obs})
    return spec


def qc_schema(axis_spec: list[dict]) -> dict:
    """캠페인 스키마의 프로덕션 서브셋 — axis/target enum을 선언 축으로 제한.

    Gemini 스키마 변환이 배열 길이 제약을 보존하지 않으므로 정확 커버리지는 validate()가 강제.
    """
    axes = sorted({e["axis"] for e in axis_spec})
    targets = sorted({e["value"] for e in axis_spec})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["identityPass", "axisPass", "mismatches"],
        "properties": {
            "identityPass": {"type": "boolean"},
            "axisPass": {"type": "array", "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["axis", "target", "pass", "observedLandmark", "visible"],
                "properties": {
                    "axis": {"type": "string", "enum": axes},
                    "target": {"type": "string", "enum": targets},
                    "pass": {"type": "boolean"},
                    "observedLandmark": {"type": "string"},
                    "visible": {"type": "boolean"},
                },
            }},
            "mismatches": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_prompt(product_count: int, has_match_image: bool, axis_spec: list[dict]) -> str:
    import json

    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    n = max(1, product_count)
    order = f"{n} SOURCE PRODUCT photo(s)"
    if has_match_image:
        order += ", then one MATCHING BOTTOM photo"
    order += ", and finally ONE GENERATED image (always the LAST image)"
    match_rule = (" If a matching bottom photo is provided, the generated image must contain it "
                  "and preserve its visible identity." if has_match_image else "")
    payload = json.dumps(
        [{"axis": e["axis"], "target": e["value"], "observableTarget": e["observableTarget"]}
         for e in axis_spec],
        ensure_ascii=False)
    return (template.replace("${imageOrder}", order)
            .replace("${matchingBottomRule}", match_rule)
            .replace("${axisSpec}", payload))


def validate(raw: dict, axis_spec: list[dict]) -> dict:
    """(axis,target) 쌍의 정확·유일 커버리지 강제. 누락/여분/중복/스왑/비불리언 → VisionError.

    fail-open 처리는 워커 소관 — 여기서는 계약 위반을 시끄럽게 올린다.
    """
    raw = raw or {}
    expected = {(e["axis"], e["value"]) for e in axis_spec}
    items = raw.get("axisPass")
    if not isinstance(items, list):
        raise VisionError("axis_qc: axisPass 배열 아님")
    seen = set()
    cleaned = []
    for it in items:
        if not isinstance(it, dict):
            raise VisionError("axis_qc: axisPass 항목이 객체 아님")
        pair = (it.get("axis"), it.get("target"))
        if pair not in expected:
            raise VisionError(f"axis_qc: 선언 밖 축 결과 {pair}")
        if pair in seen:
            raise VisionError(f"axis_qc: 중복 축 결과 {pair}")
        if not isinstance(it.get("pass"), bool) or not isinstance(it.get("visible"), bool):
            raise VisionError("axis_qc: pass/visible 비불리언")
        seen.add(pair)
        cleaned.append({
            "axis": pair[0], "target": pair[1],
            "pass": it["pass"], "visible": it["visible"],
            "observedLandmark": clean_text(it.get("observedLandmark"), 300),
        })
    if seen != expected:
        raise VisionError(f"axis_qc: 축 커버리지 불일치 (기대 {sorted(expected)}, 실제 {sorted(seen)})")
    if not isinstance(raw.get("identityPass"), bool):
        raise VisionError("axis_qc: identityPass 비불리언")
    mismatches = [m for m in (clean_text(x, 300) for x in (raw.get("mismatches") or [])) if m][:10]
    return {"identityPass": raw["identityPass"], "axisPass": cleaned, "mismatches": mismatches}


async def verdict(
    settings: Settings,
    product_images: list[InlineImage],
    generated_image: InlineImage,
    fit_profile: dict,
    match_image: InlineImage | None = None,
) -> dict:
    """소스 상품(+매칭 하의) 대비 생성 이미지의 선언 축 반영을 판정. 실패는 VisionError 전파.

    베이스 마네킹은 첨부하지 않는다(캠페인 검증 구성) — 포즈/배경 보존은 프롬프트 불변조항.
    """
    axis_spec = declared_axis_spec(fit_profile)
    if not axis_spec:
        raise VisionError("axis_qc: 선언 축 없음")
    images = [*product_images] + ([match_image] if match_image else []) + [generated_image]
    prompt = build_prompt(len(product_images), match_image is not None, axis_spec)
    raw, _provider = await analyze_with_fallback(settings, prompt, images, qc_schema(axis_spec))
    return validate(raw, axis_spec)


def failed_axis_specs(axis_spec: list[dict], verdict: dict) -> list[dict]:
    """pass && visible 이 아닌 선언 축 spec 항목들 (카탈로그 순서 유지)."""
    ok = {(x["axis"], x["target"]) for x in verdict.get("axisPass", [])
          if x.get("pass") and x.get("visible")}
    return [e for e in axis_spec if (e["axis"], e["value"]) not in ok]


def build_edit_instruction(failed_specs: list[dict]) -> str:
    """실패 축 → 고정 템플릿 문장(카탈로그 순서) + 스파이크 불변 꼬리 1회."""
    sentences = []
    for e in failed_specs:
        tpl = _EDIT_TEMPLATES.get((e["category"], e["axis"]))
        if tpl is None:  # 전 축 커버가 계약 — 테스트가 강제하지만 방어적으로 일반 문장
            tpl = "Re-tailor only the garment's {axis} in this photo until {observable}."
            sentences.append(tpl.format(axis=e["axis"], observable=e["observableTarget"]))
            continue
        sentences.append(tpl.format(observable=e["observableTarget"]))
    return " ".join(sentences) + EDIT_TAIL


def edit_improves(original_verdict: dict, edited_verdict: dict) -> bool:
    """편집본 채택 조건: 정체성 유지 + 선언 축 전부(이전 통과 축 포함) pass && visible."""
    if not edited_verdict.get("identityPass"):
        return False
    axis_pass = edited_verdict.get("axisPass", [])
    return bool(axis_pass) and all(x.get("pass") and x.get("visible") for x in axis_pass)
