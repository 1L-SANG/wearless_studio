/* =============================================================
   facemarket-landing/LandingHeader.jsx
   랜딩 상단바. 세 항목은 라우트가 아니라 같은 페이지 섹션 앵커다 —
   /model/* 은 전부 인증이 필요해서, 라우트로 걸면 첫 클릭이 곧바로
   로그인 모달이 된다(설명을 읽기 전에 가입을 요구하는 순서).
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import s from './FacemarketLanding.module.css';

const NAV = [
  { id: 'licensing', label: '라이선싱' },
  { id: 'register', label: '모델 등록' },
  { id: 'model-info', label: '모델 정보' },
];

/* CSS 의 `@media (min-width: 48rem)` 과 같은 폭이어야 한다 — 그 폭에서 햄버거가
   사라지므로, 같은 지점에서 메뉴 상태도 접어야 '열린 채 닫을 수 없는' 상태가 안 생긴다. */
const DESKTOP_QUERY = '(min-width: 48rem)';

/* 앵커 스크롤도 캐러셀과 같은 사용자 설정을 따른다. '모델 정보'는 히어로+캐러셀+
   라이선싱 아래라 스크롤 거리가 수천 px 인데, 그 전체가 애니메이션으로 흘러가는 건
   '동작 줄이기'를 켠 사람이 정확히 막으려는 종류의 모션이다. 캐러셀만 조용하고
   내비게이션만 움직이면 같은 기능 안에서 처리가 어긋난다(CarouselStage 의
   usePrefersReducedMotion 과 같은 판단 — 여기는 클릭 때 한 번 읽으면 되는 자리라
   훅이 아니다. 설정은 페이지를 연 뒤에도 바뀔 수 있으므로 매번 읽는다). */
function scrollToSection(id) {
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false;
  document.getElementById(id)?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
}

/* CTA 를 인증 부트스트랩 중에 disabled 로 잠그지 않는 건 의도다 — FacemarketLanding 의
   onPrimary 는 그 시간에 눌린 클릭을 보류함(pendingPrimary)에 담았다가 loading 이
   내려가면 한 번 실행한다. 버튼을 잠그면 그 클릭이 아예 안 들어와 보류함이 죽는다. */
export function LandingHeader({ onPrimary, primaryLabel }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const headerRef = useRef(null);

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

  const go = (id) => {
    setMenuOpen(false);
    scrollToSection(id);
  };

  return (
    <header className={s.header} ref={headerRef}>
      <a className={s.brand} href="#top">
        <img alt="" className={s.brandLogo} src="/assets/brand/logo.svg" />
        <span className={s.brandName}>FaceMarket</span>
      </a>

      <nav aria-label="랜딩 섹션" className={s.nav}>
        {NAV.map((item) => (
          <button className={s.navLink} key={item.id} onClick={() => go(item.id)} type="button">
            {item.label}
          </button>
        ))}
      </nav>

      <div className={s.headerActions}>
        <button className={s.headerCta} onClick={onPrimary} type="button">
          {primaryLabel}
          <Icon name="arrowRight" size={16} stroke={2} />
        </button>
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
            <button className={s.navLink} key={item.id} onClick={() => go(item.id)} type="button">
              {item.label}
            </button>
          ))}
        </nav>
      )}
    </header>
  );
}
