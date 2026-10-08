import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const start = source.indexOf('function storyboardPreviewSource(');
assert.ok(start >= 0, 'one preview resolver must serve the board and inspector');
const body = source.slice(start, source.indexOf('\nfunction StoryboardMedia(', start));
const preview = new Function('detailTargetPresentation', 'generationExampleImageSources', 'mineImageUrl', `${body}; return storyboardPreviewSource;`)(
  block => block.detail ? { src: block.detailSrc } : null,
  example => ({ src: example.thumb }), value => typeof value === 'string' ? value : value?.url,
);
test('new set members show the example image instead of their temporary placeholder', () => {
  const block = { source: 'ai', exampleId: 'new-member', thumb: '/placeholder.svg' };
  assert.equal(preview(block, { genExamples: [{ id: 'new-member', thumb: '/actual-example.webp' }] }), '/actual-example.webp');
  assert.equal(preview({ ...block, previewThumb: '/explicit-preview.webp' }, { genExamples: [{ id: 'new-member', thumb: '/actual-example.webp' }] }), '/explicit-preview.webp');
});
test('detail and own-photo previews retain their authoritative source', () => {
  assert.equal(preview({ detail: true, detailSrc: null, thumb: '/unrelated.webp' }), null);
  assert.equal(preview({ source: 'mine', ownImages: [{ url: '/own.webp' }] }), '/own.webp');
  assert.equal(preview(null), null);
});
test('board, selected header, and scope preview use the same source resolver', () => {
  assert.match(source, /const src = storyboardPreviewSource\(block, catalogs\)/);
  assert.match(source, /const selectedThumbnail = storyboardPreviewSource\(selected, catalogs\)/);
  assert.match(source, /src=\{storyboardPreviewSource\(member, previewCatalogs\)\}/);
});
