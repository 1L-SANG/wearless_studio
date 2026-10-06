import { isProductInfoConfirmed } from './flowSession.js';

const FLOW_SCREEN_NAMES = [
  ['/create/storyboard', '콘티 화면'],
  ['/create/mannequin', '마네킹 화면'],
  ['/editor', '에디터 화면'],
  ['/create/input', '입력 화면'],
];
export function flowScreenName(path) {
  return FLOW_SCREEN_NAMES.find(([prefix]) => path?.startsWith(prefix))?.[1] || '진행하던 화면';
}

export function shouldWarnFlowAbandon(state) {
  return Boolean(state.projectId && state.projectPersisted
    && (state.productInfoConfirmed || isProductInfoConfirmed(state.projectId))
    && ['/create/storyboard', '/create/mannequin'].some((prefix) => state.resumePath?.startsWith(prefix)));
}

export function notifyFlowKept(state, push, navigate) {
  if (!shouldWarnFlowAbandon(state)) return;
  push('만들던 상세페이지는 보관함의 만드는 중에 있어요', {
    action: { label: '보관함', onClick: () => navigate('/library') },
  });
}
