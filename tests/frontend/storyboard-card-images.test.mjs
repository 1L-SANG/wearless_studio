import assert from 'node:assert/strict';
import test from 'node:test';

import {
  generationExampleImageSources,
} from '../../src/lib/generationExamples.js';

test('storyboard cards use the lightweight release thumbnail for display and prewarming', () => {
  const thumb = 'https://images.wearless.kr/seed/genexamples/v1/releases/release-1/thumb/example.webp';
  assert.deepEqual(generationExampleImageSources({ thumb }), {
    src: thumb,
    srcSet: undefined,
    prewarm: thumb,
  });
});

test('storyboard cards do not prewarm explicit full assets or invent paths for uploads', () => {
  assert.equal(generationExampleImageSources({
    thumb: '/thumb/set.webp',
    assetUrl: '/all/set.jpg',
  }).prewarm, '/thumb/set.webp');
  assert.deepEqual(generationExampleImageSources({ thumb: 'blob:mine' }), {
    src: 'blob:mine',
    srcSet: undefined,
    prewarm: 'blob:mine',
  });
});
