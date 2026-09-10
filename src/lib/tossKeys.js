/* 토스 클라이언트 키 — 일반결제(1회 충전)와 자동결제(구독)를 나눈다.
   계획서 docs/plans/2026-09-09-toss-billing-subscription.md

   **왜 두 개인가**: 자동결제는 별도 계약이라 상점아이디(MID)가 따로 난다(우리는
   `bill_wearlau5j`). 토스 문서상 클라이언트 키와 시크릿 키는 MID 단위로 한 '세트' 이고,
   세트가 아닌 키를 섞으면 `INVALID_API_KEY`, 자동결제 계약이 없는 클라이언트 키로
   requestBillingAuth 를 부르면 `NOT_SUPPORTED_METHOD` 가 난다.

   하나로 쓰면 충전과 구독 중 **반드시 한쪽이 깨진다**. 서버도 같은 이유로
   TOSS_SECRET_KEY / TOSS_BILLING_SECRET_KEY 를 나눠 두었다(app/toss_billing.py).

   MID 가 하나뿐인 상점이면 VITE_TOSS_BILLING_CLIENT_KEY 를 비워 두면 된다 —
   일반결제 키로 떨어진다. 서버의 fallback 규칙과 같은 모양이다. */

/** 1회 충전(requestPayment) 용 — 일반결제 MID 의 클라이언트 키. */
export const TOSS_CLIENT_KEY = import.meta.env.VITE_TOSS_CLIENT_KEY;

/** 구독(requestBillingAuth) 용 — 자동결제 MID 의 클라이언트 키. 없으면 일반결제 키. */
export const TOSS_BILLING_CLIENT_KEY =
  import.meta.env.VITE_TOSS_BILLING_CLIENT_KEY || TOSS_CLIENT_KEY;

/* 퀵계좌이체 자동결제(method='TRANSFER') 노출 여부.
   문서상 카드와 같은 자동결제 계약으로 되는 것처럼 읽히지만, 2026-09-10 우리 MID
   (bill_wearlau5j)에서는 열리지 않았다. 그래서 **버튼만** 숨긴다 —
   서버(toss_billing·subscriptions)와 스키마(pay_method)는 이미 카드·계좌를 모두
   다루므로, 토스가 열어주는 날 이 값만 true 로 바꾸면 끝난다. 코드를 지우면
   그때 다시 만들어야 한다. */
export const SUBSCRIPTION_TRANSFER_ENABLED = false;

/* 크레딧 추가 구매(1회 결제) 노출 여부.
   충전은 **일반결제**라 자동결제 계약만으로는 라이브에서 동작하지 않는다
   (`requestPayment` + `/v1/payments/confirm`). 2026-09-10 토스 확인:
   자동결제 MID 로 단건 결제를 대신 처리하는 것도 불가.
   일반결제 계약(도입비 별도) 전까지 **탭을 숨긴다** — 누르면 실패할 버튼을 두면
   사용자가 결제 실패를 겪는다. 서버 라우트(app/payments.py)는 그대로 살려 둔다:
   TOSS_SECRET_KEY 가 없으면 이미 503 이고, 계약이 생기면 이 값만 true 로 바꾸면 된다.
   크레딧이 부족한 사용자는 요금제 업그레이드(즉시 비례결제)로 간다. */
export const TOPUP_ENABLED = false;
