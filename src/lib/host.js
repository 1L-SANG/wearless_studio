// 도메인 분기 — facemarket.wearless.kr = 모델 등록 전용, ai.wearless.kr = 메인 앱.
// 등록 진입은 facemarket 도메인에서만 열고, ai 프로필 메뉴에서는 숨긴다.
// 로컬/프리뷰 테스트는 ?facemarket=1 쿼리 또는 VITE_FACEMARKET_HOST 로 강제할 수 있다.

const OVERRIDE_KEY = 'wl_facemarketOverride';

/* 쿼리 강제(?facemarket=1)는 **한 번 보면 그 탭에서 기억한다.**
   안 그러면 로컬에서 로그인이 통째로 깨진다: OAuth 복귀지가 window.location.origin
   (AuthProvider)이라 쿼리스트링이 붙지 않고, IS_FACEMARKET 은 모듈 로드 시 한 번만
   계산되는 상수다. 그래서 로그인하고 돌아온 문서는 facemarket 이 아닌 것으로 판정돼
   셀러 크롬(TopNav·크레딧·오로라 배경)이 뜨고 /model/* 이 ChromeLayout 아래로 붙는다.
   같은 이유로 CTA 로 이동한 뒤 새로고침해도 플래그가 사라진다.

   prod 는 호스트명으로 갈리므로 이 저장소는 쳐다보지도 않는다 — 아래 순서상
   쿼리 → 저장값 → 호스트 순인데, 저장값은 로컬/프리뷰에서만 심긴다.
   ?facemarket=0 으로 명시적으로 끄면 저장값도 함께 지운다(빠져나갈 길). */
function readOverride() {
  try { return sessionStorage.getItem(OVERRIDE_KEY); } catch { return null; }
}

function writeOverride(value) {
  try {
    if (value === null) sessionStorage.removeItem(OVERRIDE_KEY);
    else sessionStorage.setItem(OVERRIDE_KEY, value);
  } catch { /* 저장소가 막힌 브라우저 — 쿼리가 있는 동안만 동작한다 */ }
}

function detectFacemarket() {
  if (typeof window === 'undefined') return false;

  let forced = null;
  try {
    forced = new URLSearchParams(window.location.search).get('facemarket');
  } catch { /* no-op */ }

  if (forced === '1') { writeOverride('1'); return true; }
  if (forced === '0') { writeOverride(null); return false; }

  // 쿼리가 없으면 이 탭이 기억한 값을 쓴다(위 주석의 OAuth 복귀·새로고침 경로).
  if (readOverride() === '1') return true;

  const host = (window.location.hostname || '').toLowerCase();
  const override = (import.meta.env?.VITE_FACEMARKET_HOST || '').toLowerCase();
  if (override && host === override) return true;
  return /(^|\.)facemarket\./.test(host);
}

export const IS_FACEMARKET = detectFacemarket();
