"""AG-P2 image-qc — 생성 이미지 동일성 검수와 best-of-N 선택.

생성 컷이 입력 상품과 "같은 옷인가"(색·패턴·넥라인·디테일)를 판정. retry면 mismatches +
correctionPrompt(재생성 시 보정 지시)를 반환한다(ai_agent_modules §5). vision_llm 재사용.

단일 후보 판정(verdict)과 전 후보 불합격 시 최선 후보 선택(pick_best)을 제공한다.
"""

import os

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback

VERDICTS = ("pass", "retry")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "image_qc_v1.txt")
_PICK_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "garment_pick_v1.txt")


# 4축 점수 (플랜 Phase 2). 이진 verdict 로는 "얼마나 나쁜지"를 몰라 자동통과/사람검수/자동재생성
# 3분기를 못 만든다. series_consistency 는 Phase 3(D축 에이전트)가 채우므로 여기선 항상 null.
SCORE_KEYS = ("product_fidelity", "physical_naturalness", "image_quality", "series_consistency")
_SCORE_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "image_qc_scores_v1.txt")


def qc_schema(*, scored: bool = False) -> dict:
    """동일성 판정 스키마. scored=True 면 4축 점수 + critical_errors 를 얹는다.

    **기본값은 반드시 3필드로 유지한다.** 이 스키마를 `scene_verdict`(장소 일치)와
    `best_of`(상세페이지·에디터 garment QC)가 공유하는데, 발행 공간세트 경로는 scene QC 가
    fail-closed 라(2026-07-30 PR#62) 스키마 오류가 경고 강등이 아니라 **셀러 컷 전멸**로
    이어진다. 점수는 장소 판정에 쓰이지도 않으므로 확장을 마네킹 경로 opt-in 으로 가둔다.
    """
    props = {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "mismatches": {"type": "array", "items": {"type": "string"}},
        "correctionPrompt": {"type": ["string", "null"]},
    }
    if scored:
        # 범위(0-100)는 스키마로 못 건다 — _to_gemini_schema 가 minimum/maximum 을 변환에서
        # 버린다. 프롬프트 명시 + validate() 클램핑으로 강제한다(mannequin_fit_qc 와 같은 관례).
        for key in SCORE_KEYS:
            props[key] = {"type": ["integer", "null"]}
        props["critical_errors"] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        # GPT strict: properties 의 전 키가 required 여야 400 이 안 난다.
        "required": list(props),
        "properties": props,
    }


def build_declared_fit_block(fit_profile: dict | None) -> str:
    """셀러가 **의도적으로 조정한 핏**을 판정기에 알린다.

    이게 없으면 QC 는 상품 사진만 보고 "핏이 바뀌었다"를 치명오류로 붙인다 — 그런데 핏 조정은
    지원되는 기능이고, 생성은 선언 축대로 낸 것이다. 2026-07-31 실측: 셀러가 `fit: slim`·
    `length: crop` 으로 선언한 오버사이즈 티에서 QC 가 매 시도마다 `garment fit changed from
    oversized to tight crop` 을 붙여 **무한 재생성 → 구제 출고(regenerate)** 로 끝났다.
    판정 기준을 사진이 아니라 **의도**로 옮겨야 한다.
    """
    from .fit_axes import AXIS_OBSERVABLES

    if not isinstance(fit_profile, dict):
        return ""
    category = fit_profile.get("category")
    axes = fit_profile.get("axes") if isinstance(fit_profile.get("axes"), dict) else {}
    lines = []
    for axis, value in axes.items():
        observable = AXIS_OBSERVABLES.get((category, axis, value))
        if observable:
            lines.append(f"- {axis}: {observable}")
    if not lines:
        return ""
    return (
        "\nDECLARED FIT (the seller deliberately adjusted how this garment is worn; the "
        "generated photo was asked to follow this, NOT the fit seen in the product photos):\n"
        + "\n".join(lines)
        + "\nJudge fit against this declaration. A difference in fit, ease or hem height that "
        "matches what is declared here is CORRECT and must not be reported as a mismatch or as "
        "a critical error. Everything else — colour, print, logo, structure — is still judged "
        "against the product photos as usual.\n"
    )


def build_prompt(product_count: int, *, scored: bool = False, fit_profile: dict | None = None) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    prompt = template.replace("${productCount}", str(max(1, product_count)))
    if scored:
        # 스키마만 바꾸면 근거 없는 숫자가 나온다 — 채점 기준을 프롬프트로 준다.
        with open(_SCORE_PROMPT_FILE, encoding="utf-8") as f:
            prompt = f"{prompt}\n{f.read()}"
        # 선언 핏은 **scored 경로 전용** — 다른 호출부(scene·best_of)의 요청은 불변이어야 한다.
        prompt += build_declared_fit_block(fit_profile)
    return prompt


def _score(value) -> int | None:
    """0-100 정수로 클램핑. 판독 불가는 None(=신호 없음) — 0(=최악)으로 눕히면 안 된다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(100, int(value)))


def validate(raw: dict, *, scored: bool = False) -> dict:
    """verdict∈enum(밖이면 pass), mismatches 정리, correctionPrompt 정리(retry일 때만 의미).

    scored=True 면 4축 점수(클램핑)와 critical_errors 를 **보존**한다. 기본 경로의 반환
    shape 은 3키 그대로 — scene/best_of 소비처가 키 추가를 전제하지 않는다.
    """
    raw = raw or {}
    verdict = raw.get("verdict") if raw.get("verdict") in VERDICTS else "pass"
    mismatches = [m for m in (clean_text(x, 200) for x in (raw.get("mismatches") or [])) if m]
    correction = clean_text(raw.get("correctionPrompt"), 500) or None
    if verdict == "pass":
        out = {"verdict": "pass", "mismatches": [], "correctionPrompt": None}
    else:
        out = {"verdict": "retry", "mismatches": mismatches, "correctionPrompt": correction}
    if scored:
        out.update({k: _score(raw.get(k)) for k in SCORE_KEYS})
        # 치명 오류는 pass 판정이어도 남긴다 — 점수와 무관하게 재생성을 트리거하는 신호라
        # verdict 에 종속시키면 "pass 인데 로고가 바뀐" 케이스를 놓친다.
        out["critical_errors"] = [
            c for c in (clean_text(x, 200) for x in (raw.get("critical_errors") or [])) if c
        ]
    return out


async def verdict(
    settings: Settings, product_images: list[InlineImage], generated_image: InlineImage,
    *, scored: bool = False, fit_profile: dict | None = None,
) -> dict:
    """상품사진들 + 생성이미지(맨 뒤)를 vision LLM에 넣어 동일성 판정. 실패 시 VisionError.

    scored=True(마네킹 경로)면 4축 점수를 함께 받는다. 다른 호출부(best_of 경유
    상세페이지·에디터)는 기본값 그대로라 요청·응답이 바이트 단위로 불변이다.
    """
    images = [*product_images, generated_image]  # bytes — 마지막이 생성 결과
    prompt = build_prompt(len(product_images), scored=scored, fit_profile=fit_profile)
    raw, _provider = await analyze_with_fallback(
        settings, prompt, images, qc_schema(scored=scored))
    return validate(raw, scored=scored)


def pick_schema(candidate_count: int) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chosenIndex": {
                "type": "integer",
                "minimum": 0,
                "maximum": max(0, candidate_count - 1),
            },
            "reason": {"type": "string"},
        },
        "required": ["chosenIndex", "reason"],
    }


def validate_pick(raw: dict, candidate_count: int) -> dict:
    raw = raw or {}
    chosen = raw.get("chosenIndex")
    if isinstance(chosen, bool) or not isinstance(chosen, int) or not 0 <= chosen < candidate_count:
        chosen = 0
    return {"chosenIndex": chosen, "reason": clean_text(raw.get("reason"), 300)}


async def pick_best(
    settings: Settings,
    product_images: list[InlineImage],
    candidates: list[InlineImage],
) -> dict:
    """상품 원본과 후보들을 비교해 로고·프린트·원장 동일성이 가장 높은 후보를 고른다."""
    with open(_PICK_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    prompt = prompt.replace("${productCount}", str(len(product_images)))
    prompt = prompt.replace("${candidateCount}", str(len(candidates)))
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [*product_images, *candidates], pick_schema(len(candidates)))
    return validate_pick(raw, len(candidates))


def _candidate_qc(index: int, result: dict) -> dict:
    return {
        "index": index,
        "verdict": result["verdict"],
        "mismatches": result["mismatches"][:5],
    }


def _garment_metadata(results: list[dict], chosen_index: int) -> dict:
    chosen = results[chosen_index]
    return {
        "verdict": chosen["verdict"],
        "candidates": [_candidate_qc(i, result) for i, result in enumerate(results)],
        "chosenIndex": chosen_index,
        "mismatches": chosen["mismatches"][:5],
    }


async def best_of(
    settings: Settings,
    product_images: list[InlineImage],
    initial: InlineImage,
    generate_candidate,
) -> tuple[InlineImage, dict | None, list[dict]]:
    """최초 생성본을 판정하고 필요할 때 원본 입력 기반 후보 중 최선을 채택한다."""
    mode = settings.garment_qc_mode
    if mode == "off":
        return initial, None, []
    if not product_images:
        return initial, None, [{"code": "garment_qc_product_reference_unavailable"}]

    try:
        first_result = await verdict(settings, product_images, initial)
    except VisionError:
        return initial, None, [{"code": "garment_qc_unavailable"}]

    candidates = [initial]
    results = [first_result]
    if mode == "shadow" or first_result["verdict"] == "pass":
        return initial, _garment_metadata(results, 0), []

    warnings: list[dict] = []
    for _ in range(max(0, settings.garment_qc_extra_candidates)):
        try:
            candidate = await generate_candidate()
        except Exception:
            warnings.append({"code": "garment_qc_candidate_generation_failed"})
            break
        try:
            candidate_result = await verdict(settings, product_images, candidate)
        except VisionError:
            warnings.append({"code": "garment_qc_unavailable"})
            break
        candidates.append(candidate)
        results.append(candidate_result)
        if candidate_result["verdict"] == "pass":
            chosen = len(candidates) - 1
            return candidate, _garment_metadata(results, chosen), warnings

    try:
        picked = await pick_best(settings, product_images, candidates)
        chosen = picked["chosenIndex"]
    except VisionError:
        chosen = 0
        warnings.append({"code": "garment_qc_picker_unavailable"})
    return candidates[chosen], _garment_metadata(results, chosen), warnings


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
