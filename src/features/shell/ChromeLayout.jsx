/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { TopNav } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';

function MannequinJobRibbon() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const job = useAppStore((s) => s.mannequinJob);

  if (!job || job.status === 'idle' || pathname.startsWith('/create/mannequin')) return null;
  if (job.projectId && projectId && job.projectId !== projectId) return null;

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
      <button type="button" className="job-ribbon-btn" onClick={() => navigate('/create/mannequin')}>
        마네킹 화면 보기
      </button>
    </div>
  );
}

export function ChromeLayout() {
  const { session } = useAuth();
  const loadAccount = useAppStore((s) => s.loadAccount);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);

  // 카탈로그는 공개 입력 페이지에도 필요 → 항상 로드. 계정은 로그인 후에만.
  useEffect(() => { loadCatalogs(); }, [loadCatalogs]);
  useEffect(() => { if (session) loadAccount(); }, [session, loadAccount]);

  // Background glow intensity uses the CSS default. Final orb/edge opacity is defined in app.css.
  // The wizard stepper now lives centered inside TopNav (see shell.jsx),
  // so the hero content starts directly under the nav.
  return (
    <div className="app-shell">
      <div className="app-bg">
        <div className="edge" />
        <div className="orb-bg"><div className="l1" /><div className="l2" /><div className="l3" /><div className="hi" /></div>
      </div>
      <TopNav />
      <MannequinJobRibbon />
      <div className="app-main">
        <Outlet />
      </div>
    </div>
  );
}

export default ChromeLayout;
