"""편집 패스 사전 게이트 공통 규약 — untuck·bust 게이트가 공유한다 (2026-08-19 리뷰).

임계·검증 규칙이 게이트마다 복제돼 있으면 튜닝이 한쪽에만 반영돼 두 게이트의 위험
성향이 조용히 갈라진다. 게이트마다 다른 것은 verdict 집합과 스킵 verdict 뿐이다.

비대칭 규약: 스킵은 "확신에 찬 <스킵 verdict>" 하나뿐이고, 나머지 verdict·판정 실패·
게이트 off 는 전부 기존 동작(편집 실행)으로 떨어진다. 하방은 단 하나 — 판정기가
**자신 있게 틀린** 스킵(실제로는 tuck 인데 untucked 0.9)이며, 그 위험은 보수적 프롬프트
("의심되면 unclear — 비용 0")와 아래 임계로 관리한다(오너 승인 2026-08-19). 반대 방향
(과잉 편집)은 오늘과 동일해서 게이트가 무력해질 뿐 사고가 나지 않는다.
"""

# 스킵을 허용하는 최소 확신 — 보수 초기값. 게이트별 이벤트 필드(untuck_gate·bust_gate)의
# 스킵률을 보고 조정하며, 두 게이트가 **같은 값**을 쓰는 것이 이 모듈의 존재 이유다.
GATE_SKIP_CONFIDENCE = 0.85


def schema(verdicts: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": sorted(verdicts)},
            "confidence": {"type": "number"},
        },
        "required": ["verdict", "confidence"],
    }


def validate(raw: dict | None, verdicts: tuple[str, ...]) -> dict:
    """모델 응답을 안전한 shape 로 정규화 — 스키마 밖 값은 전부 '편집 실행' 쪽으로.

    confidence 는 0..1 실수만 신뢰한다. 범위 밖·문자열 숫자는 clamp 하지 않고 0 으로
    눕힌다 — 깨진 판정기의 숫자를 잘라 맞춰서 스킵 근거로 쓰면 안 된다.
    """
    raw = raw if isinstance(raw, dict) else {}
    verdict = raw.get("verdict")
    if verdict not in verdicts:
        verdict = "unclear"
    c = raw.get("confidence")
    confidence = (
        float(c)
        if isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1
        else 0.0)
    return {"verdict": verdict, "confidence": confidence}


def skips(result: dict, skip_verdict: str) -> bool:
    """이 판정으로 편집을 건너뛰어도 되는가 (순수)."""
    return (result.get("verdict") == skip_verdict
            and result.get("confidence", 0.0) >= GATE_SKIP_CONFIDENCE)
