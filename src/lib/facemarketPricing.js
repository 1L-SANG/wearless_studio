/* =============================================================
   lib/facemarketPricing — FaceMarket 셀러 가격(초기 고정값). 2026-09-11 오너 결정.
   모델별 단가가 아니라 플랫폼 공통이다: 1건 14,900원, 월정액 49,900원(월 10건).
   화면 어디서든 가격을 적을 때는 여기 한 곳만 본다 — 카드에는 안 적고(오너 지시), 상세 창·확정
   미리보기·리스트 머리말에 같은 문장이 들어간다. 라이선스 행의 unit_price 는 서버에 남아 있지만
   초기에는 표시하지 않는다(정산 기획이 끝나면 다시 정한다).
   ============================================================= */
export const FACEMARKET_PRICING = Object.freeze({
  perCut: 14900,
  monthly: 49900,
  monthlyCap: 10,
  overage: 7900,
});

const won = (n) => `${Number(n).toLocaleString('ko-KR')}원`;

/** 조각으로 나눠 준다 — 화면이 금액만 굵게 잡고 라벨·구분자는 흐리게 두기 위해서. */
export function pricingParts(p = FACEMARKET_PRICING) {
  return {
    label: '가격',
    perCut: `1건 ${won(p.perCut)}`,
    monthly: `월정액 ${won(p.monthly)}`,
    cap: `${p.monthlyCap}건 제한`,
  };
}

/** 값만 한 줄로. "1건 14,900원 / 월정액 49,900원 (10건 제한)"
    라벨('가격')은 붙이지 않는다 — 정의 목록(dt)처럼 라벨이 이미 있는 자리에서 두 번 나오면 안 된다. */
export function pricingLine(p = FACEMARKET_PRICING) {
  const parts = pricingParts(p);
  return `${parts.perCut} / ${parts.monthly} (${parts.cap})`;
}
