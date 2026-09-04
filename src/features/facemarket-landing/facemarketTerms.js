/* FaceMarket 조건표의 프론트 단일 출처.
   서버가 아직 내려주지 않는 파생값(특히 월정액)은 이 값으로만 계산한다. */
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
export const REVIEW_SLA_LABEL = '보통 1시간 안';
export const UNIT_PRICE_DEFAULT_KRW = 10_000;
export const UNIT_PRICE_MIN_KRW = 5_000;
export const MONTHLY_MULTIPLIER = 2.5;
export const MONTHLY_PERIOD_DAYS = 30;
export const EARLYBIRD_SEATS = 10;
export const LICENSE_ISSUE_FEE_KRW = 20_000;
export const CIRCUMVENTION_MONTHS = 12;
export const APPROVAL_MODE = 'auto';

export function monthlyPriceFor(unitPrice = UNIT_PRICE_DEFAULT_KRW) {
  const normalized = Number.isFinite(Number(unitPrice)) ? Number(unitPrice) : UNIT_PRICE_DEFAULT_KRW;
  return Math.round((normalized * MONTHLY_MULTIPLIER) / 100) * 100;
}

export function validityLabel(days) {
  return days == null ? '영구' : `${days}일`;
}

export function formatKrw(value) {
  const amount = Number.isFinite(Number(value)) ? Number(value) : 0;
  return `${Math.round(amount).toLocaleString('ko-KR')}원`;
}
