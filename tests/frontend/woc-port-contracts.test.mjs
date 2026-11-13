import test from 'node:test';
import assert from 'node:assert/strict';
import { ensureSections, normalizeBoard, deriveSections } from '../../src/lib/sections.js';
import { assignInternalContentRoles, normalizedRecipePatch, productPresentationOf } from '../../src/lib/storyboardTaxonomy.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';

const productCard = (extra = {}) => ({
  id: 'product', taxonomyVersion: 4, source: 'ai', sectionId: 'products',
  pageSectionKey: 'emo.color_product', pageSectionKind: 'color_product', pageDesignId: 'CP-EMO-01',
  slotKey: null, sectionLayout: 'stack', sectionRole: 'product', contentRole: 'productOverview',
  title: '제품 전체', cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'base',
  matchIds: [], faceExposure: null, outerClosureState: null, ...extra,
});
const textCard = (extra = {}) => ({
  id: 'intro', taxonomyVersion: 4, source: 'text', sectionId: 'intro',
  pageSectionKey: 'emo.intro', pageSectionKind: 'intro', pageDesignId: 'T01-D07',
  sectionLayout: 'stack', cutType: null, contentRole: null, sectionRole: null, ...extra,
});

for (const productPresentation of ['ghost', 'flat', 'natural_light']) {
  test(`v4 ${productPresentation} survives normalization, serialized reload and duplication with inactive choices intact`, () => {
    const original = productCard({ productPresentation, exampleId: 'retained-example',
      exampleSelectionOrigin: 'manual', refImages: ['/retained.jpg'], refAssetIds: ['retained-asset'] });
    const before = structuredClone(original);
    const [loaded] = ensureSections(JSON.parse(JSON.stringify([original])));
    const duplicated = normalizeBoard([loaded, { ...loaded, id: 'copy' }]);
    assert.equal(duplicated.length, 2);
    for (const block of duplicated) {
      assert.equal(block.productPresentation, productPresentation);
      assert.equal(block.shot, 'ghost');
      assert.equal(block.direction, 'back');
      assert.equal(block.pageSectionKey, 'emo.color_product');
      assert.equal(block.pageDesignId, 'CP-EMO-01');
      assert.equal(block.exampleId, 'retained-example');
      assert.deepEqual(block.refImages, ['/retained.jpg']);
      assert.deepEqual(block.refAssetIds, ['retained-asset']);
    }
    assert.deepEqual(original, before);
  });
}

test('v4 normalization removes invalid and non-target presentations without filling legacy missing values', () => {
  for (const block of [
    productCard(), productCard({ productPresentation: null }), productCard({ productPresentation: 'flatlay' }),
    productCard({ productPresentation: 'flat', shot: 'detail' }),
    productCard({ productPresentation: 'flat', cutType: 'styling', shot: 'full' }),
    productCard({ productPresentation: 'flat', source: 'mine' }),
    textCard({ productPresentation: 'flat' }),
  ]) {
    const [normalized] = ensureSections([block]);
    assert.equal(Object.hasOwn(normalized, 'productPresentation'), false);
    assert.equal(Object.hasOwn(normalizeBoard([normalized])[0], 'productPresentation'), false);
  }
});

test('slot coercion removes a formerly valid product presentation when a card becomes worn', () => {
  const original = productCard({ productPresentation: 'natural_light', sectionId: 'first',
    pageSectionKey: 'emo.first', pageSectionKind: 'first', pageDesignId: 'P01-D09', slotKey: 'hero',
    exampleId: 'product-example', thumb: '/example.jpg', baseThumb: '/base.jpg', previewThumb: '/preview.jpg' });
  const [normalized] = assignInternalContentRoles([original]);
  assert.equal(normalized.cutType, 'styling');
  assert.equal(normalized.shot, 'full');
  assert.equal(normalized.contentRole, 'hero');
  assert.equal(Object.hasOwn(normalized, 'productPresentation'), false);
  assert.equal(normalized.exampleId, null);
  assert.equal(normalized.thumb, '/base.jpg');
  assert.equal(normalized.previewThumb, null);
});

test('presentation names never become product shot values and direction repair clears incompatible examples', () => {
  for (const shot of ['flat', 'natural_light']) {
    const original = productCard({ shot });
    assert.equal(normalizedRecipePatch(original, 'productOverview').shot, 'ghost');
    assert.equal(productPresentationOf(original), null);
  }
  const [normalized] = assignInternalContentRoles([productCard({ productPresentation: 'flat',
    direction: 'side', exampleId: 'side-example', thumb: '/example.jpg', baseThumb: '/base.jpg' })]);
  assert.equal(normalized.direction, 'front');
  assert.equal(normalized.productPresentation, 'flat');
  assert.equal(normalized.exampleId, null);
  assert.equal(normalized.thumb, '/base.jpg');
});

test('v4 section metadata and ordering survive retired hook and row cleanup', () => {
  const blocks = [textCard(), productCard({ productPresentation: 'flat', hookFrameId: 'old-hook',
    hookStyle: 'signature', layoutRowId: 'old-row', colorwayGroupId: 'old-colorway', pose: 'old-pose' })];
  const normalized = ensureSections(blocks);
  assert.deepEqual(normalized.map(block => block.id), ['intro', 'product']);
  assert.deepEqual(deriveSections(normalized).map(section => section.pageSectionKey), ['emo.intro', 'emo.color_product']);
  assert.equal(normalized[1].productPresentation, 'flat');
  for (const field of ['hookFrameId', 'hookStyle', 'layoutRowId', 'colorwayGroupId', 'pose']) {
    assert.equal(Object.hasOwn(normalized[1], field), false);
  }
});

test('v4 billing excludes text and mine cards while retaining explicit-mode and similar-cut identity', () => {
  const blocks = [productCard(), ...['ghost', 'flat', 'natural_light'].map((productPresentation, index) =>
    productCard({ id: `mode-${index}`, productPresentation })),
  productCard({ id: 'inactive', productPresentation: 'flat', exampleId: 'other', spaceGroupId: 'inactive-set',
    refScope: 'pose', refImages: ['/other.jpg'], refAssetIds: ['other'], _exampleRepeatIndex: 3 }),
  productCard({ id: 'similar', productPresentation: 'flat', similarSourceBlockId: 'mode-1' }),
  textCard(), productCard({ id: 'mine', source: 'mine', productPresentation: 'flat' })];
  const before = structuredClone(blocks);
  assert.equal(uniqueGenerationCutCount(blocks), 5);
  assert.deepEqual(blocks, before);
});
