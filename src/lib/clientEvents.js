import { CLIENT_EVENT_ACTIONS, CLIENT_EVENT_KINDS, routeTemplate, safeCode, safeErrorCode, safeInteger,
  sanitizeBreadcrumb, sanitizeContext, sanitizeQueuedEvent, validUuid } from './clientEventPrivacy.js';
import { acceptMockClientEvent } from '../mock/clientEvents.js';
import { classifyDevice } from './deviceClass.js';
import { collectDiagnostics, routeStep } from './clientEventContext.js';

export const CLIENT_EVENT_QUEUE_KEY = 'wl-client-events-v1';
export const CLIENT_EVENT_TTL_MS = 24 * 60 * 60 * 1000;
export const CLIENT_EVENT_BODY_LIMIT = 32768;
export const newRequestId = () => globalThis.crypto.randomUUID();

export function createClientEventCollector({ env = globalThis, now = Date.now, uuid = newRequestId,
  baseUrl = '', buildSha = 'dev', mock = false, transport } = {}) {
  const breadcrumbs = [];
  const pageStarted = now();
  const signatures = new Map();
  let seenErrors = new WeakSet();
  let sessionCount = 0;
  let session;
  let identityEpoch = 0;
  let flushing = null;
  let installed = false;
  let snapshotProvider = () => ({});
  const storage = () => { try { return env.localStorage; } catch { return null; } };
  const readQueue = () => {
    try {
      const saved = JSON.parse(storage()?.getItem(CLIENT_EVENT_QUEUE_KEY) || 'null');
      const items = Array.isArray(saved?.items) ? saved.items.filter((item) => (
        Number.isFinite(item?.queued_at) && now() - item.queued_at < CLIENT_EVENT_TTL_MS
        && item.queued_at <= now() && validUuid(item?.payload?.event_id)
      )).slice(-20).map((item) => ({ queued_at: item.queued_at, payload: sanitizeQueuedEvent(item.payload) }))
        .filter((item) => item.payload) : [];
      return { owner: validUuid(saved?.owner), items };
    } catch { return { owner: null, items: [] }; }
  };
  let queue = readQueue();
  const persist = () => {
    try {
      if (queue.items.length) storage()?.setItem(CLIENT_EVENT_QUEUE_KEY, JSON.stringify(queue));
      else storage()?.removeItem(CLIENT_EVENT_QUEUE_KEY);
    } catch { /* 수집기의 저장 실패는 사용자 오류가 아니다. */ }
  };
  const addBreadcrumb = (raw) => {
    const crumb = sanitizeBreadcrumb({ ...raw, at: new Date(now()).toISOString() });
    // 경로 틀이 같아도 다른 job은 합치지 않는다. 원래 경로는 메모리 키로만 사용한다.
    const pollKey = raw.method === 'GET' && /\/v1\/jobs\/[^/?#]+\/?(?:[?#]|$)/.test(raw.route || '')
      ? new URL(raw.route, 'https://route.invalid').pathname : null;
    if (pollKey) crumb.action = 'poll';
    const previous = pollKey && breadcrumbs.find((entry) => entry.pollKey === pollKey);
    if (previous) {
      previous.crumb = { ...crumb, count: Math.min(10000, (previous.crumb.count || 1) + 1) };
      breadcrumbs.splice(breadcrumbs.indexOf(previous), 1);
      breadcrumbs.push(previous);
    } else breadcrumbs.push({ crumb: { ...crumb, ...(pollKey ? { count: 1 } : {}) }, pollKey });
    if (breadcrumbs.length > 30) breadcrumbs.shift();
    return crumb;
  };
  const action = (name) => {
    if (!CLIENT_EVENT_ACTIONS.has(name) || name === 'unknown') return;
    addBreadcrumb({ type: 'action', action: name, route: env.location?.pathname });
  };
  const updateJobPoll = (path, job) => {
    const previous = breadcrumbs.find((entry) => entry.pollKey === new URL(path, 'https://route.invalid').pathname);
    if (!previous) return;
    if (['pending', 'running', 'done', 'error', 'cancelled'].includes(job.status)) {
      previous.crumb.error_code = safeErrorCode(job.result?.errorCode || `job_${job.status}`);
    }
  };
  const enqueue = (payload, epoch) => {
    if (epoch !== identityEpoch) return;
    queue.items = queue.items.filter((item) => now() - item.queued_at < CLIENT_EVENT_TTL_MS && item.payload.event_id !== payload.event_id);
    queue.items.push({ queued_at: now(), payload });
    queue.items = queue.items.slice(-20);
    persist();
  };
  const send = async (payload, epoch) => {
    if (epoch !== identityEpoch) return false;
    try {
      if (mock) { await acceptMockClientEvent(payload); return true; }
      const body = JSON.stringify(payload);
      if (new TextEncoder().encode(body).length > CLIENT_EVENT_BODY_LIMIT) return false;
      const response = await (transport || env.fetch.bind(env))(`${baseUrl}/v1/client-events`, {
        method: 'POST', keepalive: true, redirect: 'error',
        headers: { 'Content-Type': 'application/json', 'X-Client-Request-Id': uuid(),
          ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}) }, body,
      });
      return response.ok;
    } catch { return false; }
  };
  const flush = () => {
    if (flushing) return flushing;
    if (session === undefined || env.navigator?.onLine === false) return Promise.resolve();
    const epoch = identityEpoch;
    flushing = (async () => {
      queue.items = queue.items.filter((item) => now() - item.queued_at < CLIENT_EVENT_TTL_MS);
      persist();
      for (const item of [...queue.items]) {
        if (epoch !== identityEpoch || !await send(item.payload, epoch)) break;
        if (epoch !== identityEpoch) break;
        queue.items = queue.items.filter((current) => current.payload.event_id !== item.payload.event_id);
        persist();
      }
    })().finally(() => { flushing = null; });
    return flushing;
  };
  const setSession = (next) => {
    const owner = validUuid(next?.user?.id);
    if (queue.owner !== owner || (session !== undefined && validUuid(session?.user?.id) !== owner)) {
      identityEpoch += 1;
      queue = { owner, items: [] };
      breadcrumbs.length = 0;
      seenErrors = new WeakSet();
      persist();
    }
    session = next || null;
    queue.owner = owner;
    void flush();
  };
  const report = ({ kind = 'ui_error', action: name = 'unknown', messageCode, step, blocking = false, error, context = {} } = {}) => {
    try {
      if (!CLIENT_EVENT_KINDS.has(kind) || error?.name === 'AbortError'
        || ['job_cancelled', 'job_timeout', 'draft_request_cancelled', 'validation_error', 'face_quality',
          'unsupported_type', 'file_too_large', 'quote_changed'].includes(error?.code)) return null;
      if (error && typeof error === 'object' && seenErrors.has(error)) return null;
      const api = error?.clientApi || error?.cause?.clientApi || {};
      const snapshot = snapshotProvider();
      const device = classifyDevice({ userAgent: env.navigator?.userAgent, userAgentDataMobile: env.navigator?.userAgentData?.mobile,
        maxTouchPoints: env.navigator?.maxTouchPoints,
        screenShortSide: Math.min(env.screen?.width ?? env.innerWidth, env.screen?.height ?? env.innerHeight ?? env.innerWidth) });
      const payload = {
        event_id: uuid(), schema_version: 1, occurred_at: new Date(now()).toISOString(), kind,
        action: CLIENT_EVENT_ACTIONS.has(name) ? name : 'unknown', route: routeTemplate(env.location?.pathname),
        status: safeInteger(error?.status ?? api.status, 599), error_code: safeErrorCode(error?.code ?? api.error_code),
        request_id: validUuid(error?.requestId || error?.cause?.requestId || api.request_id),
        project_id: validUuid(context.projectId || snapshot.projectId), job_id: validUuid(context.jobId || error?.jobId),
        build_sha: safeCode(buildSha), device: device === 'phone' ? 'mobile' : device,
        blocking: blocking === true,
        ...collectDiagnostics({ env, kind, error, messageCode,
          step: step ?? context.step ?? snapshot.step ?? (kind === 'generation_error' ? 'generating' : routeStep(env.location?.pathname)),
          pageAge: Math.min(604800000, Math.max(0, Math.floor(env.performance?.now?.() ?? (now() - pageStarted)))) }),
        context: sanitizeContext({ ...snapshot, ...context,
          online: env.navigator?.onLine !== false, visibility: env.document?.visibilityState }),
        breadcrumbs: breadcrumbs.map((entry) => ({ ...entry.crumb })),
      };
      const signature = JSON.stringify([payload.kind, payload.action, payload.route, payload.status, payload.error_code]);
      if (sessionCount >= 30 || (signatures.has(signature) && now() - signatures.get(signature) < 30000)) return null;
      if (new TextEncoder().encode(JSON.stringify(payload)).length > CLIENT_EVENT_BODY_LIMIT) return null;
      signatures.set(signature, now());
      sessionCount += 1;
      if (error && typeof error === 'object') seenErrors.add(error);
      const epoch = identityEpoch;
      if (env.navigator?.onLine === false) enqueue(payload, epoch);
      else void send(payload, epoch).then((sent) => { if (!sent) enqueue(payload, epoch); });
      return payload;
    } catch { return null; }
  };
  const install = () => {
    if (installed || !env.addEventListener) return;
    installed = true;
    persist();
    env.addEventListener('online', () => {
      addBreadcrumb({ type: 'navigation', route: env.location?.pathname, error_code: 'online' });
      void flush();
    });
    env.addEventListener('offline', () => addBreadcrumb({ type: 'navigation', route: env.location?.pathname, error_code: 'offline' }));
    env.document?.addEventListener('visibilitychange', () => addBreadcrumb({ type: 'navigation',
      route: env.location?.pathname, error_code: env.document.visibilityState === 'hidden' ? 'visibility_hidden' : 'visibility_visible' }));
    // React가 오류 경계로 처리한 오류를 전역 리스너가 중복 보고하지 않게 한 턴 기다린다.
    env.addEventListener('error', (event) => {
      if (!event.error && typeof event.message !== 'string') return;
      env.setTimeout(() => report({ kind: 'runtime_error', action: 'unknown', messageCode: 'runtime_error', blocking: true, error: event.error }), 0);
    });
    env.addEventListener('unhandledrejection', (event) => {
      env.setTimeout(() => report({ kind: 'unhandled_rejection', action: 'unknown', messageCode: 'unhandled_rejection', blocking: true,
        error: typeof event.reason === 'object' && event.reason ? event.reason : undefined }), 0);
    });
  };
  return { report, addBreadcrumb, updateJobPoll, action, flush, setSession, install,
    setSnapshotProvider: (provider) => { snapshotProvider = provider; },
    getBreadcrumbs: () => breadcrumbs.map((entry) => ({ ...entry.crumb })) };
}

export const clientEvents = createClientEventCollector({ baseUrl: import.meta.env?.VITE_API_BASE_URL ?? '',
  buildSha: typeof __BUILD_SHA__ === 'undefined' ? 'dev' : __BUILD_SHA__, mock: (import.meta.env?.VITE_API_MODE ?? 'mock') !== 'http' });
export const reportUserVisibleError = (details) => clientEvents.report(details);
export const recordClientAction = (name) => clientEvents.action(name);
