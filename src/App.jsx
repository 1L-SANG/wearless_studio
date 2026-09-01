/* =============================================================
   App.jsx — routes (React Router).
   Flow: /create/input → storyboard → mannequin → generating → editor.
   (마네킹컷 생성이 오래 걸려 콘티를 앞으로 당겼다 — 콘티 진입 시 생성을 발사해
    사용자가 보드를 짜는 동안 백그라운드로 돈다.)
   "/" opens the input page directly (per product decision) — 입력·분석은
   로그인 없이 공개. 분석 CTA 에서 로그인 게이트(LoginGate 모달)를 띄우고,
   로그인 후 콘티부터 진행한다. storyboard·mannequin·generating·library·
   editor 는 RequireAuth 로 보호(비세션 직접 URL 진입 → 입력으로 리다이렉트).
   OAuth 복귀('/')의 리다이렉트는 도메인마다 주인이 하나씩이다 — ai 는 RootRedirect(복귀 목표
   있으면 그곳, 없으면 입력), facemarket 은 FacemarketRoot(복귀 목표 있으면 그곳, 없으면 랜딩).
   두 주인 모두 인증 부트스트랩(loading)이 끝나기 전에는 이동하지 않는다 — 첫 렌더에 이동하면
   그 replaceState 가 AuthProvider 보다 먼저 돌아 `?code=` 를 지워 로그인이 완성되지 않는다.
   Editor 는 app chrome 밖의 전체화면 surface (stub in phase 1).
   ============================================================= */
import { Suspense, useEffect, useRef, useState } from 'react';
import { Link, Routes, Route, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
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
// 랜딩은 **정적 import 로 둔다**(감사가 코드 스플리팅을 minor 로 올렸지만 기각했다).
// 셀러(ai) 도메인이 렌더하지 않는 코드를 gzip 10.3kB 지고 가는 건 맞다. 그런데 이 레포엔
// ErrorBoundary 가 하나도 없어서(componentDidCatch·getDerivedStateFromError 0건), lazy 로
// 내리면 랜딩 청크 요청이 한 번 실패하는 순간 React.lazy 가 렌더에서 던져 facemarket 루트가
// 통째로 흰 화면이 된다 — 지금은 메인 청크에 실려 있어 그 실패 모드 자체가 없다.
// 아래 LazyEditor 와 저울이 다르다: 에디터는 수백 kB 에 로그인 뒤 화면이고, 랜딩은 32kB 에
// 이 도메인의 첫 화면(=유일한 유입 경로)이다. ErrorBoundary 가 생기면 그때 lazy 가 맞다.
import { FacemarketRoot } from '@/features/facemarket-landing/FacemarketRoot.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { LazyEditor } from '@/features/editor/lazyEditor.js';
import { forgetPostLogin, readPostLogin, useAuth } from '@/features/auth/AuthProvider.jsx';
import { IS_FACEMARKET } from '@/lib/host.js';
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
import { Button, ErrorState, useToast } from '@/components/ui.jsx';
import { shouldAdoptRouteProject } from '@/lib/projectRoute.js';
import { markEditorEntered } from '@/lib/editorEntered.js';
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
// facemarket 도메인(등록 전용)에서 미인증 진입 시 — /create/input(메인 앱)로 보내면
// 등록 전용 사이트에 편집기가 뜬다. 대신 로그인 모달을 열고 등록으로 복귀시킨다.
function FacemarketLoginPrompt() {
  const { openLogin } = useAuth();
  // 모달을 딱 한 번만 연다. openLogin 은 AuthProvider 가 매 렌더 새로 만드는 함수라
  // deps 에 두면, 사용자가 모달을 닫아(closeLogin → AuthProvider 리렌더) identity 가
  // 바뀌는 순간 effect 가 다시 돌아 모달이 곧장 다시 열린다 — 닫을 수 없는 모달이 된다.
  // (AuthProvider 에서 useCallback 으로도 안정화했지만, 재발 방지는 여기서도 건다.)
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    openLogin?.('/model/register');
  }, [openLogin]);
  return (
    <div className="route-loading">
      모델 등록은 로그인이 필요해요 — 로그인 창을 열었어요.
      {/* 모달을 닫은 사람에게 나갈 길과 되돌릴 길을 준다. effect 가 1회성이라 닫은 모달은
          스스로 다시 열리지 않고, 이 화면은 등록 라우트라 링크가 없으면 주소창을 직접
          고치는 수밖에 없다.
          맨 <button>·맨 <a> 로 두면 안 된다. 이 레포의 전역 스타일에는 버튼·링크 리셋이
          없어서(app.css 는 `button { font-family: inherit }` 한 줄, 링크는 `a.link` 클래스
          한정) 그대로 두면 OS 기본 회색 버튼과 파란 밑줄 하이퍼링크가 프로덕션에 나온다 —
          생체정보를 맡기라고 설득하는 도메인의 첫 화면 중 하나다. 앱의 Button·`a.link` 를 쓴다.
          소개 링크는 Button 이 아니라 <Link> 로 남긴다: 이동이지 동작이 아니라서
          가운데클릭·새 탭 열기가 살아야 한다. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
        <Button variant="primary" size="sm" onClick={() => openLogin?.('/model/register')}>로그인 다시 열기</Button>
        <Link className="link" to="/">FaceMarket 소개 보기</Link>
      </div>
    </div>
  );
}

function RequireAuth() {
  const { session, loading } = useAuth();
  // mock 데모 샌드박스 — 로그인 없이 전 플로우 확인(주소창 직접 진입 포함).
  // mock api 는 토큰을 쓰지 않으므로 세션 부재가 기능에 영향 없다. http 모드는 기존 가드 유지.
  if (isMockMode) return <Outlet />;
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;
  if (!session) {
    if (IS_FACEMARKET) return <FacemarketLoginPrompt />;
    return <Navigate to="/create/input" replace />;
  }
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
        // 에디터가 실제로 열린 뒤에야 '편집 시작' 표식을 남긴다 — 이후 앞 단계(입력·
        // 마네킹·콘티) 재진입을 막는 근거다. 열리지도 않은 프로젝트에 표식을 남기면
        // 입력 화면과 무한히 왕복하게 된다.
        markEditorEntered(project.id);
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

/* 모델 섹션 보호 — 등록 중 모델은 허브·라이선스에 접근할 수 있지만 생성은 verified만 허용한다. */
function RequireModel({ verifiedOnly = false }) {
  const [phase, setPhase] = useState('loading'); // loading | allowed | denied | error
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    listMyModels()
      .then((models) => {
        if (!alive) return;
        const allowed = verifiedOnly
          ? models.some((model) => model.status === 'verified')
          : models.length > 0;
        setPhase(allowed ? 'allowed' : 'denied');
      })
      .catch(() => {
        if (alive) setPhase('error');
      });
    return () => { alive = false; };
  }, [attempt, verifiedOnly]);

  if (phase === 'loading') return <div className="route-loading">본인확인 상태를 확인하고 있어요…</div>;
  if (phase === 'denied') return <Navigate to="/model/register" replace />;
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

function RequireOwnedModel() {
  return <RequireModel />;
}

function RequireVerifiedModel() {
  return <RequireModel verifiedOnly />;
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

/* 재개 카드에 보여줄 화면 이름 — 셀러에게 라우트 경로(/create/…)를 그대로 노출하지 않는다. */
const FLOW_SCREEN_NAMES = [
  ['/create/storyboard', '콘티 화면'],
  ['/create/mannequin', '마네킹 화면'],
  ['/editor', '에디터 화면'],
  ['/create/input', '입력 화면'],
];
function flowScreenName(path) {
  return FLOW_SCREEN_NAMES.find(([prefix]) => path?.startsWith(prefix))?.[1] || '진행하던 화면';
}

/* '새 제작'(startProject → projectGeneration++)이면 같은 /create/input 라우트라도 ProductInput 을
   remount 해 폼·복원상태를 초기화한다 — 복구로 복원된 묵은 입력이 새 제작에 남지 않게. */
function ProductInputRoute() {
  const navigate = useNavigate();
  const { key: locationKey } = useLocation();
  const { session, loading: authLoading, signingOut } = useAuth();
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
    if (!signingOut) return;
    setEntrySources([]);
    setEntryDecision('checking');
  }, [signingOut]);

  useEffect(() => {
    if (signingOut) return;
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
      // '새로 시작'을 결정했는데 서버 장애로 못 지운 슬롯은 조회 전에 정리를 먼저 끝낸다 —
      // 정리가 포기됐다면(그 뒤 다른 기기가 슬롯을 새로 씀) 그 카드를 정상적으로 권해야 한다.
      if (draftSlot.hasPendingRemoval()) await draftSlot.retryPendingRemoval();
      const pendingRemoval = draftSlot.hasPendingRemoval();
      const [slot, localDraft] = await Promise.all([
        draftSlot.get().catch(() => null),
        hasPendingDraft() ? loadDraft().catch(() => null) : Promise.resolve(null),
      ]);
      if (!alive) return;
      const sources = [];
      if (flowDecision === 'ask') {
        sources.push({
          id: 'flow',
          title: '만들던 상세페이지',
          description: resumePath ? `${flowScreenName(resumePath)}까지 진행했어요` : '진행하던 작업이 있어요',
        });
      }
      // 사진·상품명·분석이 하나도 없는 빈 슬롯(과거 버그로 생긴 팬텀 포함)은 이어갈 것이 없다.
      const slotVisible = Boolean(slot) && slot.meta?.hasContent !== false && !pendingRemoval;
      const localMeta = localDraftMeta(localDraft);
      // 내용을 전부 지운 로컬 임시저장도 서버 hasContent 와 같은 기준으로 권하지 않는다.
      const localHasContent = Boolean(localMeta) && Boolean(
        localMeta.photoCount > 0
        || (localDraft.product?.name || '').trim()
        || localDraft.analysis,
      );
      const localDiffers = localHasContent && (
        !slotVisible
        || localDraft.updatedAt !== draftSlot.getSyncedAt()
        || slot.meta?.updatedAt !== draftSlot.getServerSyncedAt()
      );
      // 마지막 저장만 서버에 못 닿은 경우 — 사실상 같은 작업이므로 '이 기기/다른 기기' 카드
      // 두 장 대신 더 새로운 이 기기 내용 한 장으로 합친다. 같은 브라우저 판정은 UA 라벨이
      // 아니라 "슬롯의 마지막 쓰기가 이 브라우저였는가"(serverSyncedAt 일치)로 한다 —
      // 같은 기종 두 대(Mac Chrome ×2)가 서로의 카드를 가리면 안 된다.
      const sameDeviceNewerLocal = localDiffers && slotVisible
        && slot.meta?.updatedAt === draftSlot.getServerSyncedAt()
        && Date.parse(localDraft.updatedAt || 0) >= Date.parse(slot.meta?.updatedAt || 0);
      const splitCards = localDiffers && slotVisible && !sameDeviceNewerLocal;
      if (localDiffers) {
        sources.push({
          id: 'local',
          title: splitCards ? '이 기기에서 입력하던 내용' : '입력하던 상품 정보',
          description: `${formatDraftRelativeTime(localMeta.updatedAt)}에 저장 · 사진 ${localMeta.photoCount}장`,
          meta: localMeta,
          draft: localDraft,
        });
      }
      if (slotVisible && !sameDeviceNewerLocal) {
        // 로컬이 서버 슬롯과 완전히 같은 상태면(마지막 저장까지 동기화됨) 복원은 로컬 사본으로
        // 한다 — 서버가 잠시 응답하지 않아도 이어서 열기가 실패하지 않는다.
        const localMatchesSlot = Boolean(localMeta) && !localDiffers;
        sources.push({
          id: 'remote',
          title: splitCards ? '다른 기기에서 입력하던 내용' : '입력하던 상품 정보',
          description: `${formatDraftRelativeTime(slot.meta?.updatedAt)}에 저장 · ${slot.meta?.deviceLabel || '다른 기기'} · 사진 ${slot.meta?.photoCount || 0}장`,
          meta: slot.meta,
          photosPending: Boolean(slot.meta?.photosPending),
          localFallback: localMatchesSlot ? localDraft : null,
        });
      }
      setEntrySources(sources);
      setEntryDecision(sources.length ? 'ask' : 'continue');
    })().catch(() => {
      if (alive) setEntryDecision('continue');
    });
    return () => { alive = false; };
  }, [authLoading, entryDecision, signingOut, slotEnabled]);

  useEffect(() => {
    if (signingOut) return;
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
  }, [authLoading, beginProject, entryDecision, locationKey, navigate, pushToast, session, signingOut]);

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
        pushToast(error?.message || '이전 작업을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
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
    const { projectId, projectPersisted } = useAppStore.getState();
    if (sourceId === 'remote' && !source.localFallback) {
      // 내용이 서버에만 있다 — 가져와야 열 수 있다.
      try {
        const takeover = await draftSlot.takeover();
        if (!takeover?.payload) {
          pushToast('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.', { icon: 'alertTri' });
          setEntrySources([]);
          setEntryDecision('checking');
          return;
        }
        if (projectId && projectPersisted) await beginProject();
        draftSlot.stage(takeover);
      } catch (error) {
        pushToast(error?.message || '저장된 내용을 열지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
        return;
      }
    } else {
      // 내용이 이 기기에 있다 — 서버를 기다리지 않고 바로 연다. 이어쓰기 권한(작업권)은
      // 뒤에서 확보하고, 실패해도 저장 재시도 경로가 처리한다(복원 자체를 막지 않는다).
      const draft = source.draft || source.localFallback;
      if (projectId && projectPersisted) await beginProject();
      draftSlot.stage({ payload: draft, meta: source.meta });
      void draftSlot.takeover().catch(() => {});
    }
    flowRouteSeenThisSession = true;
    setEntryDecision('continue');
  };

  if (signingOut) return <div className="route-loading">로그아웃하고 있어요…</div>;
  if (entryDecision === 'ask') {
    return (
      <ResumeChoiceModal
        sources={entrySources}
        onChoose={chooseSource}
        onNew={startNew}
      />
    );
  }
  // ChromeLayout이 이미 상단 헤더와 배경을 렌더한다. 짧은 진입 판정 동안에는
  // 본문을 비워 두어 별도의 흰 로딩 화면이나 전환 문구가 배경을 가리지 않게 한다.
  if (entryDecision !== 'continue') return null;
  return <ProductInput key={`${generation}:${session?.user?.id || 'guest'}`} />;
}

/* '/' 복귀의 리다이렉트. (Option B 재활성) 익명 입력+분석 draft(사진 blob 포함)를 로그인 후
   실서버로 동기화한다 — 프로젝트 생성 + 사진 R2 업로드 + 상품/분석 저장(draftSync). 마네킹 진입
   목표 + pending draft + http 모드일 때만. 동기화 중엔 로딩 UX 를 보여주고, 지연/실패가 진입을
   무한 블록하지 않게 타임아웃 시 입력으로 폴백한다(draft 는 IndexedDB 에 남아 ProductInput 이 복원).
   목표가 마네킹인데 세션이 없으면(로그인 취소) 입력으로. */
const DRAFT_SYNC_TIMEOUT_MS = 20000;

function RootRedirect() {
  const { session, loading } = useAuth();
  // 저장소 접근은 AuthProvider 의 헬퍼를 지난다. **이 읽기는 useState 초기화 함수라 렌더
  // 중에 돈다** — 쿠키·사이트 데이터를 막은 브라우저(사파리 프라이빗, Chrome 사이트별 차단,
  // 일부 인앱 웹뷰)에서 sessionStorage 는 접근만으로 SecurityError 를 던지고, 이 레포엔
  // ErrorBoundary 가 하나도 없어 그 예외가 곧장 ai.wearless.kr 루트의 흰 화면이 됐다.
  // (facemarket 쪽 FacemarketRoot 는 이미 try/catch 라 랜딩이 멀쩡히 떴다 — 매출이 도는
  //  셀러 도메인만 죽는 반쪽 하드닝이었다.) 못 읽으면 null → target 이 기본 경로로 떨어진다.
  const [returnIntent] = useState(readPostLogin);
  // facemarket 루트는 FacemarketRoot 가 가져갔다 — 여기 오는 건 ai 도메인뿐이다.
  const target = returnIntent || '/create/input';
  const [phase, setPhase] = useState('init');   // init | syncing | done
  const [dest, setDest] = useState(null);
  const [destState, setDestState] = useState(null);

  useEffect(() => {
    // 일반 첫 진입(/create/input)은 인증 확인과 무관하게 연다. 로그인 복귀처럼 세션이
    // 실제로 필요한 목표만 bootstrap 완료를 기다린다. AuthProvider는 session을 확정한 뒤
    // loading=false로 내리므로 그 전환에서 한 번만 실행한다(토큰 갱신 때 sync 재시작 금지).
    if (loading && target !== '/create/input') return;
    forgetPostLogin();   // 읽기와 같은 규율 — 던져도 승격 경로가 멈추지 않는다.
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
        const { projectId, customMatchPromotion } = await Promise.race([promoteDraftToProject(draft), timeout]);
        promotedProjectId = projectId;
        if (!alive) return;
        // 같은 이유로 재생성 신호를 보존 — 로그인 복귀 draft sync 도 동일한 '신원 획득' 경로.
        useAppStore.getState().adoptProject(projectId, { preserveGenerationDirty: true });   // 콘티가 이 project 로 진행(+영속)
        flowRouteSeenThisSession = true;
        useAppStore.getState().confirmProductInfo(projectId);
        markFlowSession(projectId, '/create/storyboard');
        await clearDraft().then(() => { resetDraftSyncSingleFlight(); }).catch(() => {});
        setDestState({ customMatchPromotionStarted: Boolean(customMatchPromotion) });
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
  if (phase === 'done' && dest) return <Navigate to={dest} replace state={destState} />;
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
        {/* facemarket 루트는 앱 크롬 밖 랜딩이다 — 등록 전 방문자에게 TopNav(크레딧·스테퍼)는
            셀러 스튜디오 잡음이고, 랜딩 상단바는 섹션 앵커라 성격이 겹치지 않는다.
            로그인 복귀(wl_postLogin) 소비는 FacemarketRoot 가 이어받는다. */}
        {IS_FACEMARKET && <Route index element={<FacemarketRoot />} />}
        <Route element={<ChromeLayout />}>
          {!IS_FACEMARKET && <Route index element={<RootRedirect />} />}
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
              {/* 등록은 모델 생성 전에도 열고, 등록 중 모델은 상태·라이선스 화면까지 복구한다. */}
              <Route path="register" element={<ModelRegister />} />
              <Route element={<RequireOwnedModel />}>
                <Route index element={<ModelHub />} />
                <Route path="license" element={<ModelLicense />} />
                {/* 폐기된 직접 업로드 북마크는 신규 등록 경계로 되돌린다. */}
                <Route path="consent" element={<Navigate to="/model/register" replace />} />
                <Route path="face" element={<Navigate to="/model/register" replace />} />
                <Route path="body" element={<Navigate to="/model/register" replace />} />
                <Route path="generate" element={<RequireVerifiedModel />}>
                  <Route index element={<ModelGenerate />} />
                </Route>
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
        <Route path="*" element={<Navigate to={IS_FACEMARKET ? '/' : '/create/input'} replace />} />
      </Routes>
    </>
  );
}
