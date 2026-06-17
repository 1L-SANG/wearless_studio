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
import { Routes, Route, Navigate, Outlet, useNavigate } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useToast } from '@/components/ui.jsx';
import { loadDraft, clearDraft } from '@/lib/draftStore.js';
import { syncDraftToBackend } from '@/lib/draftSync.js';
import { isSupabaseConfigured } from '@/lib/supabase.js';

/* 보호 라우트 — 세션 없으면 공개 입력 페이지로. 입력은 공개라 리다이렉트 루프 없음. */
function RequireAuth() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/create/input" replace />;
  return <Outlet />;
}

/* 비로그인 입력의 로그인 후 백엔드 sync — 한 번만 실행(StrictMode 이중 effect 에도
   sync 가 두 번 안 나가게 모듈 프로미스로 단일화). draft 없으면 hadDraft:false. */
let draftSyncPromise = null;
function syncDraftOnce() {
  // 모듈 스코프 — 같은 페이지 로드에서 단 한 번만 sync(StrictMode 이중 effect 가 공유).
  // 리셋하지 않는다: 새 로그인은 풀페이지 리다이렉트라 모듈이 다시 평가돼 자연히 초기화됨.
  if (!draftSyncPromise) {
    draftSyncPromise = (async () => {
      const draft = await loadDraft();
      if (!draft) return { hadDraft: false };
      const { projectId } = await syncDraftToBackend(draft);
      await clearDraft();
      return { hadDraft: true, projectId };
    })();
  }
  return draftSyncPromise;
}

/* '/' 복귀의 단일 리다이렉트 주인 — 경쟁 navigate(레이스) 없음.
   - 목표가 /create/mannequin(분석 CTA 로그인): 로딩 표시 → IndexedDB draft 복원 →
     백엔드 sync → projectId store 반영 → draft 정리 → 마네킹. 실패 시 토스트+입력(draft 유지).
   - 그 외: 목표(없으면 입력)로 바로 이동.
   StrictMode 안전: 플래그 읽기는 useState 초기화(순수), 삭제·sync 는 effect 에서. */
/* '새 제작'(startProject → projectGeneration++)이면 같은 /create/input 라우트라도 ProductInput 을
   remount 해 폼·복원상태를 초기화한다 — 복구로 복원된 묵은 입력이 새 제작에 남지 않게. */
function ProductInputRoute() {
  const generation = useAppStore((s) => s.projectGeneration);
  return <ProductInput key={generation} />;
}

function RootRedirect() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const setProjectId = useAppStore((s) => s.setProjectId);
  const [target] = useState(() => sessionStorage.getItem('wl_postLogin') || '/create/input');
  const needsSync = target === '/create/mannequin';

  useEffect(() => {
    sessionStorage.removeItem('wl_postLogin');
    if (!needsSync) return;
    let alive = true;
    // 로그인 미완료(취소/실패)면 sync 없이 입력으로 — draft 는 IndexedDB 에 남아 있고,
    // ProductInput 이 마운트 시 draft 가 있으면 복원한다(브라우저 뒤로가기·새로고침 포함).
    if (!session) {
      navigate('/create/input', { replace: true });
      return;
    }
    syncDraftOnce()
      .then(({ hadDraft, projectId }) => {
        if (!alive) return;
        if (hadDraft && projectId) setProjectId(projectId);
        navigate('/create/mannequin', { replace: true });
      })
      .catch(() => {
        if (!alive) return;
        toast.push('입력 동기화에 실패했어요. 다시 시도해주세요.', { icon: 'alertTri' });
        navigate('/create/input', { replace: true }); // draft 유지 → 입력 화면에서 복원
      });
    return () => { alive = false; };
    // 마운트 1회만 — 세션은 App 의 loading 게이트로 마운트 시점에 이미 확정되며,
    // 토큰 갱신 등으로 effect 가 재실행돼 sync 가 다시 도는 것을 막는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (needsSync) return <div className="route-loading">입력과 사진을 동기화하는 중이에요…</div>;
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
