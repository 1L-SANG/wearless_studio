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

현재 정책은 확정 tuck 판정이 공유 임계 이상일 때만 편집을 허용한다. 정상, 불확실,
판정 실패, 게이트 off는 자율 편집을 만들지 않는다.

실행 위치는 일반 retry 가 끝난 **저장 직전 전용 post-pass** 다(2026-08-12). 편집 체인 맨 앞
공유 예산 시절에는 attempt 를 소진한 잡이 budget_exhausted 로 스킵돼 "항상 1회" 계약이
깨졌다(프로덕션 실측 2건 연속 tuck 출고). 지금은 일반 예산 2회와 무관한 전용 슬롯 1회다.
"""

from . import edit_gate
from .prompts import load_untuck_gate_prompt_template
from .vision_llm import analyze_with_fallback

# 하의 위로 입는 주상품만 대상 — 하의 상품이면 매칭이 상의라 tuck 방향 자체가 다르다(WS4).
_TUCKABLE = {"top", "outer"}

# 사전 게이트는 확정 tucked 판정이 공유 임계 이상일 때만 이미지 편집을 허용한다.
GATE_SKIP_CONFIDENCE = edit_gate.GATE_SKIP_CONFIDENCE

_GATE_VERDICTS = ("tucked", "untucked", "unclear")


def gate_schema() -> dict:
    return edit_gate.schema(_GATE_VERDICTS)


def validate_gate(raw: dict | None) -> dict:
    return edit_gate.validate(raw, _GATE_VERDICTS)


def gate_skips(result: dict) -> bool:
    """확정 tuck이 아니면 편집을 건너뛰다."""
    return edit_gate.skips(result, "tucked")


async def judge_gate(settings, cut_image) -> dict:
    """생성본 1장만 보고 tuck 여부를 판정한다. 실패는 편집 권한이 아니다.

    입력이 1장인 건 편집 패스와 같은 원칙(과제 1개) — 상품·매칭 사진은 이 질문에 필요
    없고 섞으면 판정 대상이 흐려진다. 전용 모델 설정(mannequin_untuck_gate_model)이 있으면
    gemini 오버라이드로 전달, 없으면 정본 텍스트 모델 그대로(AG-08 features 와 같은 패턴).
    """
    prompt = load_untuck_gate_prompt_template()
    model = getattr(settings, "mannequin_untuck_gate_model", "") or ""
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [cut_image], gate_schema(),
        models={"gemini": model} if model else None,
        require_complete_envelope=True)
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
