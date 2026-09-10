"""토스 빌링 클라이언트 — HTTP 계약 회귀.

여기서 지키는 것: ① 승인 실패의 '확정 거절'과 '결과 미상'을 구분한다(미상을 실패로
굳히면 돈만 받고 크레딧을 안 주는 상태가 생긴다) ② 시크릿·빌링키가 예외 메시지로
새지 않는다 ③ 빌링 전용 타임아웃(60초)을 쓴다.
"""

import asyncio

import httpx
import pytest

import app.toss_billing as tb
from conftest import make_settings

SETTINGS = make_settings(toss_billing_secret_key="test_sk_billing",
                         toss_api_base="https://toss.test", toss_billing_timeout=60.0)


@pytest.fixture()
def stub(monkeypatch):
    """_transport_for 를 MockTransport 로 갈아끼운다(네트워크 없이 계약만 검증)."""

    def install(handler):
        monkeypatch.setattr(tb, "_transport_for", lambda s: httpx.MockTransport(handler))

    return install


def test_issue_billing_key_returns_key_and_card_display(stub):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "billingKey": "bk-1", "cardCompany": "현대", "cardNumber": "43301234****1234",
            "card": {"number": "43301234****1234"},
        })

    stub(handler)
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak-1", customer_key="cus-1"))
    assert out["billingKey"] == "bk-1"
    assert out["label"] == "현대"
    assert out["last4"] == "1234"
    assert seen["url"] == "https://toss.test/v1/billing/authorizations/issue"
    assert seen["auth"].startswith("Basic ")


def test_issue_derives_brand_from_issuer_code(stub):
    """API 2024-06-01 자동결제 응답에는 cardCompany 가 없다(문서 명시 + 2026-09-10 실측).
    대체 필드 card.issuerCode 를 카드사명으로 바꿔야 화면에 '현대 ····701*' 로 뜬다."""
    stub(lambda request: httpx.Response(200, json={
        "billingKey": "bk-2", "method": "카드",
        "card": {"issuerCode": "61", "number": "49061000****701*", "cardType": "체크"},
    }))
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak", customer_key="cus"))
    assert out["billingKey"] == "bk-2"
    assert out["label"] == "현대"
    assert out["last4"] == "701*"


def test_issue_prefers_legacy_card_company_when_present(stub):
    """옛 API 버전 상점은 cardCompany 를 그대로 준다 — 그게 더 정확하니 우선한다."""
    stub(lambda request: httpx.Response(200, json={
        "billingKey": "bk-3", "cardCompany": "신한",
        "card": {"issuerCode": "61", "number": "1234****5678"},
    }))
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak", customer_key="cus"))
    assert out["label"] == "신한"


def test_issue_leaves_brand_empty_for_unknown_issuer(stub):
    """모르는 코드는 추측하지 않는다 — 엉뚱한 카드사를 보여주느니 이름을 비운다."""
    stub(lambda request: httpx.Response(200, json={
        "billingKey": "bk-4", "card": {"issuerCode": "ZZ", "number": "1234****9999"},
    }))
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak", customer_key="cus"))
    assert out["label"] is None
    assert out["last4"] == "9999"


def test_issue_reads_bank_for_quick_transfer(stub):
    """퀵계좌이체(method='TRANSFER')는 card 대신 transfers[] 로 온다 — 은행명과 계좌
    뒤 4자리를 같은 모양(label·last4)으로 정규화해야 화면이 분기 없이 그린다."""
    stub(lambda request: httpx.Response(200, json={
        "billingKey": "bk-t", "method": "계좌이체", "card": None,
        "transfers": [{"bankName": "토스", "bankCode": "92",
                       "bankAccountNumber": "123*******789"}],
    }))
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak", customer_key="cus"))
    assert out["method"] == "TRANSFER"
    assert out["label"] == "토스"
    assert out["last4"] == "*789"


def test_issue_transfer_without_account_detail_still_registers(stub):
    """토스 문서: 등록 시점에 계좌 정보를 주지 않는 경우가 있다. 그래도 빌링키는 살아야 한다."""
    stub(lambda request: httpx.Response(200, json={
        "billingKey": "bk-t2", "method": "계좌이체", "transfers": [{}],
    }))
    out = asyncio.run(tb.issue_billing_key(SETTINGS, auth_key="ak", customer_key="cus"))
    assert out["method"] == "TRANSFER"
    assert out["label"] is None
    assert out["last4"] is None


def test_charge_posts_to_billing_key_path_with_idempotency_key(stub):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["idem"] = request.headers.get("idempotency-key")
        return httpx.Response(200, json={"status": "DONE", "totalAmount": 79900,
                                         "paymentKey": "pk-1", "method": "카드"})

    stub(handler)
    out = asyncio.run(tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                                order_id="wl-sub-abc123", order_name="Seller 구독",
                                amount=79900))
    assert out["status"] == "DONE"
    assert seen["url"] == "https://toss.test/v1/billing/bk-1"
    assert seen["idem"] == "wl-sub-abc123"


def test_card_rejection_is_not_retryable(stub):
    stub(lambda request: httpx.Response(400, json={"code": "REJECT_CARD_COMPANY",
                                                   "message": "한도초과"}))
    with pytest.raises(tb.TossBillingError) as e:
        asyncio.run(tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                              order_id="wl-sub-abc123", order_name="x", amount=100))
    assert e.value.code == "REJECT_CARD_COMPANY"
    assert e.value.retryable is False


def test_gateway_5xx_is_retryable(stub):
    stub(lambda request: httpx.Response(500, json={"code": "INTERNAL", "message": "x"}))
    with pytest.raises(tb.TossBillingError) as e:
        asyncio.run(tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                              order_id="wl-sub-abc123", order_name="x", amount=100))
    assert e.value.retryable is True


def test_transport_failure_is_retryable(stub):
    def boom(request):
        raise httpx.ConnectError("down")

    stub(boom)
    with pytest.raises(tb.TossBillingError) as e:
        asyncio.run(tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                              order_id="wl-sub-abc123", order_name="x", amount=100))
    assert e.value.retryable is True


def test_unexpected_success_shape_is_not_approved(stub):
    """200 인데 금액이 어긋난다 — 적립하면 안 된다. 재시도해도 같으므로 확정 실패."""
    stub(lambda request: httpx.Response(200, json={"status": "DONE", "totalAmount": 1}))
    with pytest.raises(tb.TossBillingError) as e:
        asyncio.run(tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                              order_id="wl-sub-abc123", order_name="x", amount=100))
    assert e.value.code == "payment_not_approved"
    assert e.value.retryable is False


def test_secret_and_billing_key_never_appear_in_error(stub):
    stub(lambda request: httpx.Response(403, json={"code": "UNAUTHORIZED_KEY",
                                                   "message": "bad key"}))
    with pytest.raises(tb.TossBillingError) as e:
        asyncio.run(tb.charge(SETTINGS, billing_key="bk-SECRET", customer_key="cus-1",
                              order_id="wl-sub-abc123", order_name="x", amount=100))
    blob = f"{e.value.code} {e.value.message} {e.value!r}"
    assert "test_sk_billing" not in blob
    assert "bk-SECRET" not in blob


def test_billing_secret_falls_back_to_general_secret():
    assert tb.billing_secret(
        make_settings(toss_secret_key="general", toss_billing_secret_key=None)) == "general"
    assert tb.billing_secret(
        make_settings(toss_secret_key="general", toss_billing_secret_key="billing")) == "billing"


def test_billing_timeout_defaults_to_sixty_seconds():
    """토스 문서: 자동결제 승인은 최대 60초. 15초짜리 일반결제 값을 쓰면 안 된다."""
    assert make_settings().toss_billing_timeout == 60.0
