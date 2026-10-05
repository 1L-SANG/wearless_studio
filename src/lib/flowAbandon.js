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
