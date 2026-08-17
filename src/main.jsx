import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from '@/App.jsx';
import { AuthProvider } from '@/features/auth/AuthProvider.jsx';
import { ToastProvider } from '@/components/ui.jsx';
import '@/styles/tokens.css';
import '@/styles/app.css';
import '@/styles/features.css';
import '@/styles/moveable.css';

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
} else {
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
