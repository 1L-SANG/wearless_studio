import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let vite, api, DB;
before(async () => {
  vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false, logLevel: 'silent', root: fileURLToPath(new URL('../..', import.meta.url)),
    server: { middlewareMode: true, watch: null, hmr: false }, appType: 'custom',
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
  });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());
beforeEach(() => {
  DB.account.credits = 100;
  DB.storyboard = [{ id: 'detail-block', cutType: 'product', shot: 'detail' }];
  DB.editorBlocks = [{ elements: [{ id: 'detail-canvas', type: 'image', src: '/canvas-detail.jpg', cutType: 'product', sourceBlockId: 'detail-block' }] }];
  DB.wardrobe = {
    navy: [{ id: 'original', src: '/original.jpg', cutType: 'styling', ai: true }],
    ivory: [{ id: 'other', src: '/other.jpg', cutType: 'styling', ai: true }],
    misc: [{ id: 'product', src: '/product.jpg', cutType: 'product', ai: true }],
  };
});

const similar = (src, extra = {}) => api.generateImage('project', {
  mode: 'vary', variationMode: 'similar', changes: [],
  source: { id: 'canvas-element', src, cutType: 'styling' }, ...extra,
});

test('mock similar cuts resolve the source URL and retain its original color group and cut type', async () => {
  const { data } = await similar('/original.jpg');
  assert.equal(data.similarCut?.rootAssetId, 'original');
  assert.equal(data.similarCut?.sourceAssetId, 'original');
  assert.equal(data.cutType, 'styling');
  assert.equal(data.wardrobeGroup, 'navy');
  assert.equal(DB.wardrobe.navy.at(-1).id, data.id);
});

test('mock repeats from either a candidate or original avoid recent actions within the original family', async () => {
  const first = (await similar('/original.jpg')).data;
  const second = (await similar(first.src)).data;
  const third = (await similar('/original.jpg')).data;
  assert.equal(second.similarCut?.rootAssetId, 'original');
  assert.equal(second.similarCut?.sourceAssetId, first.id);
  assert.equal(third.similarCut?.rootAssetId, 'original');
  assert.equal(new Set([first, second, third].map(image => image.similarCut?.plan.actionKey)).size, 3);
  const other = (await similar('/other.jpg')).data;
  assert.equal(other.similarCut.rootAssetId, 'other');
  assert.equal(other.similarCut.plan.actionKey, first.similarCut.plan.actionKey);
});

test('mock rejects a product edit without an original detail recipe before spending credits', async () => {
  await assert.rejects(api.generateImage('project', {
    mode: 'vary', source: { id: 'canvas-product', src: '/product.jpg', cutType: 'product' },
  }), /제품컷은 새 이미지 추가에서 원본 사진을 골라 만들어요\./);
  assert.equal(DB.account.credits, 100);
});

test('mock detail reshoots use stored recipe flags or original canvas detail blocks', async () => {
  DB.wardrobe.misc.push({ id: 'detail', src: '/detail.jpg', cutType: 'product', detailReshoot: true });
  for (const src of ['/detail.jpg', '/canvas-detail.jpg']) {
    const { data } = await api.generateImage('project', { mode: 'vary', source: { src, cutType: 'product' } });
    assert.equal(data.detailReshoot, true);
    assert.equal(data.similarCut, undefined);
  }
});

test('mock new product details keep the recipe flag for the next reshoot', async () => {
  const { data } = await api.generateImage('project', { mode: 'new', colorId: 'navy', cutType: 'product', shot: 'detail' });
  assert.equal(data.cutType, 'product');
  assert.equal(data.detailReshoot, true);
  const reshot = await api.generateImage('project', { mode: 'vary', source: { src: data.src, cutType: 'product' } });
  assert.equal(reshot.data.detailReshoot, true);
});

test('mock does not turn a product similar request or a worn request without similar mode into a paid job', async () => {
  await assert.rejects(similar('/product.jpg'), /착용 사진/);
  await assert.rejects(api.generateImage('project', { mode: 'vary', source: { src: '/original.jpg', cutType: 'styling' } }), /비슷한 컷만/);
  assert.equal(DB.account.credits, 100);
});

test('mock similar requests cannot invent an image lineage or spend credits for a missing source', async () => {
  await assert.rejects(similar('/missing.jpg'), /원본/);
  assert.equal(DB.account.credits, 100);
});
