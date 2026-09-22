"""지급 제공자 어댑터 — 수동(기본)과 스텁(데모).

실제 지급대행(토스페이먼츠 Payouts v2)은 계약·KYC 가 선행이고, 테스트 환경에서는
**개인 셀러 등록 자체가 불가**(법인사업자만)라 샌드박스로도 흐름을 못 돌린다. 그래서
데모용 스텁을 두되, 인터페이스는 실물 모양(셀러 등록 → 지급 요청 → 상태)으로 잡아
계약이 끝나면 구현만 갈아끼우게 한다.
"""

import pytest

from app.services import payout_provider as pp


def test_manual_is_the_default_and_never_simulates():
    provider = pp.resolve_provider("manual")
    assert provider.name == "manual"
    assert provider.simulated is False
    with pytest.raises(pp.ProviderUnsupported):
        provider.execute(amount=10_000, outcome="paid")


def test_unknown_or_blank_value_falls_back_to_manual():
    # 오타 하나로 데모 모드가 켜지면 안 된다 — 모르는 값은 가장 안전한 쪽으로.
    for value in ("", None, "toss", "STUB!", "simulate"):
        assert pp.resolve_provider(value).name == "manual"


def test_stub_is_opt_in_and_marks_itself_simulated():
    provider = pp.resolve_provider("stub")
    assert provider.name == "stub"
    assert provider.simulated is True


def test_stub_success_returns_a_reference_that_admits_it_is_fake():
    result = pp.resolve_provider("stub").execute(amount=52_150, outcome="paid")
    assert result.paid is True
    assert result.failure_reason is None
    assert result.reference.startswith("stub-")
    assert len(result.reference) > len("stub-")


def test_stub_references_are_unique_per_call():
    stub = pp.resolve_provider("stub")
    refs = {stub.execute(amount=1_000, outcome="paid").reference for _ in range(5)}
    assert len(refs) == 5


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [("account_error", "account_error"), ("limit_exceeded", "limit_exceeded")],
)
def test_stub_can_fail_on_demand(outcome, reason):
    # 데모에서 성공만 보여주면 실패 경로가 있는지 아무도 모른다.
    result = pp.resolve_provider("stub").execute(amount=52_150, outcome=outcome)
    assert result.paid is False
    assert result.failure_reason == reason
    assert result.reference is None


def test_stub_rejects_an_outcome_it_does_not_model():
    with pytest.raises(pp.ProviderUnsupported):
        pp.resolve_provider("stub").execute(amount=1_000, outcome="exploded")


def test_failure_reasons_have_korean_copy_for_every_modeled_outcome():
    for outcome in pp.STUB_FAILURE_OUTCOMES:
        assert pp.failure_message(outcome)
    assert pp.failure_message("unknown-code")  # 모르는 코드도 빈 화면을 만들지 않는다
