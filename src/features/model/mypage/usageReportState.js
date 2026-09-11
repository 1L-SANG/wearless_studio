export const REPORT_REASONS = Object.freeze(['허용하지 않은 품목이에요', '원하지 않는 방식으로 쓰였어요', '다른 문제가 있어요']);
const empty = () => ({ phase: 'closed', paymentId: null, reason: '', detail: '', error: '' });
export function usageReportReducer(state = empty(), action) {
  if (state.phase === 'sending' && !['success', 'error'].includes(action.type)) return state;
  switch (action.type) {
    case 'open': return { ...empty(), phase: 'editing', paymentId: action.paymentId };
    case 'reason': return { ...state, reason: action.value, error: '' };
    case 'detail': return { ...state, detail: action.value, error: '' };
    case 'submit': return reportCanSubmit(state) ? { ...state, phase: 'sending', error: '' } : state;
    case 'error': return { ...state, phase: 'editing', error: action.message };
    case 'success': case 'close': return empty();
    default: return state;
  }
}
export function reportPayload(state) {
  return { paymentId: state.paymentId, reason: [state.reason, state.detail.trim()].filter(Boolean).join('\n') };
}
export function reportCanSubmit(state) {
  return state.phase === 'editing' && Boolean(state.paymentId) && Boolean(reportPayload(state).reason)
    && reportPayload(state).reason.length <= 1000;
}
