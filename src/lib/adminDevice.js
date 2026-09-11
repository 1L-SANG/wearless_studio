/* 관리자 콘솔 기기 토큰 — 이 브라우저 프로필이 "어느 기기인가" 를 서버에 말해 주는 값.

   서버가 등록 때 한 번 준 랜덤 문자열을 localStorage 에 쥔다. localStorage 는 오리진 단위라
   admin.wearless.kr 문서에서만 보이고, 셀러·facemarket 앱은 이 값을 읽을 길이 없다(같은 번들
   코드라도 오리진이 다르다). 지우면 새 기기로 취급된다 — 재승인 필요.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6.1

   스토리지를 인자로 받는 이유: node 테스트에서 window 없이 돌리고, 접근이 막힌 환경(사파리
   프라이빗 등)에서 던지는 것을 여기서 삼키기 위해서다. 화면은 토큰이 없을 때의 경로를 어차피
   가지고 있다(등록 화면). */
export const STORAGE_KEY = 'wl.admin.device.v1';
export const DEVICE_HEADER = 'X-Admin-Device';
// http() 가 device_* 403 을 만나면 window 에 쏘는 이벤트. RequireDevice 가 듣고 상태를 다시 본다 —
// 열어 둔 탭에서 회수됐을 때 화면마다 403 처리를 심지 않아도 대기/회수 화면으로 넘어가게.
export const DEVICE_REJECTED_EVENT = 'admin-device-rejected';

function defaultStorage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readDeviceToken(storage = defaultStorage()) {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    const token = typeof raw === 'string' ? raw.trim() : '';
    return token || null;
  } catch {
    return null;
  }
}

export function writeDeviceToken(token, storage = defaultStorage()) {
  const clean = typeof token === 'string' ? token.trim() : '';
  try {
    if (!clean) storage?.removeItem(STORAGE_KEY);
    else storage?.setItem(STORAGE_KEY, clean);
  } catch { /* 스토리지 차단 — 등록 화면이 다시 뜬다 */ }
}

export function clearDeviceToken(storage = defaultStorage()) {
  try {
    storage?.removeItem(STORAGE_KEY);
  } catch { /* no-op */ }
}

/* 서버 label_from_user_agent 와 같은 규칙 — 등록 입력칸의 초기값. 서버도 label 이 비면 같은
   값을 만들므로 둘이 어긋나지 않는다. 순서 주의: iPhone UA 에 'Mac OS X', Android 에 'Linux'. */
export function defaultDeviceLabel(ua = (typeof navigator !== 'undefined' ? navigator.userAgent : '')) {
  const s = ua || '';
  let os = null;
  if (s.includes('iPhone')) os = 'iPhone';
  else if (s.includes('iPad')) os = 'iPad';
  else if (s.includes('Android')) os = 'Android';
  else if (s.includes('Mac OS X') || s.includes('Macintosh')) os = 'macOS';
  else if (s.includes('Windows')) os = 'Windows';
  else if (s.includes('Linux')) os = 'Linux';
  let browser = null;
  if (s.includes('Edg/')) browser = 'Edge';
  else if (s.includes('Firefox/')) browser = 'Firefox';
  else if (s.includes('Chrome/') || s.includes('CriOS/')) browser = 'Chrome';
  else if (s.includes('Safari/')) browser = 'Safari';
  const parts = [os, browser].filter(Boolean);
  return parts.length ? parts.join(' · ') : '알 수 없는 기기';
}
