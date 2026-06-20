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
import { Pricing } from '@/features/pricing/Pricing.jsx';
import { CreditsHistory } from '@/features/credits/CreditsHistory.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { isSupabaseConfigured } from '@/lib/supabase.js';

/* 보호 라우트 — 세션 없으면 공개 입력 페이지로. 입력은 공개라 리다이렉트 루프 없음. */
function RequireAuth() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/create/input" replace />;
  return <Outlet />;
}

/* '새 제작'(startProject → projectGeneration++)이면 같은 /create/input 라우트라도 ProductInput 을
   remount 해 폼·복원상태를 초기화한다 — 복구로 복원된 묵은 입력이 새 제작에 남지 않게. */
function ProductInputRoute() {
  const generation = useAppStore((s) => s.projectGeneration);
  return <ProductInput key={generation} />;
}

/* '/' 복귀의 리다이렉트 — 즉시 이동. (Option B) 사진의 백엔드 동기화는 보류한다: 현재 마네킹
   등 하위 단계가 mock 이라 sync 가 결과에 영향이 없는데, 원격(Railway+R2)이 느리고 불안정해
   로그인→마네킹을 10초 지연·실패시켰다. 익명 입력+분석은 IndexedDB 에 남아 ProductInput 이
   복원하고, 실서버 사진 동기화는 R2 설정 + 업로드 병렬화 후 재활성한다(Option A).
   목표가 마네킹인데 세션이 없으면(로그인 취소) 입력으로. */
function RootRedirect() {
  const { session } = useAuth();
  const [target] = useState(() => sessionStorage.getItem('wl_postLogin') || '/create/input');
  useEffect(() => { sessionStorage.removeItem('wl_postLogin'); }, []);
  const dest = target === '/create/mannequin' && !session ? '/create/input' : target;
  return <Navigate to={dest} replace />;
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
            {/* 크레딧 에이전트 페이지 — auth 는 라우트만 등록, 본문 컴포넌트는 크레딧 에이전트 소유 */}
            <Route path="pricing" element={<Pricing />} />
            <Route path="credits/history" element={<CreditsHistory />} />
          </Route>
          <Route path="create">
            <Route index element={<Navigate to="/create/input" replace />} />
            {/* 입력·분석은 공개 */}
            <Route path="input" element={<ProductInputRoute />} />
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
