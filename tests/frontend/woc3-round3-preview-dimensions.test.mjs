import test from 'node:test';
import assert from 'node:assert/strict';
import * as preview from '../../src/features/storyboard/storyboardPreview.js';
import { detailTargetPreview } from '../../src/lib/detailRecommendations.js';
import { hookHarness, loadStoryboardComponent, readStoryboard } from './helpers/storyboardComponents.mjs';

const card = { id: 'detail', source: 'ai', cutType: 'product', shot: 'detail', detailTargetId: 'collar' };
const catalogs = {
  detailProduct: { colors: [{ isBase: true, images: [{ slot: 'Front', src: '/square.jpg' }] }] },
  detailRecommendations: { candidates: [{ id: 'collar', sourceSlot: 'Front', sourceIndex: 0,
    region: { x: .3, y: .2, w: .3, h: .2 } }] },
};
function imageHarness() {
  const images = [];
  class ImageCtor { constructor() { images.push(this); } }
  return { images, ImageCtor, load(index, width, height) {
    Object.assign(images[index], { naturalWidth: width, naturalHeight: height }); images[index].onload?.();
  } };
}

test('R3-2 original-size measurement deduplicates requests and reads natural rather than display dimensions', () => {
  assert.equal(typeof preview.observeDetailPreviewDimensions, 'function');
  const boundary = imageHarness(), measured = [];
  const cancel = preview.observeDetailPreviewDimensions(['/square.jpg', '/square.jpg', '/wide.jpg'],
    (src, size) => measured.push([src, size]), boundary);
  assert.equal(boundary.images.length, 2);
  assert.deepEqual(boundary.images.map(image => image.src), ['/square.jpg', '/wide.jpg']);
  Object.assign(boundary.images[0], { width: 184, height: 184 });
  boundary.load(0, 600, 600); boundary.load(1, 1200, 500);
  assert.deepEqual(measured, [['/square.jpg', { width: 600, height: 600 }], ['/wide.jpg', { width: 1200, height: 500 }]]);
  cancel();
});

test('R3-2 missing, failed and invalid original sizes keep the crop estimate without updates', () => {
  assert.equal(typeof preview.observeDetailPreviewDimensions, 'function');
  const boundary = imageHarness(), measured = [];
  const cancel = preview.observeDetailPreviewDimensions(['/broken.jpg', '/invalid.jpg'], (...args) => measured.push(args), boundary);
  boundary.images[0].onerror?.(); boundary.load(1, 0, 100);
  assert.deepEqual(measured, []);
  assert.doesNotThrow(() => preview.observeDetailPreviewDimensions(['/unavailable.jpg'], () => assert.fail(), { ImageCtor: null })());
  cancel();
});

test('R3-2 dimension observer releases handlers and ignores late image completion after cleanup', () => {
  assert.equal(typeof preview.observeDetailPreviewDimensions, 'function');
  const boundary = imageHarness(); let updates = 0;
  const cancel = preview.observeDetailPreviewDimensions(['/old.jpg'], () => updates++, boundary);
  const late = boundary.images[0].onload;
  cancel(); boundary.load(0, 800, 600); late();
  assert.equal(updates, 0); assert.equal(boundary.images[0].onload, null); assert.equal(boundary.images[0].onerror, null);
});

test('R3-2 vector intrinsic dimensions describe aspect, not native bitmap pixels', () => {
  const boundary = imageHarness(), measured = [];
  const urls = ['data:image/svg+xml;utf8,%3Csvg%3E', '/photo.SVG?size=300', 'https://images.example/photo.svg#front'];
  const cancel = preview.observeDetailPreviewDimensions(urls, (src, size) => measured.push([src, size]), boundary);
  urls.forEach((_, index) => boundary.load(index, 300, 400));
  assert.deepEqual(measured, urls.map(src => [src, { width: 300, height: 400, dimensionsKnown: false }]));
  cancel();
});

test('R3-2 size hook reads only bound originals, retains source identity and cleans up on source change', async () => {
  const hooks = hookHarness(), boundary = imageHarness();
  const hook = await loadStoryboardComponent('useDetailPreviewDimensions.js', 'useDetailPreviewDimensions', {
    ...hooks.hooks, detailTargetPreview,
    observeDetailPreviewDimensions: (urls, update) => preview.observeDetailPreviewDimensions(urls, update, boundary),
  });
  const render = (blocks, context) => hooks.render(() => hook(blocks, context));
  try {
    assert.deepEqual(render([card, { ...card, id: 'duplicate' }], catalogs), {});
    assert.equal(boundary.images.length, 1);
    boundary.load(0, 600, 600);
    assert.deepEqual(render([card], catalogs), { '/square.jpg': { width: 600, height: 600 } });
    assert.equal(boundary.images.length, 1, 'changing block count does not reload the same source');
    const changed = structuredClone(catalogs); changed.detailProduct.colors[0].images[0].src = '/new.jpg';
    render([card], changed);
    assert.equal(boundary.images.length, 2); assert.equal(boundary.images[0].onload, null);
    boundary.load(1, 1200, 800);
    assert.deepEqual(render([card], changed)['/new.jpg'], { width: 1200, height: 800 });
  } finally { hooks.close(); }
  assert.equal(boundary.images[1].onload, null);
});

test('R3-2 size hook does not request missing targets or already measured source metadata', async () => {
  const hooks = hookHarness(); let requests = 0;
  const hook = await loadStoryboardComponent('useDetailPreviewDimensions.js', 'useDetailPreviewDimensions', {
    ...hooks.hooks, detailTargetPreview,
    observeDetailPreviewDimensions: urls => { requests += urls.length; return () => {}; },
  });
  const known = structuredClone(catalogs);
  Object.assign(known.detailProduct.colors[0].images[0], { width: 1000, height: 750 });
  try {
    hooks.render(() => hook([card, { ...card, detailTargetId: 'missing' }], known));
    assert.equal(requests, 0);
  } finally { hooks.close(); }
});

test('R3-2 Storyboard uses measured dimensions before loading returns and shares them with preview candidates', () => {
  const source = readStoryboard('Storyboard.jsx');
  assert.ok(source.indexOf('const detailPreviewDimensions = useDetailPreviewDimensions(') < source.indexOf('if (loadError) return'));
  assert.match(source, /assembleStoryboardPreview\(\{[\s\S]*?catalogs: previewCatalogs/);
  assert.match(source, /<StoryboardSectionPicker[\s\S]*?catalogs=\{previewCatalogs\}/);
});
