import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SPACE_SET_STALE_REASONS,
  staleSpaceSetReason,
  stripStaleSpaceSetBindings,
} from '../../src/lib/storyboardSpaceSetStaleness.js';
import {
  spaceSetGroupId,
  STORYBOARD_SPACE_SETS,
} from '../../src/lib/storyboardSpaceSetCatalog.js';

const findSet = (predicate) => STORYBOARD_SPACE_SETS.find(predicate);

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

const boundBlock = (id, set, groupInstance, extra = {}) => block(id, {
  spaceGroupId: spaceSetGroupId(set.id, groupInstance),
  spaceVariation: set.spaceVariation,
  exampleId: set.members[0].exampleId,
  exampleSelectionOrigin: 'user',
  refScope: 'pose',
  ...extra,
});

test('a block with no spaceGroupId is never stale', () => {
  const plain = block('a');
  const input = [plain];
  assert.equal(staleSpaceSetReason(plain, { gender: 'women', clothingType: 'top' }), null);
  const result = stripStaleSpaceSetBindings(input, { gender: 'women', clothingType: 'top' });
  assert.equal(result, input); // 바뀐 게 없으면 원본 배열 참조를 그대로 돌려준다
  assert.equal(result[0], plain);
});

test('a valid binding (gender/clothingType/variation all match) is kept as-is', () => {
  const women = findSet((s) => s.gender === 'women');
  const b = boundBlock('a', women, 'i1');
  const context = { gender: 'women', clothingType: women.setApplicableClothingTypes[0] };
  assert.equal(staleSpaceSetReason(b, context), null);
  const result = stripStaleSpaceSetBindings([b], context);
  assert.equal(result[0], b); // 원본 블록 참조 그대로 — 불필요한 재생성 없음
  assert.equal(result[0].spaceGroupId, b.spaceGroupId);
});

test('gender mismatch strips the binding but keeps the card, order and images', () => {
  const women = findSet((s) => s.gender === 'women');
  const b = boundBlock('a', women, 'i1', { ownImages: ['https://example/mine.png'] });
  const context = { gender: 'men', clothingType: women.setApplicableClothingTypes[0] };
  assert.equal(staleSpaceSetReason(b, context), SPACE_SET_STALE_REASONS.GENDER_MISMATCH);
  const [stripped] = stripStaleSpaceSetBindings([b], context);
  assert.equal(stripped.id, b.id);
  assert.equal(stripped.spaceGroupId, null);
  assert.equal(stripped.exampleId, null);
  assert.deepEqual(stripped.ownImages, b.ownImages);
  assert.equal(stripped.thumb, b.thumb);
});

test('clothing type not applicable to the set strips the binding', () => {
  const topOnly = findSet((s) => (
    s.setApplicableClothingTypes.length === 1 && s.setApplicableClothingTypes[0] === 'top'
  ));
  const b = boundBlock('a', topOnly, 'i1');
  const context = { gender: topOnly.gender, clothingType: 'bottom' };
  assert.equal(staleSpaceSetReason(b, context), SPACE_SET_STALE_REASONS.NOT_APPLICABLE);
  const [stripped] = stripStaleSpaceSetBindings([b], context);
  assert.equal(stripped.spaceGroupId, null);
});

test('space variation mismatch strips the binding', () => {
  const set = findSet((s) => s.spaceVariation === 'subtle');
  const b = boundBlock('a', set, 'i1', { spaceVariation: 'fixed' });
  const context = { gender: set.gender, clothingType: set.setApplicableClothingTypes[0] };
  assert.equal(staleSpaceSetReason(b, context), SPACE_SET_STALE_REASONS.VARIATION_MISMATCH);
  const [stripped] = stripStaleSpaceSetBindings([b], context);
  assert.equal(stripped.spaceGroupId, null);
  assert.equal(stripped.spaceVariation, null);
});

test('an unknown/unresolvable group id strips the binding', () => {
  const b = block('a', {
    spaceGroupId: 'ssg1__does-not-exist__i1',
    spaceVariation: 'subtle',
    exampleId: 'ss_bogus',
  });
  const context = { gender: 'women', clothingType: 'top' };
  assert.equal(staleSpaceSetReason(b, context), SPACE_SET_STALE_REASONS.UNKNOWN_SET);
  const [stripped] = stripStaleSpaceSetBindings([b], context);
  assert.equal(stripped.spaceGroupId, null);
});

test('mixed board: only stale blocks are stripped, valid ones and non-set blocks pass through', () => {
  const women = findSet((s) => s.gender === 'women');
  const men = findSet((s) => s.gender === 'men');
  const valid = boundBlock('valid', women, 'i1');
  const stale = boundBlock('stale', men, 'i2');
  const plain = block('plain');
  const context = { gender: 'women', clothingType: women.setApplicableClothingTypes[0] };
  const result = stripStaleSpaceSetBindings([valid, stale, plain], context);
  assert.equal(result[0], valid);
  assert.equal(result[1].spaceGroupId, null);
  assert.equal(result[1].id, 'stale');
  assert.equal(result[2], plain);
});
