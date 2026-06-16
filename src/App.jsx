/* =============================================================
   App.jsx — routes (React Router).
   Flow: /create/input → mannequin → storyboard → generating → editor.
   "/" opens the input page directly (per product decision). Editor is
   a full-screen surface outside the app chrome (stub in phase 1).
   ============================================================= */
import { Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Login } from '@/features/auth/Login.jsx';
import { isSupabaseConfigured } from '@/lib/supabase.js';

export default function App() {
  const { session, loading } = useAuth();

  // 환경변수 미설정(예: Vercel env 누락)이면 화이트스크린 대신 원인을 보여준다.
  if (!isSupabaseConfigured) {
    return (
      <div className="route-loading">
        설정 오류: Supabase 환경변수(VITE_SUPABASE_URL·VITE_SUPABASE_ANON_KEY)가 없습니다.
      </div>
    );
  }

  // 인증 게이트: 세션 확인 전엔 로딩, 미로그인이면 로그인 화면만 (구글·카카오).
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;
  if (!session) return <Login />;

  return (
    <Routes>
      <Route element={<ChromeLayout />}>
        <Route index element={<Navigate to="/create/input" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="create">
          <Route index element={<Navigate to="/create/input" replace />} />
          <Route path="input" element={<ProductInput />} />
          <Route path="mannequin" element={<Mannequin />} />
          <Route path="storyboard" element={<Storyboard />} />
          <Route path="generating" element={<Generating />} />
        </Route>
      </Route>
      {/* editor lives outside the chrome (full-screen workspace) */}
      <Route path="editor/:id" element={<Suspense fallback={<div className="route-loading">에디터를 불러오는 중이에요</div>}><LazyEditor /></Suspense>} />
      <Route path="*" element={<Navigate to="/create/input" replace />} />
    </Routes>
  );
}
