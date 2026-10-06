import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fetchPhotoBytes, photoBytesUrl } from '../../src/lib/photoBytes.js';
import { collectDraftPhotos, ensureDraftRemotePhotos, queueProductDraftSave, flushProductDraftSave } from '../../src/lib/draftStore.js';
import { restoreDraftProduct } from '../../src/features/product-input/draftProductRestore.js';
import { withUploadedSrcs } from '../../src/lib/draftPromotionProduct.js';

const path = '/v1/assets/11111111-1111-4111-8111-111111111111/file';
const remote = `https://api.example.test${path}`;
const productWith = (...images) => ({ colors: [{ id: 'base', isBase: true, images }] });
const jpeg = () => new Response('photo', { headers: { 'Content-Type': 'image/jpeg' } });

for (const src of [remote, `${remote}?e=2&x=1`, path, `${path}?e=2`]) {
  test(`asset file URL maps to bytes: ${src}`, () => {
    assert.equal(photoBytesUrl(src), src.replace('/file', '/bytes'));
  });
}
for (const src of ['blob:photo', 'data:image/png;base64,AA==', 'https://cdn.test/image.jpg', 'https://cdn.test/file?next=' + path, remote + '/extra', remote.replace('/file', '/bytes')]) {
  test(`other src stays unchanged: ${src}`, () => assert.equal(photoBytesUrl(src), src));
}

for (const [name, response] of [
  ['404', () => new Response('missing', { status: 404, headers: { 'Content-Type': 'image/jpeg' } })],
  ['503', () => new Response('unavailable', { status: 503, headers: { 'Content-Type': 'image/jpeg' } })],
  ['non-image', () => new Response('{}', { headers: { 'Content-Type': 'application/json' } })],
  ['missing MIME', () => new Response(new Uint8Array([1]))],
  ['empty', () => new Response('', { headers: { 'Content-Type': 'image/jpeg' } })],
]) {
  test(`fetch rejects ${name} and collection preserves the failed remote id`, async (t) => {
    t.mock.method(globalThis, 'fetch', async () => response());
    await assert.rejects(fetchPhotoBytes(remote));
    const image = { id: 'remote', src: remote, type: 'image/jpeg' };
    const result = await collectDraftPhotos(productWith(image));
    assert.equal(result.failed, 1);
    assert.deepEqual(result.failedIds, ['remote']);
    assert.deepEqual(result.photos, []);
    assert.deepEqual(result.cleanProduct.colors[0].images, [image]);
    assert.equal(globalThis.fetch.mock.callCount(), 2, 'asset URLs do not retry the redirect cache');
  });
}

test('fetch accepts image bytes and forwards options through the mapped URL', async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (src, options) => {
    assert.equal(src, remote.replace('/file', '/bytes'));
    assert.equal(options.signal, controller.signal);
    return jpeg();
  });
  const blob = await fetchPhotoBytes(remote, { signal: controller.signal });
  assert.equal(blob.type, 'image/jpeg');
  assert.equal(await blob.text(), 'photo');
});

test('remote response MIME overrides stored metadata', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => jpeg());
  const result = await collectDraftPhotos(productWith({ id: 'remote', src: remote, type: 'image/png' }));
  assert.equal(result.photos[0].mime, 'image/jpeg');
});

test('non-asset HTTP retries with reload; failed blob and data keep their old removal behavior', async (t) => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (src, options) => {
    calls.push([src, options]);
    if (options?.cache === 'reload') return jpeg();
    throw new Error('unavailable');
  });
  const result = await collectDraftPhotos(productWith(
    { id: 'http', src: 'https://cdn.test/photo.jpg' },
    { id: 'blob', src: 'blob:dead' },
    { id: 'data', src: 'data:image/png;base64,AA==' },
  ));
  assert.deepEqual(result.cleanProduct.colors[0].images.map((im) => im.id), ['http']);
  assert.deepEqual(result.failedIds, ['blob', 'data']);
  assert.equal(result.failed, 2);
  assert.equal(calls.length, 4);
  assert.deepEqual(calls[1][1], { cache: 'reload' });
});

test('flush returns failed ids and IndexedDB preserves remote references for restore', async (t) => {
  let saved;
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('offline'); });
  const originals = ['sessionStorage', 'indexedDB'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  t.after(() => {
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: { setItem() {} } });
  Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: {
    open() {
      const request = {};
      queueMicrotask(() => {
        request.result = {
          close() {},
          transaction() {
            const tx = {
              objectStore: () => ({
                get() { const request = {}; queueMicrotask(() => { request.result = saved; request.onsuccess(); }); return request; },
                put(value) { saved = value; return {}; },
              }),
            };
            queueMicrotask(() => queueMicrotask(() => tx.oncomplete()));
            return tx;
          },
        };
        request.onsuccess();
      });
      return request;
    },
  } });
  queueProductDraftSave(productWith({ id: 'remote', src: remote }));
  const result = await flushProductDraftSave();
  assert.deepEqual(result, { saved: 0, failed: 1, failedIds: ['remote'] });
  const restored = restoreDraftProduct(saved);
  assert.equal(restored.colors[0].images[0].src, remote);
  assert.deepEqual(saved.photos, []);
  saved.photos = [{ imageId: 'remote', mime: 'image/jpeg', blob: new Blob(['cached'], { type: 'image/jpeg' }) }];
  queueProductDraftSave(productWith({ id: 'remote', src: remote, slot: 'Front' }));
  assert.deepEqual(await flushProductDraftSave(), { saved: 1, failed: 0, failedIds: [] });
  assert.equal(await saved.photos[0].blob.text(), 'cached');
  assert.equal(saved.photos[0].slot, 'Front');
});

test('promotion fetches missing remote bytes, preserves order, and fails on unavailable bytes', async (t) => {
  const draft = { product: productWith({ id: 'remote', src: remote, slot: 'Front', type: 'image/png' }), photos: [] };
  t.mock.method(globalThis, 'fetch', async () => jpeg());
  const prepared = await ensureDraftRemotePhotos(draft);
  assert.equal(globalThis.fetch.mock.calls[0].arguments[0], remote.replace('/file', '/bytes'));
  assert.equal(prepared.photos[0].imageId, 'remote');
  assert.equal(prepared.photos[0].mime, 'image/jpeg');
  assert.equal(prepared.photos[0].slot, 'Front');
  const promoted = withUploadedSrcs(prepared.product, { remote: { assetId: 'new', url: 'https://cdn.test/new' } });
  assert.equal(promoted.colors[0].images[0].id, 'new');
  await ensureDraftRemotePhotos(prepared);
  assert.equal(globalThis.fetch.mock.callCount(), 1, 'already collected bytes are reused');
  globalThis.fetch.mock.mockImplementation(async () => new Response('missing', { status: 404 }));
  await assert.rejects(ensureDraftRemotePhotos(draft), /사진 1장을 불러오지 못했어요/);
  assert.deepEqual(draft.photos, []);
});

test('public analysis and both promotion entries use byte validation before destructive work', () => {
  const read = (file) => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
  assert.match(read('src/lib/api/httpAdapter.js'), /await fetchPhotoBytes\(photo.src, \{ signal \}\)/);
  const sync = read('src/lib/draftSync.js');
  assert.ok(sync.indexOf('await ensureDraftRemotePhotos(draft)') < sync.indexOf('await api.createProject()'));
  const root = read('src/apps/seller/App.jsx').split('function RootRedirect()')[1];
  assert.ok(root.indexOf('await ensureDraftRemotePhotos(draft)') < root.indexOf('await draftSlot.remove()'));
});

test('actual draftSync uploads a remote image without a local blob or fails before project creation', async (t) => {
  const events = [];
  t.mock.method(globalThis, 'fetch', async () => { events.push('fetch'); return jpeg(); });
  globalThis.__remotePhotoPromotionHarness = {
    ensureDraftRemotePhotos,
    withUploadedSrcs,
    isUploadablePhotoMime: (mime) => mime === 'image/jpeg',
    api: {
      async createProject() { events.push('create'); return { id: 'project' }; },
      async uploadPhoto(_id, photo) {
        events.push('upload');
        assert.equal(await photo.blob.text(), 'photo');
        assert.equal(photo.mime, 'image/jpeg');
        return { assetId: 'uploaded', url: 'https://cdn.test/uploaded.jpg' };
      },
      async saveProduct(_id, product) {
        events.push('save');
        assert.equal(product.colors[0].images.length, 1);
        assert.equal(product.colors[0].images[0].id, 'uploaded');
      },
      async patchProject() { events.push('patch'); },
    },
    createDraftSyncSingleFlight: (sync) => ({ sync }),
    draftPromotionSession: { read: () => ({}), rememberProject() {}, rememberAsset() {} },
    startCustomMatchPromotion: () => null,
    stripLocalCustomMatch: (analysis) => analysis,
    invalidateStoryboardEntryPrefetch() {},
  };
  t.after(() => { delete globalThis.__remotePhotoPromotionHarness; });
  const source = readFileSync(new URL('../../src/lib/draftSync.js', import.meta.url), 'utf8').replace(/^import .+;\r?$/gm, '');
  const declarations = `const { ${Object.keys(globalThis.__remotePhotoPromotionHarness).join(', ')} } = globalThis.__remotePhotoPromotionHarness;\n`;
  const { promoteDraftToProject } = await import(`data:text/javascript;base64,${Buffer.from(declarations + source).toString('base64')}`);
  const draft = { product: productWith({ id: 'remote', src: remote, slot: 'Front' }), photos: [] };
  await promoteDraftToProject(draft);
  assert.deepEqual(events, ['fetch', 'create', 'upload', 'save', 'patch']);
  events.length = 0;
  globalThis.fetch.mock.mockImplementation(async () => { events.push('fetch'); return new Response('missing', { status: 404 }); });
  await assert.rejects(promoteDraftToProject(draft), /사진 1장을 불러오지 못했어요/);
  assert.deepEqual(events, ['fetch']);
});

test('HTTP photo errors retain status and cached remote bytes survive a failed fetch', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('missing', { status: 404 }));
  await assert.rejects(fetchPhotoBytes(remote), { status: 404 });
  const blob = new Blob(['cached'], { type: 'image/jpeg' });
  const cached = { imageId: 'remote', blob, mime: 'image/jpeg', colorId: 'old', slot: 'Back' };
  const result = await collectDraftPhotos(productWith({ id: 'remote', src: remote, slot: 'Front' }), undefined, [cached]);
  assert.equal(result.failed, 0);
  assert.equal(await result.photos[0].blob.text(), 'cached');
  assert.equal(result.photos[0].slot, 'Front');
  assert.equal(result.photos[0].colorId, 'base');
});

test('collection reports each HTTP failure for all-404 versus mixed error copy', async (t) => {
  t.mock.method(globalThis, 'fetch', async (src) => new Response('error', { status: src.includes('second') ? 503 : 404 }));
  const result = await collectDraftPhotos(productWith({ id: 'first', src: remote }, { id: 'second', src: `${remote}?second=1` }));
  assert.deepEqual(result.failures, [{ imageId: 'first', status: 404 }, { imageId: 'second', status: 503 }]);
  assert.equal(result.failed, 2);
});
