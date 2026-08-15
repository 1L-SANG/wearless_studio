/* =============================================================
   features/editor/editorLoadError — 에디터 첫 로딩 실패의 분류.

   콘티보드(storyboardLoadError)와 같은 계약: {kind, message}. 실패해도 스피너가
   영원히 도는 대신 무슨 일인지 말하고 다시 시도할 수 있어야 한다. 순수 함수.
   ============================================================= */

export function classifyEditorLoadError(error) {
  const status = error?.status;
  const message = error?.message || '';
  if (status === 401 || /로그인이 필요/.test(message)) {
    return { kind: 'auth', message: '로그인이 풀렸어요. 다시 로그인한 뒤 이어서 편집해 주세요.' };
  }
  if (status === 404) {
    return { kind: 'notFound', message: '이 작업을 찾을 수 없어요. 보관함에서 다시 열어 주세요.' };
  }
  return { kind: 'network', message: message || '편집 화면을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.' };
}
