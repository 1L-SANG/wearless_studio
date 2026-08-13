import { api } from '@/lib/api/index.js';
import { invalidateStoryboardEntryPrefetch } from './storyboardEntryPrefetch.js';

/* 콘티 저장 직렬 체인 — 모듈 스코프: 컴포넌트 수명(빠른 이탈→재진입의 구·신 인스턴스)과
   프로젝트 경계를 넘어 전 저장의 순서를 보장한다. 늦게 도착한 옛 PUT이 최신을 덮어쓸 수 없다.
   lastSaved 는 프로젝트별 — 다른 프로젝트의 참조와 비교되는 오판 방지. */
const DEFAULT_RETRY_DELAYS = [1_000, 2_000, 5_000, 10_000, 30_000];

export const sbStable = (value) => JSON.stringify(value, (key, item) => (
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.keys(item).sort().reduce((out, itemKey) => { out[itemKey] = item[itemKey]; return out; }, {})
    : item
));

export function createStoryboardPersistence({
  saveStoryboard = (...args) => api.saveStoryboard(...args),
  invalidate = invalidateStoryboardEntryPrefetch,
  setTimer = globalThis.setTimeout,
  clearTimer = globalThis.clearTimeout,
  onlineTarget = globalThis.window,
  retryDelays = DEFAULT_RETRY_DELAYS,
} = {}) {
  let saveChain = Promise.resolve();
  const lastSaved = new Map();
  const pending = new Map();
  const retries = new Map();

  const clearRetry = (projectId) => {
    const retry = retries.get(projectId);
    if (retry?.timer != null) clearTimer(retry.timer);
    retries.delete(projectId);
  };

  const retryPending = (projectId, retry) => {
    if (retries.get(projectId) !== retry || pending.get(projectId) !== retry.snapshot) {
      if (retries.get(projectId) === retry) clearRetry(projectId);
      return;
    }
    retry.timer = null;
    void saveNow(projectId, () => {
      // 타이머가 직렬 체인 뒤에서 기다리는 동안 더 최신 저장이 pending을 바꿀 수 있다.
      // 실제 PUT 직전에도 identity를 다시 확인해 오래된 retry가 최신 성공 뒤에 착지하지 않게 한다.
      if (pending.get(projectId) !== retry.snapshot) {
        if (retries.get(projectId) === retry) clearRetry(projectId);
        return null;
      }
      return retry.snapshot;
    }, retry.options).catch(() => {});
  };

  const scheduleRetry = (projectId, snapshot, options) => {
    const current = retries.get(projectId);
    if (current?.snapshot === snapshot && current.timer != null) return;
    if (current?.timer != null) clearTimer(current.timer);
    const attempt = current?.snapshot === snapshot ? current.attempt + 1 : 0;
    const retry = {
      snapshot,
      options,
      attempt,
      timer: null,
    };
    const delay = retryDelays[Math.min(attempt, retryDelays.length - 1)] ?? 30_000;
    retry.timer = setTimer(() => retryPending(projectId, retry), delay);
    retries.set(projectId, retry);
  };

  function saveNow(projectId, getSnapshot, options = {}) {
    // 예약된 저장이 착지하기 전의 서버본을 프리패치가 노출하지 않도록 즉시 무효화한다.
    if (projectId) invalidate(projectId);
    const run = saveChain.catch(() => {}).then(() => {
      const snapshot = getSnapshot();
      if (!projectId || !snapshot) return;
      if (lastSaved.get(projectId) === snapshot) {
        if (pending.get(projectId) === snapshot) pending.delete(projectId);
        if (retries.get(projectId)?.snapshot === snapshot) clearRetry(projectId);
        return;
      }
      return Promise.resolve().then(() => saveStoryboard(projectId, snapshot, options)).then(
        () => {
          lastSaved.set(projectId, snapshot);
          pending.delete(projectId);
          clearRetry(projectId);
        },
        (error) => {
          pending.set(projectId, snapshot);
          scheduleRetry(projectId, snapshot, options);
          throw error;
        },
      );
    });
    saveChain = run.catch(() => {});
    return run;
  }

  const retryOnline = () => {
    for (const [projectId, retry] of retries) {
      if (pending.get(projectId) !== retry.snapshot) {
        clearRetry(projectId);
        continue;
      }
      if (retry.timer != null) clearTimer(retry.timer);
      retry.timer = null;
      retryPending(projectId, retry);
    }
  };
  onlineTarget?.addEventListener?.('online', retryOnline);

  return {
    lastSaved,
    pending,
    saveIdle: () => saveChain.catch(() => {}),
    saveNow,
    dispose() {
      onlineTarget?.removeEventListener?.('online', retryOnline);
      for (const projectId of retries.keys()) clearRetry(projectId);
    },
  };
}

const storyboardPersistence = createStoryboardPersistence();
export const sbLastSaved = storyboardPersistence.lastSaved;
export const sbPending = storyboardPersistence.pending;
export const sbSaveIdle = storyboardPersistence.saveIdle;
export const sbSaveNow = storyboardPersistence.saveNow;
