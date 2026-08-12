/* =============================================================
   App.jsx — routes (React Router).
   Flow: /create/input → storyboard → mannequin → generating → editor.
   (마네킹컷 생성이 오래 걸려 콘티를 앞으로 당겼다 — 콘티 진입 시 생성을 발사해
    사용자가 보드를 짜는 동안 백그라운드로 돈다.)
   "/" opens the input page directly (per product decision) — 입력·분석은
   로그인 없이 공개. 분석 CTA 에서 로그인 게이트(LoginGate 모달)를 띄우고,
   로그인 후 콘티부터 진행한다. storyboard·mannequin·generating·library·
   editor 는 RequireAuth 로 보호(비세션 직접 URL 진입 → 입력으로 리다이렉트).
   OAuth 복귀('/')의 리다이렉트는 RootRedirect 단일 주인이 담당(복귀 목표 있으면 그곳, 없으면 입력).
   Editor 는 app chrome 밖의 전체화면 surface (stub in phase 1).
   ============================================================= */
import { Suspense, useEffect, useState } from 'react';
import { Routes, Route, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { Pricing } from '@/features/pricing/Pricing.jsx';
import { CreditsHistory } from '@/features/credits/CreditsHistory.jsx';
import { PaymentSuccess, PaymentFail } from '@/features/payments/PaymentResult.jsx';
import { ModelHub } from '@/features/model/ModelHub.jsx';
import { ModelRegister } from '@/features/model/ModelRegister.jsx';
import { ModelLicense } from '@/features/model/ModelLicense.jsx';
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
import {
  promoteDraftToProject,
  resetDraftSyncSingleFlight,
  retryDraftPromotion,
} from '@/lib/draftSync.js';
import { api, isMockMode } from '@/lib/api/index.js';
import { listMyModels } from '@/lib/api/facemarket.js';
import { ErrorState, useToast } from '@/components/ui.jsx';
import { shouldAdoptRouteProject } from '@/lib/projectRoute.js';
import {
  consumeFlowContinuation,
  hasFlowContinuation,
  isProductInfoConfirmed,
  isSameTabProjectReload,
  markFlowSession,
  markProductInfoConfirmed,
  registerConfirmedInputEntry,
} from '@/lib/flowSession.js';
import { ResumeChoiceModal } from '@/features/shell/shell.jsx';
import {
  draftSlot,
  formatDraftRelativeTime,
  localDraftMeta,
} from '@/lib/draftSlot.js';

draftSlot.configure(api);

/* 보호 라우트 — 세션 없으면 공개 입력 페이지로. 입력은 공개라 리다이렉트 루프 없음. */
function RequireAuth() {
  const { session, loading } = useAuth();
  // mock 데모 샌드박스 — 로그인 없이 전 플로우 확인(주소창 직접 진입 포함).
  // mock api 는 토큰을 쓰지 않으므로 세션 부재가 기능에 영향 없다. http 모드는 기존 가드 유지.
  if (isMockMode) return <Outlet />;
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;
  if (!session) return <Navigate to="/create/input" replace />;
  return <Outlet />;
}

// 현재 문서 수명 동안 create/editor 흐름이 한 번이라도 실제 렌더됐는지 기억한다. 새로고침은
// 모듈 변수가 초기화되므로 project-scoped sessionStorage 표식(flowSession)으로 별도 판별한다.
let flowRouteSeenThisSession = false;
const flowDocumentEntryId = Math.random().toString(36).slice(2);

/* 마네킹 이후 단계는 현재 프로젝트가 있어야 한다. 복원된 프로젝트도 유효하므로 동기 pair 만
   확인하고, 서버 유효성 검증/404 정리는 각 화면의 기존 loadProject 경로가 계속 담당한다. */
function RequireProject() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const projectPersisted = useAppStore((s) => s.projectPersisted);
  const beginProject = useAppStore((s) => s.beginProject);
  const [entryDecision, setEntryDecision] = useState(() => {
    if (!projectId || !projectPersisted) return 'continue';
    return flowRouteSeenThisSession || isSameTabProjectReload(projectId)
      || hasFlowContinuation(projectId) ? 'continue' : 'ask';
  });

  useEffect(() => {
    if (entryDecision !== 'continue') return;
    consumeFlowContinuation(projectId);
    flowRouteSeenThisSession = true;
    markFlowSession(projectId, pathname);
  }, [entryDecision, pathname, projectId]);

  // mock 데모 관례 — 주소창에 /create/storyboard·/create/generating 을 직접 쳐도
  // 시드 프로젝트를 만들어 통과시킨다(입력부터 걷지 않고 바로 확인). http 모드는 기존 가드 유지.
  useEffect(() => {
    if (!isMockMode || (projectId && projectPersisted)) return;
    let cancelled = false;
    api.createProject().then((p) => {
      if (!cancelled) useAppStore.setState({ projectId: p.id, projectPersisted: true });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId, projectPersisted]);

  const resume = () => {
    flowRouteSeenThisSession = true;
    markFlowSession(projectId, pathname);
    setEntryDecision('continue');
  };
  const startNew = async () => {
    await beginProject();
    flowRouteSeenThisSession = true;
    navigate('/create/input', { replace: true });
  };

  if (entryDecision === 'ask') {
    return <ResumeChoiceModal onResume={resume} onNew={startNew} onClose={resume} />;
  }

  if (!projectPersisted || !projectId) {
    if (isMockMode) return <div className="route-loading">데모 프로젝트 준비 중이에요</div>;
    return <Navigate to="/create/input" replace />;
  }
  return <Outlet />;
}

/* editor 는 URL 의 :id 가 공유/북마크 계약이다. store 에 남은 A가 있더라도 /editor/B 로
   들어오면 B를 서버 소유권 검증한 뒤 현재 프로젝트로 채택하고, 검증 전에는 Editor를 렌더하지 않는다. */
function RequireEditorProject() {
  const { id: routeProjectId } = useParams();
  const projectId = useAppStore((s) => s.projectId);
  const adoptProject = useAppStore((s) => s.adoptProject);
  const [phase, setPhase] = useState('loading'); // loading | ready | not-found | error
  const [attempt, setAttempt] = useState(0);

  useEffect(() => { flowRouteSeenThisSession = true; }, []);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    (async () => {
      try {
        const project = await api.getProject(routeProjectId);
        if (!alive) return;
        if (!project?.id || project.id !== routeProjectId) { setPhase('not-found'); return; }
        if (shouldAdoptRouteProject(useAppStore.getState().projectId, project.id)) {
          adoptProject(project.id);
        }
        setPhase('ready');
      } catch (error) {
        if (!alive) return;
        setPhase(error?.status === 404 ? 'not-found' : 'error');
      }
    })();
    return () => { alive = false; };
  }, [adoptProject, routeProjectId, attempt]);

  if (!routeProjectId || phase === 'not-found') return <Navigate to="/create/input" replace />;
  if (phase === 'error') {
    return (
      <div className="wizard narrow">
        <div className="surface">
          <ErrorState desc="에디터 프로젝트를 확인하지 못했어요." onRetry={() => setAttempt((value) => value + 1)} />
        </div>
      </div>
    );
  }
  if (phase === 'loading' || shouldAdoptRouteProject(projectId, routeProjectId)) {
    return <div className="route-loading">에디터 프로젝트를 확인하고 있어요…</div>;
  }
  return <Outlet />;
}

/* 모델 섹션 보호 — 로그인만으로는 얼굴·라이선스·생성 경로에 들어갈 수 없다.
   서버에 본인 소유 verified 모델이 있을 때만 /model/register 이외의 모든 하위 경로를 연다. */
function RequireVerifiedModel() {
  const [phase, setPhase] = useState('loading'); // loading | verified | unverified | error
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    listMyModels()
      .then((models) => {
        if (!alive) return;
        setPhase(models.some((model) => model.status === 'verified') ? 'verified' : 'unverified');
      })
      .catch(() => {
        if (alive) setPhase('error');
      });
    return () => { alive = false; };
  }, [attempt]);

  if (phase === 'loading') return <div className="route-loading">본인확인 상태를 확인하고 있어요…</div>;
  if (phase === 'unverified') return <Navigate to="/model/register" replace />;
  if (phase === 'error') {
    return (
      <div className="wizard narrow">
        <div className="surface">
          <ErrorState desc="본인확인 상태를 불러오지 못했어요." onRetry={() => setAttempt((value) => value + 1)} />
        </div>
      </div>
    );
  }
  return <Outlet />;
}

/* 상세페이지 제작 플로우에서 현재 머문 경로를 store 에 기록 → '이어서 작업' 재개 목표(resumePath).
   editor 는 chrome 밖 라우트라 여기(App 레벨 location 감시)서 함께 잡는다. */
function ResumeTracker() {
  const { pathname } = useLocation();
  const setResumePath = useAppStore((s) => s.setResumePath);
  const projectId = useAppStore((s) => s.projectId);
  const projectPersisted = useAppStore((s) => s.projectPersisted);
  useEffect(() => {
    if (projectId && projectPersisted && (pathname.startsWith('/create/') || pathname.startsWith('/editor/'))) {
      markFlowSession(projectId, pathname);
    }
    // 재개 대상은 서버 상태가 있는(projectPersisted) 단계만. /create/input 은 분석 전이라 복원할
    // 서버 상태가 없어, 여기로 '이어서' 하면 첫 페이지로 튕기는 것처럼 보인다 → 기록 제외.
    if (pathname.startsWith('/editor/')
        || (pathname.startsWith('/create/') && !pathname.startsWith('/create/input'))) {
      setResumePath(pathname);
    }
  }, [pathname, projectId, projectPersisted, setResumePath]);
  return null;
}

/* '새 제작'(startProject → projectGeneration++)이면 같은 /create/input 라우트라도 ProductInput 을
   remount 해 폼·복원상태를 초기화한다 — 복구로 복원된 묵은 입력이 새 제작에 남지 않게. */
function ProductInputRoute() {
  const navigate = useNavigate();
  const { key: locationKey } = useLocation();
  const { session, loading: authLoading } = useAuth();
  const { push: pushToast } = useToast();
  const generation = useAppStore((s) => s.projectGeneration);
  const beginProject = useAppStore((s) => s.beginProject);
  const [entryDecision, setEntryDecision] = useState(() => {
    const { projectId, projectPersisted } = useAppStore.getState();
    if (!projectPersisted || !projectId) return 'checking';
    if (useAppStore.getState().productInfoConfirmed || isProductInfoConfirmed(projectId)) return 'confirmed';
    return 'checking';
  });
  const [entrySources, setEntrySources] = useState([]);
  const slotEnabled = Boolean(session) || isMockMode;

  useEffect(() => {
    if (entryDecision !== 'checking') return;
    if (!isMockMode && authLoading) return;
    let alive = true;
    (async () => {
      const { projectId, projectPersisted, resumePath } = useAppStore.getState();
      const hasFlow = Boolean(projectPersisted && projectId);
      const flowDecision = !hasFlow || flowRouteSeenThisSession || isSameTabProjectReload(projectId)
        || hasFlowContinuation(projectId) ? 'continue' : 'ask';
      if (!slotEnabled) {
        if (alive) setEntryDecision(flowDecision);
        return;
      }
      const [slot, localDraft] = await Promise.all([
        draftSlot.get().catch(() => null),
        hasPendingDraft() ? loadDraft().catch(() => null) : Promise.resolve(null),
      ]);
      if (!alive) return;
      const sources = [];
      if (flowDecision === 'ask') {
        sources.push({
          id: 'flow',
          title: '진행 중인 보관함 작업',
          description: resumePath ? `마지막 화면 · ${resumePath}` : '진행 중인 상세페이지 제작',
        });
      }
      const localMeta = localDraftMeta(localDraft);
      const localDiffers = localDraft && (
        !slot
        || localDraft.updatedAt !== draftSlot.getSyncedAt()
        || slot.meta?.updatedAt !== draftSlot.getServerSyncedAt()
      );
      if (localDiffers) {
        sources.push({
          id: 'local',
          title: '이 기기 임시저장',
          description: `${formatDraftRelativeTime(localMeta.updatedAt)} · ${localMeta.deviceLabel} · 사진 ${localMeta.photoCount}장`,
          meta: localMeta,
          draft: localDraft,
        });
      }
      if (slot) {
        sources.push({
          id: 'remote',
          title: localDiffers ? '다른 기기 임시저장' : '임시저장',
          description: `${formatDraftRelativeTime(slot.meta?.updatedAt)} · ${slot.meta?.deviceLabel || '다른 기기'} · 사진 ${slot.meta?.photoCount || 0}장`,
          meta: slot.meta,
        });
      }
      setEntrySources(sources);
      setEntryDecision(sources.length ? 'ask' : 'continue');
    })().catch(() => {
      if (alive) setEntryDecision('continue');
    });
    return () => { alive = false; };
  }, [authLoading, entryDecision, slotEnabled]);

  useEffect(() => {
    if (entryDecision === 'confirmed') {
      if (!isMockMode && authLoading) return;
      // http 모드의 세션 만료 리다이렉트는 사용자의 잠금 화면 재진입이 아니다.
      // 로그인 유도 경로가 입력 화면을 맡도록 두고, 잠금 연타 카운트도 올리지 않는다.
      if (!isMockMode && !session) {
        const { projectId } = useAppStore.getState();
        setEntryDecision(registerConfirmedInputEntry(
          projectId,
          Date.now(),
          `${flowDocumentEntryId}:${locationKey}`,
          { countAsUserEntry: false },
        ));
        return;
      }
      const { projectId } = useAppStore.getState();
      if (!isProductInfoConfirmed(projectId)) markProductInfoConfirmed(projectId);
      setEntryDecision(registerConfirmedInputEntry(
        projectId,
        Date.now(),
        `${flowDocumentEntryId}:${locationKey}`,
      ));
      return;
    }
    if (entryDecision === 'redirect') {
      pushToast('의류 정보는 확정돼 수정할 수 없어요', { icon: 'alertTri' });
      navigate('/create/storyboard', { replace: true });
      return;
    }
    if (entryDecision !== 'start-new') return;
    let alive = true;
    beginProject().then(() => {
      if (!alive) return;
      flowRouteSeenThisSession = true;
      setEntryDecision('continue');
      navigate('/create/input', { replace: true });
    });
    return () => { alive = false; };
  }, [authLoading, beginProject, entryDecision, locationKey, navigate, pushToast, session]);

  useEffect(() => {
    if (entryDecision !== 'continue') return;
    consumeFlowContinuation(useAppStore.getState().projectId);
    flowRouteSeenThisSession = true;
    const { projectId } = useAppStore.getState();
    markFlowSession(projectId, '/create/input');
  }, [entryDecision]);

  const resume = () => {
    flowRouteSeenThisSession = true;
    const { projectId, resumePath } = useAppStore.getState();
    markFlowSession(projectId, resumePath || '/create/input');
    setEntryDecision('continue');
    if (resumePath && resumePath !== '/create/input') navigate(resumePath, { replace: true });
  };
  const startNew = async () => {
    if (slotEnabled) {
      try {
        await draftSlot.removeForNewFlow();
      } catch (error) {
        pushToast(error?.message || '임시저장을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
        return;
      }
    }
    await beginProject();
    flowRouteSeenThisSession = true;
    setEntryDecision('continue');
  };

  const chooseSource = async (sourceId) => {
    const source = entrySources.find((candidate) => candidate.id === sourceId);
    if (!source) return;
    if (sourceId === 'flow') {
      resume();
      return;
    }
    try {
      const takeover = await draftSlot.takeover();
      const { projectId, projectPersisted } = useAppStore.getState();
      if (projectId && projectPersisted) await beginProject();
      if (sourceId === 'remote') {
        if (!takeover?.payload) throw new Error('임시저장을 불러오지 못했어요.');
        draftSlot.stage(takeover);
      } else {
        draftSlot.stage({ payload: source.draft, meta: source.meta });
      }
      flowRouteSeenThisSession = true;
      setEntryDecision('continue');
    } catch (error) {
      pushToast(error?.message || '임시저장을 이어서 열지 못했어요.', { icon: 'alert' });
    }
  };

  if (entryDecision === 'ask') {
    return (
      <ResumeChoiceModal
        sources={entrySources}
        onChoose={chooseSource}
        onNew={startNew}
        onClose={() => chooseSource(entrySources[0]?.id)}
      />
    );
  }
  if (entryDecision !== 'continue') return <div className="route-loading">이동하고 있어요…</div>;
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
  const [returnIntent] = useState(() => sessionStorage.getItem('wl_postLogin'));
  const target = returnIntent || '/create/input';
  const [phase, setPhase] = useState('init');   // init | syncing | done
  const [dest, setDest] = useState(null);

  useEffect(() => {
    // 일반 첫 진입(/create/input)은 인증 확인과 무관하게 연다. 로그인 복귀처럼 세션이
    // 실제로 필요한 목표만 bootstrap 완료를 기다린다. AuthProvider는 session을 확정한 뒤
    // loading=false로 내리므로 그 전환에서 한 번만 실행한다(토큰 갱신 때 sync 재시작 금지).
    if (loading && target !== '/create/input') return;
    sessionStorage.removeItem('wl_postLogin');
    if (returnIntent?.startsWith('/create/')) {
      flowRouteSeenThisSession = true;
      markFlowSession(useAppStore.getState().projectId, returnIntent);
    }
    let alive = true;
    (async () => {
      let promotedProjectId = null;
      const wantsStoryboard = target === '/create/storyboard';
      if (!session) { setDest(wantsStoryboard ? '/create/input' : target); setPhase('done'); return; }
      const mode = import.meta.env.VITE_API_MODE ?? 'mock';
      if (!(wantsStoryboard && mode === 'http' && hasPendingDraft())) {
        setDest(target); setPhase('done'); return;
      }
      setPhase('syncing');
      try {
        const draft = await loadDraft();
        if (!draft?.product) { setDest(target); setPhase('done'); return; }
        // 로그인 복귀 승격도 입력 화면과 같은 작업권 계약을 따른다. active token을 서버 잠금
        // 안에서 먼저 소비해야만 프로젝트 생성을 시작할 수 있다.
        await draftSlot.remove();
        const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('sync_timeout')), DRAFT_SYNC_TIMEOUT_MS));
        const { projectId } = await Promise.race([promoteDraftToProject(draft), timeout]);
        promotedProjectId = projectId;
        if (!alive) return;
        // 같은 이유로 재생성 신호를 보존 — 로그인 복귀 draft sync 도 동일한 '신원 획득' 경로.
        useAppStore.getState().adoptProject(projectId, { preserveGenerationDirty: true });   // 콘티가 이 project 로 진행(+영속)
        flowRouteSeenThisSession = true;
        useAppStore.getState().confirmProductInfo(projectId);
        markFlowSession(projectId, '/create/storyboard');
        await clearDraft().then(() => { resetDraftSyncSingleFlight(); }).catch(() => {});
        setDest('/create/storyboard'); setPhase('done');
      } catch {
        if (!alive) return;
        if (promotedProjectId) {
          retryDraftPromotion(promotedProjectId);
          draftSlot.resume();
        }
        setDest('/create/input'); setPhase('done');   // 실패/지연 — draft 복원 + 재시도(입력에서)
      }
    })();
    return () => { alive = false; };
  }, [loading, returnIntent, target]);

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
            {/* 토스 결제 리다이렉트 착지점(WS3) — 승인은 success 화면이 서버에 위임한다 */}
            <Route path="payments/success" element={<PaymentSuccess />} />
            <Route path="payments/fail" element={<PaymentFail />} />
            {/* FaceMarket 모델 섹션 — 본인확인·라이선스(FM-10)와 개인화(사용자 얼굴·신체)가
                한 섹션이다. 개인화 화면 순서는 docs/personalization/phase0-ux-flow.md.
                본인확인(성인 인증, T2-1)은 register 하나로 흡수됐다 — FaceMarket 실명 인증
                1회가 개인화 성인 확인도 함께 기록하므로 별도 identity 라우트가 없다.
                /model 은 섹션 허브(체크리스트) — register·license 의 URL 은 종전 그대로. */}
            <Route path="model">
              {/* 본인확인 화면만 공개하고, 나머지 모든 모델 경로는 verified 모델을 요구한다. */}
              <Route path="register" element={<ModelRegister />} />
              <Route element={<RequireVerifiedModel />}>
                <Route index element={<ModelHub />} />
                <Route path="license" element={<ModelLicense />} />
                {/* 개인화 세부 단계는 라이선스 발급 여정 하나로 통합한다.
                    기존 북마크·외부 링크는 대응 단계로 안전하게 이어준다. */}
                <Route path="consent" element={<Navigate to="/model/license?step=consent" replace />} />
                <Route path="face" element={<Navigate to="/model/license?step=face" replace />} />
                <Route path="body" element={<Navigate to="/model/license?step=body" replace />} />
                <Route path="generate" element={<ModelGenerate />} />
                <Route path="withdraw" element={<ModelWithdraw />} />
                {/* 알 수 없는 /model/* 경로도 가드를 거친 뒤 허브로만 복귀한다. */}
                <Route path="*" element={<Navigate to="/model" replace />} />
              </Route>
            </Route>
          </Route>
          <Route path="create">
            <Route index element={<Navigate to="/create/input" replace />} />
            {/* 입력·분석은 공개 */}
            <Route path="input" element={<ProductInputRoute />} />
            {/* 마네킹 이후 단계는 로그인 필요 */}
            <Route element={<RequireAuth />}>
              <Route element={<RequireProject />}>
                <Route path="mannequin" element={<Mannequin />} />
                <Route path="storyboard" element={<Storyboard />} />
                <Route path="generating" element={<Generating />} />
              </Route>
            </Route>
          </Route>
        </Route>
        {/* editor lives outside the chrome (full-screen workspace) — 로그인 필요 */}
        <Route element={<RequireAuth />}>
          <Route element={<RequireEditorProject />}>
            <Route path="editor/:id" element={<Suspense fallback={<div className="route-loading">에디터를 불러오는 중이에요</div>}><LazyEditor /></Suspense>} />
          </Route>
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
