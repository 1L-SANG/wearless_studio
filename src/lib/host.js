// 도메인 분기 — facemarket.wearless.kr = 모델 등록 전용, ai.wearless.kr = 메인 앱.
// 등록 진입은 facemarket 도메인에서만 열고, ai 프로필 메뉴에서는 숨긴다.
// 로컬/프리뷰 테스트는 ?facemarket=1 쿼리 또는 VITE_FACEMARKET_HOST 로 강제할 수 있다.

function detectFacemarket() {
  if (typeof window === 'undefined') return false;
  try {
    const forced = new URLSearchParams(window.location.search).get('facemarket');
    if (forced === '1') return true;
    if (forced === '0') return false;
  } catch { /* no-op */ }
  const host = (window.location.hostname || '').toLowerCase();
  const override = (import.meta.env?.VITE_FACEMARKET_HOST || '').toLowerCase();
  if (override && host === override) return true;
  return /(^|\.)facemarket\./.test(host);
}

export const IS_FACEMARKET = detectFacemarket();

const matchesRoute = (pathname, route) => pathname === route || pathname.startsWith(`${route}/`);

export function domainRouteRedirect(pathname, isFacemarket = IS_FACEMARKET) {
  if (isFacemarket) {
    const allowed = pathname === '/'
      || ['/model', '/pricing', '/credits/history', '/payments', '/verify']
        .some((route) => matchesRoute(pathname, route));
    return allowed ? null : '/model/register';
  }
  return matchesRoute(pathname, '/model') ? '/create/input' : null;
}
