import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';

let vite, httpAdapter, mockAdapter;
const cacheDir = mkdtempSync(join(tmpdir(), 'product-visuals-'));
const originalFetch = globalThis.fetch;
before(async () => {
  vite = await createServer({
    configFile: false, root: new URL('../..', import.meta.url).pathname,
    optimizeDeps: { noDiscovery: true, include: [] }, cacheDir, logLevel: 'silent', appType: 'custom',
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, ws: false },
    plugins: [{
      name: 'product-visuals-auth', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0product-visuals-auth'; },
      load(id) { if (id === '\0product-visuals-auth') return 'export const supabase={auth:{getSession:async()=>({data:{session:{access_token:"test-token",user:{id:"seller"}}}})}};'; },
    }],
  });
  ({ httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ mockAdapter } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
});
after(async () => {
  globalThis.fetch = originalFetch;
  await vite?.close();
  rmSync(cacheDir, { recursive: true, force: true });
});

test('HTTP product visuals uses the authenticated GET endpoint and preserves all states', async () => {
  for (const tidyStatus of ['ready', 'processing', 'failed', 'off']) {
    const expected = { front: { tidyStatus, tidyUrl: tidyStatus === 'ready' ? 'https://images.wearless.kr/tidy.png' : null,
      cutoutStatus: 'ready', cutoutUrl: null, width: 768, height: 1024 } };
    globalThis.fetch = async (url, options) => {
      assert.ok(String(url).endsWith('/v1/projects/project-1/product/visuals'));
      assert.equal(options.method ?? 'GET', 'GET');
      assert.equal(options.headers.Authorization, 'Bearer test-token');
      return new Response(JSON.stringify(expected), { status: 200, headers: { 'content-type': 'application/json' } });
    };
    assert.deepEqual(await httpAdapter.getProductVisuals('project-1'), expected);
  }
});

test('mock product visuals returns a deterministic ready image without remote fetches', async () => {
  globalThis.fetch = () => { throw new Error('mock must not fetch'); };
  const result = await mockAdapter.getProductVisuals('project-1');
  assert.deepEqual(Object.keys(result), ['front']);
  assert.deepEqual(Object.keys(result.front).sort(), ['cutoutStatus', 'cutoutUrl', 'height', 'tidyStatus', 'tidyUrl', 'width']);
  assert.equal(result.front.tidyStatus, 'ready');
  assert.equal(result.front.cutoutStatus, 'ready');
  assert.match(result.front.tidyUrl, /^data:image\/svg\+xml;/);
  assert.equal(result.front.cutoutUrl, result.front.tidyUrl);
  assert.equal(result.front.width, 768);
  assert.equal(result.front.height, 1024);
  result.front.tidyStatus = 'failed';
  assert.equal((await mockAdapter.getProductVisuals('project-1')).front.tidyStatus, 'ready');
});

test('draft visuals HTTP adapters carry ownership token and project promotion source IDs', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ state: 'processing', jobId: 'job' }),
      { status: 200, headers: { 'content-type': 'application/json' } });
  };
  await httpAdapter.prepareDraftVisuals('owner-token', { role: 'product', sourceAssetIds: ['front'] });
  await httpAdapter.getDraftVisuals('owner-token');
  await httpAdapter.promoteDraftVisuals({ projectId: 'project', jobId: 'job', sourceAssetIds: ['uploaded'] });
  assert.ok(calls[0].url.endsWith('/v1/draft-slot/visuals:prepare'));
  assert.equal(calls[0].options.headers['X-Draft-Token'], 'owner-token');
  assert.deepEqual(JSON.parse(calls[0].options.body), { role: 'product', sourceAssetIds: ['front'] });
  assert.ok(calls[1].url.endsWith('/v1/draft-slot/visuals'));
  assert.equal(calls[1].options.method, 'GET');
  assert.ok(calls[2].url.endsWith('/v1/draft-slot/visuals:promote'));
  assert.deepEqual(JSON.parse(calls[2].options.body).sourceAssetIds, ['uploaded']);
});

test('mock draft visuals joins preparation, rejects ownership loss and hides replaced photos', async () => {
  globalThis.fetch = () => { throw new Error('mock must not fetch'); };
  const payload = { product: { colors: [{ isBase: true, images: [{ id: 'front', slot: 'Front', src: 'raw' }] }] } };
  const saved = await mockAdapter.putDraftSlot({ token: null, payload, deviceLabel: 'test', photosPending: false });
  mockAdapter.setDraftVisualsScenario({ status: 'ready', tidyUrl: 'mock-tidy' });
  try {
    const first = await mockAdapter.prepareDraftVisuals(saved.token, { role: 'product', sourceAssetIds: ['front'] });
    assert.equal((await mockAdapter.prepareDraftVisuals(saved.token, { role: 'product', sourceAssetIds: ['front'] })).jobId, first.jobId);
    assert.equal((await mockAdapter.getDraftVisuals(saved.token)).product.front.tidyUrl, 'mock-tidy');
    await assert.rejects(mockAdapter.getDraftVisuals('foreign'), { code: 'token_mismatch' });
    payload.product.colors[0].images[0].id = 'replaced';
    await mockAdapter.putDraftSlot({ token: saved.token, payload, deviceLabel: 'test', photosPending: false });
    assert.equal((await mockAdapter.getDraftVisuals(saved.token)).product, null);
  } finally {
    await mockAdapter.deleteDraftSlot(saved.token);
    mockAdapter.setDraftVisualsScenario(null);
  }
});

test('custom registration carries the draft job through the real HTTP boundary', async () => {
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), body: JSON.parse(options.body) };
    return new Response(JSON.stringify({ item: { id: 'custom' }, analysis: { matchClothing: [] } }),
      { status: 200, headers: { 'content-type': 'application/json' } });
  };
  await httpAdapter.addCustomMatchItem('project', { assetIds: ['source'], draftVisualJobId: 'prepared-job' });
  assert.deepEqual(request.body, { assetIds: ['source'], draftVisualJobId: 'prepared-job' });
});
