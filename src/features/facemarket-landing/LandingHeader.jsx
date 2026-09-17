/* =============================================================
   facemarket-landing/LandingHeader.jsx
   랜딩 상단바. 세 항목은 각자 자기 라우트를 갖는다(SPA — 새 페이지처럼 보이되
   문서 재적재 없이 전환).

   ⚠️ 목적지는 **공개 라우트**여야 한다. /model/license·/model/register·/model 로
   직행하게 바꾸지 마라 — 셋 다 RequireAuth 아래라(App.jsx) 비로그인 방문자가 첫
   클릭에 로그인 모달을 맞는다. 설명을 읽기 전에 가입을 요구하는 순서가 되어, 랜딩을
   만든 이유 자체가 없어진다. 인증 라우트로는 각 페이지 끝 CTA 가 보낸다.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { listMyModels } from '@/lib/api/facemarket.js';
import { landingNavAction, landingNavItems } from './facemarketRootTarget.js';
import s from './FacemarketLanding.module.css';

/* 로그인한 확정 모델은 모델 지원 메뉴를 숨겨요.
   보호 메뉴의 로그인 복귀는 facemarketRootTarget에서 판정해요. */
/* CTA 를 인증 부트스트랩 중에 disabled 로 잠그지 않는 건 의도다 — LandingShell 의
   onPrimary 는 그 시간에 눌린 클릭을 보류함(pendingPrimary)에 담았다가 loading 이
   내려가면 한 번 실행한다. 버튼을 잠그면 그 클릭이 아예 안 들어와 보류함이 죽는다. */
export function LandingHeader({ onPrimary, primaryLabel }) {
  const [pendingNav, setPendingNav] = useState(null);
  const { session, loading, openLogin, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [modelState, setModelState] = useState(null);
  const userId = session?.user?.id;
  // 로그인 사용자는 조회가 끝나 미등록으로 확인된 뒤에만 지원 메뉴를 본다.
  const hideApplication = Boolean(loading || (userId && (modelState?.userId !== userId || modelState.verified)));
  const nav = landingNavItems({ verified: hideApplication });
  useEffect(() => {
    if (!userId) { setModelState(null); return undefined; }
    let alive = true;
    listMyModels().then(models => {
      if (alive) setModelState({ userId, verified: models.some(model => ['verified', 'suspended'].includes(model.status)) });
    }).catch(() => { if (alive) setModelState(null); });
    return () => { alive = false; };
  }, [userId]);

  /* 로그아웃은 이 상단바가 유일한 출구다. facemarket 의 /model/* 은 셀러 크롬
     (shell.jsx 의 ProfileMenu)을 쓰지 않으므로, 여기 없으면 로그인한 모델이 세션을
     닫을 방법이 UI 에 하나도 없다.
     착지점을 먼저 '/' 로 옮기고 세션을 끊는다 — 순서를 바꾸면 /model/* 에 선 채로
     세션이 사라져 RequireAuth 가 FacemarketLoginPrompt 를 그리고 그 effect 가 로그인
     모달을 연다(방금 로그아웃한 사람에게 로그인 창). shell.jsx 의 로그아웃과 같은 규율. */
  const handleSignOut = () => {
    navigate('/');
    signOut?.();
  };

  // 부트스트랩 중 보호 메뉴를 누른 의도는 세션 판정 뒤 한 번만 소비한다. 이미 로그인한
  // 사용자로 확인되면 곧장 이동하고, 비로그인이면 같은 목적지를 로그인 복귀 경로로 심는다.
  useEffect(() => {
    if (!pendingNav || loading) return;
    const to = pendingNav;
    setPendingNav(null);
    if (session) navigate(to);
    else openLogin(to);
  }, [loading, navigate, openLogin, pendingNav, session]);

  const applicationActive = (item) => item.to === '/apply' && /^\/model\/apply\/?$/.test(pathname);
  const linkClass = (item) => ({ isActive }) => (isActive || applicationActive(item) ? `${s.navLink} ${s.navLinkActive}` : s.navLink);
  const onNav = (event, item) => {
    const action = landingNavAction(item.to, { session, loading });
    if (action === 'navigate') return;
    event.preventDefault();
    if (action === 'wait') setPendingNav(item.to);
    else openLogin(item.to);
  };

  return (
    <header className={`${s.header} ${primaryLabel && onPrimary ? s.headerWithCta : ''}`}>
      {/* 브랜드는 홈('/') 링크다. 예전엔 같은 문서 안 앵커(#top)였는데, 이제 상단바가
          다른 라우트로 넘어가므로 앵커면 현재 페이지 맨 위로만 가고 홈으로 못 돌아온다. */}
      {/* facemarket 전용 워드마크(2026-09-03 오너 지급 SVG). 공유 로고(/assets/brand/logo.svg)는
          셀러 앱(shell·Login·ChromeLayout)이 그대로 쓰므로 건드리지 않고, 이 헤더만 간다 —
          이 헤더가 facemarket 도메인(랜딩+/model/*)에만 얹히므로 그 경계가 곧 노출 범위다.
          워드마크에 'facemarket' 글자가 포함돼 있어 텍스트 span 은 중복이라 내렸고,
          접근성 이름은 alt 가 승계한다. */}
      <Link className={s.brand} to="/">
        <img alt="FaceMarket" className={s.brandWordmark} src="/assets/brand/facemarket-wordmark.svg" />
      </Link>

      <nav aria-label="랜딩 내비게이션" className={s.nav}>
        {nav.map((item) => (
          <NavLink aria-current={applicationActive(item) ? 'page' : undefined} className={linkClass(item)} key={item.to} onClick={(event) => onNav(event, item)} to={item.to}>
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
          <button className={s.headerQuiet} onClick={handleSignOut} type="button" aria-label="로그아웃">
            <Icon name="logOut" size={16} stroke={1.8} />
            <span className={s.headerQuietLabel}>로그아웃</span>
          </button>
        ) : (
          <button
            aria-label="로그인/회원가입"
            className={`${s.headerQuiet} ${primaryLabel && onPrimary ? s.headerAuthWithCta : ''}`}
            disabled={loading}
            onClick={() => openLogin(`${pathname}${search}`)}
            type="button"
          >
            <span className={s.headerAccountDesktopLabel}>로그인</span>
            <span className={s.headerAccountMobileLabel}>로그인/회원가입</span>
          </button>
        )}
      </div>

    </header>
  );
}
