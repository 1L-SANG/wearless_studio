// 콘티 진입 뒤에는 이 프로젝트의 상품 입력·분석을 다시 열지 않는다.
// 새 제작은 다른 프로젝트를 쓰므로 이전 표식은 지우지 않는다.
const key = projectId => `sb-entered-${projectId}`;

export const STORYBOARD_INPUT_LOCK_MESSAGE = '콘티를 만든 뒤에는 분석으로 돌아갈 수 없어요';

export function markStoryboardEntered(projectId) {
  if (!projectId) return;
  try { localStorage.setItem(key(projectId), '1'); } catch { /* 저장소 차단 시 기존 흐름 유지 */ }
}

export function hasStoryboardEntered(projectId) {
  if (!projectId) return false;
  try { return localStorage.getItem(key(projectId)) === '1'; } catch { return false; }
}
