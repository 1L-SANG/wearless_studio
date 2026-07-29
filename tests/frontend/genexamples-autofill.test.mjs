import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  assignGenerationExamples, directionBadgeLabel, exampleSelectionFingerprintFields,
  isGenerationCombinationPublic, selectGenerationExamples, shouldMarkStoryboardDirty,
  storedExampleConditionStatus,
} from '../../src/lib/generationExamples.js';
import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';

const example = (id, extra = {}) => ({
  id, thumb: `https://images.test/${id}.webp`, rank: 1, cutType: 'styling', shot: 'full',
  gender: 'women', direction: 'front', mood: 'daily', applicableClothingTypes: ['top'],
  variants: ['all'], ...extra,
});
const block = (id, extra = {}) => ({
  id, source: 'ai', cutType: 'styling', shot: 'full', direction: 'back',
  sectionId: 'section-a', sectionLayout: 'twoColumn', layoutRowId: 'row-a',
  spaceGroupId: null, thumb: `placeholder:${id}`, matchIds: ['ignored'], ...extra,
});
const product = { clothingType: 'top' };
const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);

test('owner declarations gate frontend combinations', () => {
  assert.equal(isGenerationCombinationPublic({ cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women' }), true);
  assert.equal(isGenerationCombinationPublic({ cutType: 'styling', shot: 'full', clothingType: 'dress', gender: 'women' }), false);
  assert.equal(isGenerationCombinationPublic({ cutType: 'product', shot: 'detail', clothingType: 'bottom', gender: 'women' }), true);
});

test('eligibility uses cut, shot, clothing, gender and all publication, not direction or matchIds', () => {
  const catalog = [
    example('front-ok'), example('back-ok', { direction: 'back', rank: 2 }),
    example('wrong-shot', { shot: 'medium' }), example('wrong-gender', { gender: 'men' }),
    example('wrong-clothing', { applicableClothingTypes: ['bottom'] }),
    example('pose-only', { variants: ['pose'] }),
  ];
  assert.deepEqual(selectGenerationExamples(catalog, {
    cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
    direction: 'back', matchIds: ['ignored'],
  }).map((item) => item.id), ['front-ok', 'back-ok']);
  const products = [example('product-ok', { cutType: 'product', shot: 'ghost', gender: null, mood: null })];
  assert.equal(selectGenerationExamples(products, {
    cutType: 'product', shot: 'ghost', clothingType: 'top', gender: 'women',
  })[0].id, 'product-ok');
});

test('gallery mood round-robin supplies the quality top three and six cards cycle 1,2,3', () => {
  const catalog = [
    example('a1', { mood: 'a', rank: 1 }), example('a2', { mood: 'a', rank: 2 }),
    example('a3', { mood: 'a', rank: 3 }), example('b1', { mood: 'b', rank: 1 }),
    example('b2', { mood: 'b', rank: 2 }), example('b3', { mood: 'b', rank: 3 }),
  ];
  assert.deepEqual(selectGenerationExamples(catalog, {
    cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
  }).map((item) => item.id), ['a1', 'b1', 'a2', 'b2', 'a3', 'b3']);
  const result = assignGenerationExamples(Array.from({ length: 6 }, (_, i) => block(`b${i}`)), {
    catalog, product, gender: 'women',
  });
  assert.deepEqual(result.blocks.map((item) => item.exampleId), ['a1', 'b1', 'a2', 'a1', 'b1', 'a2']);
  assert.ok(result.blocks.every((item) => item.exampleSelectionOrigin === 'auto'));
});

test('existing auto usage counts, keys stay independent, and one/two-item pools cycle', () => {
  const catalog = [example('full-1'), example('full-2', { rank: 2 }), example('medium-1', { shot: 'medium' })];
  const result = assignGenerationExamples([
    block('existing', { exampleId: 'full-2', exampleSelectionOrigin: 'auto' }),
    block('full-a'), block('full-b'), block('medium-a', { shot: 'medium' }), block('medium-b', { shot: 'medium' }),
  ], { catalog, product, gender: 'women' });
  assert.deepEqual(result.blocks.map((item) => item.exampleId), ['full-2', 'full-1', 'full-1', 'medium-1', 'medium-1']);
});

test('legacy and user choices are protected and only requested new blocks are assigned', () => {
  const result = assignGenerationExamples([
    block('legacy', { exampleId: 'legacy-choice' }),
    block('user', { exampleId: 'user-choice', exampleSelectionOrigin: 'user' }),
    block('target'), block('other'),
  ], { catalog: [example('auto-choice')], product, gender: 'women', onlyBlockIds: ['target'] });
  assert.deepEqual(result.blocks.map(({ exampleId, exampleSelectionOrigin }) => [exampleId, exampleSelectionOrigin]), [
    ['legacy-choice', undefined], ['user-choice', 'user'], ['auto-choice', 'auto'], [undefined, undefined],
  ]);
  assert.deepEqual(result.protectedIds, []);
  const migrated = assignGenerationExamples([block('legacy', { exampleId: 'legacy-choice' })], {
    catalog: [example('auto-choice')], product, gender: 'women',
  });
  assert.equal(migrated.blocks[0].exampleSelectionOrigin, 'user');
  assert.deepEqual(migrated.protectedIds, ['legacy']);
});

test('assignment is deterministic, stable after re-entry/deletion, and structure-neutral', () => {
  const catalog = [example('one'), example('two', { rank: 2 })];
  const input = [block('a'), block('b')];
  const first = assignGenerationExamples(input, { catalog, product, gender: 'women' }).blocks;
  const second = assignGenerationExamples(input, { catalog, product, gender: 'women' }).blocks;
  assert.deepEqual(first, second);
  assert.deepEqual(first.map(({ sectionId, sectionLayout, layoutRowId, spaceGroupId }) => ({ sectionId, sectionLayout, layoutRowId, spaceGroupId })),
    input.map(({ sectionId, sectionLayout, layoutRowId, spaceGroupId }) => ({ sectionId, sectionLayout, layoutRowId, spaceGroupId })));
  assert.equal(assignGenerationExamples(first, { catalog, product, gender: 'women' }).blocks, first);
  const afterDelete = first.slice(1);
  assert.equal(assignGenerationExamples(afterDelete, { catalog, product, gender: 'women' }).blocks, afterDelete);
});

test('same-space assignment requires direction-compatible pose and never falls back to all', () => {
  const catalog = [
    example('front-pose', { direction: 'front', variants: ['all', 'pose'] }),
    example('back-pose', { direction: 'back', variants: ['all', 'pose'], rank: 2 }),
    example('side-all-only', { direction: 'side', variants: ['all'], rank: 3 }),
  ];
  const back = assignGenerationExamples([block('back', { direction: 'back', spaceGroupId: 'space-a' })], { catalog, product, gender: 'women' });
  assert.equal(back.blocks[0].exampleId, 'back-pose');
  assert.equal(back.blocks[0].refScope, 'pose');
  const side = assignGenerationExamples([block('side', { direction: 'side', spaceGroupId: 'space-a' })], { catalog, product, gender: 'women' });
  assert.equal(side.blocks[0].exampleId, undefined);
  assert.deepEqual(side.missingIds, ['side']);
});

test('stored selections ignore shot/direction drift, while cut/product conditions still matter', () => {
  const stored = example('stored');
  assert.equal(storedExampleConditionStatus(stored, { cutType: 'styling', shot: 'medium', direction: 'back', clothingType: 'top', gender: 'women' }), 'valid');
  assert.equal(storedExampleConditionStatus(stored, { cutType: 'horizon', clothingType: 'top', gender: 'women' }), 'changed');
});

test('auto examples are fingerprint-neutral and auto saves are mock-dirty neutral', () => {
  assert.deepEqual(exampleSelectionFingerprintFields({ exampleId: 'auto', exampleSelectionOrigin: 'auto', refScope: 'all' }), { exampleId: null, exampleSelectionOrigin: null, refScope: null });
  assert.deepEqual(exampleSelectionFingerprintFields({ exampleId: 'user', exampleSelectionOrigin: 'user', refScope: 'pose' }), { exampleId: 'user', exampleSelectionOrigin: 'user', refScope: 'pose' });
  assert.deepEqual(exampleSelectionFingerprintFields({ exampleId: 'legacy' }), { exampleId: 'legacy', exampleSelectionOrigin: 'user', refScope: null });
  assert.equal(shouldMarkStoryboardDirty({ autoAssignment: true }), false);
  assert.equal(shouldMarkStoryboardDirty(), true);
});

test('a storyboard containing only auto assignments still matches the untouched default fingerprint', () => {
  const colors = [{ id: 'color-1', isBase: true, images: [] }];
  const automatic = defaultStoryboard(colors, 'basic').map((item) => ({
    ...item, exampleId: `example-`, exampleSelectionOrigin: 'auto',
    refScope: item.spaceGroupId ? 'pose' : 'all',
  }));
  assert.equal(isDefaultStoryboardForMode(automatic, colors, 'basic'), true);
  automatic[0] = { ...automatic[0], exampleSelectionOrigin: 'user' };
  assert.equal(isDefaultStoryboardForMode(automatic, colors, 'basic'), false);
});

test('direction badge labels are front, side and back', () => {
  assert.deepEqual(['front', 'side', 'back'].map(directionBadgeLabel), ['정면', '사이드', '뒷면']);
});

test('storyboard interaction source keeps selections non-empty, user-owned and atomically retryable', () => {
  const shotHandler = storyboardSource.slice(
    storyboardSource.indexOf('const onShotChange ='),
    storyboardSource.indexOf('const commitPendingRecipe ='),
  );
  assert.doesNotMatch(shotHandler, /exampleId:\s*null/);
  assert.match(shotHandler, /exampleSelectionOrigin: current\.exampleId \? 'user' : null/);
  assert.match(storyboardSource, /await onAtomicChange\(changes, \{ pickerOwnsError: true \}\)/);
  assert.match(storyboardSource, /\}, catalogs\), \{ retryAtomic: true \}\)/);
  assert.match(storyboardSource, /latestBlocks\.current !== atomicRetry\.previous/);
  assert.match(storyboardSource, /const copy = \{ \.\.\.withoutLayoutRow\(bs\[i\]\), id: uid\('blk'\) \}/);
  assert.match(storyboardSource, /새 섹션에 맞는 컷 예시를 먼저 골라주세요/);
});

test('storyboard exposes honest retry copy and never labels assignment as an automatic pose', () => {
  assert.match(storyboardSource, /생성예시 카탈로그를 불러오지 못했어요/);
  assert.match(storyboardSource, /저장된 예시를 불러오지 못했어요/);
  assert.match(storyboardSource, /변경 내용을 저장하지 못했어요/);
  assert.match(storyboardSource, /다시 시도/);
  assert.doesNotMatch(storyboardSource, /AI 자동 포즈/);
});
