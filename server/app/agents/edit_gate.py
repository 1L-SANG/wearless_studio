"""편집 패스 사전 게이트 공통 규약.

임계·검증 규칙이 게이트마다 복제돼 있으면 튜닝이 한쪽에만 반영돼 두 게이트의 위험
성향이 조용히 갈라진다. 게이트마다 다른 것은 verdict 집합과 스킵 verdict 뿐이다.

보수 규약: 인식된 결함 verdict가 공유 임계 이상으로 확인된 경우만 편집을
허용한다. 정상, 불확실, 임계 미만, 형식 오류, 게이트 off는 모두 편집을
건너뛴다. 판정기 장애가 자율 이미지 편집 권한으로 변하지 않게 하는 규약이다.
"""

# 자율 편집을 허용하는 최소 결함 확신. 두 게이트가 같은 보수 임계를 쓴다.
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
    """모델 응답을 안전한 shape 로 정규화. 스키마 밖 값은 편집 불가 쪽으로 눕힌다.

    confidence 는 0..1 실수만 신뢰한다. 범위 밖·문자열 숫자는 clamp 하지 않고 0 으로
    눕힌다. 깨진 판정기의 숫자를 잘라 맞춰 편집 권한으로 쓰면 안 된다.
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


def permits(result: dict, defect_verdict: str) -> bool:
    """이 판정이 자율 편집을 허용하는가 (순수)."""
    if not isinstance(result, dict):
        return False
    confidence = result.get("confidence")
    return (
        result.get("verdict") == defect_verdict
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and GATE_SKIP_CONFIDENCE <= confidence <= 1
    )


def skips(result: dict, defect_verdict: str) -> bool:
    """공개 호환 헬퍼. 확정 결함이 아니면 편집을 건너뛴다."""
    return not permits(result, defect_verdict)
