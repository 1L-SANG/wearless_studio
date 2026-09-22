"""지급 제공자 — 정산금을 모델 계좌로 실제로 보내는 수단.

오늘 프로덕션은 `manual` 이다: 관리자가 확인서를 만들고 은행 앱으로 직접 보낸 뒤 결과를
기록한다. 이 모듈은 그 자리에 **다른 수단을 끼울 틈**을 만든다.

왜 지금 실물(토스페이먼츠 지급대행 Payouts v2)을 안 붙이나 — 붙일 수 없어서다:
  · 계약 + 강화된 리스크 검토(KYC)가 선행이고,
  · **테스트 환경에서는 개인 셀러를 등록할 수 없다**(법인사업자만 가능). 우리 모델은 전부
    개인이라 샌드박스로도 흐름을 리허설할 방법이 없다.
그래서 데모용 `stub` 을 둔다. 대신 인터페이스를 실물 모양(셀러 등록 → 지급 요청 → 상태
조회)으로 잡아, 계약이 끝나면 이 파일에 구현 하나만 더하면 되게 한다 — 데모 때 만든
상태머신·화면·테스트는 그대로 산다.

🔴 스텁은 **돈을 옮기지 않는다.** 스텁으로 처리한 건은 화면·알림 어디에서도 "입금됐다"고
   말하면 안 된다(`simulated` 를 보고 반드시 표시할 것). 모델에게 가는 알림은 실제 이체가
   붙기 전까지 만들지 않는다 — 목이 거짓말이 되는 지점이 정확히 거기다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

#: 스텁이 흉내 내는 실패들. 실물 지급대행도 이 둘은 **요청 시점에** 거절한다 —
#: 그래서 송금이 시작되기 전에 끝나고, 확인서는 prepared → cancelled 로만 움직인다
#: (started_at 이 찍힌 뒤에는 실패를 표현할 자리가 스키마에 없다: 20260911170000 의 CHECK).
STUB_FAILURE_OUTCOMES = ("account_error", "limit_exceeded")

_FAILURE_MESSAGES = {
    "account_error": "계좌 정보가 맞지 않아 지급이 거절됐어요.",
    "limit_exceeded": "지급 한도를 넘어 거절됐어요.",
}


class ProviderUnsupported(Exception):
    """이 제공자가 하지 않는 동작. 호출부가 409 로 바꾼다."""


@dataclass(frozen=True)
class PayoutResult:
    paid: bool
    reference: str | None = None
    failure_reason: str | None = None


class ManualProvider:
    """사람이 은행 앱으로 보낸다. 서버는 기록만 한다 — 프로덕션 기본값."""

    name = "manual"
    simulated = False

    def execute(self, *, amount: int, outcome: str) -> PayoutResult:
        raise ProviderUnsupported("수동 지급은 관리자가 직접 송금하고 결과를 기록해요.")


class StubProvider:
    """데모용. 네트워크도 돈도 없이 결과만 만들어 준다."""

    name = "stub"
    simulated = True

    def execute(self, *, amount: int, outcome: str) -> PayoutResult:
        if outcome == "paid":
            return PayoutResult(paid=True, reference=f"stub-{uuid.uuid4().hex[:16]}")
        if outcome in STUB_FAILURE_OUTCOMES:
            return PayoutResult(paid=False, failure_reason=outcome)
        raise ProviderUnsupported("모르는 지급 결과예요.")


_PROVIDERS = {"manual": ManualProvider(), "stub": StubProvider()}


def resolve_provider(value: str | None):
    """설정값 → 제공자. 모르는 값은 **수동**이다 — 오타 하나로 데모 모드가 켜지면 안 된다."""
    return _PROVIDERS.get((value or "").strip().lower(), _PROVIDERS["manual"])


def failure_message(reason: str | None) -> str:
    return _FAILURE_MESSAGES.get(reason or "", "지급이 거절됐어요.")
