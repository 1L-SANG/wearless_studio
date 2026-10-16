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
