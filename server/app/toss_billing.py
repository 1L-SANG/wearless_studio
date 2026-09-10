"""토스페이먼츠 자동결제(빌링) HTTP 클라이언트 — 계획서 §0.3.

이 모듈은 **DB 를 모른다**. 토스와의 HTTP 계약만 담고, 상태 전이는 subscriptions.py 가 한다.
그렇게 나눈 이유: 승인 호출은 재시도 경로에서도(워커) 같은 코드를 써야 하는데, 라우트에
묶어 두면 워커가 라우트를 임포트하게 된다.

**핵심 구분 — retryable**: 4xx 는 토스/카드사의 확정 거절이다(재시도해도 같다).
5xx·전송 실패는 **승인 여부를 모르는 상태**다. 카드가 승인됐는데 응답만 유실됐을 수 있으니
실패로 굳히면 안 된다. 같은 Idempotency-Key 로 재시도하면 토스가 원 결과를 그대로 준다.
"""

import logging

import httpx

from .toss_codes import card_issuer_name

log = logging.getLogger("wearless.toss_billing")

_ISSUE_PATH = "/v1/billing/authorizations/issue"
_OK_STATUS = "DONE"


class TossBillingError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def __repr__(self) -> str:
        # 시크릿·빌링키가 섞일 여지를 원천 차단한다(코드·재시도 여부만 노출).
        return f"TossBillingError(code={self.code!r}, retryable={self.retryable})"


def billing_secret(settings) -> str | None:
    """자동결제는 별도 계약 MID 라 키가 다를 수 있다. 없으면 일반결제 키로 떨어진다."""
    return settings.toss_billing_secret_key or settings.toss_secret_key


def _transport_for(settings):
    """테스트에서 MockTransport 로 갈아끼우는 이음매. 운영에서는 None(기본 전송)."""
    return None


def _client(settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.toss_billing_timeout,
                             transport=_transport_for(settings))


async def _post(settings, path: str, payload: dict, *,
                idempotency_key: str | None = None) -> dict:
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        async with _client(settings) as client:
            res = await client.post(
                f"{settings.toss_api_base}{path}", json=payload, headers=headers,
                auth=httpx.BasicAuth(billing_secret(settings), ""),  # Basic base64("{key}:")
            )
    except httpx.HTTPError as e:
        log.warning("toss billing unreachable path=%s: %s", path, type(e).__name__)
        raise TossBillingError("payment_gateway_unreachable",
                               "결제 확인이 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
                               retryable=True) from None
    if res.status_code != 200:
        try:
            body = res.json()
        except Exception:
            body = {}
        code = str(body.get("code") or "billing_request_failed")
        message = str(body.get("message") or "결제 요청에 실패했어요.")
        log.warning("toss billing rejected path=%s status=%s code=%s",
                    path, res.status_code, code)
        # 5xx = 결과 미상 → 재시도 가능. 4xx = 확정 거절.
        raise TossBillingError(code, message, retryable=res.status_code >= 500)
    return res.json()


async def issue_billing_key(settings, *, auth_key: str, customer_key: str) -> dict:
    """authKey → 빌링키. **응답을 저장하지 못하면 빌링키는 영구 분실이다**(조회 API 없음).

    카드와 퀵계좌이체가 같은 엔드포인트를 쓰고 응답만 갈린다:
      카드 → `card.issuerCode` · `card.number`
      계좌 → `transfers[].bankName` · `transfers[].bankAccountNumber`
    화면에 필요한 건 양쪽 다 '이름 + 뒤 4자리' 하나라 여기서 그 모양으로 정규화한다.
    """
    body = await _post(settings, _ISSUE_PATH,
                       {"authKey": auth_key, "customerKey": customer_key})
    key = body.get("billingKey")
    if not key:
        raise TossBillingError("billing_key_missing", "빌링키를 받지 못했어요.", retryable=False)

    transfers = body.get("transfers") or []
    if transfers:
        # 퀵계좌이체. 등록 시점에 계좌 정보를 주는 상점도 있고 아닌 곳도 있어 방어적으로 읽는다.
        account = transfers[0] or {}
        masked = str(account.get("bankAccountNumber") or "")
        return {
            "billingKey": key,
            "method": "TRANSFER",
            "label": account.get("bankName") or None,
            "last4": masked[-4:] or None,
        }

    # cardCompany·cardNumber 는 **API 2024-06-01 부터 응답에서 제거됐다**(토스 문서 명시,
    # 2026-09-10 실측으로도 확인). 대체 필드는 card.issuerCode·card.number 다.
    # issuerCode 는 두 자리 코드라 그대로 보여줄 수 없어 이름으로 바꾼다(toss_codes).
    # 옛 버전 상점을 위해 제거된 필드도 먼저 본다 — 있으면 그게 더 정확하다.
    card = body.get("card") or {}
    masked = str(body.get("cardNumber") or card.get("number") or "")
    brand = body.get("cardCompany") or card_issuer_name(card.get("issuerCode"))
    return {
        "billingKey": key,
        "method": "CARD",
        "label": brand or None,
        "last4": masked[-4:] or None,
    }


async def charge(settings, *, billing_key: str, customer_key: str, order_id: str,
                 order_name: str, amount: int) -> dict:
    """자동결제 승인. orderId 를 Idempotency-Key 로 써 재시도가 이중 결제가 되지 않게 한다."""
    body = await _post(
        settings, f"/v1/billing/{billing_key}",
        {"customerKey": customer_key, "amount": amount,
         "orderId": order_id, "orderName": order_name},
        idempotency_key=order_id,
    )
    if body.get("status") != _OK_STATUS or body.get("totalAmount") != amount:
        # 200 인데 우리 기대와 다르다 — 적립하면 안 된다. 재시도해도 같으므로 확정 실패.
        raise TossBillingError(
            "payment_not_approved",
            f"결제가 승인되지 않았어요(상태 {body.get('status')}).", retryable=False)
    return body


async def delete_billing_key(settings, *, billing_key: str) -> None:
    """카드 교체 시 옛 키 정리. 실패해도 서비스는 진행한다(다음 결제에 쓰지 않으므로)."""
    try:
        async with _client(settings) as client:
            await client.request(
                "DELETE", f"{settings.toss_api_base}/v1/billing/{billing_key}",
                auth=httpx.BasicAuth(billing_secret(settings), ""))
    except httpx.HTTPError as e:
        log.warning("toss billing key delete failed: %s", type(e).__name__)
