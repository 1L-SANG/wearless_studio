import { useState } from 'react';

// 첫 문서의 리다이렉트는 실제 도착 화면까지 짧은 등장을 넘긴다.
const entryRedirects = new Set(['/', '/create', '/create/', '/price']);

// App이 소유하므로 에디터 등 크롬 밖을 거쳐 돌아와도 첫 진입을 다시 쓰지 않는다.
export function useDocumentEntry(pathname) {
  const [entry, setEntry] = useState(() => ({ pathname, initial: true }));
  if (entry.pathname !== pathname) {
    const initial = entry.initial && entryRedirects.has(entry.pathname);
    setEntry({ pathname, initial });
    return initial;
  }
  return entry.initial;
}
