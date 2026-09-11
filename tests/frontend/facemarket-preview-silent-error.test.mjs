import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

test('선택 미리보기 실패만 네트워크와 HTTP 오류를 콘솔에 남기지 않아요', async t => {
  const key = `__previewSilent${Math.random().toString(36).slice(2)}`;
  globalThis[key] = { mode: 'http' };
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({ configFile: false, envDir: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true }, resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    ssr: { noExternal: true }, appType: 'custom', plugins: [{ name: 'preview-silent-http', enforce: 'pre', resolveId(id) {
      // Vite's alias plugin can resolve @ before this plugin sees the import.
      // Never load the real auth client or depend on the developer's .env files.
      if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0preview-auth';
    }, load(id) {
      if (id === '\0preview-auth') return `export const supabase={auth:{getSession:async()=>({data:{session:{access_token:'token'}}})}};`;
    }}],
  });
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => globalThis[key].mode === 'network'
    ? Promise.reject(new Error('offline'))
    : ({ ok: false, status: 503, json: async () => ({ error: { code: 'preview_unavailable', message: 'unavailable' } }) });
  const logs = [];
  t.mock.method(console, 'error', (...args) => logs.push(args));
  t.after(async () => { globalThis.fetch = oldFetch; await server.close(); delete globalThis[key]; });
  const api = await server.ssrLoadModule('/src/lib/api/facemarket.js');

  await assert.rejects(api.getPublicationPreviewUrl('publication-1'));
  assert.equal(logs.length, 0);
  globalThis[key].mode = 'network';
  await assert.rejects(api.getPublicationPreviewUrl('publication-1'));
  assert.equal(logs.length, 0);

  await assert.rejects(api.getPayoutStatements());
  assert.equal(logs.length, 1, 'other requests retain the shared adapter logging');
});
