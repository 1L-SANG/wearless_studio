import test from 'node:test';
import assert from 'node:assert/strict';
import * as raster from '../../src/features/editor/editorExportRaster.js';
import { downloadEditorPhoto } from '../../src/features/editor/editorExport.js';

// Canvas is the platform boundary: the real blur receives these drawn pixels.
function canvasFactory(calls) {
  return (width, height) => {
    let data = new Uint8ClampedArray(width * height * 4);
    const canvas = { width, height, getContext: () => ({
      drawImage(...args) {
        calls.push(args);
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
          data.set([x < width / 2 ? 0 : 255, 60, 120, 255], (y * width + x) * 4);
        }
      },
      getImageData: () => ({ data }),
      putImageData(image) { data = image.data; canvas.pixels = data; },
    }), toDataURL: () => `data:image/png;blurred,${data[4 * Math.floor(width / 2)]}` };
    return canvas;
  };
}

test('baked backdrop pixels use the existing crop at export scale and retain opaque edges', () => {
  assert.equal(typeof raster.rasterizeBackdropImage, 'function');
  const calls = [], image = { naturalWidth: 100, naturalHeight: 100 };
  const canvas = raster.rasterizeBackdropImage(image, { w: 20, h: 10, blur: 3, crop: { ox: 5, oy: 8, iw: 30, ih: 40 } }, 2, { createCanvas: canvasFactory(calls) });
  assert.deepEqual([canvas.width, canvas.height], [40, 20]);
  assert.deepEqual(calls[0], [image, -10, -16, 60, 80]);
  assert.ok(canvas.pixels[4 * 19] > 0 && canvas.pixels[4 * 20] < 255);
  assert.ok(canvas.pixels.every((value, i) => i % 4 !== 3 || value === 255));
});

test('export clone replaces SVG blur with baked data pixels before capture and keeps zero', async () => {
  assert.equal(typeof raster.bakeExportBackdrops, 'function');
  for (const amount of [0, 3]) {
    const image = { src: 'data:image/png;original,bytes', style: {}, naturalWidth: 100, naturalHeight: 100, decode: async () => {}, removeAttribute() {}, setAttribute(key, value) { this[key] = value; } };
    const wrapper = { dataset: { editorBlur: String(amount), blurWidth: '20', blurHeight: '10', blurFit: 'cover',
      blurCrop: JSON.stringify({ ox: 2, oy: 3, iw: 30, ih: 40 }) },
      querySelector: () => image, replaceChildren(node) { this.children = [node]; } };
    await raster.bakeExportBackdrops({ querySelectorAll: () => [wrapper] }, 2, { createCanvas: canvasFactory([]) });
    if (amount === 0) {
      assert.equal(image.src, 'data:image/png;original,bytes');
      assert.equal(image.style.left, '-2px');
      assert.equal(image.style.width, '30px');
    } else assert.match(image.src, /^data:image\/png;blurred,/);
    assert.deepEqual(wrapper.children, [image]);
    assert.equal(wrapper.dataset.blurBaked, 'true');
  }
});

test('three-photo and very tall backdrops use bounded blur work pixels without changing the final box', () => {
  assert.equal(typeof raster.backdropRasterPlan, 'function');
  for (const h of [2800, 12000, 100000]) {
    const plan = raster.backdropRasterPlan({ w: 1000, h, blur: 48 }, 2);
    assert.ok(plan.width * plan.height <= 1048576);
    assert.ok(plan.width <= 4096 && plan.height <= 4096);
    assert.ok(plan.scale > 0 && plan.scale < 2);
    assert.equal(plan.blur, 48 * plan.scale);
  }
});

test('a normal three-photo backdrop actually allocates the bounded work canvas', () => {
  let allocation;
  const result = raster.rasterizeBackdropImage({ naturalWidth: 3000, naturalHeight: 4000 }, { w: 1000, h: 2800, blur: 48 }, 2, {
    createCanvas(width, height) { allocation = [width, height]; return canvasFactory([])(width, height); },
  });
  assert.ok(allocation[0] * allocation[1] <= 1048576);
  assert.ok(result.pixels[4 * Math.floor(result.width / 2)] < 255);
});

test('zero-blur page export retains crisp source bytes without allocating a large pixel buffer', async () => {
  const image = { src: 'data:image/png;original,bytes', style: {}, decode: async () => {}, removeAttribute() {} };
  const wrapper = { dataset: { editorBlur: '0', blurWidth: '1000', blurHeight: '100000', blurFit: 'contain' },
    querySelector: () => image, replaceChildren() {} };
  await raster.bakeExportBackdrops({ querySelectorAll: () => [wrapper] }, 2, { createCanvas() { throw new Error('zero must not allocate'); } });
  assert.equal(image.src, 'data:image/png;original,bytes');
  assert.equal(image.style.objectFit, 'contain');
});

test('positive blur bake removes crop offsets because the crop is already in the bounded pixels', async () => {
  const image = { style: { left: '-20px', width: '50px' }, naturalWidth: 100, naturalHeight: 100, decode: async () => {},
    removeAttribute(key) { if (key === 'style') this.style = {}; }, setAttribute() {} };
  const wrapper = { dataset: { editorBlur: '3', blurWidth: '20', blurHeight: '10', blurCrop: '{"ox":2,"oy":3,"iw":30,"ih":40}' },
    querySelector: () => image, replaceChildren() {} };
  await raster.bakeExportBackdrops({ querySelectorAll: () => [wrapper] }, 2, { createCanvas: canvasFactory([]) });
  assert.equal(image.style.left, undefined);
  assert.equal(image.style.width, undefined);
  assert.equal(wrapper.dataset.blurWidth, '20');
  assert.equal(wrapper.dataset.blurHeight, '10');
});

test('pixel-read errors release their canvas and oversized zero-blur selections fail before allocation', () => {
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage() {}, getImageData() { throw new Error('pixel read failed'); } }) };
  assert.throws(() => raster.rasterizeBackdropImage({ naturalWidth: 10, naturalHeight: 10 }, { w: 20, h: 10, blur: 3 }, 2, {
    createCanvas(w, h) { canvas.width = w; canvas.height = h; return canvas; },
  }), /pixel read failed/);
  assert.deepEqual([canvas.width, canvas.height], [0, 0]);
  assert.throws(() => raster.rasterizeBackdropImage({}, { w: 1000, h: 100000, blur: 0 }, 2, {
    createCanvas() { assert.fail('must reject before allocation'); },
  }), /너무 커요/);
});

test('selected backdrop download bakes a PNG while ordinary photo downloads keep original bytes', async () => {
  const saved = [];
  await downloadEditorPhoto({ type: 'image', src: '/v1/assets/a/file', w: 20, h: 10, blur: 3 }, 'shirt', {
    fetchImage: async url => { assert.match(url, /\/bytes\?/); return new Response(new Blob(['original']), { headers: { 'content-type': 'image/webp' } }); },
    loadImage: async () => ({ naturalWidth: 100, naturalHeight: 100 }),
    createCanvas: canvasFactory([]),
    encodeCanvas: async canvas => { assert.ok(canvas.pixels[4 * 19] > 0); return new Blob(['baked'], { type: 'image/png' }); },
    save: (blob, name) => saved.push({ blob, name }),
  });
  assert.equal(saved[0].name, 'shirt-photo.png');
  assert.equal(await saved[0].blob.text(), 'baked');
});

test('a GIF-replaced backdrop bakes its poster frame while ordinary GIF downloads stay original', async () => {
  const requested = [];
  const gif = { type: 'image', src: '/v1/assets/animated/file', kind: 'gif', posterSrc: '/v1/assets/poster/file', gifMeta: { frames: 4 }, w: 20, h: 10 };
  const deps = {
    fetchImage: async url => { requested.push(url); return new Response(new Blob(['image']), { headers: { 'content-type': 'image/gif' } }); },
    loadImage: async () => ({ naturalWidth: 100, naturalHeight: 100 }), createCanvas: canvasFactory([]),
    encodeCanvas: async () => new Blob(['baked'], { type: 'image/png' }), save() {},
  };
  await downloadEditorPhoto({ ...gif, blur: 48 }, 'shirt', deps);
  await downloadEditorPhoto(gif, 'shirt', deps);
  assert.match(requested[0], /\/poster\/bytes\?/);
  assert.match(requested[1], /\/animated\/bytes\?/);
});
