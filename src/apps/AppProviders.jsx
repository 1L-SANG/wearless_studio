/* 진입점 3개(셀러·facemarket·admin)가 공유하는 프로바이더 구성과 루프백 정규화.

   스타일 import 는 **여기 두지 않는다**. admin 은 Tailwind 레이어 순서를 자기 CSS 에서
   직접 정해야 하는데, JS import 로 들어온 스튜디오 CSS 는 레이어 밖(unlayered)이 되어
   레이어 안의 유틸리티를 명시도와 무관하게 이긴다. 그래서 스타일은 각 진입점이 문다. */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '@/features/auth/AuthProvider.jsx';
import { ToastProvider } from '@/components/ui.jsx';

export function renderApp(App) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false } },
  });

  // API와 R2의 브라우저 CORS는 개발 origin을 localhost로 고정한다. 127.0.0.1/::1은
  // 같은 컴퓨터여도 브라우저상 다른 origin이라 업로드 전에 `Failed to fetch`로 차단된다.
  const isLoopbackAlias = import.meta.env.DEV
    && ['127.0.0.1', '[::1]'].includes(window.location.hostname);

  if (isLoopbackAlias) {
    const canonicalUrl = new URL(window.location.href);
    canonicalUrl.hostname = 'localhost';
    window.location.replace(canonicalUrl);
    return;
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter future={{ v7_startTransition: true }}>
            <ToastProvider>
              <App />
            </ToastProvider>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </React.StrictMode>
  );
}
