/* =============================================================
   shell/shell.jsx — app chrome: TopNav, Stepper, PageHead, WizardCTA, Media
   Ported verbatim from reference/prototype/components/shell.jsx.
   Data seam: TopNav reads account from the store and navigates via
   React Router (prototype used props + a single App state machine).
   ============================================================= */
import { useState, useEffect } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Icon, Modal, Button } from '@/components/ui.jsx';
import { useAppStore } from '@/store/useAppStore.js';

export const WIZARD_STEPS = [
  { key: 'input', label: '제품 정보·분석' },
  { key: 'mannequin', label: '마네킹컷' },
  { key: 'storyboard', label: '콘티보드' },
  { key: 'editor', label: '에디터' },
];

/* input+analysis collapse into step 0; generating shares the editor step */
export const STEP_INDEX = { input: 0, analysis: 0, mannequin: 1, storyboard: 2, generating: 3, editor: 3 };

const STEPPER_STEPS = ['input', 'mannequin', 'storyboard'];

export function TopNav() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const account = useAppStore((s) => s.account) || { name: '…', avatar: '', credits: 0, plan: '' };
  const startProject = useAppStore((s) => s.startProject);
  const route = pathname.startsWith('/library') ? 'library' : 'create';
  const onNav = async (r) => { if (r === 'create') { await startProject(); navigate('/create/input'); } else navigate('/library'); };
  const step = pathname.startsWith('/create/') ? pathname.split('/')[2] : null;

  return (
    <nav className="topnav">
      <span className="brand">wearless</span>
      <div className="nav-links">
        <button className={`nav-link${route === 'create' ? ' active' : ''}`} onClick={() => onNav('create')}>상세페이지 제작</button>
        <button className={`nav-link${route === 'library' ? ' active' : ''}`} onClick={() => onNav('library')}>보관함</button>
      </div>
      {STEPPER_STEPS.includes(step) && <div className="nav-stepper"><Stepper current={step} /></div>}
      <div className="nav-right">
        <span className="credit-badge"><Icon name="coins" size={15} stroke={1.8} />크레딧 <b>{account.credits}</b></span>
        <span className="plan-badge">{account.plan}</span>
        {account.avatar
          ? <img className="avatar" src={account.avatar} alt={account.name} title={account.name} />
          : <span className="avatar" style={{ display: 'inline-block', background: 'var(--bg-2)' }} />}
      </div>
    </nav>
  );
}

export function Stepper({ current }) {
  const idx = STEP_INDEX[current] ?? 0;
  return (
    <div className="stepper dots">
      {WIZARD_STEPS.map((s, i) => (
        <div key={s.key} className={`step${i < idx ? ' done' : ''}${i === idx ? ' active' : ''}`}>
          {i > 0 && <span className="step-line" />}
          <span className="step-dot" title={s.label} />
        </div>
      ))}
    </div>
  );
}

export function PageHead({ title, sub }) {
  return (
    <div className="page-head">
      <h1 dangerouslySetInnerHTML={{ __html: title }} />
      {sub && <p>{sub}</p>}
    </div>
  );
}

/* CTA footer for wizard pages */
export function WizardCTA({ children }) {
  return <div className="wizard-cta">{children}</div>;
}

/* ---- 생성 완료 후 초안 단계 재진입 제한 (PRD §10.17 / §15.3) ----
   완료된 프로젝트는 입력·마네킹·콘티로 되돌아가 생성 전 상태를 바꿀 수 없다.
   초안 화면(input/mannequin/storyboard)이 mount 시 호출 — 완료 상태면 모달로
   안내하고 에디터로 보낸다. 새 제작은 TopNav·보관함의 startProject 경로만. */
export function useDoneGuard() {
  const [blocked, setBlocked] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      await useAppStore.getState().loadProject();
      const p = await api.getProject(useAppStore.getState().projectId);
      if (!cancelled && p.status === 'done') setBlocked(true);
    })();
    return () => { cancelled = true; };
  }, []);
  return blocked;
}

export function DoneGuardModal() {
  const navigate = useNavigate();
  const go = () => navigate(`/editor/${useAppStore.getState().projectId}`, { replace: true });
  return (
    <Modal onClose={go}>
      <h3>초안 단계로 돌아갈 수 없어요</h3>
      <p>이미 생성이 완료된 상세페이지입니다. 필요한 컷은 에디터에서 추가하거나 수정해주세요.</p>
      <div className="modal-actions"><Button variant="primary" onClick={go}>에디터로 이동</Button></div>
    </Modal>
  );
}

/* a media placeholder image that lazy-fills */
export function Media({ src, alt, style, className, ratio }) {
  return <img src={src} alt={alt || ''} className={`media ${className || ''}`}
    style={{ aspectRatio: ratio, width: '100%', ...style }} loading="lazy" />;
}
