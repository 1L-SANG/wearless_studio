import assert from 'node:assert/strict';
import test from 'node:test';

import { snapEditorDragDelta } from '../../src/features/editor/editorSnap.js';

const rect = (id, x, y, w, h, extra = {}) => ({ id, x, y, w, h, ...extra });

test('snaps a single selected image to sibling left, center, and right guides', () => {
  const selected = [rect('image-1', 100, 40, 120, 90)];
  const sibling = [rect('text-1', 300, 200, 180, 60)];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [198, 0],
    scale: 1,
    blockHeight: 800,
  }), { delta: [200, 0], guides: { vertical: 300, horizontal: null } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [228, 0],
    scale: 1,
    blockHeight: 800,
  }), { delta: [230, 0], guides: { vertical: 390, horizontal: null } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [258, 0],
    scale: 1,
    blockHeight: 800,
  }), { delta: [260, 0], guides: { vertical: 480, horizontal: null } });
});

test('snaps a single selected text box to sibling top, middle, and bottom guides', () => {
  const selected = [rect('text-1', 100, 40, 120, 90)];
  const sibling = [rect('shape-1', 300, 200, 180, 60)];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [0, 158],
    scale: 1,
    blockHeight: 800,
  }), { delta: [0, 160], guides: { vertical: null, horizontal: 200 } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [0, 143],
    scale: 1,
    blockHeight: 800,
  }), { delta: [0, 145], guides: { vertical: null, horizontal: 230 } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: sibling,
    delta: [0, 128],
    scale: 1,
    blockHeight: 800,
  }), { delta: [0, 130], guides: { vertical: null, horizontal: 260 } });
});

test('snaps group bounds for mixed image, text, shape, line, bubble, and frame-like rects', () => {
  const selected = [
    rect('image-1', 100, 120, 140, 160),
    rect('text-1', 270, 180, 110, 60),
    rect('shape-1', 180, 90, 60, 40),
    rect('line-1', 240, 150, 180, 0),
    rect('bubble-1', 110, 270, 180, 90),
    rect('frame-1', 300, 100, 80, 130),
  ];
  const guideRects = [rect('shape-guide', 600, 420, 100, 80)];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects,
    delta: [177, 92],
    scale: 1,
    blockHeight: 900,
  }), { delta: [180, 90], guides: { vertical: 600, horizontal: 450 } });
});

test('canvas x guides and block vertical center are available without sibling rects', () => {
  const selected = [rect('shape-1', 342, 80, 120, 70)];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects: [],
    delta: [96, 284],
    scale: 1,
    blockHeight: 800,
  }), { delta: [98, 285], guides: { vertical: 500, horizontal: 400 } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: [rect('line-1', 26, 20, 20, 0)],
    guideRects: [],
    delta: [7, 0],
    scale: 1,
    blockHeight: 800,
  }), { delta: [4, 0], guides: { vertical: 40, horizontal: null } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: [rect('frame-1', 850, 20, 100, 50)],
    guideRects: [],
    delta: [8, 0],
    scale: 1,
    blockHeight: 800,
  }), { delta: [10, 0], guides: { vertical: 960, horizontal: null } });
});

test('scale converts the 8px screen threshold into canvas coordinates', () => {
  const selected = [rect('shape-1', 100, 50, 100, 50)];
  const guideRects = [rect('image-1', 250, 50, 100, 50)];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects,
    delta: [141, 0],
    scale: 0.5,
    blockHeight: 600,
  }), { delta: [150, 0], guides: { vertical: 250, horizontal: 50 } });

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects,
    delta: [141, 0],
    scale: 2,
    blockHeight: 600,
  }), { delta: [141, 0], guides: { vertical: null, horizontal: 50 } });
});

test('closest match wins and each axis returns only one guide', () => {
  const selected = [rect('shape-1', 100, 100, 100, 100)];
  const guideRects = [
    rect('text-1', 247, 348, 50, 50),
    rect('shape-2', 252, 352, 50, 50),
  ];

  assert.deepEqual(snapEditorDragDelta({
    startRects: selected,
    guideRects,
    delta: [145, 245],
    scale: 1,
    blockHeight: 800,
  }), { delta: [147, 248], guides: { vertical: 247, horizontal: 348 } });
});

test('does not snap when no anchor is within threshold', () => {
  assert.deepEqual(snapEditorDragDelta({
    startRects: [rect('image-1', 100, 100, 80, 80)],
    guideRects: [rect('text-1', 300, 300, 80, 80)],
    delta: [70, 70],
    scale: 1,
    blockHeight: 700,
  }), { delta: [70, 70], guides: { vertical: null, horizontal: null } });
});

test('does not snap to gap distances between rects', () => {
  assert.deepEqual(snapEditorDragDelta({
    startRects: [rect('shape-1', 100, 100, 100, 100)],
    guideRects: [
      rect('left-guide', 20, 300, 40, 100),
      rect('right-guide', 340, 500, 40, 100),
    ],
    delta: [105, 0],
    scale: 1,
    blockHeight: 700,
  }), { delta: [105, 0], guides: { vertical: null, horizontal: null } });
});

test('does not mutate selected rects, guide rects, or delta input', () => {
  const selected = [rect('image-1', 100, 40, 120, 90)];
  const guideRects = [rect('text-1', 300, 200, 180, 60)];
  const delta = [198, 158];
  const before = JSON.stringify({ selected, guideRects, delta });

  snapEditorDragDelta({ startRects: selected, guideRects, delta, scale: 1, blockHeight: 800 });

  assert.equal(JSON.stringify({ selected, guideRects, delta }), before);
});
