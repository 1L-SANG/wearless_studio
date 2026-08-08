import { clearInitialGenerationRequested } from './initialGenerationSession.js';

const KEY_PREFIX = 'wl_generation_relevant_edits:';
const ATTEMPT_KEY_PREFIX = 'wl_generation_relevant_edits_attempt:';
let lastRevision = Date.now();
let lastAttemptId = 0;

function key(projectId) {
  return `${KEY_PREFIX}${projectId}`;
}

function attemptKey(projectId) {
  return `${ATTEMPT_KEY_PREFIX}${projectId}`;
}

function browserSessionStorage() {
  return typeof sessionStorage === 'undefined' ? null : sessionStorage;
}

function createAttemptId() {
  lastAttemptId += 1;
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${lastAttemptId}`;
}

export function createGenerationRelevantEditsSession({
  storage,
  clearInitialRequested = clearInitialGenerationRequested,
  nextAttemptId = createAttemptId,
} = {}) {
  const getStorage = () => (storage === undefined ? browserSessionStorage() : storage);
  // sessionStorage가 차단된 브라우저에서도 현재 마운트의 Zustand dirty는 동작해야 한다.
  // 메모리 미러는 새로고침 영속을 대신하지 않고, 저장소 접근 실패 시 현재 탭의 fallback만 맡는다.
  const memoryRevisions = new Map();
  const memoryAttempts = new Map();
  const readRevision = (projectId) => {
    if (!projectId) return null;
    try {
      const stored = getStorage()?.getItem(key(projectId));
      if (stored != null) {
        memoryRevisions.set(projectId, stored);
        return stored;
      }
    } catch { /* use in-memory fallback */ }
    return memoryRevisions.get(projectId) || null;
  };
  const readAttempt = (projectId) => {
    if (!projectId) return null;
    try {
      const stored = JSON.parse(getStorage()?.getItem(attemptKey(projectId)) || 'null');
      if (stored?.revision) {
        memoryAttempts.set(projectId, stored);
        return stored;
      }
    } catch { /* use in-memory fallback */ }
    return memoryAttempts.get(projectId) || null;
  };
  const read = (projectId) => readRevision(projectId) != null;
  const mark = (projectId) => {
    // 분석을 고친 순간, 현재 컷은 더 이상 "이번 최초 생성의 최신 결과"가 아니다.
    // projectId 가 아직 없는 게스트 흐름은 두 함수의 기존 가드가 그대로 흡수한다.
    clearInitialRequested(projectId);
    if (projectId) {
      const current = Number(readRevision(projectId)) || 0;
      lastRevision = Math.max(Date.now(), lastRevision + 1, current + 1);
      const revision = String(lastRevision);
      memoryRevisions.set(projectId, revision);
      memoryAttempts.delete(projectId);
      try {
        const targetStorage = getStorage();
        targetStorage?.setItem(key(projectId), revision);
        targetStorage?.removeItem(attemptKey(projectId));
      } catch { /* sessionStorage unavailable */ }
    }
    return true;
  };
  const clear = (projectId, expectedRevision) => {
    if (!projectId) return false;
    if (expectedRevision != null && readRevision(projectId) !== expectedRevision) return false;
    memoryRevisions.delete(projectId);
    memoryAttempts.delete(projectId);
    try {
      const targetStorage = getStorage();
      targetStorage.removeItem(key(projectId));
      targetStorage.removeItem(attemptKey(projectId));
    } catch { /* in-memory state was still consumed */ }
    return true;
  };
  const markAttempt = (projectId, expectedRevision, baseline) => {
    if (!projectId || !expectedRevision || !baseline) return false;
    if (readRevision(projectId) !== expectedRevision) return false;
    const existing = readAttempt(projectId);
    if (existing?.revision === expectedRevision && existing.idempotencyKey) {
      return existing.idempotencyKey;
    }
    const attempt = {
      revision: expectedRevision,
      idempotencyKey: `generation-edit-${expectedRevision}-${nextAttemptId()}`,
      ids: [...(baseline.ids || [])],
      maxVersion: Number(baseline.maxVersion) || 0,
    };
    memoryAttempts.set(projectId, attempt);
    try {
      const targetStorage = getStorage();
      targetStorage?.setItem(attemptKey(projectId), JSON.stringify(attempt));
    } catch { /* in-memory fallback */ }
    return attempt.idempotencyKey;
  };
  const clearAttempt = (projectId, expectedRevision) => {
    if (!projectId || readRevision(projectId) !== expectedRevision) return false;
    memoryAttempts.delete(projectId);
    try { getStorage()?.removeItem(attemptKey(projectId)); } catch { /* in-memory fallback */ }
    return true;
  };
  const landedAttemptRevision = (projectId, cuts) => {
    if (!projectId) return null;
    const attempt = readAttempt(projectId);
    if (!attempt?.revision || readRevision(projectId) !== attempt.revision) return null;
    const ids = new Set(attempt.ids || []);
    const maxVersion = Number(attempt.maxVersion) || 0;
    const landed = (cuts || []).some((cut) => (
      !ids.has(cut.id) || (Number(cut.version) || 0) > maxVersion
    ));
    return landed ? attempt.revision : null;
  };

  return {
    read,
    readRevision,
    mark,
    clear,
    markAttempt,
    clearAttempt,
    landedAttemptRevision,
    adopt: (projectId, { preserveDirty = false } = {}) => (
      preserveDirty ? mark(projectId) : read(projectId)
    ),
  };
}

const generationRelevantEditsSession = createGenerationRelevantEditsSession();

export const readGenerationRelevantEdits = (projectId) => generationRelevantEditsSession.read(projectId);
export const readGenerationRelevantEditsRevision = (projectId) => (
  generationRelevantEditsSession.readRevision(projectId)
);
export const markGenerationRelevantEdits = (projectId) => generationRelevantEditsSession.mark(projectId);
export const clearGenerationRelevantEdits = (projectId, expectedRevision) => (
  generationRelevantEditsSession.clear(projectId, expectedRevision)
);
export const markGenerationRelevantEditsAttempt = (projectId, expectedRevision, baseline) => (
  generationRelevantEditsSession.markAttempt(projectId, expectedRevision, baseline)
);
export const clearGenerationRelevantEditsAttempt = (projectId, expectedRevision) => (
  generationRelevantEditsSession.clearAttempt(projectId, expectedRevision)
);
export const landedGenerationRelevantEditsAttemptRevision = (projectId, cuts) => (
  generationRelevantEditsSession.landedAttemptRevision(projectId, cuts)
);
export const adoptGenerationRelevantEdits = (projectId, options) => (
  generationRelevantEditsSession.adopt(projectId, options)
);
