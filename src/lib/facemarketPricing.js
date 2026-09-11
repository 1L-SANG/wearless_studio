/* FaceMarket 서비스 표준 요금의 공용 표시 어댑터예요. */
import {
  STANDARD_UNIT_PRICE_KRW, MONTHLY_PASS_PRICE_KRW, MONTHLY_PASS_CUTS, MONTHLY_OVERAGE_KRW,
} from '../features/facemarket-landing/facemarketTerms.js';

export const FACEMARKET_PRICING = Object.freeze({
  perCut: STANDARD_UNIT_PRICE_KRW,
  monthly: MONTHLY_PASS_PRICE_KRW,
  monthlyCap: MONTHLY_PASS_CUTS,
  overage: MONTHLY_OVERAGE_KRW,
});
const won = n => `${Number(n).toLocaleString('ko-KR')}원`;
export function pricingParts(p = FACEMARKET_PRICING) {
  return { label: '가격', perCut: `1건 ${won(p.perCut)}`, monthly: `월 이용권 ${won(p.monthly)}`, cap: `월 ${p.monthlyCap}건`, overage: `초과 건당 ${won(p.overage)}` };
}
export function pricingLine(p = FACEMARKET_PRICING) {
  const parts = pricingParts(p);
  return `${parts.perCut} · ${parts.monthly} (${parts.cap}, ${parts.overage})`;
}
