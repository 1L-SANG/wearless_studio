import { isMobileDevice } from './deviceClass.js';

const KEY = 'wl_mobileGuestSave';
const TTL_MS = 30 * 60 * 1000;
const CLOCK_SKEW_MS = 5 * 60 * 1000;
let memorySave = null;

// 사진은 IndexedDB에만 둔다. 다른 탭에는 저장 의도와 첫 복귀 계정만 전달한다.
export function rememberMobileGuestSave(draftUpdatedAt, excludedUserId = null, createdAt = Date.now(), draft = null) {
  const id = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  const intent = { id, createdAt, draftUpdatedAt, userId: null, excludedUserId, ...(draft ? { memoryOnly: true } : {}) };
  memorySave = { intent, draft, persisted: false };
  try { localStorage.setItem(KEY, JSON.stringify(intent)); memorySave.persisted = true; } catch { /* 저장소 차단 */ }
  return intent;
}

export function clearMobileGuestSave(expectedId) {
  if (!expectedId || memorySave?.intent.id === expectedId) memorySave = null;
  try {
    const intent = JSON.parse(localStorage.getItem(KEY));
    if (!expectedId || intent?.id === expectedId) localStorage.removeItem(KEY);
  } catch { /* 저장소 차단 */ }
}

export function getMobileGuestDraft(intent) {
  return memorySave?.intent.id === intent?.id ? memorySave.draft : null;
}

export function getMobileGuestSave(session, { mobile = isMobileDevice(), now = Date.now() } = {}) {
  if (!mobile) return null;
  let intent;
  let readable = true;
  try { intent = JSON.parse(localStorage.getItem(KEY)); } catch { readable = false; }
  if (!intent && readable && memorySave?.persisted) { memorySave = null; return null; }
  intent ||= memorySave?.intent;
  if (!intent) return null;
  if (!intent.id || !intent.draftUpdatedAt || !Number.isFinite(intent.createdAt)
      || now < intent.createdAt || now - intent.createdAt >= TTL_MS) {
    clearMobileGuestSave();
    return null;
  }
  // 리다이렉트로 문서가 바뀌면 메모리 사진은 없다. 묵은 로컬 사본을 대신 열지 않는다.
  if (intent.memoryOnly && !getMobileGuestDraft(intent)) {
    try { sessionStorage.removeItem('wl_draftPending'); } catch { /* 저장소 차단 */ }
    return session?.user ? null : intent;
  }
  const user = session?.user;
  if (!user) return intent;
  // 이미 로그인돼 있던 다른 탭의 계정이나, 첫 복귀 뒤 바뀐 계정에는 넘기지 않는다.
  const signedInAt = Date.parse(user.last_sign_in_at);
  if (intent.excludedUserId === user.id || (intent.userId && intent.userId !== user.id)) {
    try { sessionStorage.removeItem('wl_draftPending'); } catch { /* 저장소 차단 */ }
    return null;
  }
  if (!intent.userId && !(signedInAt >= intent.createdAt - CLOCK_SKEW_MS)) return null;
  if (!intent.userId) {
    intent.userId = user.id;
    if (memorySave?.intent.id === intent.id) memorySave.intent = intent;
    try { localStorage.setItem(KEY, JSON.stringify(intent)); } catch { /* 같은 문서의 메모리는 유지 */ }
  }
  return intent;
}
