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

/* 계좌이체(무통장입금) 신청 노출 여부 — PG 심사 전 결제 경로.
   지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md. 사용자가 사업자 통장으로
   입금하고 관리자가 확인해 지급한다(충전 팩 + 구독 플랜 1개월 이용권). 켜져 있으면 요금제
   버튼이 토스 결제창 대신 신청 창을 열고, 충전 탭도 보인다(계좌이체로는 충전을 팔 수 있다).
   토스가 열리면 false 로 바꾼다. 코드는 남긴다. 계좌 정보는 서버(/v1/bank-transfer/info)가 준다. */
export const BANK_TRANSFER_ENABLED = true;

// 토스 심사용 임시 이메일 로그인. 심사 종료 후 false로 바꾸고 재배포한다.
// 화면 노출만 제어한다. 사용한 심사 계정과 세션도 별도로 비활성화해야 한다.
export const PG_REVIEW_LOGIN_ENABLED = true;
