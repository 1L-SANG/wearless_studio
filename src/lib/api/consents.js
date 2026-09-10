/* =============================================================
   lib/api/consents — 셀러 약관·처리방침 동의(첫 로그인 1회 + 개정 시 재동의).
   서버가 "이 버전에 동의했는가"를 기억하므로 로그인 화면은 체크박스를 두지 않는다.
   응답: { required: {terms, privacy}, accepted: {...}|null, needsConsent: bool }
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';

export function getSellerConsent({ signal, expectedUserId } = {}) {
  return http('/v1/me/consents', { signal, expectedUserId });
}

/* 게이트가 보여준 버전을 그대로 보낸다 — 서버가 현재 버전과 대조(불일치면 409). */
export function acceptSellerConsent({ termsVersion, privacyVersion, signal, expectedUserId }) {
  return http('/v1/me/consents', {
    method: 'POST',
    signal,
    expectedUserId,
    body: { termsVersion, privacyVersion, ageAttested: true },
  });
}
