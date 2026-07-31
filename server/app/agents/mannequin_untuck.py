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
"""

# 하의 위로 입는 주상품만 대상 — 하의 상품이면 매칭이 상의라 tuck 방향 자체가 다르다(WS4).
_TUCKABLE = {"top", "outer"}


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
