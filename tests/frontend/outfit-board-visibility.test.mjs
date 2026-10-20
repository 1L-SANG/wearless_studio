import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, drain } from './helpers/outfitHarness.mjs';

const propsFor = (shoe = 'shoe') => ({
  analysis: { clothingType: 'top', matchClothing: [{ id: 'bottom', selected: true, thumb: '/bottom.png' }],
    outfitSelections: { slots: { footwear: { itemId: shoe } } },
    outfitRecommendation: { candidates: { footwear: ['shoe', 'other'].map(id => ({ id, name: id, thumb: `/${id}.png` })) } } },
  product: { name: '상품', colors: [{ isBase: true, images: [{ slot: 'Front', src: '/product.png' }] }] },
  onOpenRole() {},
});
const images = tree => nodes(tree).filter(node => node.type === 'img');
const pieces = tree => nodes(tree).filter(node => [10, 9, 1].includes(node.props?.style?.zIndex)
  && ['div', 'button'].includes(node.type));
const shoePiece = tree => pieces(tree).find(node => node.props['aria-label'] === '신발 바꾸기');
const loadDisplayImages = tree => images(tree).forEach(img => img.props.onLoad({ currentTarget: {
  naturalWidth: 480, naturalHeight: 640, getAttribute: () => img.props.src,
} }));
const remount = h => {
  h.runtime.cleanups.forEach(cleanup => cleanup?.());
  h.runtime.slots = []; h.runtime.cleanups = [];
};

async function setup(t) {
  const previous = { Image: globalThis.Image, document: globalThis.document, window: globalThis.window };
  const requests = [];
  globalThis.window = { innerHeight: 738, addEventListener() {}, removeEventListener() {} };
  globalThis.Image = class {
    naturalWidth = 480; naturalHeight = 640;
    set src(src) { this.url = src; requests.push(this); }
    decode() { return Promise.resolve(); }
  };
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {},
    getImageData(_x, _y, width, height) {
      const data = new Uint8ClampedArray(width * height * 4);
      for (let y = 64; y < 192; y++) for (let x = 24; x < 168; x++) data[(y * width + x) * 4 + 3] = 255;
      return { data };
    },
  }) }) };
  const h = await outfitHarness({ getProductVisuals: async () => null });
  t.after(async () => { t.mock.timers.reset(); await h.close(); Object.assign(globalThis, previous); });
  const { OutfitBoard } = await h.load('/src/features/analysis/OutfitBoard.jsx');
  return { h, requests, render: props => h.render(OutfitBoard, props || propsFor()) };
}

test('unmeasured pieces reserve their assumed layout, stay hidden on display load, and reveal individually', async t => {
  const { render, requests } = await setup(t);
  const before = render();
  assert.equal(pieces(before).length, 3);
  assert.ok(pieces(before).every(node => node.props.style.visibility === 'hidden'));
  assert.ok(pieces(before).every(node => node.props.style.width > 0 && node.props.style.height > 0));
  loadDisplayImages(before);
  assert.deepEqual(pieces(render()).map(node => node.props.style), pieces(before).map(node => node.props.style));
  await requests.find(img => img.url === '/shoe.png').onload();
  await drain();
  const after = render();
  assert.notEqual(shoePiece(after).props.style.visibility, 'hidden');
  assert.ok(pieces(after).filter(node => node !== shoePiece(after)).every(node => node.props.style.visibility === 'hidden'));
  assert.equal(images(shoePiece(after)).length, 2);
  assert.ok(images(shoePiece(after)).every(img => img.props.style?.height === '200%'));
});

test('at 1500ms pieces reveal original-ratio contain images and ignore a late decoded crop even after remount', async t => {
  const { h, render, requests } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const before = render();
  loadDisplayImages(before);
  let finishDecode;
  const shoe = requests.find(img => img.url === '/shoe.png');
  shoe.decode = () => new Promise(resolve => { finishDecode = resolve; });
  const lateLoad = shoe.onload();
  t.mock.timers.tick(1499);
  await drain();
  assert.ok(pieces(render()).every(node => node.props.style.visibility === 'hidden'));
  t.mock.timers.tick(1);
  await drain();
  const fallback = render();
  assert.ok(pieces(fallback).every(node => node.props.style.visibility !== 'hidden'));
  assert.ok(images(fallback).every(img => img.props.style === undefined && img.props.className === undefined));
  const product = pieces(fallback).find(node => node.props.style.zIndex === 10);
  assert.ok(Math.abs(product.props.style.width / product.props.style.height - .75) < 1e-9);
  // Pre-S fallback: one 16cm shoe, relative to a 64cm product.
  assert.ok(Math.abs(shoePiece(fallback).props.style.height / product.props.style.height - .25) < 1e-9);
  assert.equal(images(shoePiece(fallback)).length, 1);
  assert.ok(Math.abs(shoePiece(fallback).props.style.width / shoePiece(fallback).props.style.height - .75) < 1e-9);
  assert.ok(Math.abs(product.props.style.height - 128) < 1e-9);
  finishDecode(); await lateLoad; await drain();
  assert.deepEqual(pieces(render()).map(node => node.props.style), pieces(fallback).map(node => node.props.style));
  assert.ok(images(render()).every(img => img.props.style === undefined));
  remount(h);
  const cachedFallback = render();
  assert.deepEqual(pieces(cachedFallback).map(node => node.props.style), pieces(fallback).map(node => node.props.style));
  assert.ok(images(cachedFallback).every(img => img.props.style === undefined));
  assert.equal(requests.length, 3);
});

test('successful cached URLs render cropped on the first remount and immediately when selecting the same shoe again', async t => {
  const { h, render, requests } = await setup(t);
  render();
  await Promise.all(requests.map(img => img.onload())); await drain();
  const measured = render();
  remount(h);
  const cached = render();
  assert.ok(pieces(cached).every(node => node.props.style.visibility !== 'hidden'));
  assert.deepEqual(images(cached).map(img => img.props.style), images(measured).map(img => img.props.style));
  assert.ok(images(cached).every(img => img.props.style?.height === '200%'));
  assert.deepEqual(pieces(cached).map(node => node.props.style), pieces(measured).map(node => node.props.style));
  const switched = render(propsFor('other'));
  assert.equal(shoePiece(switched).props.style.visibility, 'hidden');
  assert.ok(pieces(switched).filter(node => node !== shoePiece(switched)).every(node => node.props.style.visibility !== 'hidden'));
  const returned = render();
  assert.notEqual(shoePiece(returned).props.style.visibility, 'hidden');
  assert.ok(images(shoePiece(returned)).every(img => img.props.style?.height === '200%'));
  assert.equal(requests.length, 4);
});

test('failed URLs reveal without waiting for the timeout and stay visible at their original ratio on remount', async t => {
  const { h, render, requests } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const before = render();
  loadDisplayImages(before);
  for (const img of requests) img.onerror();
  await drain();
  const failed = render();
  assert.ok(pieces(failed).every(node => node.props.style.visibility !== 'hidden'));
  assert.ok(images(failed).every(img => img.props.style === undefined));
  assert.equal(images(shoePiece(failed)).length, 1);
  const product = pieces(failed).find(node => node.props.style.zIndex === 10);
  assert.ok(Math.abs(shoePiece(failed).props.style.height / product.props.style.height - .25) < 1e-9);
  assert.ok(Math.abs(shoePiece(failed).props.style.width / shoePiece(failed).props.style.height - .75) < 1e-9);
  assert.ok(Math.abs(product.props.style.height - 128) < 1e-9);
  remount(h);
  const cached = render();
  assert.deepEqual(pieces(cached).map(node => node.props.style), pieces(failed).map(node => node.props.style));
  assert.ok(images(cached).every(img => img.props.style === undefined));
  assert.equal(requests.length, 3);
});

test('remounting pending URLs reuses their request and original 1500ms deadline', async t => {
  const { h, render, requests } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  render();
  t.mock.timers.tick(1000);
  remount(h);
  assert.ok(pieces(render()).every(node => node.props.style.visibility === 'hidden'));
  assert.equal(requests.length, 3);
  t.mock.timers.tick(499); await drain();
  assert.ok(pieces(render()).every(node => node.props.style.visibility === 'hidden'));
  t.mock.timers.tick(1); await drain();
  assert.ok(pieces(render()).every(node => node.props.style.visibility !== 'hidden'));
  assert.ok(images(render()).every(img => img.props.style === undefined));
  assert.equal(requests.length, 3);
});

test('failed shoes with no loaded source ratio use one contain image at 2.1', async t => {
  const { render, requests } = await setup(t);
  render();
  requests.find(img => img.url === '/shoe.png').onerror();
  await drain();
  const failed = render();
  const shoes = shoePiece(failed);
  assert.equal(images(shoes).length, 1);
  assert.equal(images(shoes)[0].props.style, undefined);
  assert.ok(Math.abs(shoes.props.style.width / shoes.props.style.height - 2.1) < 1e-9);
  const product = pieces(failed).find(node => node.props.style.zIndex === 10);
  assert.ok(Math.abs(shoes.props.style.height / product.props.style.height - .25) < 1e-9);
});
