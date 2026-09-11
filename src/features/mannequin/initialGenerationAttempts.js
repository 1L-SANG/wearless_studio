const KEY_PREFIX = 'wl_initial_mannequin_attempt:';
let nextAttemptId = 0;

export function createInitialGenerationAttempts({ storage, nextId = () => (
  globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++nextAttemptId}`
) } = {}) {
  const memory = new Map();
  const getStorage = () => storage === undefined
    ? (typeof sessionStorage === 'undefined' ? null : sessionStorage) : storage;
  const read = (pid) => {
    try {
      const value = JSON.parse(getStorage()?.getItem(`${KEY_PREFIX}${pid}`) || 'null');
      if (value?.idempotencyKey) {
        memory.set(pid, value);
        return value;
      }
    } catch { /* in-memory fallback */ }
    return memory.get(pid) || null;
  };
  const save = (pid, value) => {
    memory.set(pid, value);
    try { getStorage()?.setItem(`${KEY_PREFIX}${pid}`, JSON.stringify(value)); } catch { /* in-memory fallback */ }
    return value;
  };
  const clear = (pid, expectedKey) => {
    if (expectedKey && read(pid)?.idempotencyKey !== expectedKey) return false;
    memory.delete(pid);
    try { getStorage()?.removeItem(`${KEY_PREFIX}${pid}`); } catch { /* in-memory fallback */ }
    return true;
  };
  return {
    begin: (pid) => read(pid) || save(pid, { idempotencyKey: `initial-mannequin-${nextId()}` }),
    clear,
    retry: (pid) => read(pid)?.terminalFailure ? clear(pid) : false,
    started: (pid, expectedKey, jobId) => {
      const current = read(pid);
      if (current?.idempotencyKey !== expectedKey) return false;
      save(pid, { ...current, jobId });
      return true;
    },
    fail: (pid, expectedKey, message) => {
      const current = read(pid);
      if (current?.idempotencyKey !== expectedKey) return false;
      save(pid, { ...current, terminalFailure: true, message });
      return true;
    },
  };
}

export async function runInitialGenerationAttempt(attempts, generate, pid, options = {}) {
  const attempt = attempts.begin(pid);
  if (attempt.terminalFailure) {
    throw Object.assign(new Error(attempt.message || '상품 재현 검수를 완료하지 못했어요.'), {
      code: 'mannequin_quality_failed',
    });
  }
  try {
    const result = await generate(pid, {
      ...options, idempotencyKey: attempt.idempotencyKey, resumeJobId: attempt.jobId,
      onJobStarted: (jobId) => {
        // 다른 탭의 활성 작업에 합류하면 우리 키는 서버 작업의 키가 아니다.
        // 실제 합류한 작업 번호를 저장해 다음 진입은 POST 없이 그 작업을 조회한다.
        attempts.started(pid, attempt.idempotencyKey, jobId);
        options.onJobStarted?.(jobId);
      },
    });
    attempts.clear(pid, attempt.idempotencyKey);
    return result;
  } catch (error) {
    if (error?.code === 'mannequin_quality_failed') {
      attempts.fail(pid, attempt.idempotencyKey, error.message);
    } else if (error?.code === 'job_failed' || error?.code === 'job_cancelled') {
      attempts.clear(pid, attempt.idempotencyKey);
    }
    // 결과 불명인 네트워크 실패는 키를 유지해, F5 후에도 같은 서버 작업에 합류한다.
    throw error;
  }
}

export const initialGenerationAttempts = createInitialGenerationAttempts();
