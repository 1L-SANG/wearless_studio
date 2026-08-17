/* =============================================================
   shell/shell.jsx — app chrome: TopNav, Stepper, PageHead, WizardCTA, Media
   Ported verbatim from reference/prototype/components/shell.jsx.
   Data seam: TopNav reads account from the store and navigates via
   React Router (prototype used props + a single App state machine).
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Icon, Modal, Button, useToast } from '@/components/ui.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { hasEditorEntered } from '@/lib/editorEntered.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { WIZARD_STEPS, STEP_INDEX } from '@/lib/wizardSteps.js';
import { flushProductDraftSave } from '@/lib/draftStore.js';
import { recordCreditReturn } from '@/lib/creditReturn.js';
import { draftSlot } from '@/lib/draftSlot.js';

draftSlot.configure(api);

export { WIZARD_STEPS, STEP_INDEX } from '@/lib/wizardSteps.js';

const STEPPER_STEPS = ['input', 'mannequin', 'storyboard'];

export function TopNav() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { session, openLogin } = useAuth();
  const toast = useToast();
  const account = useAppStore((s) => s.account) || { name: '…', avatar: '', credits: 0, plan: '' };
  const beginProject = useAppStore((s) => s.beginProject);
  const inputPromotionLocked = useAppStore((s) => s.inputPromotionLocked);
  const [resumeAsk, setResumeAsk] = useState(false);
  // create 흐름일 때만 'create' 활성 — /pricing·/credits 등은 어느 탭도 활성 아님(폴백 active 버그 수정)
  const route = pathname.startsWith('/library') ? 'library'
    : pathname.startsWith('/create') ? 'create' : null;
  // '새로 만들기' = 숨은 슬롯과 로컬 플로우를 비운 뒤 입력 화면. 보관함 project는 확정 때 생성한다.
  const startNew = async () => {
    if (inputPromotionLocked) return;
    setResumeAsk(false);
    try {
      await draftSlot.removeForNewFlow();
    } catch (error) {
      toast.push(error?.message || '이전 작업을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
      return;
    }
    await beginProject();
    navigate('/create/input');
  };
  // '이어서 작업' = 마지막으로 머문 create/editor 경로로 복귀(없으면 콘티 단계). 생성이 도는 동안
  // 사용자가 있어야 할 곳도 콘티라 강제 이동 없이 이 폴백 하나로 자연스럽게 돌아간다.
  const resumeWork = () => { setResumeAsk(false); navigate(useAppStore.getState().resumePath || '/create/storyboard'); };
  const onNav = async (r) => {
    if (inputPromotionLocked) return;
    if (r === 'create') {
      if (pathname === '/create/input') return;
      // 진행 중 프로젝트가 있으면 '이어서/새로' 를 물어 매번 새로 초기화돼 작업이 버려지던 문제를 막는다.
      const { projectId, projectPersisted } = useAppStore.getState();
      if (projectPersisted && projectId) { setResumeAsk(true); return; }
      navigate('/create/input');
      return;
    }
    navigate('/library');
  };
  const step = pathname.startsWith('/create/') ? pathname.split('/')[2] : null;
  const openPricing = () => {
    const { projectId } = useAppStore.getState();
    recordCreditReturn({ projectId, path: pathname });
    navigate('/pricing');
  };
  const openTopNavLogin = async () => {
    try {
      await flushProductDraftSave();
      openLogin(pathname === '/create/input' ? pathname : '/create/input');
    } catch (error) {
      toast.push(error?.message || '입력한 내용을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    }
  };

  return (
    <>
    <nav className="topnav">
      <span className="brand">
        <img className="brand-logo" src="/assets/brand/logo.svg" alt="" />
        <img className="brand-wordmark" src="/assets/brand/wordmark.png" alt="Wearless" />
      </span>
      <div className="nav-links">
        {/* 비로그인 숨김: 보관함/제작 탭은 로그인 사용자용. 비로그인 입력·분석은 '/' 공개 진입. */}
        {session && <button disabled={inputPromotionLocked} className={`nav-link${route === 'create' ? ' active' : ''}`} onClick={() => onNav('create')}>상세페이지 제작</button>}
        {session && <button disabled={inputPromotionLocked} className={`nav-link${route === 'library' ? ' active' : ''}`} onClick={() => onNav('library')}>보관함</button>}
      </div>
      {STEPPER_STEPS.includes(step) && <div className="nav-stepper"><Stepper current={step} /></div>}
      <div className="nav-right">
        {session ? (
          <>
            <button type="button" className="credit-badge" onClick={openPricing} title="요금제·크레딧 충전"><Icon name="coins" size={15} stroke={1.8} />크레딧 <b>{account.credits}</b></button>
            {account.plan && <span className="plan-badge">{account.plan}</span>}
            <ProfileMenu />
          </>
        ) : (
          <button className="nav-login" onClick={openTopNavLogin}>로그인</button>
        )}
      </div>
    </nav>
    {resumeAsk && <ResumeChoiceModal onResume={resumeWork} onNew={startNew} onClose={() => setResumeAsk(false)} />}
    </>
  );
}

/* 진행 중 상세페이지 제작이 있을 때 '상세페이지 제작' 재진입 시 — 이어서 작업 / 새로 만들기 선택.
   과거엔 무조건 새로 초기화돼 진행 중 작업이 버려졌다(이어서 재개 경로 없음). */
export function ResumeChoiceModal({ onResume, onNew, onClose, sources = null, onChoose }) {
  if (sources?.length) {
    return (
      <Modal onClose={onClose}>
        <h3>하던 작업이 있어요</h3>
        <p>이어서 열 내용을 고르거나, 새로 시작할 수 있어요.</p>
        <div className="draft-entry-sources">
          {sources.map((source) => (
            <button type="button" className="draft-entry-source" key={source.id}
              onClick={() => onChoose(source.id)}>
              <span>{source.title}</span>
              <small>{source.description}</small>
              {source.photosPending && <em>사진 몇 장은 아직 저장 중이라 빠져 있을 수 있어요.</em>}
            </button>
          ))}
        </div>
        <div className="modal-actions">
          <Button variant="ghost" onClick={onNew}>새로 시작하기</Button>
        </div>
      </Modal>
    );
  }
  return (
    <Modal onClose={onClose}>
      <h3>이어서 작업할까요?</h3>
      <p>만들던 상세페이지가 있어요. 이어서 하거나 새로 시작할 수 있어요.</p>
      <div className="modal-actions">
        <Button variant="ghost" onClick={onNew}>새로 시작하기</Button>
        <Button variant="primary" onClick={onResume}>이어서 하기</Button>
      </div>
    </Modal>
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
export function WizardCTA({ children, className = '' }) {
  return <div className={`wizard-cta${className ? ` ${className}` : ''}`}>{children}</div>;
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
      try {
        await useAppStore.getState().loadProject();
        const pid = useAppStore.getState().projectId;
        if (!pid) return;   // 콜드 진입(복원 불가) — 가드 대상 아님, 화면 자체가 입력으로 리다이렉트
        // 편집을 시작한 프로젝트도 막는다 — 생성이 실패·차단으로 끝나면 status 가 done 이
        // 아니어서 뒤로가기로 그대로 들어가진다(오너 8/15). 단 프로젝트가 실제로 열리는지
        // 먼저 확인한 뒤 막는다: 사라진 프로젝트를 막으면 에디터도 못 열려 입력 화면과
        // 무한히 왕복하게 된다(가드가 사용자를 가두는 최악).
        const p = await api.getProject(pid);
        // **그 프로젝트가 맞는지**까지 확인한다. mock 은 요청한 id 를 무시하고 현재 초안을
        // 돌려주므로, 새로고침으로 초안이 새로 깔린 개발 모드에서 옛 표식만 보고 막으면
        // 모달 → 에디터 → id 불일치 → 입력 화면으로 되튀는 왕복이 된다(2026-08-17 리뷰).
        const sameProject = p?.id === pid;
        if (!cancelled && sameProject && (p.status === 'done' || hasEditorEntered(pid))) setBlocked(true);
      } catch { /* 보호 가드 조회 실패는 공개 입력 화면을 막지 않는다. */ }
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
      <p>편집을 시작한 상세페이지예요. 여기서 되돌아가면 이미 만든 사진과 편집이 덮여요. 필요한 컷은 에디터에서 추가하거나 다시 만들 수 있어요.</p>
      <div className="modal-actions"><Button variant="primary" onClick={go}>에디터로 이동</Button></div>
    </Modal>
  );
}

/* a media placeholder image that lazy-fills */
export function Media({ src, alt, style, className, ratio }) {
  return <img src={src} alt={alt || ''} className={`media ${className || ''}`}
    style={{ aspectRatio: ratio, width: '100%', ...style }} loading="lazy" />;
}
