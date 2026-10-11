const copy = (value) => Array.isArray(value) ? value.map(copy)
  : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)])) : value;

export function canReplaceCanvasAiResult(blocks, job) {
  if (!job?.sourceSrc || !job.blockId || !job.elementId) return false;
  const element = (blocks || []).find((block) => block.id === job.blockId)?.elements?.find((item) => item.id === job.elementId);
  return element?.type === 'image' && element.src === job.sourceSrc;
}

/** A local two-second gate. Cancelling a pending job never sends a server request. */
export function createCanvasAiJobController({ schedule = setTimeout, cancelSchedule = clearTimeout,
  createKey = () => crypto.randomUUID(), now = Date.now, delayMs = 2000,
  onChange, onQueued, onCancelled, onStarted, onFinished, onResult, onError } = {}) {
  const jobs = new Map(), timers = new Map(), keys = new Map(), listeners = new Set();
  const emit = () => {
    const snapshot = new Map(jobs);
    onChange?.(snapshot);
    for (const listener of listeners) listener(snapshot);
  };
  const get = (key) => jobs.get(key);
  const patch = (key, fields) => {
    const job = get(key);
    if (!job) return undefined;
    Object.assign(job, fields);
    emit();
    return job;
  };
  const identity = (key, meta) => JSON.stringify([
    meta.isSlot || !meta.sourceSrc ? ['slot', meta.blockId || key, meta.elementId || key] : ['source', meta.sourceAssetId || meta.sourceSrc],
    meta.action ?? null, meta.value ?? null,
  ]);

  const run = async (key, job, execute, signature, retained) => {
    timers.delete(key);
    if (get(key) !== job || job.status !== 'cancel') return;
    patch(key, { status: 'busy', startedAt: now() });
    onStarted?.(job);
    let response;
    try {
      response = await execute({
        idempotencyKey: job.idempotencyKey,
        request: copy(job.request),
        onJobStarted: (jobId, contextFill) => {
          if (get(key) !== job) return;
          const fields = jobId && typeof jobId === 'object' ? jobId : { jobId, ...(contextFill ? { contextFill } : {}) };
          patch(key, fields);
        },
      });
    } catch (error) {
      if (get(key) !== job) return;
      retained.unknown ||= error?.submissionUnknown === true;
      if (!error?.submissionUnknown && !(error?.requestNotSent && retained.unknown)
        && keys.get(signature) === retained) keys.delete(signature);
      patch(key, { status: 'failed', error, message: error?.message || '이미지를 생성하지 못했어요. 다시 눌러 주세요.', finishedAt: now() });
      try { onError?.(job, error); } finally { onFinished?.(job); }
      return;
    }
    if (get(key) !== job) return;
    if (keys.get(signature) === retained) keys.delete(signature);
    patch(key, { status: 'arrived', result: response?.data ?? response, response,
      ...(response?.contextFill ? { contextFill: response.contextFill } : {}), finishedAt: now() });
    try { onResult?.(job, response); } finally { onFinished?.(job); }
  };

  const start = (key, meta, execute) => {
    const current = get(key);
    if (current && ['cancel', 'busy'].includes(current.status)) return current;
    if (!key || !meta || typeof execute !== 'function') return null;
    const signature = identity(key, meta);
    if (!keys.has(signature)) keys.set(signature, { idempotencyKey: createKey(), request: copy(meta.request) });
    const retained = keys.get(signature);
    const job = { ...meta, request: copy(retained.request), key,
      status: 'cancel', idempotencyKey: retained.idempotencyKey, queuedAt: now(), cancelUntil: now() + delayMs, dismissed: false };
    jobs.set(key, job);
    onQueued?.(job);
    timers.set(key, schedule(() => { void run(key, job, execute, signature, retained); }, delayMs));
    emit();
    return job;
  };
  const cancel = (key) => {
    const job = get(key);
    if (!job || job.status !== 'cancel') return false;
    cancelSchedule(timers.get(key));
    timers.delete(key);
    jobs.delete(key);
    onCancelled?.(job);
    emit();
    return true;
  };
  const dismiss = (key) => {
    if (!get(key)) return false;
    patch(key, { dismissed: true });
    return true;
  };
  const subscribe = (listener) => {
    listeners.add(listener);
    listener(new Map(jobs));
    return () => listeners.delete(listener);
  };
  const dispose = () => {
    for (const [key, timer] of timers) {
      cancelSchedule(timer);
      if (get(key)?.status === 'cancel') jobs.delete(key);
    }
    timers.clear();
    listeners.clear();
  };
  return { jobs, start, get, cancel, dismiss, patch, subscribe, dispose };
}
