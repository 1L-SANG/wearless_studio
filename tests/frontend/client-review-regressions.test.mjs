import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createClientEventCollector, CLIENT_EVENT_QUEUE_KEY, clientEvents } from '../../src/lib/clientEvents.js';
import { attachResponseError, clientApiFetch, readClientResponse } from '../../src/lib/api/clientRequest.js';
import { looksLikeImageFile, toUploadableImage, getUploadValidationError, MAX_UPLOAD_BYTES } from '../../src/lib/imageTranscode.js';
import { runWithConcurrency, MAX_WARDROBE_UPLOADS, WARDROBE_UPLOAD_CONCURRENCY } from '../../src/features/editor/editorUpload.js';

const A = '2df3af7f-7b3f-4a6d-bf29-147a3c59a567';
const B = '23efc769-429d-428b-97a2-bf91d647e334';
const REQUEST = 'cdcc757c-c732-4102-9114-bcfb8511163c';
const session = (id) => ({ user: { id }, access_token: `token-${id}` });
const read = (file) => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const drain = () => new Promise(setImmediate);
function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}
function fixture(storage = memoryStorage(), transport) {
  const calls = [];
  const env = new EventTarget(); env.document = new EventTarget(); env.document.visibilityState = 'visible';
  env.location = { pathname: '/editor/' + A }; env.navigator = { onLine: false }; env.innerWidth = 390; env.localStorage = storage;
  const collector = createClientEventCollector({ env, transport: transport || (async (_url, options) => { calls.push(JSON.parse(options.body)); return { ok: true }; }) });
  return { collector, env, calls, storage };
}
const entries = (storage) => JSON.parse(storage.getItem(CLIENT_EVENT_QUEUE_KEY) || '{"items":[]}').items;
const failure = (code) => Object.assign(new Error('operation failed'), { code, requestId: REQUEST, status: 503 });
const report = (f, code) => f.collector.report({ kind: 'save_error', action: 'save', messageCode: 'save_failed', error: failure(code), blocking: true });
function globalProperty(t, key, value) {
  const prior = Object.getOwnPropertyDescriptor(globalThis, key);
  Object.defineProperty(globalThis, key, { configurable: true, value });
  t.after(() => { if (prior) Object.defineProperty(globalThis, key, prior); else delete globalThis[key]; });
}

test('offline editor image preflight reaches fetch and consumes cached own API bytes', async (t) => {
  globalProperty(t, 'location', { origin: 'https://api.test' }); globalProperty(t, 'navigator', { onLine: false });
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('cached', { headers: { 'content-type': 'image/jpeg' } }); });
  const source = read('src/features/editor/editorExport.js');
  const body = source.slice(source.indexOf('async function preflightImages'), source.indexOf('async function prepareClone'));
  class FileReader { readAsDataURL() { this.result = 'data:image/jpeg;base64,cached'; this.onload(); } }
  const run = new Function('clientApiFetch', 'readClientResponse', 'attachResponseError', 'FileReader', 'isAssetBytesUrl',
    `${body}; return preflightImages;`)(clientApiFetch, readClientResponse, attachResponseError, FileReader, () => true);
  const img = { getAttribute: () => `https://api.test/v1/assets/${A}/bytes`, removeAttribute() {}, setAttribute(_name, value) { this.result = value; } };
  await run({ querySelectorAll: () => [img] }, new Map());
  assert.equal(calls, 1); assert.equal(img.result, 'data:image/jpeg;base64,cached');
});

test('late request errors and successful request breadcrumbs are dropped after account changes', async (t) => {
  globalProperty(t, 'location', { origin: 'https://api.test' }); globalProperty(t, 'localStorage', memoryStorage());
  const gate = deferred();
  clientEvents.setSession(session(A));
  t.mock.method(globalThis, 'fetch', () => gate.promise);
  const pending = clientApiFetch('/v1/projects/' + A);
  clientEvents.setSession(session(B));
  clientEvents.setSnapshotProvider(() => ({ projectId: B }));
  gate.resolve(Response.json({ error: { code: 'upload_failed' } }, { status: 503, headers: { 'X-Request-Id': REQUEST } }));
  const response = await pending;
  const error = attachResponseError(failure('upload_failed'), response);
  assert.equal(clientEvents.report({ kind: 'upload_error', action: 'upload', error }), null);
  assert.deepEqual(clientEvents.getBreadcrumbs(), []);
  clientEvents.setSession(session(A));
  assert.equal(clientEvents.report({ kind: 'upload_error', action: 'upload', error }), null, 'A to B to A is a new generation');
  clientEvents.setSnapshotProvider(() => ({}));
});

test('JSON request scope is pinned before the authentication lookup, and offline JSON still does not fetch', async (t) => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  globalProperty(t, 'location', { origin: 'https://app.test', pathname: '/editor/' + A });
  globalProperty(t, 'localStorage', memoryStorage());
  const gate = deferred();
  globalThis.__reviewAuth = { auth: { getSession: () => gate.promise } };
  const vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, root, configFile: false, logLevel: 'silent', resolve: { alias: { '@': `${root}/src` } },
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('https://api.test'), 'import.meta.env.VITE_API_MODE': JSON.stringify('http') },
    server: { middlewareMode: true, watch: null, ws: false }, plugins: [{ name: 'review-auth', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0review-auth'; },
      load(id) { if (id === '\0review-auth') return 'export const supabase = globalThis.__reviewAuth;'; } }] });
  t.after(async () => { await vite.close(); delete globalThis.__reviewAuth; });
  const { http } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const { clientEvents: events } = await vite.ssrLoadModule('/src/lib/clientEvents.js');
  events.setSession(session(A));
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return Response.json({ error: { code: 'upload_failed' } }, { status: 503 }); });
  const pending = http('/v1/projects/' + A, { suppressErrorLog: true });
  events.setSession(session(B)); gate.resolve({ data: { session: session(A) } });
  const error = await pending.catch((error) => error);
  assert.ok(error instanceof Error);
  assert.equal(events.report({ kind: 'upload_error', action: 'upload', error }), null);
  assert.deepEqual(events.getBreadcrumbs(), []);
  globalProperty(t, 'navigator', { onLine: false });
  const before = calls;
  await assert.rejects(http('/v1/projects/' + B, { suppressErrorLog: true }), { code: 'browser_offline', requestNotSent: true });
  assert.equal(calls, before);
});

test('two tabs merge events by ID and an empty tab cannot delete their entries', () => {
  const shared = memoryStorage(); const a = fixture(shared); const b = fixture(shared); const empty = fixture(shared);
  for (const f of [a, b, empty]) f.collector.setSession(session(A));
  const one = report(a, 'first_failure'); const two = report(b, 'second_failure');
  assert.deepEqual(new Set(entries(shared).map((item) => item.payload.event_id)), new Set([one.event_id, two.event_id]));
  empty.collector.install();
  assert.equal(entries(shared).length, 2);
  const three = report(a, 'third_failure');
  assert.deepEqual(new Set(entries(shared).map((item) => item.payload.event_id)), new Set([one.event_id, two.event_id, three.event_id]));
});

test('simultaneous tab persistence uses a shared Web Lock and preserves both events', async () => {
  const shared = memoryStorage(); let chain = Promise.resolve(); let active = 0; let maxActive = 0;
  const locks = { request: (_key, callback) => {
    const result = chain.then(async () => { active++; maxActive = Math.max(maxActive, active); try { return await callback(); } finally { active--; } });
    chain = result.catch(() => {}); return result;
  } };
  const a = fixture(shared); const b = fixture(shared);
  for (const f of [a, b]) { f.env.navigator.locks = locks; f.collector.setSession(session(A)); }
  const one = report(a, 'first_failure'); const two = report(b, 'second_failure');
  await drain();
  assert.deepEqual(new Set(entries(shared).map((item) => item.payload.event_id)), new Set([one.event_id, two.event_id]));
  assert.equal(maxActive, 1);
});

test('an old action keeps its generation when a nested operation fails in the new account', () => {
  const f = fixture(); f.collector.setSession(session(A));
  const oldGeneration = f.collector.getAccountGeneration();
  f.collector.setSession(session(B));
  const error = f.collector.bindGeneration(failure('nested_failure'));
  f.collector.bindGeneration(error, oldGeneration);
  assert.equal(f.collector.report({ kind: 'ui_error', action: 'download', error }), null);
});

test('account switches purge the old owner and keep the new account queue written by another tab', () => {
  const shared = memoryStorage(); const a = fixture(shared); const b = fixture(shared);
  a.collector.setSession(session(A)); b.collector.setSession(session(A));
  report(a, 'old_failure');
  b.collector.setSession(session(B)); const current = report(b, 'current_failure');
  a.collector.setSession(session(B));
  assert.deepEqual(entries(shared).map((item) => item.payload.event_id), [current.event_id]);
});

test('flush acknowledgements do not erase an event appended by another tab during transport', async () => {
  const shared = memoryStorage(); const gate = deferred(); const sent = [];
  const a = fixture(shared, async (_url, options) => { sent.push(JSON.parse(options.body).event_id); if (sent.length === 1) await gate.promise; return { ok: true }; });
  const b = fixture(shared); a.collector.setSession(session(A)); b.collector.setSession(session(A));
  report(a, 'first_failure'); a.env.navigator.onLine = true;
  const flushing = a.collector.flush(); await drain();
  const appended = report(b, 'second_failure'); gate.resolve(); await flushing;
  assert.ok(sent.includes(appended.event_id) || entries(shared).some((item) => item.payload.event_id === appended.event_id));
});

test('40 rounds of both job status and job event polling retain actions and separate per-job poll counters', () => {
  const f = fixture(); f.collector.action('detailPageGenerate');
  for (let round = 0; round < 40; round++) {
    for (const job of [A, B]) {
      f.collector.addBreadcrumb({ type: 'api', method: 'GET', route: `/v1/jobs/${job}`, status: 200 });
      f.collector.addBreadcrumb({ type: 'api', method: 'GET', route: `/v1/jobs/${job}/events?cursor=${round}`, status: round === 39 ? 503 : 200 });
    }
  }
  const crumbs = f.collector.getBreadcrumbs();
  assert.equal(crumbs.length, 5); assert.equal(crumbs[0].action, 'detailPageGenerate');
  assert.ok(crumbs.slice(1).every((crumb) => crumb.count === 40));
  assert.equal(crumbs.filter((crumb) => crumb.route.endsWith('/events') && crumb.status === 503).length, 2);
});

function editorHarness({ picked = [], upload = async () => ({ assetId: REQUEST, url: 'blob:photo' }), transform = toUploadableImage } = {}) {
  const f = fixture(); f.collector.setSession(session(A));
  const toasts = []; const reports = []; let wardrobe = {}; let pending = {};
  const source = read('src/features/editor/Editor.jsx');
  const handlers = source.slice(source.indexOf('  const uploadEditorImage ='), source.indexOf('  // fresh ='));
  const bindings = { clientEvents: f.collector, looksLikeImageFile, toUploadableImage: transform, getUploadValidationError,
    readImageDimensions: async () => ({ width: 100, height: 100 }), api: { uploadPhoto: upload }, projectId: A,
    uid: () => REQUEST, patchImageImport() {}, finishImageImport() {}, waitForImageSource: async () => {},
    setWardrobe: (fn) => { wardrobe = fn(wardrobe); }, setWardrobeUploadLoading: (fn) => { pending = fn(pending); },
    wardrobeInsert() {}, insertImage() {}, pickEditorImageFile: async () => picked,
    runWithConcurrency, MAX_WARDROBE_UPLOADS, WARDROBE_UPLOAD_CONCURRENCY,
    toast: { push: (msg, opts = {}) => {
      toasts.push({ msg, opts });
      if (opts.variant === 'error') {
        const result = f.collector.report({ kind: opts.kind || 'toast_error', action: opts.actionName,
          messageCode: opts.messageCode, blocking: opts.blocking, error: opts.error, context: opts.context, accountGeneration: opts.accountGeneration });
        if (result) reports.push(result);
      }
    } } };
  const methods = new Function(...Object.keys(bindings), `${handlers}; return { uploadEditorImage, pickAndInsertImage };`)(...Object.values(bindings));
  return { ...f, ...methods, toasts, reports, wardrobe: () => wardrobe, pending: () => pending };
}

test('unsupported editor photos pass through the real conversion and validation path as guidance', async (t) => {
  globalProperty(t, 'createImageBitmap', async () => ({ width: 100, height: 100, close() {} }));
  let uploads = 0;
  const h = editorHarness({ upload: async () => { uploads++; } });
  const file = new File(['not HEIC'], 'private.svg', { type: 'image/svg+xml' });
  await h.uploadEditorImage(file);
  assert.equal(h.toasts[0].opts.error.code, 'unsupported_type');
  assert.equal(uploads, 0); assert.equal(h.reports.length, 0); assert.equal(Object.values(h.pending()).reduce((a, b) => a + b, 0), 0);
});

test('conversion which still exceeds 25MB yields file_too_large and does not report an outage', async (t) => {
  globalProperty(t, 'createImageBitmap', async () => ({ width: 8000, height: 8000, close() {} }));
  globalProperty(t, 'document', { createElement: () => ({ getContext: () => ({ drawImage() {} }),
    toBlob: (callback) => callback(new Blob([new Uint8Array(MAX_UPLOAD_BYTES + 1)], { type: 'image/jpeg' })) }) });
  const h = editorHarness({ upload: async () => assert.fail('invalid file must not upload') });
  await h.uploadEditorImage(new File(['small input'], 'private.png', { type: 'image/png' }));
  assert.equal(h.toasts[0].opts.error.code, 'file_too_large'); assert.equal(h.reports.length, 0);
});

for (const partial of [true, false]) {
  test(`multiple editor upload failures report once with the original Error, partial=${partial}`, async (t) => {
    globalProperty(t, 'createImageBitmap', async () => ({ width: 100, height: 100, close() {} }));
    const original = failure('upload_failed');
    const picked = ['one.jpg', 'two.jpg'].map((name) => new File(['jpeg'], name, { type: 'image/jpeg' }));
    const h = editorHarness({ picked, upload: async (_project, photo) => {
      if (!partial || photo.filename === 'two.jpg') throw original;
      return { assetId: A, url: 'blob:photo' };
    } });
    await h.pickAndInsertImage();
    assert.equal(h.toasts.length, 1); assert.equal(h.toasts[0].opts.variant, 'error');
    assert.equal(h.toasts[0].opts.error, original); assert.equal(h.reports.length, 1);
    assert.equal(h.reports[0].request_id, REQUEST); assert.equal(h.reports[0].kind, 'upload_error');
    assert.equal(h.reports[0].blocking, !partial);
  });
}

test('a batch with both input guidance and an API failure selects the actual outage for its single report', async (t) => {
  globalProperty(t, 'createImageBitmap', async () => ({ width: 100, height: 100, close() {} }));
  const original = failure('upload_failed');
  const picked = [new File(['svg'], 'private.svg', { type: 'image/svg+xml' }), new File(['jpg'], 'private.jpg', { type: 'image/jpeg' })];
  const h = editorHarness({ picked, upload: async () => { throw original; } }); await h.pickAndInsertImage();
  assert.equal(h.toasts[0].opts.error, original); assert.equal(h.reports.length, 1);
});

test('editor actions which began before account change cannot report even if failure comes from conversion', async () => {
  const gate = deferred(); const original = failure('conversion_failed');
  const h = editorHarness({ transform: () => gate.promise });
  const pending = h.uploadEditorImage(new File(['image'], 'private.jpg', { type: 'image/jpeg' }));
  h.collector.setSession(session(B)); gate.resolve(Promise.reject(original)); await pending;
  assert.equal(h.reports.length, 0); assert.equal(entries(h.storage).length, 0);
});
