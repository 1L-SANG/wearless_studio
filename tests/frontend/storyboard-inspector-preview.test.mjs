import test from 'node:test';
import assert from 'node:assert/strict';
import { storyboardPreviewSource, storyboardPreviewCuts } from '../../src/features/storyboard/storyboardPreview.js';

test('new set members show their selected example rather than a temporary placeholder', () => {
  const block = { source: 'ai', exampleId: 'new', thumb: '/placeholder.svg' }, catalogs = { genExamples: [{ id: 'new', thumb: '/actual.webp' }] };
  assert.equal(storyboardPreviewSource(block, catalogs), '/actual.webp');
  assert.equal(storyboardPreviewSource({ ...block, previewThumb: '/explicit.webp' }, catalogs), '/explicit.webp');
});
test('seller photos and missing selection preserve the authoritative source', () => {
  assert.equal(storyboardPreviewSource({ source: 'mine', ownImages: [{ url: '/own.webp' }] }), '/own.webp');
  assert.equal(storyboardPreviewSource(null), null);
});
test('the assembly thumbnail input uses the same selected source and excludes text cards', () => {
  const blocks = [{ id: 'photo', source: 'ai', exampleId: 'chosen' }, { id: 'text', source: 'text' }];
  const catalogs = { genExamples: [{ id: 'chosen', thumb: '/chosen.webp' }] };
  const cuts = storyboardPreviewCuts(blocks, catalogs);
  assert.equal(cuts.length, 1); assert.equal(cuts[0].imageUrl, storyboardPreviewSource(blocks[0], catalogs));
});
