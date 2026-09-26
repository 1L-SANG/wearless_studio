export const STORYBOARD_SAVE_FAILURE_MESSAGE = '변경 내용을 저장하지 못했어요';

// afterFlush: 저장이 끝난 뒤 이동 직전에 한 번 돈다. 저장 실패면 돌지 않고, 던져도 이동은 막지 않는다.
export async function continueAfterStoryboardFlush({ flush, navigate, onFailure, afterFlush }) {
  try {
    await flush();
  } catch (error) {
    onFailure(error?.message || STORYBOARD_SAVE_FAILURE_MESSAGE);
    return false;
  }
  try { afterFlush?.(); } catch { /* 부가 작업 실패가 다음 단계 이동을 막지 않는다 */ }
  navigate();
  return true;
}
