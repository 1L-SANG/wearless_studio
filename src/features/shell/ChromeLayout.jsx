/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { TopNav } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';

const DONE_BADGE_MS = 3000;

function MannequinJobRibbon() {
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const job = useAppStore((s) => s.mannequinJob);
  const [doneBadge, setDoneBadge] = useState(false);
  const runningProjectIdRef = useRef(null);

  // 끝난 순간을 짧게 알린다 — 지금은 idle 로 돌아가며 리본이 즉시 사라져 완료를 놓친다.
  // '무언가 실행 중이었다'(bare boolean)가 아니라 '이 프로젝트가 실행 중이었다'를 기억해야
  // 한다 — beginProject/adoptProject 의 무조건 리셋(initialMannequinJob())도 status 를
  // idle 로 되돌리지만 projectId 는 null 로 지운다. 러너의 종결 기록은 항상
  // updateMannequinJob(pid, ...) 을 거쳐 projectId: pid 를 함께 찍으므로, 두 idle 을
  // projectId 일치 여부로 구분해야 다른 프로젝트로의 리셋이 완료 배지로 오인되지 않는다.
  useEffect(() => {
    if (job?.status === 'running' && job.projectId) {
      runningProjectIdRef.current = job.projectId;
      return undefined;
    }
    if (job?.status !== 'idle' || !runningProjectIdRef.current || job.projectId !== runningProjectIdRef.current) {
      return undefined;
    }
    runningProjectIdRef.current = null;
    setDoneBadge(true);
    const timer = setTimeout(() => setDoneBadge(false), DONE_BADGE_MS);
    return () => clearTimeout(timer);
  }, [job?.status, job?.projectId]);

  if (!job || pathname.startsWith('/create/mannequin')) return null;
  if (job.projectId && projectId && job.projectId !== projectId) return null;
  if (job.status === 'idle' && !doneBadge) return null;

  if (job.status === 'idle') {
    return (
      <div className="job-ribbon done" role="status" aria-live="polite">
        <div className="job-ribbon-main">
          <span className="job-ribbon-label"><Icon name="check" size={15} />마네킹컷 준비 완료</span>
        </div>
      </div>
    );
  }

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
  const { pathname } = useLocation();
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
