import { isMobileDevice } from './deviceClass.js';

const KEY = 'wl_mobileGuestSave';
const OWNER_KEY = 'wl_mobileGuestDraftOwner';
const TTL_MS = 30 * 60 * 1000;
const CLOCK_SKEW_MS = 5 * 60 * 1000;
let memorySave = null;
const draftOwners = new Map();

// 저장 의도를 지워도 사진을 처음 받은 계정은 초안을 지울 때까지 유지한다.
export function getMobileGuestDraftOwner(updatedAt) {
  try {
    for (const [version, userId] of JSON.parse(localStorage.getItem(OWNER_KEY)) || []) {
      if (!draftOwners.has(version)) draftOwners.set(version, userId);
    }
  } catch { /* 저장소 차단 */ }
  return draftOwners.get(updatedAt) || null;
}

export function clearMobileGuestDraftOwner() {
  draftOwners.clear();
  try { localStorage.removeItem(OWNER_KEY); } catch { /* 저장소 차단 */ }
}

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

// 잠금이나 실패로 돌아와도 같은 저장 의도를 자동 실행하지 않는다.
export function claimMobileGuestSave(intent) {
  if (!intent?.id || intent.saveStarted) return false;
  let stored;
  try { stored = JSON.parse(localStorage.getItem(KEY)); } catch { /* 저장소 차단 */ }
  if ((stored?.id === intent.id && stored.saveStarted)
      || (memorySave?.intent.id === intent.id && memorySave.intent.saveStarted)) return false;
  intent.saveStarted = true;
  if (memorySave?.intent.id === intent.id) memorySave.intent.saveStarted = true;
  if (stored?.id === intent.id) {
    try { localStorage.setItem(KEY, JSON.stringify({ ...stored, saveStarted: true })); } catch { /* 메모리 실행 기록은 유지 */ }
  }
  return true;
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
  const owner = getMobileGuestDraftOwner(intent.draftUpdatedAt);
  if (intent.excludedUserId === user.id || (intent.userId && intent.userId !== user.id)
      || (owner && owner !== user.id)) {
    try { sessionStorage.removeItem('wl_draftPending'); } catch { /* 저장소 차단 */ }
    return null;
  }
  if (!intent.userId && !(signedInAt >= intent.createdAt - CLOCK_SKEW_MS)) return null;
  if (!owner) {
    draftOwners.set(intent.draftUpdatedAt, user.id);
    try { localStorage.setItem(OWNER_KEY, JSON.stringify([...draftOwners])); } catch { /* 메모리 귀속은 유지 */ }
  }
  if (memorySave?.intent.id === intent.id && memorySave.userId !== user.id) {
    memorySave.userId = user.id;
    if (memorySave.draft) memorySave.draft = memorySave.draft.then((draft) => {
      if (!draft.userId) draft.userId = user.id;
      return draft;
    });
  }
  if (!intent.userId) {
    intent.userId = user.id;
    if (memorySave?.intent.id === intent.id) memorySave.intent = intent;
    try { localStorage.setItem(KEY, JSON.stringify(intent)); } catch { /* 같은 문서의 메모리는 유지 */ }
  }
  return intent;
}
