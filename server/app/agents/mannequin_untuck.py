"""untuck 2패스 — 상의 밑단을 하의 허리밴드 밖으로 빼는 전용 편집.

왜 전용 패스인가(2026-08-01, 시도-실패 5회의 결론):
  1. 생성 프롬프트 "COMPLETELY OUTSIDE / no French tuck" → 재발
  2. 부분 tuck 금지 명문화(1d70338) → 재발
  3. 레이어 순서 + 허리밴드 tell(8f05d5d) → 재발
  4. QC 치명오류 + enforce 재생성 → 3회 연속 tuck → 구제 출고(job 2b6a2e35)
  5. QC 검출 자체가 불안정 — 같은 유형의 부분 tuck 을 잡기도(02:05) 놓치기도(04:57) 한다
생성-시점 예방과 검출-후 재생성이 둘 다 소진됐다. 남은 구조가 편집 분리다 — 이미지 1장·
과제 1개일 때 모델이 반영한다는 성질은 가슴·원단 2패스에서 검증됐고, bust v3 안에 untuck 이
**부차 지시**로 있을 때는 실패했다(02:05: bust applied 후에도 tuck 잔존). 단일 과제가 변수다.

실행 조건이 QC 검출에 **의존하지 않는 이유**: 5번 항목. 검출이 불안정한 신호를 게이트로 쓰면
놓친 컷이 그대로 출고된다. 매칭 하의가 붙는 잡마다 항상 1회 돈다(사용자 승인, 2026-08-01) —
이미 빠져 있으면 프롬프트가 무변경 반환을 지시하므로 no-op 호출이다.

실행 위치는 일반 retry 가 끝난 **저장 직전 전용 post-pass** 다(2026-08-12). 편집 체인 맨 앞
공유 예산 시절에는 attempt 를 소진한 잡이 budget_exhausted 로 스킵돼 "항상 1회" 계약이
깨졌다(프로덕션 실측 2건 연속 tuck 출고). 지금은 일반 예산 2회와 무관한 전용 슬롯 1회다.
"""

import os

from .vision_llm import analyze_with_fallback

# 하의 위로 입는 주상품만 대상 — 하의 상품이면 매칭이 상의라 tuck 방향 자체가 다르다(WS4).
_TUCKABLE = {"top", "outer"}

_GATE_PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts", "untuck_gate_v1.txt")

# 사전 게이트(2026-08-19 오너 승인) — 편집 콜(이미지, 40~60초·$0.14) 전에 값싼 판정 콜
# (vision, 3~5초·~$0.01)로 "이미 빠져 있나"를 묻는다. 위 주석 5항(검출 불안정)과 충돌하지
# 않는 이유 = **비대칭**: 불안정이 사고를 냈던 방향은 "tuck 을 놓쳐 교정을 안 하는" 쪽인데,
# 그 방향으로는 게이트에 권한이 없다. 스킵은 오직 확신에 찬 untucked 뿐이고 tucked/unclear/
# 판정실패/게이트 off 는 전부 기존 동작(무조건 편집)으로 떨어진다. 게이트가 완전히 틀려도
# 최악이 "오늘 상태"라는 뜻이다. 임계 0.85 는 보수 초기값 — untuck_pass 이벤트의
# untuck_gate 필드(스킵률·오탐)를 보고 조정한다.
GATE_SKIP_CONFIDENCE = 0.85

_GATE_VERDICTS = {"tucked", "untucked", "unclear"}


def gate_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": sorted(_GATE_VERDICTS)},
            "confidence": {"type": "number"},
        },
        "required": ["verdict", "confidence"],
    }


def validate_gate(raw: dict | None) -> dict:
    """모델 응답을 안전한 shape 로 정규화한다 — 스키마 밖 값은 전부 '편집 실행' 쪽으로.

    confidence 는 0..1 실수만 신뢰한다. 범위 밖·문자열 숫자는 clamp 하지 않고 0 으로
    눕힌다 — 깨진 판정기의 숫자를 잘라 맞춰서 스킵 근거로 쓰면 안 된다.
    """
    raw = raw if isinstance(raw, dict) else {}
    verdict = raw.get("verdict")
    if verdict not in _GATE_VERDICTS:
        verdict = "unclear"
    c = raw.get("confidence")
    confidence = (
        float(c)
        if isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1
        else 0.0)
    return {"verdict": verdict, "confidence": confidence}


def gate_skips(result: dict) -> bool:
    """이 판정으로 편집을 건너뛰어도 되는가 (순수). 확신에 찬 untucked 만 True."""
    return (result.get("verdict") == "untucked"
            and result.get("confidence", 0.0) >= GATE_SKIP_CONFIDENCE)


async def judge_gate(settings, cut_image) -> dict:
    """생성본 1장만 보고 밑단이 이미 빠져 있는지 판정한다. 실패는 호출자가 잡아 편집 실행.

    입력이 1장인 건 편집 패스와 같은 원칙(과제 1개) — 상품·매칭 사진은 이 질문에 필요
    없고 섞으면 판정 대상이 흐려진다. 전용 모델 설정(mannequin_untuck_gate_model)이 있으면
    gemini 오버라이드로 전달, 없으면 정본 텍스트 모델 그대로(AG-08 features 와 같은 패턴).
    """
    with open(_GATE_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    model = getattr(settings, "mannequin_untuck_gate_model", "") or ""
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [cut_image], gate_schema(),
        models={"gemini": model} if model else None)
    return validate_gate(raw)


def should_apply(mode: str, clothing_type: str | None, has_match_image: bool) -> bool:
    """untuck 패스를 돌릴지. 플래그 on + 주상품이 top/outer + 매칭 하의 이미지가 붙었을 때만.

    매칭 이미지가 없으면 하의가 화면에 없어 tuck 이 성립하지 않는다 — 호출 낭비.
    dress 는 제외: 원피스는 매칭 하의가 붙지 않고(2026-08-01 matching 제거), 붙었다 해도
    밑단을 빼는 과제가 성립하지 않는다.
    """
    if mode != "on":
        return False
    if str(clothing_type or "").lower() not in _TUCKABLE:
        return False
    return bool(has_match_image)


def build_prompt(template: str) -> str:
    """untuck 템플릿. 치환 토큰 없음 — 과제가 상품과 무관하게 동일하다.
    토큰이 남아 있으면 즉시 실패(다른 패스와 같은 규약)."""
    if "${" in template:
        leftover = sorted({p.split("}")[0] + "}" for p in template.split("${")[1:]})
        raise ValueError(f"untuck 프롬프트 템플릿에 해결되지 않은 토큰: {leftover}")
    return template
