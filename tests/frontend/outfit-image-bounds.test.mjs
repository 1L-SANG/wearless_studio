import test from 'node:test';
import assert from 'node:assert/strict';
import * as images from '../../src/lib/outfitImageBounds.js';

const pixels = (width, height, alpha) => {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 3; i < data.length; i += 4) data[i] = alpha;
  return data;
};
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test('alpha bounds ignore the 1–7 haze and include alpha 8 at both edges', () => {
  const data = pixels(10, 8, 7);
  data[(2 * 10 + 1) * 4 + 3] = 8;
  data[(5 * 10 + 8) * 4 + 3] = 255;
  assert.deepEqual(images.alphaBoundsFromPixels(data, 10, 8), { x: .1, y: .25, w: .8, h: .5 });
  assert.equal(images.alphaBoundsFromPixels(pixels(10, 8, 7), 10, 8), null);
});

test('opaque decoded JPEG pixels retain the full canvas', () => {
  assert.deepEqual(images.alphaBoundsFromPixels(pixels(6, 4, 255), 6, 4), { x: 0, y: 0, w: 1, h: 1 });
});

test('only cross-origin HTTP images get a separate measurement URL and CORS mode', () => {
  const base = 'https://ai.wearless.kr/analysis';
  for (const src of ['/photo.png?a=1', 'https://ai.wearless.kr/photo.png',
    'data:image/png;base64,abc', 'blob:https://ai.wearless.kr/abc']) {
    assert.deepEqual(images.imageBoundsRequest(src, base), { src });
  }
  for (const src of ['https://images.wearless.kr/shoe.png', '//images.wearless.kr/shoe.png?v=3#image',
    'https://images.wearless.kr/shoe.png?bbox=1', 'https://images.wearless.kr/shoe.png?name=a%20b']) {
    const request = images.imageBoundsRequest(src, base);
    const url = new URL(request.src);
    assert.equal(url.searchParams.get('bbox'), '1');
    assert.equal(request.crossOrigin, 'anonymous');
    assert.notEqual(request.src, src);
    if (src.includes('v=3')) { assert.equal(url.searchParams.get('v'), '3'); assert.equal(url.hash, '#image'); }
    if (src.includes('a%20b')) assert.ok(request.src.includes('name=a%20b&bbox=1'));
  }
  assert.equal(images.imageBoundsRequest('https://images.wearless.kr/a.png', 'http://localhost:5173').crossOrigin, 'anonymous');
});

test('crop geometry fills the box with the measured pixels and falls back without a measurement', () => {
  const measurement = { width: 480, height: 640, bounds: { x: .1, y: .25, w: .8, h: .5 } };
  near(images.imageBoundsAspectRatio(measurement, .75), 1.2);
  assert.deepEqual(images.croppedImageStyle(measurement.bounds), {
    width: '125%', height: '200%', left: '-12.5%', top: '-50%',
  });
  assert.equal(images.imageBoundsAspectRatio(null, .75), .75);
  assert.equal(images.croppedImageStyle(null), undefined);
  assert.deepEqual(images.croppedImageStyle({ x: 0, y: 0, w: 1, h: 1 }), {
    width: '100%', height: '100%', left: '0%', top: '0%',
  });
});

test('loader deduplicates addresses, downsamples to 256, and caches quiet failures', async (t) => {
  const requests = [], draws = [];
  const previous = { Image: globalThis.Image, document: globalThis.document, location: globalThis.location };
  globalThis.location = { href: 'https://ai.wearless.kr/analysis' };
  globalThis.Image = class {
    naturalWidth = 480;
    naturalHeight = 640;
    set src(value) {
      requests.push({ src: value, crossOrigin: this.crossOrigin });
      if (value.includes('timeout')) return;
      queueMicrotask(() => value.includes('blocked') ? this.onerror() : this.onload());
    }
    decode() { return Promise.resolve(); }
  };
  globalThis.document = { createElement: () => ({
    getContext: () => ({
      drawImage: (...args) => draws.push(args.slice(1)),
      getImageData: (_x, _y, w, h) => ({ data: pixels(w, h, 255) }),
    }),
  }) };
  try {
    const src = 'https://images.wearless.kr/bounds-cache.png?v=3';
    const first = images.loadOutfitImageBounds(src);
    assert.equal(images.loadOutfitImageBounds(src), first);
    assert.deepEqual(await first, { width: 480, height: 640, bounds: { x: 0, y: 0, w: 1, h: 1 } });
    assert.deepEqual(draws, [[0, 0, 192, 256]]);
    assert.deepEqual(requests, [{ src: `${src}&bbox=1`, crossOrigin: 'anonymous' }]);
    const failed = images.loadOutfitImageBounds('https://images.wearless.kr/blocked.png');
    assert.equal(await failed, null);
    assert.equal(images.loadOutfitImageBounds('https://images.wearless.kr/blocked.png'), failed);
    globalThis.document.createElement = () => ({ getContext: () => ({ drawImage() { throw new Error('tainted'); } }) });
    assert.equal(await images.loadOutfitImageBounds('/tainted.png'), null);
  } finally { Object.assign(globalThis, previous); }
});

test('timeout and decode errors resolve null without a browser or network', async (t) => {
  const previousImage = globalThis.Image;
  globalThis.Image = class {
    set src(value) { if (!value.includes('timeout')) queueMicrotask(() => this.onload()); }
    decode() { return Promise.reject(new Error('decode failed')); }
  };
  t.mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const timedOut = images.loadOutfitImageBounds('/timeout.png');
    t.mock.timers.tick(10000);
    assert.equal(await timedOut, null);
    assert.equal(await images.loadOutfitImageBounds('/decode-failed.png'), null);
  } finally { globalThis.Image = previousImage; }
});
