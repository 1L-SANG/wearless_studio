/* =============================================================
   facemarket-landing/facemarketRootTarget.js
   로그인 복귀 경로(sessionStorage 'wl_postLogin')를 어디로 해석할지.

   원래 이 소비는 RootRedirect(App.jsx)가 했다. facemarket 루트를 랜딩이
   가져가면서 그 계약을 여기가 이어받는다 — 안 그러면 "모델 등록 시작"에서
   로그인한 사용자가 등록 위저드가 아니라 랜딩으로 돌아온다.

   두 겹으로 거른다.
   1) 앱 밖으로 새는 값 차단(오픈 리다이렉트).
   2) facemarket 도메인에서 **의미 있는 화면**만 통과(화이트리스트).
      2번이 왜 필요한지: 플래그를 심는 쪽에는 도메인 가드가 없다 —
      shell.jsx 의 TopNav 로그인은 '/create/input' 을, ProductInput 은
      '/create/storyboard' 를, Editor 는 '/editor/:id' 를 심는다. 그 TopNav 는
      facemarket 에서도 렌더되므로(ChromeLayout), 통과시키면 모델 등록 전용
      도메인에 셀러 스튜디오가 열린다. 랜딩 도입 전 RootRedirect 는
      `if (IS_FACEMARKET) { setDest('/model/register'); return; }` 로 복귀 의도를
      통째로 무시해서 이 문이 아예 없었다 — 그 봉쇄를 여기서 이어받는다.
   ============================================================= */

/* 실제로 심는 값은 '/model/register' 같은 짧은 앱 내 경로다(AuthProvider.openLogin).
   그보다 훨씬 긴 값은 우리가 심은 게 아니다 — 판정할 것 없이 버린다. */
const MAX_LENGTH = 512;

/* facemarket 도메인에 존재하는 화면의 뿌리. '/model' 은 지원서·등록·라이선스·발급이고,
   '/status' 는 등록 상태(옛 허브, 로그인 프롬프트가 여기로 복귀시킨다), '/verify' 는 QR
   공개 검증이다. 그 밖은(=셀러 스튜디오) 이 도메인의 화면이 아니다. */
const ALLOWED_ROOTS = ['/model', '/status', '/verify'];

/* 개행·탭 같은 제어문자는 URL 파서가 조용히 지운다. 탭을 끼운 "/[탭]/evil.com" 처럼
   지우고 나면 다른 경로가 되는 값을 통과시키면, 우리가 검사한 문자열과 브라우저가
   실제로 이동하는 경로가 달라진다. */
function hasControlCharacter(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/* 화이트리스트는 쿼리·해시를 뺀 경로에만 건다 — '/model/register?step=2' 는 정상값이다. */
function pathnameOf(path) {
  const cut = path.search(/[?#]/);
  return cut === -1 ? path : path.slice(0, cut);
}

/* '/model' 자신과 '/model/...' 만 통과. '/models-evil' 처럼 접두사만 겹치는 값은 막는다. */
function isAllowedScreen(pathname) {
  return ALLOWED_ROOTS.some((root) => pathname === root || pathname.startsWith(`${root}/`));
}

export function facemarketRootTarget(returnIntent) {
  if (typeof returnIntent !== 'string') return null;

  const path = returnIntent.trim();
  // 앱 안 절대경로만 받는다. sessionStorage 는 같은 오리진의 다른 스크립트도 쓸 수 있어서,
  // 외부 URL 이 들어오면 로그인 복귀가 그대로 열린 리다이렉트가 된다.
  if (path.length === 0 || path.length > MAX_LENGTH) return null;
  if (hasControlCharacter(path)) return null;
  // 브라우저 URL 파서는 http(s) 에서 역슬래시를 슬래시로 읽는다. '/\evil.com' 을 그냥 두면
  // 파서 눈에는 '//evil.com' — 프로토콜 상대 URL 이다. 앱 경로에 역슬래시는 없으니 통째로 막는다.
  if (path.includes('\\')) return null;
  if (!path.startsWith('/')) return null;
  if (path.startsWith('//')) return null;   // protocol-relative
  // '/%2f%2fevil.com' — 인코딩으로 슬래시를 숨긴 값. 지금 라우터는 이걸 디코드하지 않아
  // 그대로도 앱 밖으로 새지 않지만, 중간에 한 번이라도 디코드하는 층이 끼면 위의 '//' 가 된다.
  if (/^\/%(?:2f|5c)/i.test(path)) return null;
  if (path === '/') return null;            // 자기 자신 — 랜딩을 그린다
  // 여기서 걸리는 값(예: '/create/input')은 공격이 아니라 셀러 도메인의 정상 플래그다.
  // 버리고 랜딩을 그리는 게 맞다 — 등록하러 온 사람에게 상품 입력 화면을 열지 않는다.
  if (!isAllowedScreen(pathnameOf(path))) return null;

  return path;
}
