import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import JSZip from 'jszip';

let vite, logic, service, adapter, supabase, mock, DB, exports;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', logLevel: 'silent' });
  logic = await vite.ssrLoadModule('/src/features/editor/detailPublish.js');
  service = await vite.ssrLoadModule('/src/lib/api/detailPublications.js');
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
  ({ api: mock } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  exports = await vite.ssrLoadModule('/src/features/editor/editorExport.js');
});
after(() => vite?.close());
const files = [1, 2].map((index) => ({ index, url: `https://images.example.test/pub/u/p/0${index}.jpg`, width: 860, height: 300, bytes: 3 }));
const blobs = [new Blob(['one'], { type: 'image/jpeg' }), new Blob(['two'], { type: 'image/jpeg' })];
const uploads = [1, 2].map((index) => ({ index, uploadUrl: `put-${index}`, uploadToken: `token-${index}` }));
const deps = (api, overrides = {}) => ({ api, renderStorefrontFiles: async () => ({ files: blobs, width: 860 }),
  readDimensions: async () => ({ width: 860, height: 300 }), fetchImpl: async () => ({ ok: true }), ...overrides });
const options = { projectId: 'p', productName: '셔츠', preset: 'smartstore' };

test('builds html with one img per file', () => {
  const html = logic.buildPublicationHtml(files, { productName: '셔츠', width: 860 });
  assert.equal((html.match(/<img /g) || []).length, 2);
  assert.match(html, /^<div style="text-align:center;">\n/);
  assert.match(html, /alt="셔츠 상세 1" style="display:block;width:100%;max-width:860px;margin:0 auto;"/);
  assert.deepEqual(logic.PUBLISH_PRESETS.map((p) => p.id), ['smartstore', 'coupang', 'high']);
});
test('escapes product name in alt', () => {
  const html = logic.buildPublicationHtml(files, { productName: '<셔츠> "A" & \'B\'', width: 860 });
  assert.match(html, /alt="&lt;셔츠&gt; &quot;A&quot; &amp; &#39;B&#39; 상세 1"/);
});
test('url list is newline separated', () => assert.equal(logic.buildPublicationUrlList(files), files.map((f) => f.url).join('\n')));
test('upload retries failed slice and reports remaining', async () => {
  const calls = [], progress = [];
  const api = { createDetailPublication: async () => ({ publicationId: 'p', uploads }), completeDetailPublication: async () => ({ publicationId: 'p', version: 1, files }) };
  await logic.publishDetailPage([], options, deps(api, { fetchImpl: async (url) => { calls.push(url); return { ok: url !== 'put-2' || calls.filter((u) => u === url).length > 1 }; } }));
  assert.deepEqual(calls, ['put-1', 'put-2', 'put-2']);
  const result = await logic.uploadPublicationFiles(blobs, uploads, { fetchImpl: async () => ({ ok: false }), onProgress: (...args) => progress.push(args) });
  assert.deepEqual(result.failed, [1, 2]);
  assert.ok(progress.length);
});
test('publishDetailPage retries complete once after files_missing', async () => {
  const calls = []; let complete = 0, submitted;
  const api = { createDetailPublication: async (_id, body) => { submitted = body; return { publicationId: 'p', uploads }; }, completeDetailPublication: async () => {
    complete++; if (complete === 1) throw Object.assign(new Error('missing'), { status: 409, code: 'files_missing', missing: [2] });
    return { publicationId: 'p', version: 2, files, expiresAt: null };
  } };
  let size = 0;
  const result = await logic.publishDetailPage([], options, deps(api, { readDimensions: async () => ({ width: 860, height: ++size * 100 }), fetchImpl: async (url) => { calls.push(url); return { ok: true }; } }));
  assert.deepEqual(calls, ['put-1', 'put-2', 'put-2']);
  assert.equal(complete, 2);
  assert.deepEqual(submitted.files.map((f) => [f.index, f.byteSize, f.width, f.height]), [[1, 3, 860, 100], [2, 3, 860, 200]]);
  assert.match(result.html, /셔츠 상세 1/);
});
test('listDetailPublications maps 404 to disabled', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  t.mock.method(globalThis, 'fetch', async () => new Response('{}', { status: 404 }));
  assert.deepEqual(await service.listDetailPublications('p'), { items: [], disabled: true });
  assert.equal(typeof adapter.createDetailPublication, 'function');
  assert.equal(typeof adapter.completeDetailPublication, 'function');
  assert.equal(typeof adapter.deleteDetailPublication, 'function');
});
test('complete adapter preserves missing slice numbers from the server error', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'files_missing', missing: [2] } }), { status: 409 }));
  await assert.rejects(service.completeDetailPublication('p', { uploads }), (error) => error.code === 'files_missing' && error.missing[0] === 2);
});
test('capture dependency is available for the independent publication renderer', () => assert.equal(typeof exports.captureBlockCanvasSlices, 'function'));
test('failed uploads stop after three attempts and retain local ZIP files', async () => {
  let calls = 0, completes = 0;
  const api = { createDetailPublication: async () => ({ publicationId: 'p', uploads }), completeDetailPublication: async () => { completes++; } };
  await assert.rejects(logic.publishDetailPage([], options, deps(api, { fetchImpl: async () => { calls++; return { ok: false }; } })), /지금은 올리지 못했어요/);
  let failure;
  try { await logic.publishDetailPage([], options, deps(api, { fetchImpl: async () => ({ ok: false }) })); } catch (error) { failure = error; }
  assert.equal(calls, 6); assert.equal(completes, 0); assert.deepEqual(failure.files, blobs);
  const zip = await JSZip.loadAsync(await (await logic.downloadPublicationZip(failure.files, '한글 상품')).arrayBuffer());
  assert.deepEqual(Object.keys(zip.files), ['01.jpg', '02.jpg']);
  assert.equal(await zip.file('02.jpg').async('string'), 'two');
});
test('render failure creates no server publication and JSON requests time out', async () => {
  let called = false;
  const api = { createDetailPublication: () => { called = true; return new Promise(() => {}); } };
  await assert.rejects(logic.publishDetailPage([], options, deps(api, { renderStorefrontFiles: async () => { throw new Error('capture failed'); } })), /capture failed/);
  assert.equal(called, false);
  await assert.rejects(logic.publishDetailPage([], options, deps(api, { timeouts: { network: 5 } })), /시간/);
  assert.deepEqual(await logic.uploadPublicationFiles([blobs[0]], [uploads[0]], { fetchImpl: () => new Promise(() => {}), timeoutMs: 5 }), { failed: [1] });
});
test('create times out at eight seconds and complete at thirty seconds', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const [operation, timeout] of [['create', 8000], ['complete', 30000]]) {
    const calls = [];
    const api = {
      createDetailPublication: () => { calls.push('create'); return operation === 'create' ? new Promise(() => {}) : { publicationId: 'p', uploads }; },
      completeDetailPublication: () => { calls.push('complete'); return new Promise(() => {}); },
    };
    let timedOut = false;
    const pending = logic.publishDetailPage([], options, deps(api));
    pending.catch(() => { timedOut = true; });
    const failure = assert.rejects(pending, /시간/);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.at(-1), operation);
    t.mock.timers.tick(timeout - 1);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(timedOut, false);
    t.mock.timers.tick(1);
    await failure;
  }
});
test('mock publication workflow succeeds and exports ZIP without external image requests', async (t) => {
  DB.detailPublications = [];
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('mock must remain local'); });
  const result = await logic.publishDetailPage([], { ...options, projectId: DB.project.id }, deps(mock, { fetchImpl: undefined }));
  assert.match(result.files[0].url, /^https:\/\/images\.example\.test\/pub\//);
  assert.equal((await mock.listDetailPublications(DB.project.id)).items.length, 1);
  const zip = await logic.downloadPublicationZip(result.files, '셔츠');
  assert.equal(await (await JSZip.loadAsync(await zip.arrayBuffer())).file('01.jpg').async('string'), 'one');
  await mock.deleteDetailPublication(result.publicationId);
  assert.equal((await mock.listDetailPublications(DB.project.id)).items.length, 0);
});
test('zip after publish uses local blobs', async () => {
  const api = { createDetailPublication: async () => ({ publicationId: 'p', uploads }),
    completeDetailPublication: async () => ({ publicationId: 'p', version: 1, files }) };
  const result = await logic.publishDetailPage([], options, deps(api));
  const zip = await logic.downloadPublicationZip(result.files, '셔츠', {
    fetchImpl: async () => { assert.fail('ZIP must use the rendered images without fetching the CDN'); },
  });
  const contents = await JSZip.loadAsync(await zip.arrayBuffer());
  assert.equal(await contents.file('01.jpg').async('string'), 'one');
  assert.equal(await contents.file('02.jpg').async('string'), 'two');
});
test('publication releases capture protection before waiting for network uploads', async () => {
  let released = false;
  const api = { createDetailPublication: async () => {
    assert.equal(released, true); return { publicationId: 'p', uploads };
  }, completeDetailPublication: async () => ({ publicationId: 'p', version: 1, files }) };
  await logic.publishDetailPage([], options, deps(api, { onRendered: () => { released = true; } }));
});
