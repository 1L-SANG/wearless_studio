import assert from 'node:assert/strict';
import test from 'node:test';

import { paginateGenerationGalleryItems } from '../../src/lib/generationExamples.js';

test('generation gallery reserves every sixth cell by paging content in groups of five', () => {
  assert.deepEqual(paginateGenerationGalleryItems(['a', 'b', 'c', 'd', 'e', 'f']), [
    ['a', 'b', 'c', 'd', 'e'],
    ['f'],
  ]);
  assert.deepEqual(paginateGenerationGalleryItems([]), [[]]);
});

test('user images remain at the end of the generation gallery content', () => {
  const examples = ['example-1', 'example-2', 'example-3', 'example-4'];
  const userImages = ['mine-1', 'mine-2'];
  assert.deepEqual(paginateGenerationGalleryItems([...examples, ...userImages]), [
    ['example-1', 'example-2', 'example-3', 'example-4', 'mine-1'],
    ['mine-2'],
  ]);
});
