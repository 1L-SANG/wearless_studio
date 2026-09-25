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
  · virtual — **studio·styling 섹션 밖의 모든 블록**(2026-09-14 결정, 09-25 styling 추가). 얼굴 합성이 검증된 건
    studio 섹션뿐이라, 나머지까지 그리면 gpt-image 비용만 나간다. 푸는 방법은
    REAL_ALLOWED_SECTION_ROLES 에 섹션을 더하는 것 하나뿐이다.
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

#: 실제 모델로 만들 수 있는 섹션. **여기 한 줄이 정책의 전부다** — 검증이 끝난 섹션을 더하면 풀린다.
#: studio 는 얼굴 합성을 실측으로 확인한 곳(2026-09-14 사용자 결정). styling 은 2026-09-25 사용자
#: 결정으로 연다 — 호리존만으로는 상세페이지가 모자라다. 옆·뒤(각도 교체)는 단색 배경 기준으로
#: 만든 경로라 스타일링 배경에서는 실패가 더 날 수 있다(실패는 컷 단위로 닫히고 미차감).
REAL_ALLOWED_SECTION_ROLES = ("studio", "styling")
#: 그 규칙에 걸렸을 때 셀러가 보는 것. 인프라 단어(파드·라이선스·LoRA)는 쓰지 않는다.
STUDIO_ONLY_CODE = "real_model_studio_only"
STUDIO_ONLY_MESSAGE = "실제 모델은 현재 스튜디오·스타일링 컷만 만들 수 있어요."
#: 그 밖의 이유(가상 전용 예시·공간세트)로 막힌 경우
MISMATCH_CODE = "identity_scope_mismatch"
MISMATCH_MESSAGE = "이 모델로는 만들 수 없는 컷이에요."

#: 선택된 모델 종류 → 그 모델로 만들 수 있는 범위
_ALLOWED: dict[str, frozenset[str]] = {
    REAL: frozenset({REAL, BOTH}),
    VIRTUAL: frozenset({VIRTUAL, BOTH}),
}


def section_of(block: Mapping[str, Any] | None) -> str | None:
    """이 블록이 속한 섹션. 옛 블록은 contentRole·cutType 에서 추론된다(content_roles 규칙)."""
    from . import content_roles

    return content_roles.resolve_section_role(dict(block or {}))


def studio_only_block(block: Mapping[str, Any] | None) -> bool:
    """실제 모델로는 못 만드는 섹션인가. 섹션을 못 알아내면 **막지 않는다**(both 로 둔다)."""
    section = section_of(block)
    return section is not None and section not in REAL_ALLOWED_SECTION_ROLES


def scope_for_block(block: Mapping[str, Any] | None) -> str:
    """이 콘티 블록(=컷 스펙)의 범위. 모르는 값은 전부 both 로 떨어진다."""
    if not isinstance(block, Mapping):
        return BOTH
    if studio_only_block(block):
        return VIRTUAL
    return shape_scope_for_block(block)


def shape_scope_for_block(block: Mapping[str, Any] | None) -> str:
    """**섹션 규칙을 빼고** 컷 모양만 본 범위.

    공간세트 표(gen_identity_scopes)가 이걸 쓴다 — 세트의 가상 전용 여부는 세트 고유의
    이유(REAL 미검증)여야지, 그 멤버가 어느 섹션에 놓이느냐로 정해지면 안 된다.
    섹션 규칙은 블록마다 따로 걸린다.
    """
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


def block_rejection(block: Mapping[str, Any] | None,
                    model_id: str | None) -> tuple[str, str] | None:
    """막혔으면 (코드, 셀러 문구). 만들 수 있으면 None.

    이유를 갈라 두는 값어치: "스튜디오 컷만 된다" 와 "이 예시는 가상 전용이다" 는 셀러가
    할 수 있는 일이 다르다(섹션을 옮긴다 vs 예시를 바꾼다).
    """
    if block_allowed(block, model_id):
        return None
    if identity_kind(model_id) == REAL and studio_only_block(block):
        return STUDIO_ONLY_CODE, STUDIO_ONLY_MESSAGE
    return MISMATCH_CODE, MISMATCH_MESSAGE
