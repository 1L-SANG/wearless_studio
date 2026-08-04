import assert from 'node:assert/strict';
import test from 'node:test';

import { paginateGenerationGalleryItems } from '../../src/lib/generationExamples.js';

test('generation gallery fills every 3×2 page with six examples', () => {
  assert.deepEqual(paginateGenerationGalleryItems(['a', 'b', 'c', 'd', 'e', 'f']), [
    ['a', 'b', 'c', 'd', 'e', 'f'],
  ]);
  assert.deepEqual(paginateGenerationGalleryItems([]), [[]]);
});

test('explicit page sizes remain supported for non-gallery consumers', () => {
  const examples = ['example-1', 'example-2', 'example-3', 'example-4'];
  const userImages = ['mine-1', 'mine-2'];
  assert.deepEqual(paginateGenerationGalleryItems([...examples, ...userImages], 5), [
    ['example-1', 'example-2', 'example-3', 'example-4', 'mine-1'],
    ['mine-2'],
  ]);
});
