/* =============================================================
   lib/authCookieStorage — Supabase 세션을 **도메인 공유 쿠키**에 담는 storage 어댑터.

   왜 localStorage 가 아닌가: 세션이 origin 에 갇히기 때문이다. 랜딩(www.wearless.kr)에서
   로그인해도 그 세션은 랜딩 것이고, 스튜디오(ai.wearless.kr)로 넘어가면 로그아웃 상태다.
   PKCE 의 code verifier 도 같은 저장소를 쓰므로, 랜딩에서 시작한 OAuth 를 앱이 교환하는
   것도 불가능하다. `domain=.wearless.kr` 쿠키 하나로 두 문제가 같이 풀린다.

   ▶ 청크: 쿠키 한 장은 4KB 근처가 한계인데 소셜 로그인 세션(access+refresh+user metadata)은
     그보다 커질 수 있다. 그래서 `key.0`, `key.1` … 로 쪼개 담고 읽을 때 이어 붙인다.
     (Supabase 의 SSR 헬퍼가 쓰는 방식과 같다.) 줄어든 청크는 반드시 지운다 — 남으면
     다음 읽기가 옛 조각을 이어 붙여 JSON 이 깨진다.

   ▶ 도메인 판정: wearless.kr 아래일 때만 `.wearless.kr` 을 붙인다. 프리뷰 배포(*.vercel.app)나
     localhost 에서 그 값을 붙이면 브라우저가 **조용히 쿠키를 버려** 로그인이 통째로 죽는다.
     그 경우 호스트 한정 쿠키로 떨어뜨린다 — 공유는 안 되지만 그 환경엔 공유할 상대도 없다.

   ▶ Secure 는 https 에서만 붙인다(로컬 http 에서 붙이면 저장되지 않는다).
     SameSite=Lax 면 www→ai 최상위 내비게이션에 쿠키가 함께 간다.

   ▶ 이 파일은 **랜딩 레포에도 같은 내용으로 있다**(landing-page-fasho/src/lib/auth-cookie-storage.ts).
     둘이 같은 쿠키를 읽고 쓰므로 한쪽만 고치면 세션이 갈라진다.
   ============================================================= */

// 4096 은 name·속성까지 포함한 한계다. 이름(`sb-…-auth-token.0`)과 Path/Domain/Expires 분량을
// 넉넉히 빼고 값에 3200 만 쓴다.
const CHUNK_SIZE = 3200;
const SHARED_DOMAIN = 'wearless.kr';

const hasDocument = () => typeof document !== 'undefined';

function cookieDomain() {
  const host = (typeof location !== 'undefined' && location.hostname) || '';
  if (host === SHARED_DOMAIN || host.endsWith(`.${SHARED_DOMAIN}`)) return `.${SHARED_DOMAIN}`;
  return null; // localhost · 프리뷰 도메인 → 호스트 한정 쿠키
}

function attrs() {
  const domain = cookieDomain();
  const secure = typeof location !== 'undefined' && location.protocol === 'https:';
  return `path=/; SameSite=Lax${domain ? `; domain=${domain}` : ''}${secure ? '; Secure' : ''}`;
}

function readAll() {
  const jar = {};
  if (!hasDocument() || !document.cookie) return jar;
  for (const part of document.cookie.split('; ')) {
    const eq = part.indexOf('=');
    if (eq < 1) continue;
    jar[decodeURIComponent(part.slice(0, eq))] = decodeURIComponent(part.slice(eq + 1));
  }
  return jar;
}

function writeCookie(name, value, maxAgeSeconds) {
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; ${attrs()}; max-age=${maxAgeSeconds}`;
}

function deleteCookie(name) {
  // 만료로 지운다. 속성(domain·path)이 쓸 때와 같아야 지워진다 — 다르면 유령 쿠키가 남아
  // 다음 읽기를 오염시킨다.
  document.cookie = `${encodeURIComponent(name)}=; ${attrs()}; max-age=0`;
}

export const cookieStorage = {
  getItem(key) {
    const jar = readAll();
    if (jar[key] !== undefined) return jar[key];
    // 청크 형식: key.0 부터 끊길 때까지
    if (jar[`${key}.0`] === undefined) return null;
    let out = '';
    for (let i = 0; jar[`${key}.${i}`] !== undefined; i += 1) out += jar[`${key}.${i}`];
    return out;
  },

  setItem(key, value) {
    if (!hasDocument()) return;
    // 1년. Supabase 가 refresh 때마다 다시 쓰므로 실제 수명은 refresh token 이 정한다.
    const maxAge = 365 * 24 * 60 * 60;
    const jar = readAll();
    if (value.length <= CHUNK_SIZE) {
      writeCookie(key, value, maxAge);
      // 단일 쿠키로 줄었으면 남은 청크를 치운다.
      for (let i = 0; jar[`${key}.${i}`] !== undefined; i += 1) deleteCookie(`${key}.${i}`);
      return;
    }
    // 청크로 쓸 때는 단일 쿠키를 치운다(둘 다 있으면 getItem 이 단일 쪽을 먼저 집는다).
    if (jar[key] !== undefined) deleteCookie(key);
    let written = 0;
    for (let i = 0; i * CHUNK_SIZE < value.length; i += 1) {
      writeCookie(`${key}.${i}`, value.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE), maxAge);
      written = i + 1;
    }
    for (let i = written; jar[`${key}.${i}`] !== undefined; i += 1) deleteCookie(`${key}.${i}`);
  },

  removeItem(key) {
    if (!hasDocument()) return;
    const jar = readAll();
    if (jar[key] !== undefined) deleteCookie(key);
    for (let i = 0; jar[`${key}.${i}`] !== undefined; i += 1) deleteCookie(`${key}.${i}`);
  },
};
