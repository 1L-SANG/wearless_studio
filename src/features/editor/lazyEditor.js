import { lazy } from 'react';

let editorModulePromise;

export function preloadEditor() {
  // 실패한 import promise를 메모이즈하면 lazy 라우트가 영구 오염된다(새로고침 전까지
  // 재시도 불가). 거부 시 캐시를 비워 다음 호출/리마운트에서 다시 시도하게 한다.
  if (!editorModulePromise) {
    editorModulePromise = import('@/features/editor/Editor.jsx').catch((err) => {
      editorModulePromise = undefined;
      throw err;
    });
  }
  return editorModulePromise;
}

export const LazyEditor = lazy(preloadEditor);
