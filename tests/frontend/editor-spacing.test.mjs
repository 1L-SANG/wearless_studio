import test from 'node:test';
import assert from 'node:assert/strict';
import * as snap from '../../src/features/editor/editorSnap.js';
const rect = (x, y, w = 100, h = 100) => ({ x, y, w, h });

test('drag snaps between two overlapping neighbors to equal visible gaps', () => {
  assert.equal(typeof snap.snapEditorSpacing, 'function');
  const result = snap.snapEditorSpacing({ startRects: [rect(100, 100)], guideRects: [rect(20, 100), rect(400, 100)], delta: [112, 0], scale: 1 });
  assert.deepEqual(result.delta, [110, 0]);
  assert.deepEqual(result.gaps.filter(g => g.axis === 'x').map(g => g.distance), [90, 90]);
});

test('drag repeats an existing row gap and Alt bypasses alignment and spacing', () => {
  assert.equal(typeof snap.snapEditorSpacing, 'function');
  const result = snap.snapEditorSpacing({ startRects: [rect(500, 100)], guideRects: [rect(20, 100), rect(180, 100)], delta: [-158, 0], scale: 1 });
  assert.deepEqual(result.delta, [-160, 0]);
  assert.deepEqual(snap.snapEditorDragDelta({ startRects: [rect(100, 40)], guideRects: [rect(300, 200)], delta: [198, 0], altKey: true }),
    { delta: [198, 0], guides: { vertical: null, horizontal: null } });
});

test('unrelated rows do not attract equal-spacing snaps and measurements never change inputs', () => {
  assert.equal(typeof snap.snapEditorSpacing, 'function');
  const input = { startRects: [rect(100, 100)], guideRects: [rect(20, 300, 40), rect(340, 500, 40)], delta: [105, 0] };
  const before = JSON.stringify(input);
  assert.deepEqual(snap.snapEditorSpacing(input).delta, [105, 0]);
  assert.equal(JSON.stringify(input), before);
});
