import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EXAMPLE_STALE_REASONS,
  staleExampleReason,
  stripStaleExampleSelections,
} from '../../src/lib/storyboardExampleStaleness.js';
import { STORYBOARD_SPACE_SET_EXAMPLES } from '../../src/lib/storyboardSpaceSetCatalog.js';

const block = (id, extra = {}) => ({
  id,
  source: 'ai',
  sectionId: 'fit',
  sectionRole: 'fit',
  contentRole: 'coordination',
  cutType: 'styling',
  direction: 'front',
  shot: 'full',
  thumb: `${id}.png`,
  ownImages: [],
  ...extra,
});

const example = (id, extra = {}) => ({
  id,
  cutType: 'styling',
  gender: 'women',
  applicableClothingTypes: ['top'],
  variants: ['all', 'pose'],
  thumb: `${id}-thumb.png`,
  ...extra,
});

const catalog = [
  example('ex_women_top_styling'),
  example('ex_men_top_styling', { gender: 'men' }),
  example('ex_women_bottom_styling', { applicableClothingTypes: ['bottom'] }),
  example('ex_women_top_horizon', { cutType: 'horizon' }),
  example('ex_product_top', { cutType: 'product', gender: null, applicableClothingTypes: ['top'] }),
  example('ex_not_published', { variants: ['pose'] }),
];

test('a block with no exampleId is never stale', () => {
  const plain = block('a');
  const input = [plain];
  assert.equal(staleExampleReason(plain, catalog, { gender: 'women', clothingType: 'top' }), null);
  const result = stripStaleExampleSelections(input, catalog, { gender: 'women', clothingType: 'top' });
  assert.equal(result, input);
  assert.equal(result[0], plain);
});

test('a block belonging to a space-set group is left to the sibling module', () => {
  const grouped = block('a', {
    spaceGroupId: 'ssg1__some-set__i1',
    exampleId: 'ex_men_top_styling', // 성별이 안 맞아도
  });
  assert.equal(staleExampleReason(grouped, catalog, { gender: 'women', clothingType: 'top' }), null);
});

test('a valid example (gender/clothingType/cutType all match) is kept as-is', () => {
  const b = block('a', { exampleId: 'ex_women_top_styling', exampleSelectionOrigin: 'auto' });
  const context = { gender: 'women', clothingType: 'top' };
  assert.equal(staleExampleReason(b, catalog, context), null);
  const [kept] = stripStaleExampleSelections([b], catalog, context);
  assert.equal(kept, b);
});

test('gender mismatch strips the selection but keeps the card, order and images', () => {
  const b = block('a', {
    exampleId: 'ex_women_top_styling',
    exampleSelectionOrigin: 'user',
    ownImages: ['https://example/mine.png'],
  });
  const context = { gender: 'men', clothingType: 'top' };
  assert.equal(staleExampleReason(b, catalog, context), EXAMPLE_STALE_REASONS.GENDER_MISMATCH);
  const [stripped] = stripStaleExampleSelections([b], catalog, context);
  assert.equal(stripped.id, b.id);
  assert.equal(stripped.exampleId, null);
  assert.equal(stripped.exampleSelectionOrigin, null);
  assert.deepEqual(stripped.ownImages, b.ownImages);
  assert.equal(stripped.thumb, b.thumb);
});

test('clothing type not applicable to the example strips the selection', () => {
  const b = block('a', { exampleId: 'ex_women_bottom_styling', exampleSelectionOrigin: 'auto' });
  const context = { gender: 'women', clothingType: 'top' };
  assert.equal(staleExampleReason(b, catalog, context), EXAMPLE_STALE_REASONS.NOT_APPLICABLE);
  const [stripped] = stripStaleExampleSelections([b], catalog, context);
  assert.equal(stripped.exampleId, null);
});

test('cut type mismatch strips the selection', () => {
  const b = block('a', {
    cutType: 'horizon',
    exampleId: 'ex_women_top_styling', // styling example on a horizon block
    exampleSelectionOrigin: 'auto',
  });
  const context = { gender: 'women', clothingType: 'top' };
  assert.equal(staleExampleReason(b, catalog, context), EXAMPLE_STALE_REASONS.CUT_MISMATCH);
  const [stripped] = stripStaleExampleSelections([b], catalog, context);
  assert.equal(stripped.exampleId, null);
});

test('an unknown/unpublished example id strips the selection', () => {
  const missing = block('a', { exampleId: 'ex_does_not_exist', exampleSelectionOrigin: 'auto' });
  const unpublished = block('b', { exampleId: 'ex_not_published', exampleSelectionOrigin: 'auto' });
  const context = { gender: 'women', clothingType: 'top' };
  assert.equal(staleExampleReason(missing, catalog, context), EXAMPLE_STALE_REASONS.UNKNOWN_ID);
  assert.equal(staleExampleReason(unpublished, catalog, context), EXAMPLE_STALE_REASONS.UNKNOWN_ID);
});

test('product cuts carry a genderless example and are never stripped for a gender change', () => {
  const b = block('a', {
    cutType: 'product',
    exampleId: 'ex_product_top',
    exampleSelectionOrigin: 'auto',
  });
  for (const gender of ['women', 'men']) {
    assert.equal(staleExampleReason(b, catalog, { gender, clothingType: 'top' }), null);
    const [kept] = stripStaleExampleSelections([b], catalog, { gender, clothingType: 'top' });
    assert.equal(kept, b);
  }
});

test('a standalone (no spaceGroupId) reference to a real space-set member example is also caught', () => {
  // 서버의 두 번째 경로(resolve_published_example_reference — 그룹 없이 세트 단품을 참고용
  // 으로 고른 블록, space_set_example_incompatible)와 같은 데이터를 본다는 걸 실제 릴리스
  // 카탈로그로 확인한다. hydratedCatalogs.genExamples 는 이 배열이 합쳐진 것이라(withStoryboard
  // SpaceSetExamples), ss_ 예시를 세트 밖에서 고른 블록도 같은 함수로 잡힌다.
  const women = STORYBOARD_SPACE_SET_EXAMPLES.find((item) => item.gender === 'women');
  const catalogWithSetExamples = [...catalog, women];
  const b = block('a', {
    cutType: women.cutType,
    exampleId: women.id,
    exampleSelectionOrigin: 'user',
    // 그룹에 속하지 않음 — spaceGroupId 없음(세트 단품을 참고용으로 고른 경우)
  });
  const staleContext = { gender: 'men', clothingType: women.applicableClothingTypes[0] };
  assert.equal(
    staleExampleReason(b, catalogWithSetExamples, staleContext),
    EXAMPLE_STALE_REASONS.GENDER_MISMATCH,
  );
  const [stripped] = stripStaleExampleSelections([b], catalogWithSetExamples, staleContext);
  assert.equal(stripped.exampleId, null);
  // 같은 성별이면 유효하게 유지된다
  const validContext = { gender: 'women', clothingType: women.applicableClothingTypes[0] };
  assert.equal(staleExampleReason(b, catalogWithSetExamples, validContext), null);
});

test('mixed board: only the stale block is stripped, valid/product/grouped blocks pass through', () => {
  const valid = block('valid', { exampleId: 'ex_women_top_styling', exampleSelectionOrigin: 'auto' });
  const stale = block('stale', { exampleId: 'ex_men_top_styling', exampleSelectionOrigin: 'user' });
  const product = block('product', {
    cutType: 'product', exampleId: 'ex_product_top', exampleSelectionOrigin: 'auto',
  });
  const grouped = block('grouped', { spaceGroupId: 'ssg1__x__i1', exampleId: 'ex_men_top_styling' });
  const plain = block('plain');
  const context = { gender: 'women', clothingType: 'top' };
  const result = stripStaleExampleSelections([valid, stale, product, grouped, plain], catalog, context);
  assert.equal(result[0], valid);
  assert.equal(result[1].exampleId, null);
  assert.equal(result[1].id, 'stale');
  assert.equal(result[2], product);
  assert.equal(result[3], grouped);
  assert.equal(result[4], plain);
});
