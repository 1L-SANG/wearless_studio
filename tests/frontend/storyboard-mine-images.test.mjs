import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeMineImages,
  promoteMineImage,
} from '../../src/lib/storyboardMineImages.js';

test('selecting a mine image promotes the exact image used by the page assembler', () => {
  assert.deepEqual(
    promoteMineImage(['first.png', 'second.png'], 'second.png'),
    ['second.png', 'first.png'],
  );
});

test('mine image state stores URL strings and clears the thumbnail source when empty', () => {
  assert.deepEqual(normalizeMineImages([{ url: 'one.png' }, null, 'two.png']), [
    'one.png', 'two.png',
  ]);
  assert.deepEqual(normalizeMineImages([]), []);
});
