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
import { Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { Pricing } from '@/features/pricing/Pricing.jsx';
import { CreditsHistory } from '@/features/credits/CreditsHistory.jsx';
import { ModelHub } from '@/features/model/ModelHub.jsx';
import { ModelRegister } from '@/features/model/ModelRegister.jsx';
import { ModelLicense } from '@/features/model/ModelLicense.jsx';
import { ModelConsent } from '@/features/model/ModelConsent.jsx';
import { ModelFaceUpload } from '@/features/model/ModelFaceUpload.jsx';
import { ModelBodyProfile } from '@/features/model/ModelBodyProfile.jsx';
import { ModelGenerate } from '@/features/model/ModelGenerate.jsx';
import { ModelWithdraw } from '@/features/model/ModelWithdraw.jsx';
import { PublicVerify } from '@/features/verify/PublicVerify.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { isSupabaseConfigured } from '@/lib/supabase.js';
import { loadDraft, clearDraft, hasPendingDraft } from '@/lib/draftStore.js';
import { syncDraftToBackend } from '@/lib/draftSync.js';

/* 보호 라우트 — 세션 없으면 공개 입력 페이지로. 입력은 공개라 리다이렉트 루프 없음. */
function RequireAuth() {
  const { session, loading } = useAuth();
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;
  if (!session) return <Navigate to="/create/input" replace />;
  return <Outlet />;
}

/* 상세페이지 제작 플로우에서 현재 머문 경로를 store 에 기록 → '이어서 작업' 재개 목표(resumePath).
   editor 는 chrome 밖 라우트라 여기(App 레벨 location 감시)서 함께 잡는다. */
function ResumeTracker() {
  const { pathname } = useLocation();
  const setResumePath = useAppStore((s) => s.setResumePath);
  useEffect(() => {
    // 재개 대상은 서버 상태가 있는(projectPersisted) 단계만. /create/input 은 분석 전이라 복원할
    // 서버 상태가 없어, 여기로 '이어서' 하면 첫 페이지로 튕기는 것처럼 보인다 → 기록 제외.
    if (pathname.startsWith('/editor/')
        || (pathname.startsWith('/create/') && !pathname.startsWith('/create/input'))) {
      setResumePath(pathname);
    }
  }, [pathname, setResumePath]);
  return null;
}

/* '새 제작'(startProject → projectGeneration++)이면 같은 /create/input 라우트라도 ProductInput 을
   remount 해 폼·복원상태를 초기화한다 — 복구로 복원된 묵은 입력이 새 제작에 남지 않게. */
function ProductInputRoute() {
  const generation = useAppStore((s) => s.projectGeneration);
  return <ProductInput key={generation} />;
}

/* '/' 복귀의 리다이렉트. (Option B 재활성) 익명 입력+분석 draft(사진 blob 포함)를 로그인 후
   실서버로 동기화한다 — 프로젝트 생성 + 사진 R2 업로드 + 상품/분석 저장(draftSync). 마네킹 진입
   목표 + pending draft + http 모드일 때만. 동기화 중엔 로딩 UX 를 보여주고, 지연/실패가 진입을
   무한 블록하지 않게 타임아웃 시 입력으로 폴백한다(draft 는 IndexedDB 에 남아 ProductInput 이 복원).
   목표가 마네킹인데 세션이 없으면(로그인 취소) 입력으로. */
const DRAFT_SYNC_TIMEOUT_MS = 20000;

function RootRedirect() {
  const { session, loading } = useAuth();
  const [target] = useState(() => sessionStorage.getItem('wl_postLogin') || '/create/input');
  const [phase, setPhase] = useState('init');   // init | syncing | done
  const [dest, setDest] = useState(null);

  useEffect(() => {
    // 일반 첫 진입(/create/input)은 인증 확인과 무관하게 연다. 로그인 복귀처럼 세션이
    // 실제로 필요한 목표만 bootstrap 완료를 기다린다. AuthProvider는 session을 확정한 뒤
    // loading=false로 내리므로 그 전환에서 한 번만 실행한다(토큰 갱신 때 sync 재시작 금지).
    if (loading && target !== '/create/input') return;
    sessionStorage.removeItem('wl_postLogin');
    let alive = true;
    (async () => {
      const wantsMannequin = target === '/create/mannequin';
      if (!session) { setDest(wantsMannequin ? '/create/input' : target); setPhase('done'); return; }
      const mode = import.meta.env.VITE_API_MODE ?? 'mock';
      if (!(wantsMannequin && mode === 'http' && hasPendingDraft())) {
        setDest(target); setPhase('done'); return;
      }
      setPhase('syncing');
      try {
        const draft = await loadDraft();
        if (!draft?.product) { setDest(target); setPhase('done'); return; }
        const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('sync_timeout')), DRAFT_SYNC_TIMEOUT_MS));
        const { projectId } = await Promise.race([syncDraftToBackend(draft), timeout]);
        if (!alive) return;
        useAppStore.getState().adoptProject(projectId);   // 마네킹이 이 project 로 진행(+영속)
        await clearDraft().catch(() => {});
        setDest('/create/mannequin'); setPhase('done');
      } catch {
        if (!alive) return;
        setDest('/create/input'); setPhase('done');   // 실패/지연 — draft 복원 + 재시도(입력에서)
      }
    })();
    return () => { alive = false; };
  }, [loading, target]);

  if (phase === 'syncing') return <div className="route-loading">입력 내용을 안전하게 저장하고 있어요…</div>;
  if (phase === 'done' && dest) return <Navigate to={dest} replace />;
  return <div className="route-loading">불러오는 중이에요</div>;
}

export default function App() {
  // 환경변수 미설정(예: Vercel env 누락)이면 화이트스크린 대신 원인을 보여준다.
  if (!isSupabaseConfigured) {
    return (
      <div className="route-loading">
        설정 오류: Supabase 환경변수(VITE_SUPABASE_URL·VITE_SUPABASE_ANON_KEY)가 없습니다.
      </div>
    );
  }

  return (
    <>
      <ResumeTracker />
      <Routes>
        <Route element={<ChromeLayout />}>
          <Route index element={<RootRedirect />} />
          {/* 보관함은 로그인 필요 */}
          <Route element={<RequireAuth />}>
            <Route path="library" element={<Library />} />
            {/* 크레딧 에이전트 페이지 — auth 는 라우트만 등록, 본문 컴포넌트는 크레딧 에이전트 소유 */}
            <Route path="pricing" element={<Pricing />} />
            <Route path="credits/history" element={<CreditsHistory />} />
            {/* FaceMarket 모델 섹션 — 본인확인·라이선스(FM-10)와 개인화(사용자 얼굴·신체)가
                한 섹션이다. 개인화 화면 순서는 docs/personalization/phase0-ux-flow.md.
                본인확인(성인 인증, T2-1)은 register 하나로 흡수됐다 — FaceMarket 실명 인증
                1회가 개인화 성인 확인도 함께 기록하므로 별도 identity 라우트가 없다.
                /model 은 섹션 허브(체크리스트) — register·license 의 URL 은 종전 그대로. */}
            <Route path="model">
              <Route index element={<ModelHub />} />
              <Route path="register" element={<ModelRegister />} />
              <Route path="license" element={<ModelLicense />} />
              <Route path="consent" element={<ModelConsent />} />
              <Route path="face" element={<ModelFaceUpload />} />
              <Route path="body" element={<ModelBodyProfile />} />
              <Route path="generate" element={<ModelGenerate />} />
              <Route path="withdraw" element={<ModelWithdraw />} />
            </Route>
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
        {/* 얼굴 라이선스 공개 검증(step02 QR 대상) — **RequireAuth 밖**. 심사위원·구매자가
            VC 카드의 QR 을 자기 폰으로 찍어 로그인 없이 유효성을 확인한다(로그인 게이트를
            두면 QR 이 무의미해진다). 크롬(TopNav) 밖에도 둔다 — 스캔으로 진입한 사람에게
            앱 내비게이션은 잡음이다. 얼굴은 이 페이지에 렌더되지 않는다(PublicVerify 주석). */}
        <Route path="verify/:licenseId" element={<PublicVerify />} />
        <Route path="*" element={<Navigate to="/create/input" replace />} />
      </Routes>
    </>
  );
}
