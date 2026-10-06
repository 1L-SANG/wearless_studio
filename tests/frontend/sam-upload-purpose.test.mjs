import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

function uploadHarness({ cancelled = false } = {}) {
  const source = read('src/lib/api/httpAdapter.js');
  const http = source.slice(source.indexOf('export async function http('), source.indexOf('\n/* 톤 에디터')).replace('export ', '');
  const uploads = source.slice(source.indexOf('export async function uploadPhoto('), source.indexOf('// ---- 매칭 의류')).replace('export ', '');
  const pickerStart = source.indexOf('  async pickAnyImage(');
  const pickers = source.slice(pickerStart, source.indexOf('  // AG-06', pickerStart));
  const requests = [];
  const file = Object.assign(new Blob(['image'], { type: 'image/png' }), { name: 'picked.png' });
  const bindings = {
    supabase: { auth: { getSession: async () => ({ data: { session: { access_token: 'test-token' } } }) } },
    BASE_URL: '', DEVICE_HEADER: 'test-device', readDeviceToken: () => null,
    isBrowserOffline: () => false, absolutizeAssetUrls: (value) => value,
    document: { createElement: () => ({ files: cancelled ? [] : [file], click() { this.onchange(); } }) },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      const body = url === '/v1/assets/upload-url'
        ? { assetId: 'asset-test', uploadUrl: 'https://upload.test/image' }
        : {};
      return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
    },
  };
  const adapter = new Function(...Object.keys(bindings), `${http}\n${uploads}\nreturn { uploadPhoto, ${pickers} };`)(...Object.values(bindings));
  return { adapter, requests, file };
}

function assertUploadPurpose(requests, purpose) {
  assert.equal(requests.length, 3);
  assert.equal(requests[0].url, '/v1/assets/upload-url');
  assert.equal(requests[0].method, 'POST');
  assert.equal(requests[1].method, 'PUT');
  assert.equal(requests[2].url, '/v1/assets/asset-test/complete');
  assert.equal(requests[2].method, 'POST');
  for (const request of [requests[0], requests[2]]) {
    const body = JSON.parse(request.body);
    assert.equal(body.projectId, 'project-test');
    assert.equal(body.purpose, purpose);
  }
}

for (const [method, purpose] of [
  ['pickAnyImage', 'storyboard_insert'], ['pickRefImage', 'mood_reference'],
]) {
  test(`${method} sends ${purpose} on upload URL and completion requests`, async () => {
    const { adapter, requests } = uploadHarness();
    const result = await adapter[method]('project-test');
    assert.deepEqual(result, { assetId: 'asset-test', url: '/v1/assets/asset-test/file' });
    assertUploadPurpose(requests, purpose);
  });

  test(`${method} cancellation does not issue an upload request`, async () => {
    const { adapter, requests } = uploadHarness({ cancelled: true });
    assert.equal(await adapter[method]('project-test'), null);
    assert.deepEqual(requests, []);
  });
}

for (const [path, purpose] of [
  ['src/features/editor/Editor.jsx', 'editor_insert'],
  ['src/features/mannequin/ToneEditor.jsx', 'tone_render'],
]) {
  test(`${path} sends ${purpose} on upload URL and completion requests`, async () => {
    const { adapter, requests, file } = uploadHarness();
    const source = read(path);
    const start = source.indexOf('await api.uploadPhoto(');
    const call = source.slice(start, source.indexOf(';', start));
    await new Function('api', 'projectId', 'prepared', 'file', 'blob', `return (async () => ${call})();`)(
      adapter, 'project-test', file, file, file,
    );
    assertUploadPurpose(requests, purpose);
  });
}

test('product uploads keep the default upload purpose', async () => {
  const { adapter, requests, file } = uploadHarness();
  await adapter.uploadPhoto('project-test', { filename: file.name, mime: file.type, blob: file });
  assertUploadPurpose(requests, 'upload');
});
