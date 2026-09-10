/* FaceMarket 계약과 정산의 공통 조건.
   서비스 이용가격 표시는 lib/facemarketPricing.js를 사용한다. */
export const MODEL_SHARE = 0.70;
export const PLATFORM_SHARE = 0.20;
export const OPS_SHARE = 0.10;
export const SETTLEMENT_DAY = 10;
export const MIN_PAYOUT_KRW = 10_000;
export const DISPUTE_WINDOW_DAYS = 30;
export const VALIDITY_OPTIONS = Object.freeze([365, 730, null]);
export const EXPIRY_NOTICE_DAYS = 30;
export const RENEWAL_SLOT_MONTHS = 6;
export const PURGE_DAYS = 30;
export const BACKUP_PURGE_DAYS = 90;
export const ID_FACE_RETENTION = '2차 검수 완료 즉시 파기, 최대 확정 후 30일';
export const APPLICATION_REJECT_PURGE_DAYS = 30;
export const REVIEW_SLA_DAYS = 3;
export const REVIEW_SLA_LABEL = '24시간 이내';
export const UNIT_PRICE_MIN_KRW = 5_000;
export const MONTHLY_PERIOD_DAYS = 30;
export const EARLYBIRD_SEATS = 10;
export const LICENSE_ISSUE_FEE_KRW = 20_000;
export const CIRCUMVENTION_MONTHS = 12;
export const APPROVAL_MODE = 'auto';

export function validityLabel(days) {
  return days == null ? '영구' : `${days}일`;
}

export function formatKrw(value) {
  const amount = Number.isFinite(Number(value)) ? Number(value) : 0;
  return `${Math.round(amount).toLocaleString('ko-KR')}원`;
}
