/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Icon, useToast } from '@/components/ui.jsx';
import { useSmoothProgress } from '@/components/SmoothProgress.jsx';
import { EXPECTED_MS } from '@/lib/smoothProgress.js';
import { TopNav } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { api } from '@/lib/api/index.js';
import { thumbUrl } from '@/lib/imageCdn.js';
import {
  advanceMannequinCompletion,
  createMannequinCompletionState,
} from '@/features/mannequin/completionToastCore.js';

const mannequinCuts = (envelope) => {
  if (Array.isArray(envelope)) return envelope;
  if (Array.isArray(envelope?.cuts)) return envelope.cuts;
  if (Array.isArray(envelope?.data?.cuts)) return envelope.data.cuts;
  return [];
};

function MannequinCompletionToast() {
  const navigate = useNavigate();
  const { push: pushToast } = useToast();
  const { pathname } = useLocation();
  const job = useAppStore((s) => s.mannequinJob);
  const transitionRef = useRef(createMannequinCompletionState(job));
  const pathnameRef = useRef(pathname);
  const mountedRef = useRef(false);
  pathnameRef.current = pathname;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    const transition = advanceMannequinCompletion(transitionRef.current, job, pathnameRef.current);
    transitionRef.current = transition.state;
    if (!transition.completedProjectId) return;

    const projectId = transition.completedProjectId;
    void (async () => {
      let thumb = '';
      try {
        const cuts = mannequinCuts(await api.getMannequins(projectId));
        const firstImage = cuts[0]?.imageUrl || cuts[0]?.src || '';
        thumb = thumbUrl(firstImage, 400);
      } catch { /* 목록 조회 실패여도 완료 알림은 텍스트로 보여준다. */ }

      if (!mountedRef.current || pathnameRef.current === '/create/mannequin') return;
      if (useAppStore.getState().projectId !== projectId) return;
      pushToast('마네킹컷이 만들어졌어요', {
        thumb,
        duration: 5000,
        variant: 'mannequinCompletion',
        onClick: () => navigate('/create/mannequin'),
      });
    })();
  }, [job, navigate, pushToast]);

  return null;
}

function MannequinJobRibbon() {
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const job = useAppStore((s) => s.mannequinJob);
  // 훅은 early-return 위에 (훅 개수 불변). mannequinJob 엔 시작시각이 없어 훅이
  // running 으로 바뀐 순간을 시작점으로 잡는다.
  const progress = useSmoothProgress(Math.max(0, Math.min(100, Number(job?.progress) || 0)), {
    active: job?.status === 'running',
    expectedMs: EXPECTED_MS.mannequin,
  });
  if (!job || pathname.startsWith('/create/mannequin')) return null;
  if (job.projectId && projectId && job.projectId !== projectId) return null;
  if (job.status === 'idle') return null;

  const isError = job.status === 'error';
  const label = isError ? '마네킹컷 생성에 실패했어요' : '마네킹컷을 만들고 있어요';
  const detail = isError ? (job.errorMessage || '다시 시도할 수 있어요.') : `${Math.round(progress)}%`;

  return (
    <div className={`job-ribbon${isError ? ' error' : ''}`} role={isError ? 'alert' : 'status'} aria-live="polite">
      <div className="job-ribbon-main">
        <span className="job-ribbon-label">
          <Icon name={isError ? 'alertTri' : 'loader'} size={15} className={isError ? '' : 'spin'} />
          {label}
        </span>
        {!isError && (
          <div className="job-ribbon-track" aria-hidden="true">
            <i className="job-ribbon-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
        <span className="job-ribbon-detail">{detail}</span>
      </div>
    </div>
  );
}

function StoryboardTransitionOverlay() {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setVisible(false), 4725);  // CSS 5.25s − 음수딜레이 0.525s 와 동기
    return () => clearTimeout(timer);
  }, []);

  if (!visible) return null;
  return (
    <button type="button" className="storyboard-transition-overlay"
      aria-label="전환 안내 닫기" onClick={() => setVisible(false)}>
      <span className="storyboard-transition-copy" role="status" aria-live="polite">
        <img className="transition-brand-logo" src="/assets/brand/logo.svg" alt="" />
        <strong>의류 구현 진행중<span className="transition-dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span></strong>
        <small>의류의 재현성을 높이기 위해 마네킹 이미지를 생성중이에요.<br />그동안 예시이미지들을 바탕으로 원하는 상세페이지를 구성해보세요.</small>
      </span>
    </button>
  );
}

/* 상세페이지 생성 리본 — "창을 닫아도 계속"을 사실로 만드는 장치(editor_wait_dev_spec §5).
   store.detailPageJob 은 폴링 수명까지 소유하므로 어느 화면에 가 있든 진행이 산다.
   대기 화면(/create/generating) 안에서는 자체 리본이 있어 숨긴다. */
function DetailPageJobRibbon() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const job = useAppStore((s) => s.detailPageJob);

  // 새로고침으로 store 메모리의 폴링 루프만 사라진 경우, 저장된 jobId로 같은 작업을 재추적한다.
  // 이미 루프가 살아 있으면 startDetailPageGeneration 내부 가드가 아무 일도 하지 않는다.
  useEffect(() => {
    if (job?.status === 'running' && job.projectId) {
      useAppStore.getState().startDetailPageGeneration(job.projectId);
    }
  }, [job?.projectId, job?.status]);

  // 훅은 early-return 위에. startedAt 은 새로고침 복원 시에도 살아 있어(detailPageJobMarker)
  // 되돌아온 뒤에도 바가 처음부터 다시 기지 않는다.
  const progress = useSmoothProgress(Math.max(0, Math.min(100, Number(job?.progress) || 0)), {
    active: job?.status === 'running',
    startedAt: job?.startedAt,
    expectedMs: EXPECTED_MS.detailPage,
  });

  if (!job || job.status === 'idle' || job.status === 'done' || job.status === 'blocked') return null;
  if (pathname.startsWith('/create/generating')) return null;

  const isError = job.status === 'error';
  const label = isError ? '상세페이지 생성에 실패했어요' : '상세페이지를 만들고 있어요';
  const detail = isError ? (job.errorMessage || '다시 시도할 수 있어요.')
    : job.cutsTotal ? `${job.cutsDone}/${job.cutsTotal}컷` : `${Math.round(progress)}%`;

  return (
    <div className={`job-ribbon${isError ? ' error' : ''}`} role={isError ? 'alert' : 'status'} aria-live="polite">
      <div className="job-ribbon-main">
        <span className="job-ribbon-label">
          <Icon name={isError ? 'alertTri' : 'loader'} size={15} className={isError ? '' : 'spin'} />
          {label}
        </span>
        {!isError && (
          <div className="job-ribbon-track" aria-hidden="true">
            <i className="job-ribbon-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
        <span className="job-ribbon-detail">{detail}</span>
      </div>
      <button type="button" className="job-ribbon-btn" onClick={() => navigate('/create/generating')}>
        생성 화면 보기
      </button>
    </div>
  );
}

export function ChromeLayout() {
  const { session } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const { pathname } = location;
  // 도착 전환은 state 로 한 번만 — 래치에 옮겨 담고 history state 는 즉시 소비한다.
  // 오버레이 마운트를 state 에 직접 걸면 소비 순간 언마운트되고, 소비를 안 하면
  // 뒤로가기 재진입마다 4.7초 오버레이가 재생된다(리뷰 P2).
  const [transitionKey, setTransitionKey] = useState(null);
  useEffect(() => {
    if (pathname === '/create/storyboard' && location.state?.showMannequinTransition) {
      setTransitionKey(location.key);
      navigate(pathname, { replace: true, state: null });
    }
  }, [pathname, location.state, location.key, navigate]);
  const storyboardOwnsEntrance = pathname === '/create/storyboard';
  const loadAccount = useAppStore((s) => s.loadAccount);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);

  // 카탈로그는 공개 입력 페이지에도 필요 → 항상 로드. 계정은 로그인 후에만.
  useEffect(() => { loadCatalogs(); }, [loadCatalogs]);
  useEffect(() => { if (session) loadAccount(); }, [session, loadAccount]);

  // Background glow intensity uses the CSS default. Final orb/edge opacity is defined in app.css.
  // The wizard stepper now lives centered inside TopNav (see shell.jsx),
  // so the hero content starts directly under the nav.
  return (
    <div className="app-shell fx-sheen fx-lift fx-pagefade">
      <div className="app-bg">
        <div className="edge-glow" />
        <div className="orb-bg"><div className="l1" /><div className="l2" /><div className="l3" /><div className="hi" /></div>
      </div>
      <TopNav />
      <MannequinCompletionToast />
      {pathname === '/create/storyboard' && transitionKey && (
        <StoryboardTransitionOverlay key={transitionKey} />
      )}
      {/* 두 잡 리본이 동시에 뜰 수 있다(마네킹+상세페이지) — 각자 sticky top:60px 이면
          서로 겹치므로 스택 컨테이너가 sticky 를 소유하고 리본은 static 으로 쌓는다(codex F8). */}
      <div className="job-ribbon-stack">
        <MannequinJobRibbon />
        <DetailPageJobRibbon />
      </div>
      {/* 콘티는 데이터 준비 후 자체 진입 모션을 재생해 스켈레톤과 이중 애니메이션되지 않게 한다. */}
      <div className={`app-main${storyboardOwnsEntrance ? '' : ' page-enter'}`} key={pathname}>
        <Outlet />
      </div>
    </div>
  );
}

export default ChromeLayout;
