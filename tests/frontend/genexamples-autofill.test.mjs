import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  assignGenerationExamples, directionBadgeLabel, exampleSelectionFingerprintFields,
  isGenerationCombinationPublic, selectGenerationExamples, shouldMarkStoryboardDirty,
  storedExampleConditionStatus,
} from '../../src/lib/generationExamples.js';
import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import { pickEntrySets } from '../../src/lib/storyboardEntryPlacement.js';
import {
  genderForClothingType,
  normalizeTargetGendersForClothingType,
} from '../../src/lib/productGender.js';
import {
  spaceSetGroupId,
  spaceSetIdFromGroupId,
  storyboardSpaceSetsFor,
} from '../../src/lib/storyboardSpaceSetCatalog.js';

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
const releasedStylingSet = storyboardSpaceSetsFor({
  gender: 'women',
  clothingType: 'top',
}).find((set) => set.setType === 'styling');
const releasedSpaceGroupId = spaceSetGroupId(releasedStylingSet.id, 'autofill-test');
const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
const editorSource = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);
const httpAdapterSource = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
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

test('space-set-only examples stay out of the default selector and autofill pool', () => {
  const catalog = [
    example('generic'),
    example('set-member', { setOnly: true, variants: ['all', 'pose'], spaceSetId: 'released-set' }),
  ];
  const selected = selectGenerationExamples(catalog, {
    cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
  });
  assert.deepEqual(selected.map((item) => item.id), ['generic']);
  const assigned = assignGenerationExamples([block('new')], { catalog, product, gender: 'women' });
  assert.equal(assigned.blocks[0].exampleId, 'generic');
});

test('generic gallery appends all matching set members after up to six ordinary examples', () => {
  const catalog = [
    ...Array.from({ length: 8 }, (_, index) => example(`generic-${index + 1}`, {
      rank: index + 1,
      mood: 'daily',
    })),
    example('set-2', {
      setOnly: true,
      variants: ['all', 'pose'],
      spaceSetId: 'released-set',
      rank: 2,
    }),
    example('set-1', {
      setOnly: true,
      variants: ['all', 'pose'],
      spaceSetId: 'released-set',
      rank: 1,
    }),
  ];
  const selected = selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'top',
    gender: 'women',
    appendSetOnly: true,
  });
  assert.deepEqual(selected.map((item) => item.id), [
    'generic-1', 'generic-2', 'generic-3', 'generic-4', 'generic-5', 'generic-6',
    'set-1', 'set-2',
  ]);
  const assigned = assignGenerationExamples([block('new')], { catalog, product, gender: 'women' });
  assert.equal(assigned.blocks[0].exampleId, 'generic-1');
});

test('space-set-only poses enter only the in-space compatible pose pool', () => {
  const catalog = [
    example('generic-back', { direction: 'back', variants: ['all', 'pose'] }),
    example('set-back', {
      direction: 'back',
      setOnly: true,
      variants: ['all', 'pose'],
      spaceSetId: 'released-set',
      rank: 2,
    }),
    example('set-front', {
      direction: 'front',
      setOnly: true,
      variants: ['all', 'pose'],
      spaceSetId: 'released-set',
      rank: 3,
    }),
  ];
  const generic = selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'top',
    gender: 'women',
  });
  assert.deepEqual(generic.map((item) => item.id), ['generic-back']);
  const inSpace = selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'top',
    gender: 'women',
    spaceGroupId: 'ssg1__set__instance',
    direction: 'back',
    includeSetOnly: true,
  });
  assert.deepEqual(inSpace.map((item) => item.id), ['generic-back', 'set-back']);
});

test('a released set pose is available even when the flat combination is not published', () => {
  const catalog = [example('released-dress-pose', {
    setOnly: true,
    variants: ['all', 'pose'],
    applicableClothingTypes: ['dress'],
    spaceSetId: 'released-dress-set',
  })];
  assert.equal(isGenerationCombinationPublic({
    cutType: 'styling',
    shot: 'full',
    clothingType: 'dress',
    gender: 'women',
  }), false);
  assert.deepEqual(selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'dress',
    gender: 'women',
  }), []);
  assert.deepEqual(selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'dress',
    gender: 'women',
    appendSetOnly: true,
  }).map((item) => item.id), ['released-dress-pose']);
  assert.deepEqual(selectGenerationExamples(catalog, {
    cutType: 'styling',
    shot: 'full',
    clothingType: 'dress',
    gender: 'women',
    spaceGroupId: 'ssg1__released-dress-set__instance',
    direction: 'front',
    includeSetOnly: true,
  }).map((item) => item.id), ['released-dress-pose']);
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
  const back = assignGenerationExamples([block('back', { direction: 'back', spaceGroupId: releasedSpaceGroupId })], { catalog, product, gender: 'women' });
  assert.equal(back.blocks[0].exampleId, 'back-pose');
  assert.equal(back.blocks[0].refScope, 'pose');
  const side = assignGenerationExamples([block('side', { direction: 'side', spaceGroupId: releasedSpaceGroupId })], { catalog, product, gender: 'women' });
  assert.equal(side.blocks[0].exampleId, undefined);
  assert.deepEqual(side.missingIds, ['side']);
});

test('same-space autofill also stays flat-only even when matching released set poses exist', () => {
  const catalog = [
    example('set-back', {
      direction: 'back',
      variants: ['all', 'pose'],
      setOnly: true,
      rank: 1,
    }),
    example('flat-back', {
      direction: 'back',
      variants: ['all', 'pose'],
      rank: 2,
    }),
  ];
  const assigned = assignGenerationExamples([
    block('back', { direction: 'back', spaceGroupId: releasedSpaceGroupId }),
  ], { catalog, product, gender: 'women' });
  assert.equal(assigned.blocks[0].exampleId, 'flat-back');

  const missing = assignGenerationExamples([
    block('back', { direction: 'back', spaceGroupId: releasedSpaceGroupId }),
  ], { catalog: [catalog[0]], product, gender: 'women' });
  assert.equal(missing.blocks[0].exampleId, undefined);
  assert.deepEqual(missing.missingIds, ['back']);
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

test('every supported gender and clothing category seeds styling and horizon sets', () => {
  const colors = [{ id: 'color-1', isBase: true, images: [] }];
  const fourColorsWithDetail = Array.from({ length: 4 }, (_, index) => ({
    id: `color-${index + 1}`,
    isBase: index === 0,
    images: index === 0 ? [{ slot: 'Detail' }] : [],
  }));
  const supported = [
    ['women', 'top'], ['women', 'bottom'], ['women', 'outer'], ['women', 'dress'],
    ['men', 'top'], ['men', 'bottom'], ['men', 'outer'],
  ];

  for (const [gender, clothingType] of supported) {
    const context = { projectId: `test-${gender}-${clothingType}`, clothingType, targetGenders: [gender] };
    const basic = defaultStoryboard(colors, 'basic', context);
    const setMembers = basic.filter((item) => item.spaceGroupId);
    const stylingMembers = setMembers.filter((item) => item.cutType === 'styling');
    const horizonMembers = setMembers.filter((item) => item.cutType === 'horizon');

    // 전의류 선언 전 서버 정합: 회전 세트는 의류 메타(bottom)가 맞을 때만, 그 외엔 낱장 트리오 폴백.
    const rotationApplies = clothingType === 'bottom';
    assert.equal(basic.length, 14, `${gender}/${clothingType} basic`);
    assert.equal(stylingMembers.length, 6, `${gender}/${clothingType} styling members`);
    assert.equal(horizonMembers.length, rotationApplies ? 3 : 0, `${gender}/${clothingType} rotation members`);
    assert.equal(new Set(setMembers.map((item) => item.spaceGroupId)).size, rotationApplies ? 3 : 2);
    assert.ok(spaceSetIdFromGroupId(setMembers[0].spaceGroupId));
    assert.ok(setMembers.every((item) => (
      item.exampleSelectionOrigin === 'auto'
      && item.setSelectionOrigin === 'auto'
      && item.refScope === 'pose'
      && item.exampleId
    )));
    // 확장형 기대 컷수를 실제 추첨 결과에서 유도 — 세트 슬롯 고갈(낱장 2컷 폴백)과
    // 호리존 시퀀스 미커버(트리오 3컷 폴백)를 카테고리별로 그대로 반영한다.
    const picked = pickEntrySets({
      gender, clothingType, projectId: context.projectId, stylingCount: 3,
    });
    const stylingCuts = picked.stylingSets
      .reduce((sum, set) => sum + (set ? set.members.length : 2), 0);
    const horizonCuts = (picked.sequenceSet || picked.rotationSet)?.members.length ?? 3;
    assert.equal(
      defaultStoryboard(fourColorsWithDetail, 'extended', context).length,
      2 + stylingCuts + horizonCuts + 1 + 12 + 4,
      `${gender}/${clothingType} extended`,
    );
  }
});

test('dress is women-only even when stale input still says men', () => {
  assert.equal(genderForClothingType('dress', ['men']), 'women');
  assert.deepEqual(normalizeTargetGendersForClothingType('dress', ['men']), ['women']);
  assert.deepEqual(normalizeTargetGendersForClothingType('dress', []), ['women']);
  assert.equal(genderForClothingType('outer', ['men']), 'men');
  assert.deepEqual(normalizeTargetGendersForClothingType('outer', ['men']), ['men']);
  assert.match(storyboardSource, /if \(clothingType === 'dress'\) return genderForClothingType/);
  assert.match(storyboardSource, /exampleGenderFromAnalysis\(\s*a,\s*hydratedCatalogs,\s*p\.clothingType/);
  assert.match(httpAdapterSource, /savedAnalysis = await http\([^]*method: 'PATCH'/);
  assert.match(httpAdapterSource, /analysisCache = \{ projectId, analysis: savedAnalysis \}/);
  assert.match(httpAdapterSource, /return savedAnalysis/);
});

test('direction badge labels are front, side and back', () => {
  assert.deepEqual(['front', 'side', 'back'].map(directionBadgeLabel), ['정면', '사이드', '뒷면']);
});

test('storyboard interaction source clears an incompatible in-space shot and remains atomically retryable', () => {
  const shotHandler = storyboardSource.slice(
    storyboardSource.indexOf('const onShotChange ='),
    storyboardSource.indexOf('const commitPendingRecipe ='),
  );
  assert.match(shotHandler, /includeSetOnly:\s*true/);
  assert.match(shotHandler, /exampleId:\s*null/);
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

test('storyboard and editor both hydrate released set members into selectable galleries', () => {
  assert.match(storyboardSource, /appendSetOnly:\s*!inSpace && cut !== 'product'/);
  assert.match(storyboardSource, /공간세트에서 사용된 컷/);
  assert.match(storyboardSource, /appendSetOnly:\s*cutType !== 'product'/);
  assert.match(editorSource, /withStoryboardSpaceSetExamples\(c\)/);
  assert.match(editorSource, /setCatalogs\(hydratedCatalogs\)/);
});
