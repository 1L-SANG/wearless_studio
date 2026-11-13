import test from 'node:test';
import assert from 'node:assert/strict';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const inputFor = (source, src, meta = {}) => ({
  storyboard: [{ id: 'frame-photo', taxonomyVersion: 4, source, ownImages: source === 'mine' ? [src] : [],
    sectionId: 'wear', pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', pageSectionUsage: 'styling',
    pageDesignId: 'WEAR-STYLING-FRAME-COLUMN-01', slotKey: null }],
  product: { name: '코튼 셔츠', colors: [] }, analysis: {}, copywriting: false,
  cutResults: [{ blockId: 'frame-photo', imageUrl: src, ...meta }],
});

for (const shape of ['square', 'landscape']) {
  test(`frame preserves contain for an uploaded ${shape} with unknown dimensions`, () => {
    const [block] = assemblePage(inputFor('mine', `seller:${shape}`));
    const photo = block.elements.find(element => element.type === 'image' && !element.blur);
    assert.equal(photo.fit, 'contain');
    assert.equal(photo.src, `seller:${shape}`);
    assert.deepEqual([photo.x, photo.y, photo.w, photo.h], [220, 301, 560, 747]);
    assert.equal(photo.crop, undefined);
    assert.equal(block.elements.find(element => element.blur).fit, undefined, 'backdrop retains default cover');
  });
}

test('frame keeps known generated photo geometry while persisting its contain mode', () => {
  const [block] = assemblePage(inputFor('ai', 'generated:portrait', { width: 900, height: 1200 }));
  const photo = block.elements.find(element => element.type === 'image' && !element.blur);
  assert.equal(photo.fit, 'contain');
  assert.deepEqual([photo.x, photo.y, photo.w, photo.h], [220, 301, 560, 747]);
  assert.equal(photo.crop, undefined);
});
