import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

test('draft uploads validate the original account and operation at URL, R2 and completion boundaries', async (t) => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  let user = 'original'; let current = true;
  globalThis.__draftUploadAuth = { auth: { getSession: async () => ({ data: {
    session: { user: { id: user }, access_token: 'test-token' },
  } }) } };
  const vite = await createServer({ root, configFile: false, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root}/src` } },
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('https://api.test') },
    server: { middlewareMode: true, watch: null, hmr: false },
    plugins: [{ name: 'draft-upload-auth', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0draft-upload-auth'; },
      load(id) { if (id === '\0draft-upload-auth') return 'export const supabase = globalThis.__draftUploadAuth;'; },
    }],
  });
  t.after(async () => { await vite.close(); delete globalThis.__draftUploadAuth; });
  const { httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const calls = []; let boundary; let change;
  t.mock.method(globalThis, 'fetch', async (url) => {
    calls.push(url);
    if (url.endsWith('upload-url')) {
      if (boundary === 'r2') change();
      return Response.json({ assetId: 'owned-asset', uploadUrl: 'https://r2.test/upload' });
    }
    if (url === 'https://r2.test/upload') {
      if (boundary === 'complete') change();
      return new Response('ok');
    }
    return Response.json({});
  });
  for (const changed of ['account', 'operation']) {
    for (const [stage, expectedCalls] of [['url', 0], ['r2', 1], ['complete', 2]]) {
      user = 'original'; current = true; calls.length = 0; boundary = stage;
      change = () => { if (changed === 'account') user = 'new-account'; else current = false; };
      if (stage === 'url') change();
      await assert.rejects(httpAdapter.uploadDraftSlotPhoto({ filename: 'mine.png', mime: 'image/png', blob: new Blob(['photo']) },
        { expectedUserId: 'original', isCurrent: () => current }),
      (error) => error.code === (changed === 'account' ? 'auth_user_changed' : 'draft_request_cancelled'));
      assert.equal(calls.length, expectedCalls, `${changed} change before ${stage}`);
    }
  }
  user = 'original'; current = true; boundary = 'none'; calls.length = 0;
  await httpAdapter.uploadDraftSlotPhoto({ filename: 'mine.png', mime: 'image/png', blob: new Blob(['photo']) },
    { expectedUserId: 'original', isCurrent: () => current });
  assert.equal(calls.length, 3);
});
