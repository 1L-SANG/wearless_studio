import { GENERAL_PAYMENT_REVIEW_PRODUCTS } from './generalPaymentProducts.js';

function randomOrderId() {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `wl-review-${uuid}`.slice(0, 64);
  const bytes = new Uint8Array(18);
  globalThis.crypto?.getRandomValues?.(bytes);
  const token = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  return `wl-review-${token || Date.now().toString(36)}`.slice(0, 64);
}

/**
 * 일반결제 시크릿 키가 아직 발급되지 않은 카드사 확인 단계에서만 쓰는 테스트 주문이다.
 * 서버 승인이나 크레딧 지급을 우회하지 않는다. 테스트 클라이언트 키로 결제창을 열어
 * 카드 입력 화면까지만 확인하고, 완료 복귀 뒤 confirm 은 기존 서버 가드가 503으로 막는다.
 */
export function generalPaymentReviewOrder(planCode, customerKey) {
  const product = GENERAL_PAYMENT_REVIEW_PRODUCTS[planCode];
  if (!product || !customerKey) return null;
  return {
    orderId: randomOrderId(),
    amount: product.price,
    credits: product.credits,
    bonusCredits: 0,
    totalCredits: product.credits,
    orderName: product.name,
    customerKey,
  };
}

export async function resolveTopupCheckout({
  createCheckout, planCode, customerKey, isReviewHost,
}) {
  try {
    return await createCheckout(planCode);
  } catch (error) {
    const reviewOrder = isReviewHost && error?.code === 'payment_not_configured'
      ? generalPaymentReviewOrder(planCode, customerKey)
      : null;
    if (!reviewOrder) throw error;
    return reviewOrder;
  }
}

export async function requestTossCardPayment({ loadTossPayments, clientKey, order, origin }) {
  const toss = await loadTossPayments(clientKey);
  const payment = toss.payment({ customerKey: order.customerKey });
  return payment.requestPayment({
    method: 'CARD',
    amount: { currency: 'KRW', value: order.amount },
    orderId: order.orderId,
    orderName: order.orderName,
    successUrl: `${origin}/payments/success`,
    failUrl: `${origin}/payments/fail`,
  });
}
