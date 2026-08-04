import assert from 'node:assert/strict';
import test from 'node:test';

import {
  generationExampleImageSources,
} from '../../src/lib/generationExamples.js';

test('storyboard cards pair the release thumbnail with the published all image', () => {
  const thumb = 'https://images.wearless.kr/seed/genexamples/v1/releases/release-1/thumb/example.webp';
  assert.deepEqual(generationExampleImageSources({ thumb }), {
    src: thumb,
    srcSet: `${thumb} 1x, https://images.wearless.kr/seed/genexamples/v1/releases/release-1/all/example.png 2x`,
    prewarm: 'https://images.wearless.kr/seed/genexamples/v1/releases/release-1/all/example.png',
  });
});

test('storyboard cards prefer an explicit set asset URL and do not invent paths for uploads', () => {
  assert.equal(generationExampleImageSources({
    thumb: '/thumb/set.webp',
    assetUrl: '/all/set.jpg',
  }).prewarm, '/all/set.jpg');
  assert.deepEqual(generationExampleImageSources({ thumb: 'blob:mine' }), {
    src: 'blob:mine',
    srcSet: undefined,
    prewarm: 'blob:mine',
  });
});
