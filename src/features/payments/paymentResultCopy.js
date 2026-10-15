// Keep actionable states without displaying provider codes or raw error details.
export function paymentResultErrorCopy(error, { cardOnly = false } = {}) {
  if (error?.code === 'subscription_exists') return {
    title: '이미 구독 중이에요',
    message: '구독 관리에서 이용 중인 요금제를 확인해 주세요.',
  };
  if (error?.code === 'manual_plan_active') return {
    title: '계좌이체 이용권을 사용 중이에요',
    message: '이용권이 끝난 뒤 정기결제를 시작할 수 있어요.',
  };
  // Both confirmation APIs use 503 for indeterminate provider outcomes.
  // Missing configuration is also 503, but no payment was attempted in that case.
  if (error?.code === 'payment_gateway_unreachable'
    || (error?.status === 503 && error?.code !== 'payment_not_configured')) return cardOnly ? {
    title: '결제수단 변경을 확인하지 못했어요',
    message: '구독 관리에서 등록된 결제수단을 확인해 주세요.',
  } : {
    title: '결제 확인이 지연되고 있어요',
    message: '다시 결제하기 전에 결제 내역을 확인해 주세요.',
  };
  return { message: '잠시 후 다시 시도해 주세요.' };
}
