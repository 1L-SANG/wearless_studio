"""AG-06 상세페이지 인물 일관성 — resolve_effective_model_id 순수 로직 테스트.

버그: 실존 모델을 골랐는데 facemarket off 라 해석 불가(select_source=VIRTUAL 이지만 가상 registry
밖) → 참조 0장 → 컷마다 인물 랜덤. 폴백으로 전 컷 동일 인물 보장하는지 검증."""

from app.agents.cut_generator import resolve_effective_model_id

VIRT = {"mA", "mB", "mC"}


def test_virtual_selection_honored():
    # 진짜 가상모델을 고르면 그대로, 폴백/치환 없음
    assert resolve_effective_model_id("mA", fallback_model_id="mB", virtual_ids=VIRT) == ("mA", False)
    assert resolve_effective_model_id("mC", fallback_model_id="mB", virtual_ids=VIRT) == ("mC", False)


def test_real_uuid_dropped_falls_back_and_warns():
    # 실존 UUID(가상 밖) → 폴백 + substituted=True(경고 대상) = 버그의 핵심 케이스
    eff, sub = resolve_effective_model_id(
        "d2e74c66-1ea9-4113-a260-eafec115f36f", fallback_model_id="mB", virtual_ids=VIRT)
    assert eff == "mB" and sub is True


def test_none_selection_falls_back_silently():
    # 미선택 → 일관성 위해 폴백하되 경고 없음(치환할 선택이 없었음)
    assert resolve_effective_model_id(None, fallback_model_id="mB", virtual_ids=VIRT) == ("mB", False)


def test_empty_fallback_keeps_existing_behavior():
    # 폴백 비활성(빈 문자열) → 기존 동작 유지(치환 안 함)
    assert resolve_effective_model_id("real-uuid", fallback_model_id="", virtual_ids=VIRT) == ("real-uuid", False)
    assert resolve_effective_model_id(None, fallback_model_id="", virtual_ids=VIRT) == (None, False)


def test_invalid_fallback_id_no_substitution():
    # 폴백 id 가 registry 밖이면 폴백 불가 → 기존 동작
    assert resolve_effective_model_id("real-uuid", fallback_model_id="mZ", virtual_ids=VIRT) == ("real-uuid", False)
