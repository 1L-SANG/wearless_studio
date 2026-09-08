/* =============================================================
   lib/facemarketPricing — FaceMarket 셀러 가격(초기 고정값). 2026-09-08 오너 결정.
   모델별 단가가 아니라 플랫폼 공통이다: 1건 9,900원, 월정액 29,900원(월 10건).
   화면 어디서든 가격을 적을 때는 여기 한 곳만 본다 — 카드에는 안 적고(오너 지시), 상세 창·확정
   미리보기·리스트 머리말에 같은 문장이 들어간다. 라이선스 행의 unit_price 는 서버에 남아 있지만
   초기에는 표시하지 않는다(정산 기획이 끝나면 다시 정한다).
   ============================================================= */
export const FACEMARKET_PRICING = Object.freeze({
  perCut: 9900,
  monthly: 29900,
  monthlyCap: 10,
});

const won = (n) => `${Number(n).toLocaleString('ko-KR')}원`;

/** 두 조각으로 나눠 준다 — 화면이 주(건당)와 보조(월정액)에 다른 굵기를 쓰기 위해서. */
export function pricingParts(p = FACEMARKET_PRICING) {
  return {
    perCut: `1건 ${won(p.perCut)}`,
    monthly: `월정액 ${won(p.monthly)}`,
    cap: `월 ${p.monthlyCap}건`,
  };
}

/** 한 문장. "1건 9,900원 / 월정액 29,900원 (월 10건)" */
export function pricingLine(p = FACEMARKET_PRICING) {
  const parts = pricingParts(p);
  return `${parts.perCut} / ${parts.monthly} (${parts.cap})`;
}
