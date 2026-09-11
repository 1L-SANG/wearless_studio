"""컷을 어떤 모델로 만들 수 있는가 — **판정은 여기 한 곳**.

콘티보드에서 실제(REAL)/가상(VIRTUAL) 모델의 컷을 갈라 두기 위한 분류다. 실제 모델에
가상 전용 컷을 억지로 일반 패킷으로 만들지 않는다(2026-09-11 사용자 결정) — 그런 조합은
애초에 콘티에 들어가지 않게 막고, 이미 들어와 있으면 생성 전에 건너뛴다.

초기 분류(근거):
  · virtual — 확정 GPT 프로필을 요구하는 예시. 그 근거는 **가상 모델의 확정 시트**라서
    실제 등록자에게는 존재할 수 없다(confirmed_gpt_runtime.resolve_profile_request 가
    fail-closed 로 컷을 버린다).
  · virtual — 스튜디오(horizon) 공간세트 전부. hatchingroom_2161 세트가 REAL 0/9,
    VIRTUAL 3/3 이었다(2026-09-11 실측). 나머지 스튜디오 세트는 REAL 미검증이라 함께 막는다.
  · real    — 아직 없다. 구조만 지원한다(콘티보드에 실제 모델 전용 입력이 생기면 채운다).
  · both    — 그 밖의 전부.

프런트는 이 규칙을 **다시 구현하지 않는다**. scripts/gen_identity_scopes.py 가 이 모듈로
src/data/identityScopes.json 을 만들고, 어긋나면 test_identity_scope.py 가 깨진다.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import confirmed_gpt_runtime

VIRTUAL = "virtual"
REAL = "real"
BOTH = "both"
SCOPES = (VIRTUAL, REAL, BOTH)

#: 선택된 모델 종류 → 그 모델로 만들 수 있는 범위
_ALLOWED: dict[str, frozenset[str]] = {
    REAL: frozenset({REAL, BOTH}),
    VIRTUAL: frozenset({VIRTUAL, BOTH}),
}


def scope_for_block(block: Mapping[str, Any] | None) -> str:
    """이 콘티 블록(=컷 스펙)의 범위. 모르는 값은 전부 both 로 떨어진다."""
    if not isinstance(block, Mapping):
        return BOTH
    try:
        if confirmed_gpt_runtime.profile_requested(dict(block)):
            return VIRTUAL
    except Exception:  # noqa: BLE001 — 레지스트리 오류는 "확정 경로"로 본다(fail-closed 유지)
        return VIRTUAL
    if str(block.get("cutType") or "") == "horizon" and (
        block.get("spaceGroupId") or block.get("space_group_id")
        or block.get("spaceSetId") or block.get("setType") == "horizon"
    ):
        return VIRTUAL
    return BOTH


def allows(scope: str | None, identity_kind: str | None) -> bool:
    """이 범위의 컷을 그 모델 종류로 만들 수 있는가. 모르는 종류면 막지 않는다."""
    allowed = _ALLOWED.get(str(identity_kind or ""))
    if allowed is None:
        return True
    return str(scope or BOTH) in allowed


def identity_kind(model_id: str | None) -> str:
    """선택된 모델 id → 'real' | 'virtual'. FaceMarket 모델(uuid)만 real 이다."""
    from .. import facemarket

    return REAL if facemarket.is_real_model_id(model_id) else VIRTUAL


def block_allowed(block: Mapping[str, Any] | None, model_id: str | None) -> bool:
    return allows(scope_for_block(block), identity_kind(model_id))
