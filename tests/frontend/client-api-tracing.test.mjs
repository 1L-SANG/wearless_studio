import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const REQUEST = 'cdcc757c-c732-4102-9114-bcfb8511163c';
const PROJECT = '2df3af7f-7b3f-4a6d-bf29-147a3c59a567';

test('all adapter call paths trace only own APIs, preserve request IDs, and match the collector build SHA', async (t) => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const origin = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://app.test', pathname: '/editor/' + PROJECT } });
  globalThis.__clientEventAuth = { auth: { getSession: async () => ({ data: { session: { user: { id: PROJECT }, access_token: 'test-token' } } }) } };
  const vite = await createServer({ root, configFile: false, logLevel: 'silent',
    resolve: { alias: { '@': `${root}/src` } },
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('https://api.test'), 'import.meta.env.VITE_API_MODE': JSON.stringify('http'),
      'import.meta.env.DEV': 'false', __BUILD_SHA__: JSON.stringify('test-vercel-sha') },
    server: { middlewareMode: true, watch: null },
    plugins: [{ name: 'client-event-auth', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0client-event-auth'; },
      load(id) { if (id === '\0client-event-auth') return 'export const supabase = globalThis.__clientEventAuth;'; } }],
  });
  t.after(async () => { await vite.close(); delete globalThis.__clientEventAuth;
    if (origin) Object.defineProperty(globalThis, 'location', origin); else delete globalThis.location; });
  const httpModule = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const personal = await vite.ssrLoadModule('/src/lib/api/personalization.js');
  const market = await vite.ssrLoadModule('/src/lib/api/facemarket.js');
  const { clientEvents } = await vite.ssrLoadModule('/src/lib/clientEvents.js');
  const { fetchPhotoBytes } = await vite.ssrLoadModule('/src/lib/photoBytes.js');
  const calls = [];
  let scenario = 'error';
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (url.startsWith('blob:') || url.includes('r2.test') || url.includes('cdn.test')) return new Response('photo', { headers: { 'Content-Type': 'image/jpeg' } });
    if (scenario === 'upload') return url.endsWith('upload-url')
      ? Response.json({ assetId: PROJECT, uploadUrl: 'https://r2.test/upload' }, { headers: { 'X-Request-Id': REQUEST } })
      : Response.json({ url: 'https://cdn.test/photo' }, { headers: { 'X-Request-Id': REQUEST } });
    if (scenario === 'job') return Response.json(url.includes('/jobs/')
      ? { id: PROJECT, status: 'error', errorMessage: '생성 실패' } : { jobId: PROJECT }, { headers: { 'X-Request-Id': REQUEST } });
    return Response.json({ error: { code: 'test_failure', message: 'request failed' } }, { status: 500, headers: { 'X-Request-Id': REQUEST } });
  });
  const photo = { filename: 'private-photo.jpeg', fileBlob: new Blob(['photo'], { type: 'image/jpeg' }), angle: 'front' };
  const cases = [
    ['http', () => httpModule.http('/v1/projects/' + PROJECT, { suppressErrorLog: true })],
    ['httpBlob', () => httpModule.httpBlob('/v1/assets/' + PROJECT + '/bytes')],
    ['publicHttp', () => httpModule.httpAdapter.publicAnalyze({ colors: [{ isBase: true, images: [{ src: 'blob:photo', slot: 'Front' }] }] })],
    ['personalization multipart', () => personal.uploadFacePhoto(photo)],
    ['personalization gated file', () => personal.fetchFacePhotoUrl('/v1/personalization/face-photos/front/file')],
    ['personalization generated file', () => personal.fetchGenerationResultUrl('/v1/personalization/generations/' + PROJECT + '/results/0/file')],
    ['personalization delete', () => personal.deleteFacePhoto('front')],
    ['facemarket multipart', () => market.uploadEnrollmentPhoto({ ...photo, enrollmentId: PROJECT, slot: 'front' })],
    ['facemarket gated bytes', () => market.fetchLicenseFaceUrl('/v1/facemarket/licenses/' + PROJECT + '/face')],
    ['facemarket public license', () => market.verifyLicensePublic(PROJECT)],
    ['facemarket public publication', () => market.verifyPublicationPublic(PROJECT)],
    ['photo bytes', () => fetchPhotoBytes('https://api.test/v1/assets/' + PROJECT + '/file')],
  ];
  for (const [name, run] of cases) {
    await assert.rejects(run(), (error) => error.requestId === REQUEST, name);
    const own = calls.at(-1);
    assert.ok(own.url.startsWith('https://api.test/v1/'), name);
    assert.match(new Headers(own.options.headers).get('X-Client-Request-Id'), /^[0-9a-f-]{36}$/, name);
    assert.equal(own.options.redirect, 'error', name);
  }
  scenario = 'upload';
  await httpModule.uploadPhoto(PROJECT, { filename: 'private.jpeg', mime: 'image/jpeg', blob: photo.fileBlob });
  const r2 = calls.find((call) => call.url === 'https://r2.test/upload');
  assert.equal(new Headers(r2.options.headers).has('X-Client-Request-Id'), false);
  assert.equal(r2.options.method, 'PUT');
  for (const target of ['https://cdn.test/photo', 'https://r2.test/photo', 'blob:photo']) await fetchPhotoBytes(target);
  for (const call of calls.filter((call) => !call.url.startsWith('https://api.test'))) assert.equal(new Headers(call.options?.headers).has('X-Client-Request-Id'), false);
  scenario = 'job';
  await assert.rejects(httpModule.httpAdapter.generateMannequins(PROJECT), { requestId: REQUEST, jobId: PROJECT });
  await assert.rejects(personal.startGeneration({ projectId: PROJECT }), { requestId: REQUEST, jobId: PROJECT, message: '생성 실패' });
  const payload = clientEvents.report({ kind: 'generation_error', action: 'generate', blocking: true, error: new Error('technical') });
  assert.equal(payload.build_sha, 'test-vercel-sha');
  assert.equal(clientEvents.getBreadcrumbs().filter((crumb) => crumb.action === 'poll').length, 1);
  assert.ok(clientEvents.getBreadcrumbs().some((crumb) => crumb.error_code === 'job_error' && crumb.count === 2));
  assert.equal(JSON.stringify(payload).includes('private'), false);
});
