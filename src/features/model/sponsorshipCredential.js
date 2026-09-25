/* 협찬 동의 증명서(협찬 VC) 상태 → 화면 문구.

   모델 마이페이지·공개 검증·관리자 상세가 같은 상태를 같은 말로 부르도록 한곳에 모았어요.
   화면(JSX)은 여기서 고른 문구를 그리기만 해요. 상대 경로 import 만 써요 — 테스트가 이 파일을
   vite 없이 node 로 바로 읽어요. */
import { seoulDateKey } from '../../lib/datetime.js';

// 발급 중이면 10초마다, 등록이 끝나길 기다리는 중이면 1분마다 다시 확인해요.
// 등록은 며칠 걸릴 수도 있어서, 화면을 연 지 30분이 지나면 더 묻지 않아요(새로고침하면 다시 봐요).
export const SPONSORSHIP_CREDENTIAL_POLL_MS = 10_000;
export const SPONSORSHIP_CREDENTIAL_SLOW_POLL_MS = 60_000;
export const SPONSORSHIP_CREDENTIAL_POLL_LIMIT_MS = 30 * 60_000;
export const SPONSORSHIP_OFF_NOTICE = '협찬을 끄면 협찬 동의 증명서가 무효 처리돼요.';
export const SPONSORSHIP_UNAVAILABLE_MESSAGE = '지금은 협찬 요청을 받을 수 없는 모델이에요.';

const MODEL_COPY = {
  // 협찬은 켜져 있는데 증서가 아직 없어요(기능을 켜기 전에 협찬을 켠 모델) — 서버가 곧 만들어요.
  none: { tone: 'pending', text: '협찬 동의 증명서를 준비하고 있어요.' },
  waiting_license: { tone: 'waiting', text: '등록이 끝나면 협찬 동의 증명서가 발급돼요.' },
  pending: { tone: 'pending', text: '협찬 동의 증명서를 발급하고 있어요 (1~2분)' },
  active: { tone: 'active', text: '협찬 동의 증명서가 발급됐어요.' },
};

/** 긴 VC id 는 앞뒤만 남겨요. 짧으면 그대로예요. */
export function shortVcId(vcId) {
  if (typeof vcId !== 'string' || !vcId) return '';
  return vcId.length > 22 ? `${vcId.slice(0, 12)}…${vcId.slice(-6)}` : vcId;
}

/** 모델 본인 화면 문구. 기능이 꺼져 있거나 협찬이 꺼져 있으면 null(지금처럼 아무것도 안 그려요). */
export function sponsorshipCredentialCopy(credential) {
  if (!credential || credential.featureEnabled !== true) return null;
  const copy = MODEL_COPY[credential.status];
  if (!copy) return null;
  const vcId = credential.status === 'active' ? shortVcId(credential.vcId) : '';
  return { ...copy, vcId };
}

/** 다음 확인까지 기다릴 ms. 더 확인할 필요가 없으면 null.
    sponsorshipEnabled: 모델이 협찬을 켜 둔 상태인지(꺼져 있으면 none 은 끝난 상태예요). */
export function sponsorshipCredentialPollDelay(credential, { sponsorshipEnabled = false, elapsedMs = 0 } = {}) {
  if (credential?.featureEnabled !== true || elapsedMs >= SPONSORSHIP_CREDENTIAL_POLL_LIMIT_MS) return null;
  if (credential.status === 'pending') return SPONSORSHIP_CREDENTIAL_POLL_MS;
  if (credential.status === 'waiting_license') return SPONSORSHIP_CREDENTIAL_SLOW_POLL_MS;
  if (credential.status === 'none' && sponsorshipEnabled) return SPONSORSHIP_CREDENTIAL_POLL_MS;
  return null;
}

export function shouldPollSponsorshipCredential(credential, options) {
  return sponsorshipCredentialPollDelay(credential, options) !== null;
}

/** 끄기 전에 무효 처리 안내를 보여줄지. 이미 발급됐거나 발급 중일 때만이에요. */
export function sponsorshipOffNeedsNotice(credential) {
  return credential?.featureEnabled === true
    && (credential.status === 'active' || credential.status === 'pending');
}

/** 셀러 협찬 요청: 유효한 증서가 없는 모델은 서버가 404 로 답해요. */
export function sponsorshipInterestErrorMessage(error, fallback) {
  if (error?.status === 404) return SPONSORSHIP_UNAVAILABLE_MESSAGE;
  return error?.message || fallback;
}

/** 공개 검증 페이지 한 줄. 개인정보 없이 동의 여부·동의일(KST)만 보여줘요. */
export function verifySponsorshipLine(sponsorship) {
  if (!sponsorship || sponsorship.active !== true) return { active: false, text: '협찬 동의 없음', vcId: null };
  // 서버가 KST 날짜(YYYY-MM-DD)로 잘라서 보내요. 옛 응답(consentedAt 시각)도 읽어요.
  const day = typeof sponsorship.consentedOn === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(sponsorship.consentedOn)
    ? sponsorship.consentedOn
    : seoulDateKey(sponsorship.consentedAt, '');
  return {
    active: true,
    text: `협찬 동의 · 증명서 유효${day ? ` · 동의일 ${day} (KST)` : ''}`,
    vcId: typeof sponsorship.vcId === 'string' && sponsorship.vcId ? sponsorship.vcId : null,
  };
}

const ADMIN_LABEL = { waiting_license: '발급 대기', pending: '발급 중', active: '유효', revoked: '무효' };

/** 관리자 모델 상세의 한 줄. 증서가 없으면 null. */
export function adminSponsorshipCredentialLine(credential) {
  if (!credential) return null;
  const parts = [ADMIN_LABEL[credential.status] || credential.status || '알 수 없음'];
  if (credential.vcId) parts.push(shortVcId(credential.vcId));
  if (credential.lastErrorCode) parts.push(`마지막 오류 ${credential.lastErrorCode}`);
  if (credential.attempts > 0 && credential.status !== 'active') parts.push(`시도 ${credential.attempts}회`);
  return parts.join(' · ');
}
