import test from 'node:test';
import assert from 'node:assert/strict';

import { MAX_WARDROBE_UPLOADS, runWithConcurrency } from '../../src/features/editor/editorUpload.js';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test('runWithConcurrency never runs more than the limit at once', async () => {
  let active = 0;
  let peak = 0;
  await runWithConcurrency([1, 2, 3, 4, 5, 6, 7], 3, async () => {
    active += 1;
    peak = Math.max(peak, active);
    await tick();
    active -= 1;
  });
  assert.equal(peak, 3);
});

test('runWithConcurrency keeps results in input order', async () => {
  const results = await runWithConcurrency([30, 10, 20], 2, async (ms) => {
    await new Promise((resolve) => setTimeout(resolve, ms));
    return ms * 2;
  });
  assert.deepEqual(results.map((r) => r.value), [60, 20, 40]);
});

test('one failing item does not stop the others', async () => {
  const seen = [];
  const results = await runWithConcurrency(['a', 'bad', 'c'], 2, async (item) => {
    if (item === 'bad') throw new Error('upload failed');
    seen.push(item);
    return item;
  });
  assert.deepEqual(seen.sort(), ['a', 'c']);
  assert.equal(results[1].ok, false);
  assert.equal(results[1].error.message, 'upload failed');
  assert.equal(results.filter((r) => r.ok).length, 2);
});

test('empty input resolves to an empty list', async () => {
  assert.deepEqual(await runWithConcurrency([], 3, async () => 1), []);
});

test('wardrobe multi upload has a sane cap', () => {
  assert.ok(MAX_WARDROBE_UPLOADS >= 10 && MAX_WARDROBE_UPLOADS <= 30);
});
