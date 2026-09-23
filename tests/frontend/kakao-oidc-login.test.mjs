/* 카카오 OpenID Connect 로그인 — 회귀 가드.

   여기서 지키는 것:
     ① authorize 요청의 scope 에 **account_email 이 절대 안 들어간다**(그게 KOE205 다).
     ② state 가 안 맞으면 콜백이 거부한다(CSRF: 남의 인가코드로 로그인시키기).
     ③ 로그인 복귀는 **앱 안쪽 경로만** 통과한다(오픈 리다이렉트).
     ④ AuthProvider 가 콜백 경로의 `?code=` 를 지우지 않는다.
     ⑤ 플래그는 기본 OFF 이고, 기존 signInWithOAuth 경로가 롤백용으로 살아 있다.
     ⑥ 콜백 라우트가 세 앱 모두에서 인증 가드 밖·catch-all 앞에 있다.
     ⑦ nonce 는 **카카오에 해시, Supabase 에 원본**을 보낸다(같은 값이면 Nonces mismatch).

   순수 함수는 lib 모듈을 그대로 import 해 확인하고(host-route-boundary 와 같은 방식),
   배선(라우트 위치·플래그 분기)은 소스 텍스트로 확인한다(login-local-password 와 같은 방식).
*/
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  KAKAO_CALLBACK_PATH,
  KAKAO_OIDC_ENABLED,
  KAKAO_SCOPE,
  buildKakaoAuthorizeUrl,
  consumeKakaoAuthRequest,
  startKakaoLogin,
  isKakaoCallbackPath,
  readKakaoCallbackParams,
  safeInternalPath,
  writeKakaoAuthRequest,
} from '../../src/lib/kakaoOidc.js';
import { runKakaoCallbackFlow } from '../../src/lib/kakaoCallbackFlow.js';
import { hasFreshSignupConsent, markSignupConsent } from '../../src/lib/signupConsent.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

/* 주석을 걷어낸 코드만 본다. 이 레포는 '왜' 를 주석으로 길게 남기는 곳이라, 금지어 검사를
   원문에 그대로 걸면 **설명하는 문장이 위반으로 잡힌다**(실제로 'wl_postLogin 을 직접 읽지
   마라'는 주석과 'client_secret 은 서버에만 있다'는 주석이 걸렸다). 그러면 다음 사람은
   테스트를 통과시키려고 설명을 지우게 된다 — 정확히 반대 방향이다. */
const code = (path) => read(path)
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

/* sessionStorage 는 node 에 없다. 이 모듈의 모든 접근이 try/catch 라 없어도 안 터지지만,
   저장·소비 계약을 보려면 최소 구현이 필요하다. */
function withSessionStorage(run) {
  const previous = globalThis.sessionStorage;
  const memory = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, String(value)),
    removeItem: (key) => memory.delete(key),
  };
  try { return run(memory); } finally { globalThis.sessionStorage = previous; }
}

/* ── ① scope — 되돌리면 로그인이 전원 죽는다 ─────────────────────────────────── */

test('authorize 요청은 openid 만 요구한다 — account_email 이 KOE205 의 원인이다', () => {
  const url = new URL(buildKakaoAuthorizeUrl({
    clientId: 'rest-key', redirectUri: 'https://ai.wearless.kr/auth/kakao/callback',
    state: 's', nonce: 'n', codeChallenge: 'c',
  }));
  assert.equal(url.origin + url.pathname, 'https://kauth.kakao.com/oauth/authorize');
  assert.equal(url.searchParams.get('scope'), 'openid');
  assert.equal(KAKAO_SCOPE, 'openid');
  assert.doesNotMatch(url.search, /account_email/);
  assert.equal(url.searchParams.get('response_type'), 'code');
  assert.equal(url.searchParams.get('client_id'), 'rest-key');
  assert.equal(url.searchParams.get('nonce'), 'n');
  assert.equal(url.searchParams.get('code_challenge'), 'c');
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
});

test('crypto.subtle 이 없는 환경(폰 QA LAN IP)에서는 PKCE 없이 나간다', () => {
  const url = new URL(buildKakaoAuthorizeUrl({
    clientId: 'k', redirectUri: 'https://ai.wearless.kr/auth/kakao/callback',
    state: 's', nonce: 'n', codeChallenge: null,
  }));
  assert.equal(url.searchParams.has('code_challenge'), false);
  assert.equal(url.searchParams.has('code_challenge_method'), false);
  assert.equal(url.searchParams.get('scope'), 'openid');
});

/* ── ② state 대조 ─────────────────────────────────────────────────────────── */

test('콜백은 state 가 맞을 때만 요청을 돌려준다', () => {
  withSessionStorage(() => {
    writeKakaoAuthRequest({ state: 'real', nonce: 'n', redirectUri: 'r', codeVerifier: 'v' });
    const request = consumeKakaoAuthRequest('real');
    assert.equal(request.nonce, 'n');
    // 일회용 — 같은 state 로 두 번은 안 된다(StrictMode 재실행·뒤로가기).
    assert.equal(consumeKakaoAuthRequest('real'), null);
  });
});

test('state 불일치·누락은 거부하고 저장값도 버린다', () => {
  withSessionStorage((memory) => {
    writeKakaoAuthRequest({ state: 'real', nonce: 'n', redirectUri: 'r', codeVerifier: null });
    assert.equal(consumeKakaoAuthRequest('forged'), null);
    // 대조 실패한 요청은 남겨 두지 않는다 — 남기면 공격자가 타이밍을 바꿔 재시도한다.
    assert.equal(memory.size, 0);
  });
  withSessionStorage(() => {
    writeKakaoAuthRequest({ state: 'real', nonce: 'n', redirectUri: 'r', codeVerifier: null });
    assert.equal(consumeKakaoAuthRequest(null), null);
  });
  // 저장된 요청 자체가 없으면(다른 탭·저장소 비움) 역시 거부.
  withSessionStorage(() => assert.equal(consumeKakaoAuthRequest('anything'), null));
});

test('콜백 쿼리 파싱은 취소(error)도 읽는다', () => {
  assert.deepEqual(
    readKakaoCallbackParams('?code=abc&state=xyz'),
    { code: 'abc', state: 'xyz', error: null, errorDescription: null },
  );
  const cancelled = readKakaoCallbackParams('?error=access_denied&error_description=User+denied');
  assert.equal(cancelled.error, 'access_denied');
  assert.equal(cancelled.code, null);
});

/* ── ③ 오픈 리다이렉트 ────────────────────────────────────────────────────── */

test('복귀 목적지는 앱 안쪽 경로만 통과한다', () => {
  assert.equal(safeInternalPath('/'), '/');
  assert.equal(safeInternalPath('/model/register?step=2'), '/model/register?step=2');
  for (const evil of [
    'https://evil.example/', '//evil.example', '/\\evil.example', '\\\\evil.example',
    '/%2f%2fevil.example', '/%5c%5cevil.example', 'evil.example', '',
    '/ok\tevil', '/ok\nevil', `/${'x'.repeat(600)}`, null, undefined, 42,
  ]) {
    assert.equal(safeInternalPath(evil), null, JSON.stringify(evil));
  }
});

/* ── ④ AuthProvider 가 콜백의 code 를 지우지 않는다 ────────────────────────── */

const AUTH = read('src/features/auth/AuthProvider.jsx');

test('AuthProvider 는 카카오 콜백 경로에서 ?code= 를 건드리지 않는다', () => {
  // 부트스트랩이 경로 불문 code 를 집어 exchangeCodeForSession 을 돌리고 finally 에서
  // 주소창의 code 를 지운다. 결제 경로를 제외한 것과 **같은 방식**으로 카카오 콜백도 뺀다.
  assert.match(AUTH, /isKakaoCallbackPath\(\)\s*\|\|\s*isPaymentResultPath\(\)/);
  // 기존 회귀 가드(subscription.test.mjs)가 보는 모양도 그대로여야 한다.
  assert.match(AUTH, /isPaymentResultPath\(\)\s*\?\s*null/);
  // SSR 하네스가 노드에서 이 코드를 돌린다 — window 가드 없이 pathname 을 읽으면 터진다.
  assert.match(AUTH, /function isKakaoCallbackPath\(\)\s*\{\s*\n\s*if \(typeof window === 'undefined'\) return false;/);
});

/* ── ⑤ 플래그와 롤백 경로 ─────────────────────────────────────────────────── */

test('플래그는 기본 OFF 다', () => {
  // env 미설정(테스트 실행 환경 그대로) = OFF. 켜는 건 배포 순서를 밟은 뒤다.
  assert.equal(KAKAO_OIDC_ENABLED, false);
  assert.match(read('src/lib/kakaoOidc.js'), /VITE_KAKAO_OIDC_ENABLED === 'true'/);
});

test('기존 supabase OAuth 경로는 롤백용으로 살아 있고 구글은 손대지 않았다', () => {
  assert.match(AUTH, /if \(provider === 'kakao' && KAKAO_OIDC_ENABLED\) return startKakaoLogin\(\);/);
  // 플래그를 끄면 그대로 돌아갈 자리. 지우면 되돌릴 때 다시 만들어야 한다.
  assert.match(AUTH, /supabase\.auth\.signInWithOAuth\(\{/);
  assert.match(AUTH, /redirectTo: window\.location\.origin/);
  // 구글 전용 분기가 새로 생기지 않았는지(= 구글은 종전 경로 그대로).
  assert.doesNotMatch(AUTH, /provider === 'google'/);
});

test('LoginGate 는 손대지 않는다 — 분기는 AuthProvider 안에서만 일어난다', () => {
  const login = read('src/features/auth/Login.jsx');
  // 카카오 버튼은 여전히 같은 handle() → signIn('kakao') 를 지난다. 그래야 pending 유지·
  // 가입 동의 marker 롤백·레이스 가드(6단계 계약)를 그대로 물려받는다.
  assert.match(login, /onClick=\{\(\) => handle\('kakao'\)\}/);
  assert.doesNotMatch(login, /supabase\.auth\./);
  assert.doesNotMatch(login, /kakaoOidc/);
});

/* ── ⑥ 라우트 배선 ───────────────────────────────────────────────────────── */

test('콜백 경로 상수는 한 곳에서만 나온다', () => {
  assert.equal(KAKAO_CALLBACK_PATH, '/auth/kakao/callback');
  assert.equal(isKakaoCallbackPath('/auth/kakao/callback'), true);
  assert.equal(isKakaoCallbackPath('/auth/kakao/callback/'), false);
  assert.equal(isKakaoCallbackPath('/'), false);
});

test('세 앱 모두 콜백 라우트를 인증 가드 밖·catch-all 앞에 둔다', () => {
  const ROUTE = '<Route path="auth/kakao/callback" element={<KakaoCallback />} />';
  for (const [app, catchAll] of [
    ['src/apps/seller/App.jsx', '<Route path="*" element={<NotFound brand="WEARLESS" />} />'],
    ['src/apps/facemarket/App.jsx', '<Route path="*" element={<NotFound brand="FACEMARKET"'],
    ['src/apps/admin/App.jsx', '<Route path="*" element={<Navigate to="/" replace />} />'],
  ]) {
    const src = read(app);
    const route = src.indexOf(ROUTE);
    assert.ok(route !== -1, `${app}: 콜백 라우트 없음 — 카카오 복귀가 catch-all 로 떨어진다`);
    assert.ok(route < src.indexOf(catchAll), `${app}: catch-all 보다 뒤에 있다`);
    assert.match(src, /import \{ KakaoCallback \} from '@\/features\/auth\/KakaoCallback\.jsx';/);
  }
  // admin 은 모든 화면이 RequireAuth 안이라 특히 위험하다 — 가드 블록이 닫힌 뒤여야 한다.
  const admin = read('src/apps/admin/App.jsx');
  assert.ok(admin.indexOf('</Route>') < admin.indexOf(ROUTE),
    'admin: 콜백이 RequireAuth 블록 안에 있으면 로그인 프롬프트가 다시 뜬다');
});

test('콜백은 wl_postLogin 을 직접 읽지 않고 기존 소비자에게 맡긴다', () => {
  const callback = read('src/features/auth/KakaoCallback.jsx');
  // 여기서 직접 읽어 navigate 하면 facemarketRootTarget 화이트리스트와 draft→콘티 승격을
  // 통째로 우회한다. '/' 로 보내면 RootRedirect·FacemarketRoot 가 그대로 처리한다.
  assert.doesNotMatch(code('src/features/auth/KakaoCallback.jsx'),
    /readPostLogin|wl_postLogin|forgetPostLogin/);
  assert.match(callback, /navigate\(safeInternalPath\(POST_LOGIN_DESTINATION\) \|\| '\/', \{ replace: true \}\)/);
  // 인가코드는 일회용 — StrictMode 이중 마운트를 모듈 스코프 싱글플라이트로 접는다.
  assert.match(callback, /let pendingPromise = null;/);
});

test('클라이언트 시크릿은 프론트에 없다 — 교환은 서버가 한다', () => {
  for (const path of ['src/lib/kakaoOidc.js', 'src/lib/kakaoCallbackFlow.js',
    'src/features/auth/KakaoCallback.jsx', 'src/features/auth/AuthProvider.jsx']) {
    assert.doesNotMatch(code(path), /VITE_KAKAO_CLIENT_SECRET|client_secret/,
      `${path}: 카카오 시크릿이 번들에 실린다`);
  }
});

/* ── ⑦ 콜백 흐름 — 실제로 실행해서 확인한다 ────────────────────────────────────
   판단 로직을 lib/kakaoCallbackFlow.js 로 빼 둔 덕에 React·vite SSR 하네스 없이
   그냥 import 해서 돌릴 수 있다. http·signInWithKakaoIdToken 은 인자로 들어온다. */

function flowHarness({ stored = null, http, signIn } = {}) {
  const calls = { http: [], signIn: [] };
  const run = (search) => withSessionStorageAsync(async () => {
    if (stored) writeKakaoAuthRequest(stored);
    return runKakaoCallbackFlow({
      search,
      http: async (path, options) => {
        calls.http.push({ path, options });
        return http ? http(path, options) : { id_token: 'id-token-1' };
      },
      signInWithKakaoIdToken: async (args) => {
        calls.signIn.push(args);
        return signIn ? signIn(args) : { error: null };
      },
    });
  });
  return { calls, run };
}

async function withSessionStorageAsync(run) {
  const previous = globalThis.sessionStorage;
  const memory = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, String(value)),
    removeItem: (key) => memory.delete(key),
  };
  try { return await run(memory); } finally { globalThis.sessionStorage = previous; }
}

const STORED = { state: 'st', nonce: 'no', redirectUri: 'https://ai.wearless.kr/auth/kakao/callback', codeVerifier: 'ver' };

test('정상 흐름 — 본문으로 교환하고 nonce 와 함께 세션을 만든다', async () => {
  const h = flowHarness({ stored: STORED });
  const result = await h.run('?code=abc&state=st');
  assert.deepEqual(result, { ok: true });
  assert.equal(h.calls.http.length, 1);
  const [{ path, options }] = h.calls.http;
  assert.equal(path, '/v1/auth/kakao/token');
  assert.equal(options.method, 'POST');
  assert.equal(options.requireAuth, false);   // 공개 라우트 — 로그인 전이라 세션이 없다
  assert.deepEqual(options.body, {
    code: 'abc', redirectUri: STORED.redirectUri, codeVerifier: 'ver',
  });
  // nonce 를 빠뜨리면 GoTrue 가 nonce 클레임 대조에서 거절한다.
  assert.deepEqual(h.calls.signIn, [{ token: 'id-token-1', nonce: 'no' }]);
});

test('PKCE 없이 시작했으면 codeVerifier 를 보내지 않는다', async () => {
  const h = flowHarness({ stored: { ...STORED, codeVerifier: null } });
  await h.run('?code=abc&state=st');
  assert.equal('codeVerifier' in h.calls.http[0].options.body, false);
});

test('state 가 안 맞으면 교환을 **시도조차** 하지 않는다', async () => {
  const h = flowHarness({ stored: STORED });
  const result = await h.run('?code=abc&state=forged');
  assert.equal(result.ok, false);
  assert.equal(result.code, 'state_mismatch');
  assert.deepEqual(h.calls.http, [], '남의 인가코드를 우리 키로 교환해 주면 안 된다');
  assert.deepEqual(h.calls.signIn, []);
});

test('사용자가 취소하면(access_denied) 친절한 문구를 돌려준다', async () => {
  const h = flowHarness({ stored: STORED });
  const result = await h.run('?error=access_denied&error_description=User+denied');
  assert.equal(result.ok, false);
  assert.equal(result.code, 'access_denied');
  assert.match(result.message, /취소했어요/);
  assert.deepEqual(h.calls.http, []);
});

test('code·state 가 없으면(직접 방문) 그대로 안내한다', async () => {
  const h = flowHarness({ stored: STORED });
  const result = await h.run('');
  assert.equal(result.code, 'missing_code');
  assert.deepEqual(h.calls.http, []);
});

test('서버 에러 봉투의 한국어 message 를 그대로 보여준다', async () => {
  const rejected = Object.assign(new Error('카카오 로그인이 아직 설정되지 않았어요.'),
    { code: 'kakao_login_not_configured', status: 503 });
  const h = flowHarness({ stored: STORED, http: () => { throw rejected; } });
  const result = await h.run('?code=abc&state=st');
  assert.equal(result.code, 'kakao_login_not_configured');
  assert.equal(result.message, '카카오 로그인이 아직 설정되지 않았어요.');
  assert.deepEqual(h.calls.signIn, []);
});

test('id_token 이 없으면 세션을 만들려 들지 않는다', async () => {
  const h = flowHarness({ stored: STORED, http: () => ({}) });
  const result = await h.run('?code=abc&state=st');
  assert.equal(result.code, 'id_token_missing');
  assert.deepEqual(h.calls.signIn, []);
});

test('signInWithIdToken 거절은 provider 설정을 의심하게 만드는 문구로 돌려준다', async () => {
  const h = flowHarness({ stored: STORED, signIn: () => ({ error: new Error('nope') }) });
  const result = await h.run('?code=abc&state=st');
  assert.equal(result.code, 'id_token_rejected');
  assert.match(result.message, /로그인하지 못했어요/);
});

/* 실패한 카카오 시도가 '가입 동의 표시'를 남기면, 30분 TTL 안의 다음 로그인(심지어 다른
   계정)이 그 동의를 소비해 서버에 기록한다. 전체 페이지 이동으로 돌아오는 이 경로는
   LoginGate 가 언마운트된 뒤라 그쪽의 pageshow 롤백이 안 돈다 — 여기서 버려야 한다. */
test('실패한 시도는 가입 동의 표시를 버리고, 성공은 남긴다', async () => {
  await withSessionStorageAsync(async () => {
    markSignupConsent();
    writeKakaoAuthRequest(STORED);
    await runKakaoCallbackFlow({
      search: '?error=access_denied', http: async () => ({}), signInWithKakaoIdToken: async () => ({}),
    });
    assert.equal(hasFreshSignupConsent(), false, '취소한 로그인의 동의가 남았다');
  });
  await withSessionStorageAsync(async () => {
    markSignupConsent();
    writeKakaoAuthRequest(STORED);
    const result = await runKakaoCallbackFlow({
      search: '?code=abc&state=st',
      http: async () => ({ id_token: 't' }),
      signInWithKakaoIdToken: async () => ({ error: null }),
    });
    assert.equal(result.ok, true);
    // 성공 경로에서는 남긴다 — SignupCompletion 이 그걸 보고 서버에 동의를 기록한다.
    assert.equal(hasFreshSignupConsent(), true);
  });
});

/* ── ⑦ nonce 해시 계약 — 이걸 어기면 마지막 한 걸음에서만 죽는다 ─────────────────
   GoTrue 는 우리가 준 nonce 를 sha256 해서 id_token 의 nonce 클레임과 맞춘다
   (token_oidc.go 301-305: `hash := fmt.Sprintf("%x", sha256.Sum256([]byte(params.Nonce)))`).
   그래서 **인가 요청에는 해시, signInWithIdToken 에는 원본**을 보내야 한다. 양쪽에 같은 값을
   보내면 KOE205 도 통과하고 토큰 교환도 성공한 뒤 마지막 단계에서만 `Nonces mismatch` 로
   죽는다 — 2026-09-22 프로덕션에서 실제로 그렇게 걸렸다. */

/* sessionStorage 스텁은 동기 버전이 이미 있지만(withSessionStorage) 여기서는 await 가
   필요하다 — 동기 버전의 finally 는 프라미스를 기다리지 않고 스텁을 걷어간다. */
async function withAsyncSessionStorage(run) {
  const previous = globalThis.sessionStorage;
  const memory = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, String(value)),
    removeItem: (key) => memory.delete(key),
  };
  try { return await run(memory); } finally { globalThis.sessionStorage = previous; }
}

/** startKakaoLogin 을 돌리고 인가 화면으로 넘긴 URL 들을 돌려준다. */
async function startAndCapture() {
  const previous = globalThis.window;
  const assigned = [];
  globalThis.window = {
    location: { origin: 'https://ai.wearless.kr', assign: (url) => assigned.push(url) },
  };
  try {
    // clientId 를 주입한다 — node 에는 import.meta.env 가 없어 기본값이 빈 문자열이다.
    const result = await startKakaoLogin({ clientId: 'rest-key' });
    return { assigned, result };
  } finally { globalThis.window = previous; }
}

const sha256Hex = async (value) => {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
};

test('인가 요청의 nonce 는 저장한 원본의 sha256 해시다', async () => {
  await withAsyncSessionStorage(async () => {
    const { assigned, result } = await startAndCapture();
    assert.equal(result.error, null);
    assert.equal(assigned.length, 1, '인가 화면으로 한 번 이동한다');
    const sent = new URL(assigned[0]).searchParams.get('nonce');
    const stored = JSON.parse(globalThis.sessionStorage.getItem('wl_kakaoOidc'));

    assert.ok(stored.nonce, '원본 nonce 를 저장해 둔다 — signInWithIdToken 이 받을 값이다');
    assert.notEqual(sent, stored.nonce, '같은 값을 보내면 GoTrue 가 Nonces mismatch 로 거절한다');
    assert.equal(sent, await sha256Hex(stored.nonce));
    assert.match(sent, /^[0-9a-f]{64}$/, 'Go 의 %x 와 같은 소문자 16진수 64자');
  });
});

test('해시를 못 만들면 nonce 를 양쪽 다 포기한다 — 한쪽만 있으면 GoTrue 가 거절한다', async () => {
  const realCrypto = globalThis.crypto;
  // 보안 컨텍스트가 아닌 브라우저(LAN IP QA)에는 crypto.subtle 이 없다.
  Object.defineProperty(globalThis, 'crypto', {
    value: { getRandomValues: realCrypto.getRandomValues.bind(realCrypto) },
    configurable: true,
  });
  try {
    await withAsyncSessionStorage(async () => {
      const { assigned } = await startAndCapture();
      const url = new URL(assigned[0]);
      assert.equal(url.searchParams.get('nonce'), null, '인가 요청에 nonce 가 없다');
      const stored = JSON.parse(globalThis.sessionStorage.getItem('wl_kakaoOidc'));
      assert.equal(stored.nonce, null, '저장에도 없다 — 콜백이 signInWithIdToken 에 안 싣는다');
    });
  } finally {
    Object.defineProperty(globalThis, 'crypto', { value: realCrypto, configurable: true });
  }
});

test('nonce 없이 시작했으면 signInWithIdToken 에도 nonce 를 싣지 않는다', async () => {
  await withAsyncSessionStorage(async () => {
    writeKakaoAuthRequest({ state: 'st', nonce: null, redirectUri: STORED.redirectUri, codeVerifier: null });
    let seen = null;
    const result = await runKakaoCallbackFlow({
      search: '?code=abc&state=st',
      http: async () => ({ id_token: 'id-token-9' }),
      signInWithKakaoIdToken: async (payload) => { seen = payload; return { error: null }; },
    });
    assert.equal(result.ok, true);
    assert.equal('nonce' in seen, false, 'nonce 키 자체가 없어야 한다');
  });
});

test('buildKakaoAuthorizeUrl 은 nonce 가 없으면 파라미터를 아예 안 붙인다', () => {
  const url = new URL(buildKakaoAuthorizeUrl({
    clientId: 'k', redirectUri: 'https://ai.wearless.kr/auth/kakao/callback',
    state: 's', nonce: null, codeChallenge: null,
  }));
  assert.equal(url.searchParams.get('nonce'), null);
  assert.equal(url.searchParams.get('state'), 's');
});
