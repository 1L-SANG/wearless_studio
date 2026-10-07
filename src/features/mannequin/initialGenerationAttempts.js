const KEY_PREFIX = 'wl_initial_mannequin_attempt:';
let nextAttemptId = 0;

export function createInitialGenerationAttempts({ storage, keyPrefix = KEY_PREFIX, nextId = () => (
  globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++nextAttemptId}`
) } = {}) {
  const memory = new Map();
  const getStorage = () => storage === undefined
    ? (typeof sessionStorage === 'undefined' ? null : sessionStorage) : storage;
  const read = (pid) => {
    try {
      const value = JSON.parse(getStorage()?.getItem(`${keyPrefix}${pid}`) || 'null');
      if (value?.idempotencyKey) {
        memory.set(pid, value);
        return value;
      }
    } catch { /* in-memory fallback */ }
    return memory.get(pid) || null;
  };
  const save = (pid, value) => {
    memory.set(pid, value);
    try { getStorage()?.setItem(`${keyPrefix}${pid}`, JSON.stringify(value)); } catch { /* in-memory fallback */ }
    return value;
  };
  const clear = (pid, expectedKey) => {
    const current = read(pid);
    if (expectedKey && current?.idempotencyKey !== expectedKey) return false;
    // 컷 조회·입력 수정만으로 서버의 종결 상태를 풀지 않는다.
    if (!expectedKey && (current?.terminalFailure || current?.serverStatus === 'running')) return false;
    memory.delete(pid);
    try { getStorage()?.removeItem(`${keyPrefix}${pid}`); } catch { /* in-memory fallback */ }
    return true;
  };
  return {
    read,
    seed: (pid, job) => {
      const current = read(pid) || { idempotencyKey: `initial-mannequin-${nextId()}` };
      if (job === null) return save(pid, { ...current, serverStatus: 'none' });
      const previousJobId = current.serverJobId || current.jobId;
      // 다른 잡이나 조회 불명 상태를 심은 뒤 옛 폴링의 성공이 차단을 지우면 안 된다.
      const keyChanged = (previousJobId && job?.id && previousJobId !== job.id)
        || (!job && !current.terminalFailure && !current.retry);
      const idempotencyKey = keyChanged ? `initial-mannequin-${nextId()}` : current.idempotencyKey;
      if (job?.id && ['pending', 'running', 'done'].includes(job.status)) {
        return save(pid, { idempotencyKey, jobId: job.id,
          serverStatus: job.status === 'pending' ? 'running' : job.status });
      }
      if (current.retry && (!job || current.retryOf === job.id)) return current;
      const code = job?.errorCode === 'mannequin_quality_failed' ? 'mannequin_quality_failed'
        : job?.status === 'cancelled' ? 'job_cancelled'
          : job?.status === 'error' ? 'job_failed' : 'mannequin_state_unknown';
      return save(pid, { idempotencyKey, terminalFailure: true,
        serverStatus: job?.status || 'unknown', serverJobId: job?.id || null, code,
        message: code === 'mannequin_state_unknown' ? '마네킹 생성 상태를 확인하지 못했어요.'
          : code === 'job_cancelled' ? '마네킹 생성이 취소됐어요.' : '마네킹 생성에 실패했어요.' });
    },
    begin: (pid, idempotencyKey) => read(pid) || save(pid, { idempotencyKey: idempotencyKey || `initial-mannequin-${nextId()}` }),
    clear,
    retry: (pid) => {
      const current = read(pid);
      if (!current?.terminalFailure) return false;
      save(pid, { idempotencyKey: `initial-mannequin-${nextId()}`, retry: true,
        retryOf: current.serverJobId || current.jobId || null });
      return true;
    },
    started: (pid, expectedKey, jobId) => {
      const current = read(pid);
      if (current?.idempotencyKey !== expectedKey) return false;
      save(pid, { ...current, jobId, serverStatus: 'running', retry: false });
      return true;
    },
    fail: (pid, expectedKey, message, code = 'mannequin_quality_failed') => {
      const current = read(pid);
      if (current?.idempotencyKey !== expectedKey) return false;
      save(pid, { ...current, terminalFailure: true, message, code });
      return true;
    },
    complete: (pid, expectedKey) => {
      const current = read(pid);
      if (current?.idempotencyKey !== expectedKey) return false;
      save(pid, { ...current, terminalFailure: true, serverStatus: 'done',
        code: 'mannequin_job_done', message: '마네킹 생성이 완료됐어요.' });
      return true;
    },
  };
}

export async function runInitialGenerationAttempt(attempts, generate, pid, options = {}) {
  const attempt = attempts.begin(pid);
  if (attempt.terminalFailure) {
    throw Object.assign(new Error(attempt.message || '상품 재현 검수를 완료하지 못했어요.'), {
      code: attempt.code || 'mannequin_quality_failed',
    });
  }
  try {
    const result = await generate(pid, {
      ...options, idempotencyKey: attempt.idempotencyKey, resumeJobId: attempt.jobId,
      onProgress: attempt.serverStatus === 'done' ? undefined : options.onProgress,
      onJobStarted: (jobId) => {
        if (attempt.serverStatus === 'done') return;
        // 다른 탭의 활성 작업에 합류하면 우리 키는 서버 작업의 키가 아니다.
        // 실제 합류한 작업 번호를 저장해 다음 진입은 POST 없이 그 작업을 조회한다.
        attempts.started(pid, attempt.idempotencyKey, jobId);
        options.onJobStarted?.(jobId);
      },
    });
    attempts.clear(pid, attempt.idempotencyKey);
    return result;
  } catch (error) {
    if (['mannequin_quality_failed', 'job_failed', 'job_cancelled'].includes(error?.code)) {
      attempts.fail(pid, attempt.idempotencyKey, error.message, error.code);
    }
    // 결과 불명인 네트워크 실패는 키를 유지해, F5 후에도 같은 서버 작업에 합류한다.
    throw error;
  }
}

export const initialGenerationAttempts = createInitialGenerationAttempts();
