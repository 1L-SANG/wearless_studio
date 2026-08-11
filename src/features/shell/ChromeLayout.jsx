/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { TopNav } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';

function MannequinJobRibbon() {
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const job = useAppStore((s) => s.mannequinJob);
  if (!job || pathname.startsWith('/create/mannequin')) return null;
  if (job.projectId && projectId && job.projectId !== projectId) return null;
  if (job.status === 'idle') return null;

  const isError = job.status === 'error';
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const label = isError ? '마네킹컷 생성에 실패했어요' : '마네킹컷을 만들고 있어요';
  const detail = isError ? (job.errorMessage || '다시 시도할 수 있어요.') : `${progress}%`;

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
    const timer = setTimeout(() => setVisible(false), 2500);
    return () => clearTimeout(timer);
  }, []);

  if (!visible) return null;
  return (
    <button type="button" className="storyboard-transition-overlay"
      aria-label="전환 안내 닫기" onClick={() => setVisible(false)}>
      <span className="storyboard-transition-copy" role="status" aria-live="polite">
        <strong>마네킹컷을 먼저 만들고 있어요</strong>
        <small>상세페이지를 구성하는 동안 뒤에서 계속 준비할게요.</small>
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

  if (!job || job.status === 'idle' || job.status === 'done' || job.status === 'blocked') return null;
  if (pathname.startsWith('/create/generating')) return null;

  const isError = job.status === 'error';
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const label = isError ? '상세페이지 생성에 실패했어요' : '상세페이지를 만들고 있어요';
  const detail = isError ? (job.errorMessage || '다시 시도할 수 있어요.')
    : job.cutsTotal ? `${job.cutsDone}/${job.cutsTotal}컷` : `${progress}%`;

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
  const { pathname } = location;
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
      {pathname === '/create/storyboard' && location.state?.showMannequinTransition && (
        <StoryboardTransitionOverlay key={location.key} />
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
