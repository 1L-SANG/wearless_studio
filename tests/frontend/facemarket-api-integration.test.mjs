import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

async function apiHarness(t) {
  const server = await createServer({
    configFile: false,
    root: new URL('../..', import.meta.url).pathname,
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    ssr: { noExternal: true },
    appType: 'custom',
    plugins: [{
      name: 'facemarket-api-integration-auth', enforce: 'pre',
      resolveId(id) { if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0fm-integration-auth'; },
      load(id) {
        if (id === '\0fm-integration-auth') return `export const supabase = { auth: {
          getSession: async () => ({ data: { session: { access_token: 'session-token' } } }),
        } };`;
      },
    }],
  });
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: { getItem: () => 'approved-device' } };
  t.after(async () => { globalThis.window = previousWindow; await server.close(); });
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return { api: await server.ssrLoadModule('/src/lib/api/facemarket.js'), calls };
}

test('얼굴 렌더 워밍은 JSON 객체를 한 번만 직렬화해서 보내요', async t => {
  const { api, calls } = await apiHarness(t);
  await api.warmFaceRender('model-1');
  assert.match(calls[0].url, /\/face-render\/warm$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { modelId: 'model-1' });
  assert.equal(calls[0].options.headers.Authorization, 'Bearer session-token');
});

test('관리자 테스트컷 업로드와 비공개 조회도 승인 기기 헤더를 전송해요', async t => {
  const { api, calls } = await apiHarness(t);
  const file = new File(['test'], 'test-cut.jpg', { type: 'image/jpeg' });
  await api.adminUploadModelTestCuts('model-1', [file], 'closeup');
  const preview = await api.adminFetchModelTestCutUrl('/v1/facemarket/admin/models/model-1/test-cuts/cut-1/image');
  try {
    assert.equal(calls.length, 2);
    for (const { options } of calls) {
      assert.equal(options.headers['X-Admin-Device'], 'approved-device');
      assert.equal(options.headers.Authorization, 'Bearer session-token');
    }
    assert.equal(calls[0].options.body.get('images').name, 'test-cut.jpg');
    assert.equal(calls[0].options.headers['Content-Type'], undefined, 'multipart 경계는 브라우저가 정해요');
  } finally { URL.revokeObjectURL(preview); }
});
