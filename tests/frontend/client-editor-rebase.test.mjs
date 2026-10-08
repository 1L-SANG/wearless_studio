import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { attachResponseError, clientApiFetch, readClientResponse } from '../../src/lib/api/clientRequest.js';

const read = (file) => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
const editor = read('src/features/editor/Editor.jsx');
const exporter = read('src/features/editor/editorExport.js');
const REQUEST = 'cdcc757c-c732-4102-9114-bcfb8511163c';

function preflight(t, fetchImpl) {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://api.test' } });
  t.after(() => { if (prior) Object.defineProperty(globalThis, 'location', prior); else delete globalThis.location; });
  t.mock.method(globalThis, 'fetch', fetchImpl);
  // 충돌 상태의 RED 검증도 main 쪽 함수로 실행한다. 별도 테스트가 표식 제거를 확인한다.
  const head = exporter.replace(/^<<<<<<< HEAD\n([\s\S]*?)^=======\n[\s\S]*?^>>>>>>>.*\n/gm, '$1');
  const source = head.slice(head.indexOf('async function preflightImages'), head.indexOf('async function prepareClone'));
  class FileReader {
    readAsDataURL() { this.result = 'data:image/jpeg;base64,cached'; this.onload(); }
  }
  return new Function('clientApiFetch', 'readClientResponse', 'attachResponseError', 'fetch', 'FileReader', 'isAssetBytesUrl',
    `${source}; return preflightImages;`)(clientApiFetch, readClientResponse, attachResponseError, globalThis.fetch, FileReader,
    (src) => src.includes('/v1/assets/') && src.includes('/bytes'));
}

function image(src) {
  return { getAttribute: () => src, removeAttribute(name) { assert.equal(name, 'srcset'); },
    setAttribute(name, value) { assert.equal(name, 'src'); this.result = value; } };
}

test('rebase keeps the main download modal, imageCache and raster exporter without conflict markers or removed policy', () => {
  for (const source of [editor, exporter]) assert.doesNotMatch(source, /^(?:<<<<<<<|=======|>>>>>>>)/m);
  assert.match(editor, /setDlError\(e\?\.message/);
  assert.match(editor, /<EditorDownloadModal[\s\S]*?error=\{dlError\}/);
  assert.match(editor, /<ClientErrorCapture[\s\S]*?error=\{dlOriginalError\}/);
  assert.match(exporter, /imageCache\.set\(src/);
  assert.match(exporter, /rasterizeExportSvg/);
  assert.doesNotMatch(exporter, /bySrc|coreFailed|const isRemoteSrc/);
});

test('imageCache reuses successful bytes and traces only own API fetches', async (t) => {
  const calls = [];
  const run = preflight(t, async (src, options) => {
    calls.push([src, options]);
    return new Response('photo', { headers: { 'content-type': 'image/jpeg' } });
  });
  const own = 'https://api.test/v1/assets/' + REQUEST + '/bytes';
  const imgs = [image(own), image(own), image('https://cdn.test/photo.jpg')];
  const cache = new Map();
  assert.deepEqual(await run({ querySelectorAll: () => imgs }, cache), { softFailed: 0 });
  assert.equal(calls.length, 2);
  assert.ok(new Headers(calls[0][1]?.headers).get('X-Client-Request-Id'));
  assert.equal(new Headers(calls[1][1]?.headers).has('X-Client-Request-Id'), false);
  assert.ok(imgs.every((img) => img.result === 'data:image/jpeg;base64,cached'));
  await run({ querySelectorAll: () => [image(own)] }, cache);
  assert.equal(calls.length, 2);
});

test('failed cached image fetch preserves request ID and is retried with a fresh cache entry', async (t) => {
  let calls = 0;
  const run = preflight(t, async () => {
    calls++;
    return new Response('unavailable', { status: 503, headers: { 'X-Request-Id': REQUEST } });
  });
  const cache = new Map();
  const clone = { querySelectorAll: () => [image('https://api.test/v1/assets/' + REQUEST + '/bytes')] };
  await assert.rejects(run(clone, cache), { requestId: REQUEST, status: 503 });
  assert.equal(cache.size, 0);
  await assert.rejects(run(clone, cache), { requestId: REQUEST });
  assert.equal(calls, 2);
});

test('the new download entry records one action for default JPG and original PNG exports', async () => {
  const source = exporter.slice(exporter.indexOf('export async function exportStorefrontDownload')).replace('export ', '');
  for (const original of [false, true]) {
    const actions = [];
    const stop = new Error('stop before browser capture');
    const bindings = { recordClientAction: (action) => actions.push(action), normalizeExportSettings: (value) => value,
      exportNodeHeight: () => 100, buildExportSummary: () => ({}), exportPreset: () => ({ original }),
      getFontEmbedCSS: async () => { throw stop; },
      exportLongPng: async () => { actions.push('download'); throw stop; } };
    const run = new Function(...Object.keys(bindings), `${source}; return exportStorefrontDownload;`)(...Object.values(bindings));
    await assert.rejects(run([{}], 'product', null, { format: original ? 'png' : 'jpeg', delivery: original ? 'single' : 'split' }), stop);
    assert.deepEqual(actions, ['download']);
  }
});
