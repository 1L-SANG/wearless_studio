import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clampDragDelta,
  clampElementRect,
  expandBlockHeights,
  getBlockRenderHeight,
  resolveEditorBorderRadius,
  resolveEditorElementResize,
} from '../../src/features/editor/editorGeometry.js';

test('editor geometry keeps derived heights and selections inside the canvas', () => {
  assert.equal(getBlockRenderHeight({ h: 660, elements: [{ y: 640, h: 120 }] }), 810);
  assert.equal(expandBlockHeights([{ h: 660, elements: [{ y: 640, h: 120 }] }])[0].h, 810);

  const group = {
    a: { x: 20, y: 10, w: 100 },
    b: { x: 300, y: 40, w: 200 },
  };
  assert.deepEqual(clampDragDelta(group, [-50, -30]), [-20, -10]);
  assert.deepEqual(clampDragDelta(group, [600, 0]), [500, 0]);

  assert.deepEqual(clampElementRect(-10, -20, 110, 120), { x: 0, y: 0, w: 100, h: 100 });
  assert.deepEqual(clampElementRect(950, 20, 100, 40), { x: 950, y: 20, w: 50, h: 40 });
});

test('large corner radii render rectangular images as full ellipses', () => {
  assert.equal(resolveEditorBorderRadius(400, 880, 1312), 400);
  assert.equal(resolveEditorBorderRadius(440, 880, 1312), '50%');
  assert.equal(resolveEditorBorderRadius(10_000, 880, 1312), '50%');
});

test('unlocked middle resize handles can change one image dimension independently', () => {
  const start = { x: 60, y: 40, w: 880, h: 1312 };

  assert.deepEqual(resolveEditorElementResize({
    start,
    width: 880,
    height: 1562,
    keepImageRatio: false,
    naturalRatio: 2 / 3,
  }), { x: 60, y: 40, w: 880, h: 1562 });

  assert.deepEqual(resolveEditorElementResize({
    start,
    width: 920,
    height: 1312,
    keepImageRatio: false,
    naturalRatio: 2 / 3,
  }), { x: 60, y: 40, w: 920, h: 1312 });
});
