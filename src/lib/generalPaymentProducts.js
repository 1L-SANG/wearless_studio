/** 카드사에 제출한 일반결제 상품 정본. */
export const GENERAL_PAYMENT_REVIEW_PRODUCTS = Object.freeze({
  topup_100: Object.freeze({ credits: 100, price: 5500, name: '100 크레딧' }),
  topup_500: Object.freeze({ credits: 500, price: 26000, name: '500 크레딧' }),
});

export function isGeneralPaymentReviewProduct(plan) {
  const expected = GENERAL_PAYMENT_REVIEW_PRODUCTS[plan?.code];
  return Boolean(expected
    && plan?.kind === 'topup'
    && Number(plan?.credits) === expected.credits
    && Number(plan?.price) === expected.price);
}
