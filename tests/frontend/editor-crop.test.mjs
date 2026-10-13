import test from 'node:test';
import assert from 'node:assert/strict';
const crop = await import('../../src/features/editor/editorCrop.js').catch(() => ({}));
const draft = { blockId: 'a', elId: 'p', fx: 100, fy: 80, fw: 200, fh: 300, ox: 50, oy: 40, iw: 400, ih: 500 };

test('interior crop drag moves the retained range while keeping the full photo anchored', () => {
  assert.equal(typeof crop.dragEditorCrop, 'function');
  assert.deepEqual(crop.dragEditorCrop(draft, 'range', '', 30, -20), { ...draft, fx: 130, fy: 60, ox: 80, oy: 20 });
  assert.deepEqual(crop.dragEditorCrop(draft, 'range', '', -500, 900), { ...draft, fx: 50, fy: 240, ox: 0, oy: 200 });
});

test('underlying photo drag leaves the retained frame fixed and never exposes a gap', () => {
  assert.equal(typeof crop.dragEditorCrop, 'function');
  assert.deepEqual(crop.dragEditorCrop(draft, 'img', '', 100, -100), { ...draft, ox: 0, oy: 140 });
});

test('each crop edge resizes independently, clamped to the full image and minimum 24px', () => {
  assert.equal(typeof crop.dragEditorCrop, 'function');
  const fixtures = [
    ['e', 30, 0, { fw: 230 }], ['s', 0, 30, { fh: 330 }],
    ['w', 30, 0, { fx: 130, fw: 170, ox: 80 }], ['n', 0, 30, { fy: 110, fh: 270, oy: 70 }],
    ['nw', -100, -100, { fx: 50, fy: 40, fw: 250, fh: 340, ox: 0, oy: 0 }],
    ['se', -900, -900, { fw: 24, fh: 24 }],
  ];
  for (const [dir, dx, dy, patch] of fixtures) assert.deepEqual(crop.dragEditorCrop(draft, 'frame', dir, dx, dy), { ...draft, ...patch });
});

test('crop ratio is centered, bounded and photo zoom preserves the frame-center image point', () => {
  assert.equal(typeof crop.ratioEditorCrop, 'function');
  assert.equal(typeof crop.zoomEditorCrop, 'function');
  assert.deepEqual(crop.ratioEditorCrop(draft, 1), { ...draft, fy: 130, fh: 200, oy: 90, ratio: 1 });
  assert.deepEqual(crop.zoomEditorCrop(draft, 2), { ...draft, iw: 800, ih: 1000, ox: 200, oy: 230 });
});

test('crop commit keeps the saved schema and clamps the radius to the new frame', () => {
  assert.equal(typeof crop.editorCropPatch, 'function');
  assert.deepEqual(crop.editorCropPatch({ ...draft, radius: 400 }), { x: 100, y: 80, w: 200, h: 300,
    radius: 100, crop: { ox: 50, oy: 40, iw: 400, ih: 500 } });
});

test('choosing a ratio keeps that ratio when an edge is resized', () => {
  const square = { ...draft, fy: 130, fh: 200, oy: 90, ratio: 1 };
  const next = crop.dragEditorCrop(square, 'frame', 'e', 50, 0);
  assert.deepEqual(next, { ...square, fy: 105, fw: 250, fh: 250, oy: 65 });
  const large = crop.dragEditorCrop(square, 'frame', 'se', 900, 900);
  assert.equal(large.fw / large.fh, 1);
  assert.ok(large.ox + large.fw <= large.iw && large.oy + large.fh <= large.ih);
});

test('an untouched crop and a deleted or replaced target create no document/history change', () => {
  assert.equal(typeof crop.applyEditorCrop, 'function');
  const el = { id: 'p', type: 'image', src: '/original', x: 100, y: 80, w: 200, h: 300 };
  const blocks = [{ id: 'a', elements: [el] }];
  const initial = { ...draft, src: '/original', ox: 0, oy: 0, iw: 200, ih: 300 };
  assert.equal(crop.applyEditorCrop(blocks, initial), blocks);
  assert.equal(crop.applyEditorCrop(blocks, { ...initial, elId: 'missing' }), blocks);
  assert.equal(crop.applyEditorCrop(blocks, { ...initial, src: '/outdated', fw: 100 }), blocks);
  const next = crop.applyEditorCrop(blocks, { ...initial, fw: 100 });
  assert.equal(next[0].elements[0].w, 100);
  assert.equal(crop.applyEditorCrop(next, { ...initial, fw: 100 }), next);
});

test('crop entry preserves the visible cover composition instead of stretching the original photo', () => {
  assert.equal(typeof crop.beginEditorCrop, 'function');
  const el = { id: 'p', type: 'image', src: '/photo', x: 100, y: 80, w: 100, h: 200, radius: 12 };
  assert.deepEqual(crop.beginEditorCrop('a', el, { width: 200, height: 100 }), {
    blockId: 'a', elId: 'p', src: '/photo', radius: 12, fx: 100, fy: 80, fw: 100, fh: 200,
    ox: 150, oy: 0, iw: 400, ih: 200,
    initial: { fx: 100, fy: 80, fw: 100, fh: 200, ox: 150, oy: 0, iw: 400, ih: 200 },
  });
});

test('inspector photo resizing scales the committed crop just like canvas resize', () => {
  assert.equal(typeof crop.resizedPhotoPatch, 'function');
  const el = { w: 200, h: 300, crop: { ox: 50, oy: 40, iw: 400, ih: 500 } };
  assert.deepEqual(crop.resizedPhotoPatch(el, 400, 600), { w: 400, h: 600, crop: { ox: 100, oy: 80, iw: 800, ih: 1000 } });
});

test('opening a cover crop and closing it untouched does not create an undo step', () => {
  const el = { id: 'p', type: 'image', src: '/photo', x: 100, y: 80, w: 200, h: 300 };
  const blocks = [{ id: 'a', elements: [el] }];
  const initial = crop.beginEditorCrop('a', el, { width: 200, height: 100 });
  assert.equal(crop.applyEditorCrop(blocks, initial), blocks);
});
