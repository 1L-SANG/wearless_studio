/* 토스 클라이언트 키 — 일반결제(1회 충전)와 자동결제(구독)를 나눈다.
   계획서 docs/plans/2026-09-09-toss-billing-subscription.md

   **왜 두 개인가**: 자동결제는 별도 계약이라 상점아이디(MID)가 따로 난다(우리는
   `bill_wearl02h5`). 토스 문서상 클라이언트 키와 시크릿 키는 MID 단위로 한 '세트' 이고,
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
