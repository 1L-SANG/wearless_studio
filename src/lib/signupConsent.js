/* =============================================================
   lib/signupConsent — 회원가입 탭에서 받은 동의를 OAuth 왕복 너머로 넘긴다.

   가입 동의는 소셜 버튼을 누르기 **전에** 받지만, 그 순간 우리에겐 사용자가 없다
   (계정은 OAuth 복귀 후에 생긴다). 그래서 "이 사람은 방금 가입 탭에서 동의했다"는
   사실만 탭 저장소에 남기고, 복귀 후 SignupCompletion 이 서버에 실제 기록을 남긴다.
   같은 탭 안에서만 살아 있고(sessionStorage), 오래된 흔적은 무시한다 — 브라우저를
   닫았다 다시 열거나 30분이 지나면 가입 완료 화면에서 다시 확인한다.
   'wl_postLogin'(AuthProvider) 과 같은 방식·같은 수명 가정이다.
   ============================================================= */
const KEY = 'wl_signupConsent';
const TTL_MS = 30 * 60 * 1000;

export function markSignupConsent() {
  try { sessionStorage.setItem(KEY, String(Date.now())); } catch { /* 사파리 프라이빗 등 */ }
}

/** 지우지 않고 확인만 한다 — 서버 기록에 성공한 뒤에 clearSignupConsent 로 지운다. */
export function hasFreshSignupConsent() {
  try {
    const at = Number(sessionStorage.getItem(KEY));
    return Boolean(at) && Date.now() - at < TTL_MS;
  } catch { return false; }
}

export function clearSignupConsent() {
  try { sessionStorage.removeItem(KEY); } catch { /* 위와 같다 */ }
}
