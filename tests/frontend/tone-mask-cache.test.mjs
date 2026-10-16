import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('reopening tone editing fetches the selected mask despite an older cached response', async (t) => {
  const server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL('../..', import.meta.url)),
    logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, hmr: false },
    plugins: [{
      name: 'tone-mask-auth',
      enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0tone-mask-auth'; },
      load(id) {
        if (id === '\0tone-mask-auth') {
          return 'export const supabase = { auth: { getSession: async () => ({ data: { session: { access_token: "test" } } }) } };';
        }
      },
    }],
  });
  t.after(() => server.close());
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let selectedMask = 'fal-mask';
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    const bytes = options.cache === 'no-store' ? selectedMask : 'fal-mask';
    return new Response(bytes, { headers: { 'Content-Type': 'image/png' } });
  };
  const { httpAdapter } = await server.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const controller = new AbortController();
  assert.equal(await (await httpAdapter.toneEditorMask('p', 'A-1')).text(), 'fal-mask');
  selectedMask = 'sam2-mask';
  assert.equal(await (await httpAdapter.toneEditorMask('p', 'A-1', { signal: controller.signal })).text(), 'sam2-mask');
  assert.equal(calls[1].options.cache, 'no-store');
  assert.equal(calls[1].options.signal, controller.signal);
  assert.equal(calls[1].options.headers.Authorization, 'Bearer test');
  assert.equal(calls[0].url, calls[1].url);

  await httpAdapter.toneEditorSource('p', 'A-1');
  await httpAdapter.detailCutPreview('p', 'j', 'b');
  assert.equal(calls[2].options.cache, undefined);
  assert.equal(calls[3].options.cache, undefined);
});
