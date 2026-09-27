import test from 'node:test';
import assert from 'node:assert/strict';
import { setPreviewPlacement } from '../../src/lib/storyboardSetPreviewPlacement.js';
const panel = { left: 800, right: 1152, top: 80, bottom: 780, width: 352 };
const place = (card = { left: 820, top: 160 }, extra = {}) => setPreviewPlacement({ panel, card, viewportWidth: 1440, viewportHeight: 900, ...extra });
test('both columns use the same right-side dock without moving the inspector', () => {
  const first = place(), second = place({ left: 1000, top: 460 });
  assert.deepEqual(first, second); assert.equal(first.placement, 'right');
  assert.equal(first.left, panel.right + 20); assert.equal(first.width, 180);
});
test('1440 sidebar keeps a narrower right dock when 120px remains', () => {
  const result = place(null, { panel: { ...panel, left: 930, right: 1282 } });
  assert.equal(result.placement, 'right'); assert.equal(result.width, 126);
  assert.ok(result.left + result.width <= 1428);
});
test('only insufficient right space permits the left fallback', () => {
  const result = place(null, { viewportWidth: 1280, panel: { ...panel, left: 900, right: 1252 } });
  assert.equal(result.placement, 'left'); assert.equal(result.width, 180);
});
test('narrow screens use an above strip or a bounded compact fallback', () => {
  const top = place(null, { viewportWidth: 390, panel: { left: 12, right: 378, top: 310, bottom: 880 } });
  assert.equal(top.placement, 'top'); assert.ok(top.top >= 12);
  const compact = place(null, { viewportWidth: 390, panel: { left: 12, right: 378, top: 60, bottom: 840 } });
  assert.equal(compact.placement, 'compact'); assert.ok(compact.left + compact.width <= 378); assert.ok(compact.top + compact.height <= 888);
});
