/* =============================================================
   facemarket-landing/LandingHeader.jsx
   랜딩 상단바. 세 항목은 각자 자기 라우트를 갖는다(SPA — 새 페이지처럼 보이되
   문서 재적재 없이 전환).

   ⚠️ 목적지는 **공개 라우트**여야 한다. /model/license·/model/register·/model 로
   직행하게 바꾸지 마라 — 셋 다 RequireAuth 아래라(App.jsx) 비로그인 방문자가 첫
   클릭에 로그인 모달을 맞는다. 설명을 읽기 전에 가입을 요구하는 순서가 되어, 랜딩을
   만든 이유 자체가 없어진다. 인증 라우트로는 각 페이지 끝 CTA 가 보낸다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import s from './FacemarketLanding.module.css';

const NAV = [
  { to: '/licensing', label: '라이선싱' },
  { to: '/register', label: '모델 등록' },
  { to: '/model-info', label: '모델 정보' },
];

/* CSS 의 `@media (min-width: 48rem)` 과 같은 폭이어야 한다 — 그 폭에서 햄버거가
   사라지므로, 같은 지점에서 메뉴 상태도 접어야 '열린 채 닫을 수 없는' 상태가 안 생긴다. */
const DESKTOP_QUERY = '(min-width: 48rem)';

/* CTA 를 인증 부트스트랩 중에 disabled 로 잠그지 않는 건 의도다 — LandingShell 의
   onPrimary 는 그 시간에 눌린 클릭을 보류함(pendingPrimary)에 담았다가 loading 이
   내려가면 한 번 실행한다. 버튼을 잠그면 그 클릭이 아예 안 들어와 보류함이 죽는다. */
export function LandingHeader({ onPrimary, primaryLabel }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const headerRef = useRef(null);
  const { session, signOut } = useAuth();
  const navigate = useNavigate();

  /* 로그아웃은 이 상단바가 유일한 출구다. facemarket 의 /model/* 은 셀러 크롬
     (shell.jsx 의 ProfileMenu)을 쓰지 않으므로, 여기 없으면 로그인한 모델이 세션을
     닫을 방법이 UI 에 하나도 없다.
     착지점을 먼저 '/' 로 옮기고 세션을 끊는다 — 순서를 바꾸면 /model/* 에 선 채로
     세션이 사라져 RequireAuth 가 FacemarketLoginPrompt 를 그리고 그 effect 가 로그인
     모달을 연다(방금 로그아웃한 사람에게 로그인 창). shell.jsx 의 로그아웃과 같은 규율. */
  const handleSignOut = () => {
    setMenuOpen(false);
    navigate('/');
    signOut?.();
  };

  // 메뉴를 닫는 길을 세 개 둔다. 토글 버튼만으로는 갇히는 경우가 있었다:
  // (1) 데스크톱 폭이 되면 햄버거가 display:none 이라 누를 대상 자체가 없어진다,
  // (2) 바깥을 눌러도 안 닫혀 스크롤하면 헤더와 함께 화면 밖으로 열린 채 흘러간다,
  // (3) 그동안 aria-expanded 는 계속 true 라 스크린리더에 '열림'으로 남는다.
  // 셸의 ProfileMenu(shell/shell.jsx)와 같은 방식이다.
  useEffect(() => {
    if (!menuOpen) return undefined;

    const close = () => setMenuOpen(false);
    const onDoc = (e) => { if (headerRef.current && !headerRef.current.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    // 데스크톱 폭으로 넘어가는 순간에만 접는다(반대 방향은 햄버거가 그대로 있으니 둔다).
    const onDesktop = (e) => { if (e.matches) close(); };
    const desktop = window.matchMedia(DESKTOP_QUERY);

    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    desktop.addEventListener('change', onDesktop);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      desktop.removeEventListener('change', onDesktop);
    };
  }, [menuOpen]);

  // 라우트가 바뀌면 모바일 메뉴는 닫힌다 — 링크를 눌러 페이지가 넘어갔는데 드롭다운이
  // 새 페이지 위에 그대로 떠 있으면 안 된다.
  const closeMenu = () => setMenuOpen(false);
  const linkClass = ({ isActive }) => (isActive ? `${s.navLink} ${s.navLinkActive}` : s.navLink);

  return (
    <header className={s.header} ref={headerRef}>
      {/* 브랜드는 홈('/') 링크다. 예전엔 같은 문서 안 앵커(#top)였는데, 이제 상단바가
          다른 라우트로 넘어가므로 앵커면 현재 페이지 맨 위로만 가고 홈으로 못 돌아온다. */}
      <Link className={s.brand} onClick={closeMenu} to="/">
        <img alt="" className={s.brandLogo} src="/assets/brand/logo.svg" />
        <span className={s.brandName}>FaceMarket</span>
      </Link>

      <nav aria-label="랜딩 내비게이션" className={s.nav}>
        {NAV.map((item) => (
          <NavLink className={linkClass} key={item.to} onClick={closeMenu} to={item.to}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className={s.headerActions}>
        {/* CTA 는 선택이다. 등록 위저드(/model/register)처럼 **이미 그 CTA 의 목적지에 서
            있는** 화면에서는 상단바에 같은 버튼을 또 두지 않는다 — 누르면 자기 자신으로
            가는 버튼이라 아무 일도 안 일어나고, KYC 진행 중에 다른 데로 튀는 것처럼 보인다. */}
        {primaryLabel && onPrimary ? (
          <button className={s.headerCta} onClick={onPrimary} type="button">
            {primaryLabel}
            <Icon name="arrowRight" size={16} stroke={2} />
          </button>
        ) : null}
        {session ? (
          <button className={s.headerQuiet} onClick={handleSignOut} type="button">
            <Icon name="logOut" size={16} stroke={1.8} />
            로그아웃
          </button>
        ) : null}
        <button
          aria-expanded={menuOpen}
          aria-label={menuOpen ? '메뉴 닫기' : '메뉴 열기'}
          className={s.menuButton}
          onClick={() => setMenuOpen((open) => !open)}
          type="button"
        >
          <Icon name={menuOpen ? 'x' : 'listBullet'} size={22} stroke={2} />
        </button>
      </div>

      {menuOpen && (
        <nav aria-label="모바일 메뉴" className={s.mobileNav}>
          {NAV.map((item) => (
            <NavLink className={linkClass} key={item.to} onClick={closeMenu} to={item.to}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      )}
    </header>
  );
}
