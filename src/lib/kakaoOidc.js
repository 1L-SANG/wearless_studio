/* =============================================================
   lib/kakaoOidc — 카카오만 Supabase OAuth 가 아니라 **OpenID Connect** 로 로그인한다.

   ## 왜 이 파일이 있나 (지우거나 signInWithOAuth 로 되돌리기 전에 읽어라)

   Supabase GoTrue 는 카카오 authorize 요청의 scope 를 코드에 **하드코딩**해서 붙인다 —
   `account_email profile_image profile_nickname`
   (auth/internal/api/provider/kakao.go, append 이지 replace 가 아니다).
   클라이언트에서 빼는 수단이 없다. 그런데 우리 카카오 앱의 `account_email` 은
   **"권한 없음"** 이다(비즈앱 전환 + 추가기능 심사 3~5영업일). 카카오는 앱에 설정되지 않은
   동의항목이 섞인 인가 요청을 **KOE205** 로 거절한다 → 2026-09-22 카카오 로그인 전원 실패.

   그래서 카카오만 길을 바꾼다:
     로그인 버튼 → kauth authorize(scope=openid) → 우리 콜백 라우트 → 우리 서버가 code 를
     id_token 으로 교환 → supabase.auth.signInWithIdToken({provider:'kakao', token, nonce}).
     ⚠️ 그 nonce 는 **원본**이고, 카카오 인가 요청에는 그 값의 **sha256 해시**가 들어간다.
     같은 값을 양쪽에 쓰면 마지막 단계에서 `Nonces mismatch` 로만 죽는다(sha256Hex 주석).

   GoTrue 의 id_token 경로는 그대로 살아 있다(internal/api/token_oidc.go 가
   `p.Provider === kakao || p.Issuer === 'https://kauth.kakao.com'` 로 분기한다).
   ⇒ **Supabase 대시보드의 Kakao provider 는 계속 Enabled 여야 하고 client_id 도 그대로여야
   한다.** id_token 의 aud 가 그 값과 대조되기 때문이다. 여기 VITE_KAKAO_REST_API_KEY 는
   그 client_id 와 **같은 값**이다.

   **scope 에 account_email 을 넣지 마라.** 그게 KOE205 의 원인이다. 필수 동의 항목
   (profile_nickname·profile_image)은 카카오가 동의 화면에 알아서 포함하므로 따로 적지
   않는다. 심사가 승인되면 그때 scope 에 account_email 을 더하면 된다.

   ## 이 모듈의 규율
   · 모듈 최상위에서 window·document·crypto 를 만지지 않는다. AuthProvider 를 vite SSR 로
     **실제로 실행하는** 테스트가 둘 있다(signup-consent-behavior·legal-pages) — 최상위에서
     브라우저 API 를 건드리면 그 테스트들이 이 변경과 무관해 보이는 이유로 터진다.
   · state·nonce·PKCE verifier 는 sessionStorage + `wl_` 접두사 + 이 모듈의 read/write/clear
     3함수만 지난다(creditReturn.js·signupConsent.js 와 같은 형태). 접근은 전부 try/catch —
     사파리 프라이빗에서는 **읽는 것만으로도 던진다**.
   · 쿠키(authCookieStorage)에 넣지 마라. 그건 Supabase 세션 전용이고 랜딩 레포와 복제 쌍이라
     한쪽만 고치면 세션이 갈라진다. 여기 값들은 같은 탭·같은 오리진 왕복이라 sessionStorage 면 충분하다.
   ============================================================= */

/** 세 앱이 공유하는 콜백 경로. 라우트 3곳·host.js 허용목록·redirect_uri 빌더가 이 상수를 쓴다. */
export const KAKAO_CALLBACK_PATH = '/auth/kakao/callback';

export const KAKAO_AUTHORIZE_URL = 'https://kauth.kakao.com/oauth/authorize';

/* discovery 실측(https://kauth.kakao.com/.well-known/openid-configuration):
   claims_supported 에 nonce 는 있고 **at_hash 는 없다** ⇒ signInWithIdToken 에
   access_token 을 같이 넘길 필요가 없다. openid 가 없으면 id_token 자체가 안 나온다. */
export const KAKAO_SCOPE = 'openid';

/** 카카오 REST API 키(=client_id). 공개값이라 VITE_ 로 내보내도 된다.
    ⚠️ client_secret 은 절대 VITE_ 로 넣지 마라 — 번들에 평문으로 실린다. 교환은 서버가 한다. */
export const KAKAO_REST_API_KEY = import.meta.env?.VITE_KAKAO_REST_API_KEY || '';

/* 롤백 스위치. **기본 OFF** 다 — 켜기 전에 서버(/v1/auth/kakao/token)가 먼저 나가고
   카카오 콘솔에 Redirect URI 4개가 등록돼 있어야 한다. 되돌리기는 Vercel 환경변수 한 줄
   + Redeploy(빌드타임 인라인이라 재배포가 필요하다).
   기존 signInWithOAuth('kakao') 분기는 **지우지 않는다**(AuthProvider.signIn) — 코드를
   지우면 되돌릴 때 다시 만들어야 한다(tossKeys.js 의 기능 스위치와 같은 판단). */
export const KAKAO_OIDC_ENABLED = import.meta.env?.VITE_KAKAO_OIDC_ENABLED === 'true';

const REQUEST_KEY = 'wl_kakaoOidc';

/** 이 경로에서는 AuthProvider 가 `?code=` 를 건드리면 안 된다(AuthProvider 주석 참고). */
export function isKakaoCallbackPath(pathname) {
  return pathname === KAKAO_CALLBACK_PATH;
}

/* ── 탭 저장소 (creditReturn.js·signupConsent.js 와 같은 read/write/clear 3함수) ───── */

export function readKakaoAuthRequest() {
  try {
    const raw = sessionStorage.getItem(REQUEST_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value.state === 'string' ? value : null;
  } catch { return null; }
}

/** 저장에 실패하면 false — 호출부가 로그인을 시작하지 않고 되돌린다(아래 startKakaoLogin). */
export function writeKakaoAuthRequest(request) {
  try {
    sessionStorage.setItem(REQUEST_KEY, JSON.stringify(request));
    return true;
  } catch { return false; }
}

export function clearKakaoAuthRequest() {
  try { sessionStorage.removeItem(REQUEST_KEY); } catch { /* 위와 같다 */ }
}

/* ── 순수 함수들 (node --test 가 그대로 import 해서 단위 테스트한다) ───────────────── */

/**
 * 콜백 URL 의 쿼리에서 필요한 것만 꺼낸다.
 * 카카오가 사용자 취소를 `?error=access_denied` 로 돌려보내므로 error 도 같이 읽는다.
 */
export function readKakaoCallbackParams(search) {
  const params = new URLSearchParams(search || '');
  return {
    code: params.get('code'),
    state: params.get('state'),
    error: params.get('error'),
    errorDescription: params.get('error_description'),
  };
}

/**
 * 저장해 둔 요청을 꺼내 **state 를 대조**하고 지운다. 불일치면 null(= 거부).
 *
 * state 가 맞아야만 이 콜백이 '우리가 시작한 그 로그인'이다. 대조 없이 code 를 교환하면
 * 공격자가 자기 인가코드를 피해자 브라우저에 밀어 넣어 **피해자를 공격자 계정으로
 * 로그인시키는** CSRF 가 된다. 맞든 틀리든 저장값은 지운다 — 일회용이다.
 */
export function consumeKakaoAuthRequest(state) {
  const request = readKakaoAuthRequest();
  clearKakaoAuthRequest();
  if (!request || !state || request.state !== state) return null;
  return request;
}

/**
 * 앱 **안쪽 경로**만 통과시키는 마지막 문(오픈 리다이렉트 방어).
 * facemarketRootTarget.js 의 검사와 같은 항목을 본다 — 제어문자·역슬래시·'//'·인코딩된
 * 슬래시·절대 URL. 여기서 통과한 값만 navigate 한다.
 */
export function safeInternalPath(value) {
  if (typeof value !== 'string') return null;
  const path = value.trim();
  if (path.length === 0 || path.length > 512) return null;
  for (let index = 0; index < path.length; index += 1) {
    const code = path.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return null;
  }
  // 브라우저 URL 파서는 http(s) 에서 역슬래시를 슬래시로 읽는다 — '/\evil.com' 은 '//evil.com'.
  if (path.includes('\\')) return null;
  if (!path.startsWith('/')) return null;
  if (path.startsWith('//')) return null;                 // protocol-relative
  if (/^\/%(?:2f|5c)/i.test(path)) return null;           // 인코딩으로 숨긴 슬래시
  return path;
}

/** authorize URL 조립. 순수 함수라 테스트가 scope·필수 파라미터를 그대로 확인한다. */
export function buildKakaoAuthorizeUrl({ clientId, redirectUri, state, nonce, codeChallenge }) {
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    // 🔴 account_email 을 더하지 마라 — 그게 KOE205 다(머리말).
    scope: KAKAO_SCOPE,
    state,
  });
  /* 🔴 여기 들어가는 nonce 는 **원본이 아니라 sha256 해시**다(hashedNonce 주석 참고).
     해시를 못 만든 브라우저에서는 아예 넣지 않는다 — GoTrue 는 "id_token 에 nonce 가 있으면
     파라미터에도 있어야 하고, 없으면 양쪽 다 없어야 한다"를 강제한다(token_oidc.go 298-299).
     한쪽만 있으면 "Passed nonce and nonce in id_token should either both exist or not." 로 거절된다. */
  if (nonce) params.set('nonce', nonce);
  if (codeChallenge) {
    params.set('code_challenge', codeChallenge);
    params.set('code_challenge_method', 'S256');
  }
  return `${KAKAO_AUTHORIZE_URL}?${params.toString()}`;
}

/* ── 브라우저에서만 도는 부분 ───────────────────────────────────────────────── */

/* 콜백은 로그인을 **시작한 오리진**으로 돌아와야 한다. 프로덕션 3호스트는 `.wearless.kr`
   공유 쿠키라 세션 자체는 나눠 쓰지만, PKCE verifier·nonce·state 와 복귀 목표
   (wl_postLogin)는 sessionStorage = 오리진 한정이다. 한 호스트로 모으면 그 값들을 전부
   공유 쿠키로 옮기고 state 에 복귀 호스트를 실어 화이트리스트를 또 만들어야 한다. */
export function kakaoRedirectUri() {
  return `${window.location.origin}${KAKAO_CALLBACK_PATH}`;
}

function randomHex(byteLength) {
  const webCrypto = globalThis.crypto;
  if (!webCrypto?.getRandomValues) return null;
  const bytes = webCrypto.getRandomValues(new Uint8Array(byteLength));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/* 🔴 nonce 는 **원본을 그대로 카카오에 보내면 안 된다.**

   GoTrue 의 id_token 그랜트는 우리가 준 nonce 를 sha256 해서 id_token 의 nonce 클레임과
   맞춘다(token_oidc.go 301-305):

       hash := fmt.Sprintf("%x", sha256.Sum256([]byte(params.Nonce)))
       if hash != idToken.Nonce { return ... "Nonces mismatch" }

   즉 계약은 **인가 요청에는 sha256 해시를, Supabase 에는 원본을** 보내는 것이다(Apple·구글
   네이티브 로그인과 같은 형태). 양쪽에 같은 값을 보내면 교환까지 다 성공한 뒤 마지막
   signInWithIdToken 에서만 `Nonces mismatch` 로 죽는다 — 2026-09-22 프로덕션에서 그렇게 걸렸다.
   Go 의 `%x` 는 소문자 16진수 64자라 여기서도 같은 표기로 만든다. */
async function sha256Hex(value) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const digest = await subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  } catch { return null; }
}

/* PKCE code_challenge. crypto.subtle 은 **보안 컨텍스트에서만** 산다 — https 와 localhost 는
   되지만 폰 QA 용 LAN IP(http://192.168.x.x)에서는 없다. 그때는 null 을 돌려주고 PKCE 없이
   진행한다(카카오는 PKCE 를 요구하지 않는다). 조용히 죽는 것보다 낫다. */
async function pkceChallenge(verifier) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const digest = await subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    let binary = '';
    for (const byte of new Uint8Array(digest)) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  } catch { return null; }
}

/**
 * 카카오 인가 화면으로 **전체 페이지 이동**한다.
 *
 * `clientId` 는 테스트가 갈아끼우는 이음매다 — node:test 에는 import.meta.env 가 없어
 * 기본값이 빈 문자열이라, 주입점이 없으면 nonce 해시 계약을 단위 테스트할 수 없다.
 *
 * 반환 형태를 `{ error }` 로 맞춘 이유: LoginGate 의 handle() 이 `const { error } =
 * await signIn(provider)` 로 받고, 에러가 없으면 "리다이렉트되어 언마운트될 것"을 전제로
 * 그대로 return 한다(pending 을 일부러 안 내린다). 그 6단계 계약 — 가입 동의 marker 롤백,
 * pending 유지, 레이스 가드 — 을 그대로 물려받으려면 여기서도 같은 모양을 돌려줘야 한다.
 */
export async function startKakaoLogin({ clientId = KAKAO_REST_API_KEY } = {}) {
  try {
    if (!clientId) {
      return { error: new Error('카카오 로그인이 아직 설정되지 않았어요.') };
    }
    const state = randomHex(16);
    const nonce = randomHex(16);
    const codeVerifier = randomHex(32);     // hex 64자 = PKCE 규격(43~128자) 안
    if (!state || !nonce || !codeVerifier) {
      return { error: new Error('이 브라우저에서는 카카오 로그인을 시작할 수 없어요. 다른 브라우저로 시도해 주세요.') };
    }
    const codeChallenge = await pkceChallenge(codeVerifier);
    /* 카카오에 보낼 값은 해시, 우리가 들고 있을 값은 원본이다(sha256Hex 주석).
       해시를 못 만들면(보안 컨텍스트 아님) nonce 를 **양쪽 다** 포기한다 — 한쪽만 있으면
       GoTrue 가 거절한다. state 는 그대로라 CSRF 방어는 남는다. */
    const hashedNonce = await sha256Hex(nonce);
    const redirectUri = kakaoRedirectUri();
    // 저장에 실패하면 **시작하지 않는다.** nonce 가 왕복을 못 넘기면 돌아와서
    // signInWithIdToken 이 어차피 실패하는데, 그때는 이미 인가 화면을 지나온 뒤라
    // 사용자에겐 "로그인했는데 안 된다"로만 보인다.
    const stored = writeKakaoAuthRequest({
      state, redirectUri,
      nonce: hashedNonce ? nonce : null,     // 저장은 **원본**(signInWithIdToken 이 받을 값)
      codeVerifier: codeChallenge ? codeVerifier : null,
    });
    if (!stored) {
      return { error: new Error('브라우저 저장소가 막혀 있어 카카오 로그인을 시작할 수 없어요. 시크릿 모드나 쿠키 차단 설정을 확인해 주세요.') };
    }
    window.location.assign(buildKakaoAuthorizeUrl({
      clientId, redirectUri, state, codeChallenge,
      nonce: hashedNonce,                    // 인가 요청에 실리는 건 **해시**
    }));
    return { error: null };
  } catch (error) {
    clearKakaoAuthRequest();
    return { error };
  }
}
