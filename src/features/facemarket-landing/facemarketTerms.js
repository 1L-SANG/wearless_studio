/* FaceMarket 계약과 정산의 공통 조건.
   서비스 이용가격 표시는 lib/facemarketPricing.js를 사용한다. */
export const MODEL_SHARE = 0.70;
export const PLATFORM_SHARE = 0.20;
export const OPS_SHARE = 0.10;
export const SETTLEMENT_DAY = 10;
export const MIN_PAYOUT_KRW = 10_000;
export const VALIDITY_OPTIONS = Object.freeze([365, 730, null]);
export const APPLICATION_REJECT_PURGE_DAYS = 30;
export const REVIEW_SLA_LABEL = '24시간 이내';
export const MONTHLY_PERIOD_DAYS = 30;
export const LICENSE_ISSUE_FEE_KRW = 20_000;
export const APPROVAL_MODE = 'auto';

export function validityLabel(days) {
  return days == null ? '철회 시까지' : `${days}일`;
}

export function formatKrw(value) {
  const amount = Number.isFinite(Number(value)) ? Number(value) : 0;
  return `${Math.round(amount).toLocaleString('ko-KR')}원`;
}
