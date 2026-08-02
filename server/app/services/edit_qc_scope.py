"""Edit QC 호출 규약 한 벌 — 워커와 수집기가 **같은 함수**를 쓴다 (Phase 3 P0-C 9/N).

9/N 수집에서 배운 것: 수집기가 운영 워커와 "거의 같게" 부르면 그 데이터는 못 쓴다.
실제로 수집기는 Vision 에 `{requestedTypes, allowedObservations, forbiddenObservations}`
를 넘겼고 프롬프트는 `{allowed, forbidden}` 을 읽었다. 그래서 허용/금지 목록이
통째로 빈 채로 30건이 수집됐다 — 크래시도 경고도 없이.

두 곳이 각자 dict 를 조립하는 한 같은 사고가 또 난다. 그래서 변환을 여기 한 곳에
두고, 양쪽이 이 함수만 부르게 한다. QC 정책 버전도 여기서 찍는다 — 판정이 바뀌면
이전 수집분과 섞으면 안 되기 때문이다.
"""

from __future__ import annotations

# 판정 규칙이 바뀌면 올린다. 수집 표본에 박혀 나가서 "어느 정책으로 잰 값인가"를 남긴다.
QC_POLICY_VERSION = "edit_qc_v1"

# 워크플로 상태(edit_sessions.status)와 기계 판정(edit_qc_result.decision)은 다른 것이다.
# 전자는 "잡이 어디까지 갔나", 후자는 "결과가 어땠나". 집계에서 섞으면 provider 실패가
# reject 로 둔갑한다.
_DECISION_MAP = {
    "pass": "pass",
    "review": "review_required",
    "review_required": "review_required",
    "reject": "reject",
    "failed": "failed",
}


def vision_scope(semantic_scope: dict | None) -> dict:
    """Vision 프롬프트가 읽는 DTO. 키 이름이 계약이다."""
    s = semantic_scope or {}
    return {"allowed": list(s.get("allowedObservations") or ()),
            "forbidden": list(s.get("forbiddenObservations") or ())}


def qc_allowed_scope() -> dict:
    """정량 판정의 allowed_scope.

    editor vary 는 정량 축을 이 dict 로 열지 않는다 — 허용 범위는 semantic_scope 와
    entailed metrics 가 정한다. 워커가 쓰는 값과 같아야 하므로 상수로 고정한다.
    """
    return {"allowed": [], "forbidden": []}


def normalize_decision(raw, *, had_output: bool = True) -> str:
    """기계 판정 정규화 → pass | review_required | reject | failed.

    모르는 값은 통과가 아니라 review 다. 결과 이미지가 없으면(=provider/세션 실패)
    판정 자체가 성립하지 않으므로 failed 다.
    """
    if not had_output:
        return "failed"
    key = str(raw or "").strip().lower()
    return _DECISION_MAP.get(key, "review_required")


def machine_decision(qc_result, *, had_output: bool = True) -> str:
    """edit_qc_result 에서 기계 판정을 꺼낸다 — status 를 대신 쓰지 않는다."""
    if not isinstance(qc_result, dict):
        return "failed" if not had_output else "review_required"
    if qc_result.get("error") and not qc_result.get("decision"):
        return "failed"
    return normalize_decision(qc_result.get("decision"), had_output=had_output)
