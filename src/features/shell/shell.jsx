/* =============================================================
   shell/shell.jsx — app chrome: TopNav, Stepper, PageHead, WizardCTA, Media
   Ported verbatim from reference/prototype/components/shell.jsx.
   Data seam: TopNav reads account from the store and navigates via
   React Router (prototype used props + a single App state machine).
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Icon, Modal, Button } from '@/components/ui.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';

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
  const { session, openLogin } = useAuth();
  const account = useAppStore((s) => s.account) || { name: '…', avatar: '', credits: 0, plan: '' };
  const beginProject = useAppStore((s) => s.beginProject);
  const mannequinJob = useAppStore((s) => s.mannequinJob);
  // create 흐름일 때만 'create' 활성 — /pricing·/credits 등은 어느 탭도 활성 아님(폴백 active 버그 수정)
  const route = pathname.startsWith('/library') ? 'library'
    : pathname.startsWith('/create') ? 'create' : null;
  // '상세페이지 제작' 은 로컬 플로우만 초기화하고 입력 화면으로 — 서버 project(보관함 행)는
  // AI 분석 시작 때 생성한다(빈 프로젝트 양산 방지).
  const onNav = async (r) => {
    if (r === 'create') {
      if (mannequinJob?.status === 'running') {
        navigate('/create/mannequin');
        return;
      }
      await beginProject();
      navigate('/create/input');
    } else navigate('/library');
  };
  const step = pathname.startsWith('/create/') ? pathname.split('/')[2] : null;

  return (
    <nav className="topnav">
      <span className="brand">
        <img className="brand-logo" src="/assets/brand/logo.svg" alt="" />
        <img className="brand-wordmark" src="/assets/brand/wordmark.png" alt="Wearless" />
      </span>
      <div className="nav-links">
        {/* 비로그인 숨김: 보관함/제작 탭은 로그인 사용자용. 비로그인 입력·분석은 '/' 공개 진입. */}
        {session && <button className={`nav-link${route === 'create' ? ' active' : ''}`} onClick={() => onNav('create')}>상세페이지 제작</button>}
        {session && <button className={`nav-link${route === 'library' ? ' active' : ''}`} onClick={() => onNav('library')}>보관함</button>}
      </div>
      {STEPPER_STEPS.includes(step) && <div className="nav-stepper"><Stepper current={step} /></div>}
      <div className="nav-right">
        {session ? (
          <>
            <span className="credit-badge"><Icon name="coins" size={15} stroke={1.8} />크레딧 <b>{account.credits}</b></span>
            {account.plan && <span className="plan-badge">{account.plan}</span>}
            <ProfileMenu />
          </>
        ) : (
          <button className="nav-login" onClick={() => openLogin()}>로그인</button>
        )}
      </div>
    </nav>
  );
}

/* 로그인 사용자 프로필 — 구글 계정 메뉴 형태. 아바타(사진/이니셜) 클릭 시
   헤더(이름·이메일) + 요금제 관리(/pricing) + 크레딧 사용 내역(/credits/history) + 로그아웃.
   두 페이지 본문은 크레딧 에이전트 소유 — 여기선 라우트 이동만 한다. */
function ProfileMenu() {
  const { session, signOut } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const meta = session?.user?.user_metadata || {};
  const name = meta.full_name || meta.name || meta.user_name || session?.user?.email || '사용자';
  const email = session?.user?.email || meta.email || '';
  const photo = meta.avatar_url || meta.picture || '';
  const initial = (name || email || '?').trim().charAt(0).toUpperCase();
  const avatar = (cls) => photo
    ? <img className={cls} src={photo} alt="" referrerPolicy="no-referrer" />
    : <span className={`${cls} avatar-initial`}>{initial}</span>;

  return (
    <div className="profile" ref={ref}>
      <button className="profile-btn" onClick={() => setOpen((o) => !o)} title={name} aria-haspopup="menu" aria-expanded={open}>
        {avatar('avatar')}
      </button>
      {open && (
        <div className="profile-menu" role="menu">
          <div className="profile-head">
            {avatar('avatar lg')}
            <div className="profile-id">
              <div className="profile-name">{name}</div>
              {email && <div className="profile-email">{email}</div>}
            </div>
          </div>
          <div className="profile-sep" />
          <button className="profile-item" role="menuitem"
            onClick={() => { setOpen(false); navigate('/pricing'); }}>
            <Icon name="star" size={16} stroke={1.8} />요금제 관리
          </button>
          <button className="profile-item" role="menuitem"
            onClick={() => { setOpen(false); navigate('/credits/history'); }}>
            <Icon name="coins" size={16} stroke={1.8} />크레딧 사용 내역
          </button>
          <button className="profile-item" role="menuitem"
            onClick={() => { setOpen(false); signOut(); }}>
            <Icon name="logOut" size={16} stroke={1.8} />로그아웃
          </button>
        </div>
      )}
    </div>
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
      const pid = useAppStore.getState().projectId;
      if (!pid) return;   // 콜드 진입(복원 불가) — 가드 대상 아님, 화면 자체가 입력으로 리다이렉트
      const p = await api.getProject(pid);
      if (!cancelled && p?.status === 'done') setBlocked(true);
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
