/* =============================================================
   App.jsx — routes (React Router).
   Flow: /create/input → mannequin → storyboard → generating → editor.
   "/" opens the input page directly (per product decision) — 입력·분석은
   로그인 없이 공개. 분석 CTA 에서 로그인 게이트(LoginGate 모달)를 띄우고,
   로그인 후 마네킹부터 진행한다. mannequin·storyboard·generating·library·
   editor 는 RequireAuth 로 보호(비세션 직접 URL 진입 → 입력으로 리다이렉트).
   OAuth 복귀('/')의 리다이렉트는 RootRedirect 단일 주인이 담당(복귀 목표 있으면 그곳, 없으면 입력).
   Editor 는 app chrome 밖의 전체화면 surface (stub in phase 1).
   ============================================================= */
import { Suspense, useEffect, useState } from 'react';
import { Routes, Route, Navigate, Outlet } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { isSupabaseConfigured } from '@/lib/supabase.js';

/* 보호 라우트 — 세션 없으면 공개 입력 페이지로. 입력은 공개라 리다이렉트 루프 없음. */
function RequireAuth() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/create/input" replace />;
  return <Outlet />;
}

/* '/' 복귀의 단일 리다이렉트 주인 — 로그인 직후 복귀 목표(wl_postLogin)가 있으면 그곳으로,
   없으면 입력으로. 인덱스 라우트가 유일한 결정권자라 경쟁 navigate(레이스)가 없다.
   StrictMode 안전: 플래그 읽기는 useState 초기화(순수), 삭제는 effect 에서(렌더/초기화 금지). */
function RootRedirect() {
  const [target] = useState(() => sessionStorage.getItem('wl_postLogin') || '/create/input');
  useEffect(() => { sessionStorage.removeItem('wl_postLogin'); }, []);
  return <Navigate to={target} replace />;
}

export default function App() {
  const { loading } = useAuth();

  // 환경변수 미설정(예: Vercel env 누락)이면 화이트스크린 대신 원인을 보여준다.
  if (!isSupabaseConfigured) {
    return (
      <div className="route-loading">
        설정 오류: Supabase 환경변수(VITE_SUPABASE_URL·VITE_SUPABASE_ANON_KEY)가 없습니다.
      </div>
    );
  }

  // 세션 확인 전엔 로딩만 (미로그인이어도 입력 페이지는 공개로 진입).
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;

  return (
    <>
      <Routes>
        <Route element={<ChromeLayout />}>
          <Route index element={<RootRedirect />} />
          {/* 보관함은 로그인 필요 */}
          <Route element={<RequireAuth />}>
            <Route path="library" element={<Library />} />
          </Route>
          <Route path="create">
            <Route index element={<Navigate to="/create/input" replace />} />
            {/* 입력·분석은 공개 */}
            <Route path="input" element={<ProductInput />} />
            {/* 마네킹 이후 단계는 로그인 필요 */}
            <Route element={<RequireAuth />}>
              <Route path="mannequin" element={<Mannequin />} />
              <Route path="storyboard" element={<Storyboard />} />
              <Route path="generating" element={<Generating />} />
            </Route>
          </Route>
        </Route>
        {/* editor lives outside the chrome (full-screen workspace) — 로그인 필요 */}
        <Route element={<RequireAuth />}>
          <Route path="editor/:id" element={<Suspense fallback={<div className="route-loading">에디터를 불러오는 중이에요</div>}><LazyEditor /></Suspense>} />
        </Route>
        <Route path="*" element={<Navigate to="/create/input" replace />} />
      </Routes>
    </>
  );
}
