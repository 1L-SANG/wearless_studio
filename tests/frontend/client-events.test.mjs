import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createClientEventCollector, CLIENT_EVENT_BODY_LIMIT, CLIENT_EVENT_QUEUE_KEY, CLIENT_EVENT_TTL_MS } from '../../src/lib/clientEvents.js';
import { routeTemplate, sanitizeTechnicalMessage, sanitizeTechnicalStack, sanitizeQueuedEvent } from '../../src/lib/clientEventPrivacy.js';
import { attachResponseError, clientApiFetch, isOwnApiRequest, readClientResponse } from '../../src/lib/api/clientRequest.js';
import { fetchPhotoBytes } from '../../src/lib/photoBytes.js';
import { acceptMockClientEvent } from '../../src/mock/clientEvents.js';

const REQUEST = 'cdcc757c-c732-4102-9114-bcfb8511163c';
const USER = '2df3af7f-7b3f-4a6d-bf29-147a3c59a567';
const OTHER = '23efc769-429d-428b-97a2-bf91d647e334';
const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const drain = () => new Promise(setImmediate);
const failureCode = (index) => `failure_${String.fromCharCode(97 + Math.floor(index / 26))}${String.fromCharCode(97 + index % 26)}`;
const codeError = (code) => Object.assign(new Error('operation failed'), { code });
const session = (id) => ({ user: { id }, access_token: 'test-token' });
const useApiOrigin = (t) => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://api.test' } });
  t.after(() => { if (prior) Object.defineProperty(globalThis, 'location', prior); else delete globalThis.location; });
};

function fixture({ offline = false, initial, fail = false, mock = false } = {}) {
  const values = new Map(initial ? [[CLIENT_EVENT_QUEUE_KEY, JSON.stringify(initial)]] : []);
  const env = new EventTarget();
  env.document = new EventTarget();
  env.document.visibilityState = 'visible';
  env.navigator = { onLine: !offline };
  env.location = { pathname: `/editor/${USER}?email=private@example.com#typed` };
  env.innerWidth = 390;
  env.setTimeout = setTimeout;
  env.localStorage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let time = Date.parse('2026-10-08T10:00:00Z');
  let failed = fail;
  const calls = [];
  const collector = createClientEventCollector({ env, now: () => time, buildSha: 'a123bc', baseUrl: 'https://api.test', mock,
    transport: async (url, options) => { calls.push({ url, options }); if (failed) throw new Error('collector offline'); return { ok: true }; } });
  return { env, values, calls, collector, advance: (ms) => { time += ms; }, failure: (value) => { failed = value; },
    queue: () => JSON.parse(values.get(CLIENT_EVENT_QUEUE_KEY) || 'null') };
}

const details = (overrides = {}) => ({ kind: 'save_error', action: 'save', messageCode: 'save_failed', blocking: true, ...overrides });

test('own API classifier excludes Supabase, R2 GET/PUT, CDN, static, blob, data and redirect file routes', () => {
  const base = 'https://api.test';
  for (const target of [`${base}/v1/projects/${USER}`, `${base}/v1/assets/${USER}/bytes`, '/v1/projects']) {
    assert.equal(isOwnApiRequest(target, base, base), true, target);
  }
  for (const target of ['https://auth.supabase.co/auth/v1/token', 'https://auth.supabase.co/v1/test',
    'https://bucket.r2.cloudflarestorage.com/v1/upload', 'https://cdn.test/v1/assets/bytes', `${base}/assets/static.js`,
    `${base}/v1/assets/${USER}/file?download=1`, 'blob:https://api.test/photo', 'data:image/png;base64,AA==',
    'https://api.test.evil.test/v1/projects', 'https://app.test/v1/projects']) {
    assert.equal(isOwnApiRequest(target, base, 'https://app.test'), false, target);
  }
  assert.equal(isOwnApiRequest('/v1/projects', '', 'https://app.test'), true);
  assert.equal(isOwnApiRequest('https://api.test/v1/projects', base, 'https://app.test'), true);
});

test('request UUID is unique, server response ID survives HTTP and parse errors, redirect follow is disabled', async (t) => {
  useApiOrigin(t);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return new Response('broken JSON', { status: 500, headers: { 'X-Request-Id': REQUEST } });
  });
  const a = await clientApiFetch('/v1/projects', { headers: { Authorization: 'Bearer test' } });
  const b = await clientApiFetch('/v1/projects', { method: 'POST' });
  const ids = calls.map(({ options }) => options.headers.get('X-Client-Request-Id'));
  assert.match(ids[0], /^[0-9a-f-]{36}$/);
  assert.notEqual(ids[0], ids[1]);
  assert.equal(calls[0].options.headers.get('Authorization'), 'Bearer test');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(attachResponseError(Object.assign(new Error('failed'), { status: 500 }), a).requestId, REQUEST);
  await assert.rejects(readClientResponse(b), { requestId: REQUEST });
});

test('external byte requests and redirect /file never receive a client header, including caller supplied headers', async (t) => {
  useApiOrigin(t);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push([url, options]); return new Response('photo', { headers: { 'Content-Type': 'image/jpeg' } }); });
  for (const url of ['https://cdn.test/photo.jpg', 'https://bucket.r2.test/upload', 'https://supabase.test/v1/token',
    'blob:photo', 'data:image/png;base64,AA==', '/static.js', `/v1/assets/${USER}/file`]) {
    await clientApiFetch(url, { headers: { 'X-Client-Request-Id': REQUEST } });
    assert.equal(new Headers(calls.at(-1)[1].headers).has('X-Client-Request-Id'), false);
    assert.equal(calls.at(-1)[1].redirect, undefined);
  }
  await fetchPhotoBytes(`/v1/assets/${USER}/file`);
  assert.equal(calls.at(-1)[0], `/v1/assets/${USER}/bytes`);
  assert.ok(calls.at(-1)[1].headers.get('X-Client-Request-Id'));
});

test('network and invalid image response errors retain the original request UUID', async (t) => {
  useApiOrigin(t);
  let id;
  t.mock.method(globalThis, 'fetch', async (_url, options) => { id = options.headers.get('X-Client-Request-Id'); throw new TypeError('Failed to fetch'); });
  await assert.rejects(clientApiFetch('/v1/projects'), (error) => error.requestId === id && error.clientApi.status === 0);
  globalThis.fetch = async () => new Response('{}', { headers: { 'Content-Type': 'application/json', 'X-Request-Id': REQUEST } });
  await assert.rejects(fetchPhotoBytes(`/v1/assets/${USER}/bytes`), { requestId: REQUEST });
});

test('offline requests retain a UUID and API breadcrumb without calling fetch', async (t) => {
  useApiOrigin(t);
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { onLine: false } });
  t.after(() => { if (prior) Object.defineProperty(globalThis, 'navigator', prior); else delete globalThis.navigator; });
  const spy = t.mock.method(globalThis, 'fetch', async () => assert.fail('offline fetch'));
  await assert.rejects(clientApiFetch('/v1/projects'), (error) => {
    assert.equal(error.code, 'browser_offline');
    assert.equal(error.requestNotSent, true);
    assert.match(error.requestId, /^[0-9a-f-]{36}$/);
    assert.equal(error.clientApi.status, 0);
    return true;
  });
  assert.equal(spy.mock.callCount(), 0);
});

test('custom abort reasons retain request ID and are classified as cancellation', async (t) => {
  useApiOrigin(t);
  const controller = new AbortController(); controller.abort('typed cancellation reason');
  t.mock.method(globalThis, 'fetch', async () => { throw controller.signal.reason; });
  await assert.rejects(clientApiFetch('/v1/projects', { signal: controller.signal }), (error) => {
    assert.equal(error.name, 'AbortError'); assert.match(error.requestId, /^[0-9a-f-]{36}$/);
    assert.equal(error.message.includes('typed'), false); return true;
  });
});

test('breadcrumb ring has 30 sanitized entries and rejects arbitrary named actions', () => {
  const { collector } = fixture();
  collector.action('상품명 입력 private@example.com');
  assert.equal(collector.getBreadcrumbs().length, 0);
  for (let index = 0; index < 35; index++) collector.addBreadcrumb({ type: 'navigation', route: `/projects/${index}?token=secret#typed` });
  assert.equal(collector.getBreadcrumbs().length, 30);
  assert.equal(collector.getBreadcrumbs()[0].route, '/projects/:id');
  collector.action('analyze');
  assert.equal(collector.getBreadcrumbs().at(-1).action, 'analyze');
  assert.equal(JSON.stringify(collector.getBreadcrumbs()).includes('secret'), false);
  const copy = collector.getBreadcrumbs(); copy[0].route = '/leak';
  assert.notEqual(collector.getBreadcrumbs()[0].route, '/leak');
});

test('route templates match server UUID, numeric, unknown and bounded segment rules', () => {
  assert.equal(routeTemplate(`https://api.test/v1/projects/${USER}/editor?token=secret#text`), '/v1/projects/:id/editor');
  assert.equal(routeTemplate('/projects/123/private-product-name.jpeg'), '/projects/:id/:segment');
  assert.equal(routeTemplate('/projects/１２３/editor'), '/projects/:id/editor');
  assert.equal(routeTemplate('/projects/{project_id}/:id/:segment'), '/projects/:id/:id/:segment');
  assert.equal(routeTemplate('x'.repeat(513)), '/unknown');
  assert.equal(routeTemplate('/v1'.repeat(20)).split('/').length, 16);
});

test('polls collapse per actual job identity with count, last HTTP status and terminal job status', () => {
  const { collector } = fixture();
  const path = `/v1/jobs/${USER}`;
  for (let count = 0; count < 9; count++) collector.addBreadcrumb({ type: 'api', method: 'GET', route: path, status: 200, request_id: REQUEST });
  collector.action('save');
  collector.addBreadcrumb({ type: 'api', method: 'GET', route: path, status: 503 });
  collector.updateJobPoll(path, { status: 'error', result: { errorCode: 'job_failed' } });
  collector.addBreadcrumb({ type: 'api', method: 'GET', route: `/v1/jobs/${OTHER}`, status: 200 });
  const crumbs = collector.getBreadcrumbs();
  assert.equal(crumbs.length, 3);
  assert.deepEqual([crumbs[1].count, crumbs[1].status, crumbs[1].error_code], [10, 503, 'job_failed']);
  assert.equal(crumbs[2].count, 1);
});

test('privacy filters discard query, hash, filenames, emails, typed text, DOM and store dumps', () => {
  const secret = '입력한 상품 문구 private@example.com personal-photo.jpeg';
  const message = sanitizeTechnicalMessage(`TypeError ${secret} https://api.test/v1/projects?token=secret#typed`);
  const stack = sanitizeTechnicalStack(`TypeError ${secret}\n at ${secret} (https://api.test/assets/private-file.js?token=secret#typed:12:4)`);
  for (const text of [message, stack]) {
    for (const value of ['private', '입력한', '상품', 'token', 'secret', 'typed', '.js', '@', '?', '#', 'jpeg']) assert.equal(text.includes(value), false, text);
  }
  assert.equal(sanitizeTechnicalMessage('자유 입력값'), '[redacted]');
  assert.ok(sanitizeTechnicalMessage('x'.repeat(1000)).length <= 500);
  assert.ok(sanitizeTechnicalStack('x'.repeat(3000)).length <= 2000);
  const { collector } = fixture({ offline: true });
  collector.setSnapshotProvider(() => ({ online: true, photo_pending_count: 2, draft_slot_status: 'owned', local_backup: true,
    product: secret, dom: secret, store: { text: secret } }));
  const payload = collector.report(details({ error: new Error(secret), context: { projectId: USER, typedText: secret } }));
  assert.deepEqual(payload.context, { online: false, local_backup: true, visibility: 'visible', draft_slot_status: 'owned', photo_pending_count: 2 });
  assert.equal(JSON.stringify(payload).includes(secret), false);
});

test('payload is the exact server allowlist, re-sanitization is stable and worst case is below 32768 UTF-8 bytes', () => {
  const { collector } = fixture({ offline: true });
  for (let i = 0; i < 30; i++) collector.addBreadcrumb({ type: 'api', route: '/v1/projects/:id/editor', method: 'PUT', status: 500,
    duration_ms: 3600000, request_id: REQUEST, error_code: 'a'.repeat(32), count: 10000 });
  const payload = collector.report(details({ error: Object.assign(new Error('typed'), { requestId: REQUEST, status: 500, code: 'save_failed' }),
    context: { projectId: USER, jobId: OTHER, local_backup: true, photo_pending_count: 1000 } }));
  assert.deepEqual(Object.keys(payload).sort(), ['event_id', 'schema_version', 'occurred_at', 'kind', 'action', 'route', 'status', 'error_code',
    'request_id', 'project_id', 'job_id', 'build_sha', 'device', 'blocking', 'context', 'breadcrumbs',
    'step', 'message_code', 'browser', 'os', 'viewport', 'network', 'page_age_ms', 'visibility', 'tech_message'].sort());
  assert.deepEqual(sanitizeQueuedEvent(payload), payload);
  assert.ok(new TextEncoder().encode(JSON.stringify(payload)).length < CLIENT_EVENT_BODY_LIMIT);
  const server = read('server/app/client_events.py');
  for (const key of Object.keys(payload)) assert.ok(server.includes(`"${key}"`), key);
  const sanitized = sanitizeQueuedEvent({ ...payload, technical_message: 'typed private@example.com', context: { ...payload.context, input: 'typed' } });
  assert.equal(JSON.stringify(sanitized).includes('typed'), false);
});

test('signature throttles for exactly 30 seconds and caps the session at 30 events', () => {
  const { collector, advance } = fixture({ offline: true });
  assert.ok(collector.report(details()));
  assert.equal(collector.report(details()), null);
  advance(29999); assert.equal(collector.report(details()), null);
  advance(1); assert.ok(collector.report(details()));
  for (let i = 0; i < 28; i++) assert.ok(collector.report(details({ messageCode: failureCode(i), error: codeError(failureCode(i)) })));
  assert.equal(collector.report(details({ messageCode: 'over_session_limit', error: codeError('over_session_limit') })), null);
});

test('same original Error is reported once across visible surfaces and global listeners', () => {
  const { collector, advance } = fixture({ offline: true });
  const error = new Error('private typed');
  assert.ok(collector.report(details({ error })));
  advance(31000);
  assert.equal(collector.report({ kind: 'toast_error', action: 'unknown', error }), null);
  for (const code of ['job_cancelled', 'job_timeout', 'draft_request_cancelled', 'validation_error', 'face_quality', 'quote_changed']) {
    assert.equal(collector.report(details({ error: Object.assign(new Error('input guidance'), { code }) })), null);
  }
  assert.equal(collector.report(details({ error: new DOMException('cancel', 'AbortError') })), null);
});

test('failed collector transport queues at most 20 events and never reports itself', async () => {
  const { collector, queue, calls } = fixture({ fail: true });
  collector.setSession(null);
  for (let i = 0; i < 25; i++) collector.report(details({ messageCode: failureCode(i), error: codeError(failureCode(i)) }));
  await drain();
  assert.equal(calls.length, 25);
  assert.equal(queue().items.length, 20);
  assert.equal(queue().items[0].payload.error_code, failureCode(5));
  assert.equal(collector.getBreadcrumbs().length, 0);
});

test('queue expires after 24 hours, online flush retains event_id, authentication uses current token', async () => {
  const f = fixture({ offline: true });
  f.collector.setSession(session(USER));
  const expired = f.collector.report(details());
  f.advance(CLIENT_EVENT_TTL_MS);
  const fresh = f.collector.report(details({ messageCode: 'new_failure' }));
  assert.equal(f.queue().items.length, 1);
  f.collector.install();
  f.env.navigator.onLine = true;
  f.env.dispatchEvent(new Event('online'));
  await drain();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].options.keepalive, true);
  assert.equal(f.calls[0].options.headers.Authorization, 'Bearer test-token');
  assert.equal(JSON.parse(f.calls[0].options.body).event_id, fresh.event_id);
  assert.notEqual(fresh.event_id, expired.event_id);
  assert.equal(f.queue(), null);
});

test('next load waits for account bootstrap and only flushes the matching owner', async () => {
  const first = fixture({ offline: true });
  first.collector.setSession(session(USER)); first.collector.report(details());
  const initial = first.queue();
  const next = fixture({ initial }); next.collector.install();
  await drain(); assert.equal(next.calls.length, 0);
  next.collector.setSession(session(USER)); await drain();
  assert.equal(next.calls.length, 1);
  assert.equal(JSON.parse(next.calls[0].options.body).event_id, initial.items[0].payload.event_id);
  const other = fixture({ initial }); other.collector.setSession(session(OTHER)); await drain();
  assert.equal(other.calls.length, 0); assert.equal(other.queue(), null);
});

test('logout and account switch purge queued payloads and private breadcrumb history', () => {
  const f = fixture({ offline: true });
  f.collector.setSession(session(USER)); f.collector.action('save'); f.collector.report(details());
  assert.equal(f.queue().items.length, 1);
  f.collector.setSession(session(OTHER));
  assert.equal(f.queue(), null); assert.deepEqual(f.collector.getBreadcrumbs(), []);
  f.collector.report(details({ messageCode: 'different', error: codeError('different_error') }));
  assert.equal(f.queue().items.length, 1);
  f.collector.setSession(null); assert.equal(f.queue(), null);
});

test('account switch during failed send cannot repopulate the old queue', async () => {
  let reject;
  const f = fixture();
  const collector = createClientEventCollector({ env: f.env, transport: () => new Promise((_resolve, r) => { reject = r; }) });
  collector.setSession(session(USER)); collector.report(details());
  collector.setSession(session(OTHER)); reject(new Error('offline')); await drain();
  assert.equal(f.queue(), null);
});

test('startup runtime errors can send anonymously while saved queues still await account bootstrap', async () => {
  const f = fixture();
  f.collector.report({ kind: 'error_boundary', action: 'load', blocking: true, messageCode: 'screen_render_failed' });
  await drain();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].options.headers.Authorization, undefined);
  assert.equal(JSON.parse(f.calls[0].options.body).kind, 'error_boundary');
});

test('queue reload sanitizes stored content, ignores invalid entries and purges expired entries', async () => {
  const f = fixture({ offline: true }); f.collector.report(details());
  const item = f.queue().items[0];
  const initial = { owner: null, items: [
    { ...item, queued_at: item.queued_at - CLIENT_EVENT_TTL_MS },
    { ...item, payload: { ...item.payload, schema_version: 3 } },
    { ...item, payload: { ...item.payload, text: 'typed private@example.com', stack: 'file.jpeg', context: { online: true, input: 'typed' } } },
  ] };
  const next = fixture({ initial }); next.collector.setSession(null); await drain();
  assert.equal(next.calls.length, 1);
  assert.equal(/typed|private|jpeg/.test(next.calls[0].options.body), false);
  assert.equal(next.queue(), null);
});

test('online, offline, visibility and global errors install once with stable transition codes', async () => {
  const f = fixture({ offline: true }); f.collector.install(); f.collector.install();
  f.env.dispatchEvent(new Event('offline'));
  f.env.document.visibilityState = 'hidden'; f.env.document.dispatchEvent(new Event('visibilitychange'));
  assert.deepEqual(f.collector.getBreadcrumbs().map((crumb) => crumb.error_code), ['offline', 'visibility_hidden']);
  const error = Object.assign(new Event('error'), { error: new Error('typed') });
  f.env.dispatchEvent(error);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(f.queue().items.length, 1);
  assert.equal(f.queue().items[0].payload.kind, 'runtime_error');
  assert.equal(f.queue().items[0].payload.blocking, true);
  f.env.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason: 'typed private@example.com' }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(f.queue().items.at(-1).payload.kind, 'unhandled_rejection');
  assert.equal(JSON.stringify(f.queue()).includes('private'), false);
});

test('cross-origin script errors without an Error object report a fixed code without raw text', async () => {
  const f = fixture({ offline: true }); f.collector.install();
  f.env.dispatchEvent(Object.assign(new Event('error'), { message: 'private@example.com typed text.jpg', error: null }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(f.queue().items[0].payload.kind, 'runtime_error');
  assert.equal(JSON.stringify(f.queue()).includes('private'), false);
});

test('device class uses the existing physical device policy and never sends the raw user agent', () => {
  const f = fixture({ offline: true });
  f.env.navigator.userAgent = 'Mozilla Macintosh private@example.com';
  f.env.screen = { width: 1440, height: 900 };
  const desktop = f.collector.report(details());
  assert.equal(desktop.device, 'desktop');
  f.env.navigator.maxTouchPoints = 5;
  const tablet = f.collector.report(details({ messageCode: 'tablet_failure', error: codeError('tablet_failure') }));
  assert.equal(tablet.device, 'tablet');
  assert.equal(JSON.stringify(tablet).includes('Mozilla'), false);
});

test('storage denial and mock mode remain silent and do not access the network', async () => {
  const f = fixture({ mock: true }); f.collector.setSession(null);
  Object.defineProperty(f.env, 'localStorage', { get() { throw new Error('denied'); } });
  assert.ok(f.collector.report(details())); await drain();
  assert.equal(f.calls.length, 0);
  assert.deepEqual(await acceptMockClientEvent({}), { accepted: true });
  assert.match(read('src/mock/api.js'), /reportClientEvent: acceptMockClientEvent/);
});

test('Vite defines the Vercel commit SHA with dev fallback', () => {
  assert.match(read('vite.config.js'), /__BUILD_SHA__: JSON.stringify\(process.env.VERCEL_GIT_COMMIT_SHA \|\| 'dev'\)/);
  assert.match(read('src/lib/clientEvents.js'), /buildSha: typeof __BUILD_SHA__/);
});
