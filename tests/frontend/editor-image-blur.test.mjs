import test from 'node:test';
import assert from 'node:assert/strict';
import { replaceEditorPhoto } from '../../src/features/editor/editorPhotoEditing.js';

const blur = await import('../../src/features/editor/editorImageBlur.js').catch(() => ({}));
const pixels = (width, height, color) => Uint8ClampedArray.from(Array.from({ length: width * height }, (_, i) => color(i % width, Math.floor(i / width))).flat());

test('only finite image blur fields including zero identify an editable backdrop', () => {
  assert.equal(typeof blur.hasBackdropBlur, 'function');
  for (const value of [0, 48, 100]) assert.equal(blur.hasBackdropBlur({ type: 'image', blur: value }), true);
  for (const value of [null, undefined, -1, 101, NaN, Infinity, '48', true]) assert.equal(blur.hasBackdropBlur({ type: 'image', blur: value }), false);
  assert.equal(blur.hasBackdropBlur({ type: 'shape', blur: 48 }), false);
});

test('pixel blur spreads an impulse, keeps opaque edges, and leaves its input intact', () => {
  assert.equal(typeof blur.blurPixels, 'function');
  const input = pixels(31, 7, (x, y) => x === 15 && y === 3 ? [255, 255, 255, 255] : [0, 0, 0, 255]);
  const original = input.slice();
  const output = blur.blurPixels(input, 31, 7, 3);
  assert.ok(output[(3 * 31 + 14) * 4] > 0);
  assert.ok(output[(3 * 31 + 15) * 4] < 255);
  assert.ok(output.every((value, i) => i % 4 !== 3 || value === 255));
  assert.deepEqual(input, original);
});

test('clamped edges stay colored at large radii instead of turning white or transparent', () => {
  assert.equal(typeof blur.blurPixels, 'function');
  const input = pixels(3, 2, () => [25, 80, 135, 255]);
  assert.deepEqual(blur.blurPixels(input, 3, 2, 100), input);
  assert.deepEqual(blur.blurPixels(input, 3, 2, 0), input);
});

test('premultiplied alpha prevents transparent RGB from making a dark fringe', () => {
  assert.equal(typeof blur.blurPixels, 'function');
  const input = pixels(7, 1, x => x < 3 ? [255, 50, 20, 255] : [0, 0, 0, 0]);
  const output = blur.blurPixels(input, 7, 1, 2);
  for (let i = 0; i < output.length; i += 4) if (output[i + 3] > 0) {
    assert.equal(output[i], 255);
    assert.equal(output[i + 1], 50);
    assert.equal(output[i + 2], 20);
  }
});

test('blur uses 1000px canvas units at every export scale', () => {
  assert.equal(typeof blur.scaledBlurRadius, 'function');
  assert.equal(blur.scaledBlurRadius(48, 0.86), 41.28);
  assert.equal(blur.scaledBlurRadius(48, 2), 96);
  assert.equal(blur.scaledBlurRadius(0, 2), 0);
});

test('visible pixel spread scales with output resolution instead of growing or shrinking in canvas units', () => {
  const spans = [0.5, 1, 2].map(scale => {
    const width = Math.round(200 * scale);
    const input = pixels(width, 1, x => [x < width / 2 ? 0 : 255, 0, 0, 255]);
    const output = blur.blurPixels(input, width, 1, 8 * scale);
    const values = Array.from({ length: width }, (_, x) => output[x * 4]);
    return (values.findIndex(value => value >= 230) - values.findIndex(value => value >= 25)) / scale;
  });
  assert.ok(Math.max(...spans) - Math.min(...spans) <= 1.5);
});

test('replacing an ordinary backdrop photo keeps zero or changed blur and crop behavior', () => {
  for (const value of [0, 73]) {
    const before = [{ id: 'b', kind: 'styling', elements: [{ id: 'p', type: 'image', src: '/old', blur: value, w: 1000, h: 500 }] }];
    const after = replaceEditorPhoto(before, 'p', { src: '/new', width: 1200, height: 900 });
    assert.equal(after[0].elements[0].src, '/new');
    assert.equal(after[0].elements[0].blur, value);
    assert.equal(before[0].elements[0].src, '/old');
  }
});
