import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import {
  hashSeed,
  normalizePlaceType,
  pickEntrySets,
  seededPick,
} from '../../src/lib/storyboardEntryPlacement.js';
import {
  spaceSetIdFromGroupId,
  storyboardSpaceSetById,
  storyboardSpaceSetsFor,
} from '../../src/lib/storyboardSpaceSetCatalog.js';

const baseColors = [{ id: 'base', isBase: true, images: [] }];
const context = (projectId, clothingType = 'top', gender = 'women') => ({
  projectId,
  clothingType,
  targetGenders: gender ? [gender] : [],
});

function storyboardFingerprint(blocks) {
  const groupOrdinals = new Map();
  const groupOrdinal = (groupId) => {
    if (!groupId) return null;
    if (!groupOrdinals.has(groupId)) groupOrdinals.set(groupId, groupOrdinals.size + 1);
    return groupOrdinals.get(groupId);
  };
  return JSON.stringify(blocks.map((block) => ({
    taxonomyVersion: block.taxonomyVersion,
    sectionRole: block.sectionRole,
    contentRole: block.contentRole,
    source: block.source,
    cutType: block.cutType,
    direction: block.direction,
    shot: block.shot,
    colorId: block.colorId,
    faceExposure: block.faceExposure,
    exampleId: block.exampleId || null,
    spaceSetId: spaceSetIdFromGroupId(block.spaceGroupId),
    spaceGroup: groupOrdinal(block.spaceGroupId),
    spaceVariation: block.spaceVariation || null,
    spaceSetMemberOrder: block.spaceSetMemberOrder || null,
  })));
}

function chosenSetIds(blocks) {
  return [...new Set(blocks.map((block) => spaceSetIdFromGroupId(block.spaceGroupId)).filter(Boolean))];
}

test('hash selection is stable, catalog-order independent, and empty-safe', () => {
  assert.equal(hashSeed('wearless'), hashSeed('wearless'));
  const forward = [{ id: 'c' }, { id: 'a' }, { id: 'b' }];
  assert.equal(seededPick(forward, 'project:entry').id, seededPick([...forward].reverse(), 'project:entry').id);
  assert.equal(seededPick([], 'project:entry'), null);
});

test('entry placement is deterministic per project and distributes different projects', () => {
  const first = defaultStoryboard(baseColors, 'basic', context('p0'));
  const again = defaultStoryboard(baseColors, 'basic', context('p0'));
  const another = defaultStoryboard(baseColors, 'basic', context('p1'));

  assert.equal(storyboardFingerprint(first), storyboardFingerprint(again));
  assert.notDeepEqual(chosenSetIds(first), chosenSetIds(another));
});

test('seeded boards round-trip as defaults and mode changes re-seed only the matching template', () => {
  const seedContext = context('round-trip', 'outer', 'women');
  const basic = defaultStoryboard(baseColors, 'basic', seedContext);
  const extended = defaultStoryboard(baseColors, 'extended', seedContext);

  assert.equal(isDefaultStoryboardForMode(basic, baseColors, 'basic', seedContext), true);
  assert.equal(isDefaultStoryboardForMode(extended, baseColors, 'extended', seedContext), true);
  assert.equal(isDefaultStoryboardForMode(basic, baseColors, 'extended', seedContext), false);
  const relabeled = basic.map((block) => block.spaceGroupId
    ? { ...block, setSelectionOrigin: 'user' }
    : block);
  assert.equal(isDefaultStoryboardForMode(relabeled, baseColors, 'basic', seedContext), true);
});

test('styling sets use distinct normalized place types in basic and extended modes', () => {
  const basic = pickEntrySets({
    gender: 'women', clothingType: 'top', projectId: 'places-basic', stylingCount: 2,
  }).stylingSets;
  const extended = pickEntrySets({
    gender: 'women', clothingType: 'bottom', projectId: 'project-a', stylingCount: 3,
  }).stylingSets;
  const placeKeys = (sets) => sets.map((set) => normalizePlaceType(set.placeType, set.setType));

  assert.equal(basic.filter(Boolean).length, 2);
  assert.equal(new Set(placeKeys(basic)).size, 2);
  assert.equal(extended.filter(Boolean).length, 3);
  assert.equal(new Set(placeKeys(extended)).size, 3);
  assert.equal(normalizePlaceType('mixed-cafe', 'styling'), 'cafe');
  assert.equal(normalizePlaceType('anything', 'horizon-rotation'), 'studio');
  assert.equal(normalizePlaceType('future-place', 'styling'), 'future-place');
});

test('category filters stay server-consistent for styling and horizon pools', () => {
  const supported = [
    ['women', 'top'], ['women', 'bottom'], ['women', 'outer'], ['women', 'dress'],
    ['men', 'top'], ['men', 'bottom'], ['men', 'outer'],
  ];
  for (const [gender, clothingType] of supported) {
    const picked = pickEntrySets({ gender, clothingType, projectId: `${gender}-${clothingType}`, stylingCount: 2 });
    assert.ok(picked.stylingSets.every((set) => (
      !set || (
        set.gender === gender
        && set.setApplicableClothingTypes.includes(clothingType)
      )
    )));
    // 세트 배치 범위는 서버 저장 검증(space_set_not_applicable)과 동일하다.
    for (const set of [picked.rotationSet, picked.sequenceSet].filter(Boolean)) {
      assert.equal(set.gender, gender);
      assert.ok(set.setApplicableClothingTypes.includes(clothingType));
    }
  }
  assert.equal(pickEntrySets({
    gender: 'women', clothingType: 'bottom', projectId: 'rot-bottom', stylingCount: 2,
  }).rotationSet?.setType, 'horizon-rotation');
  assert.equal(pickEntrySets({
    gender: 'women', clothingType: 'top', projectId: 'rot-top', stylingCount: 2,
  }).rotationSet?.setType, 'horizon-rotation');
  assert.equal(pickEntrySets({
    gender: 'women', clothingType: 'dress', projectId: 'rot-dress', stylingCount: 2,
  }).rotationSet?.setType, 'horizon-rotation');
  assert.equal(pickEntrySets({
    gender: 'men', clothingType: 'outer', projectId: 'rot-outer', stylingCount: 2,
  }).rotationSet?.setType, 'horizon-rotation');
  assert.ok(pickEntrySets({
    gender: 'women', clothingType: 'dress', projectId: 'dress-women', stylingCount: 2,
  }).stylingSets.every(Boolean));
  assert.ok(pickEntrySets({
    gender: 'men', clothingType: 'dress', projectId: 'dress-men', stylingCount: 2,
  }).stylingSets.every((set) => set === null));
});

test('women receive a mirror, men receive the styling fallback, unknown defaults to women like the server', () => {
  const women = defaultStoryboard(baseColors, 'basic', context('women', 'bottom', 'women'));
  const men = defaultStoryboard(baseColors, 'basic', context('men', 'bottom', 'men'));
  const unknown = defaultStoryboard(baseColors, 'basic', context('unknown', 'bottom', null));
  const standaloneFitStyling = (blocks) => blocks.filter((block) => (
    block.sectionRole === 'fit'
    && block.cutType === 'styling'
    && block.shot === 'full'
    && !block.spaceGroupId
  ));

  assert.equal(women.filter((block) => block.cutType === 'mirror').length, 1);
  assert.equal(women.find((block) => block.cutType === 'mirror').faceExposure, 'hide');
  assert.equal(men.some((block) => block.cutType === 'mirror'), false);
  assert.equal(standaloneFitStyling(men).at(-1).direction, 'back');
  // 성별 미상은 서버(select_base_gender)와 동일하게 women 기본 — 세트·거울 모두 정상 배치.
  assert.equal(unknown.filter((block) => block.cutType === 'mirror').length, 1);
  assert.equal(unknown.some((block) => block.spaceGroupId), true);
});

test('multi-color basic and extended seeds follow product and studio repetition rules', () => {
  const colors = [
    { id: 'base', isBase: true, images: [] },
    { id: 'blue', images: [] },
    { id: 'red', images: [] },
  ];
  const seedContext = context('multi-color', 'top', 'women');
  const basic = defaultStoryboard(colors, 'basic', seedContext);
  const extended = defaultStoryboard(colors, 'extended', seedContext);

  const basicFrontProducts = basic.filter((block) => (
    block.cutType === 'product' && block.shot === 'ghost' && block.direction === 'front'
  ));
  assert.deepEqual(basicFrontProducts.map((block) => block.colorId), ['base', 'blue', 'red']);

  for (const colorId of ['blue', 'red']) {
    const horizon = extended.filter((block) => block.cutType === 'horizon' && block.colorId === colorId);
    assert.deepEqual(horizon.map((block) => [block.direction, block.shot]), [
      ['front', 'medium'], ['front', 'full'], ['back', 'full'],
    ]);
    assert.equal(extended.filter((block) => (
      block.cutType === 'product'
      && block.direction === 'front'
      && block.shot === 'ghost'
      && block.colorId === colorId
    )).length, 1);
  }
  assert.ok(extended.filter((block) => block.spaceGroupId && block.cutType === 'styling')
    .every((block) => block.colorId === 'base'));
});

test('cut counts include normal ranges and a forced one-slot styling fallback', () => {
  const basic = defaultStoryboard(baseColors, 'basic', context('counts', 'top', 'women'));
  assert.equal(basic.length, 14);
  // 확장형: 하의는 카테고리별 시퀀스, 상의는 전 의류 회전 세트를 사용한다.
  assert.equal(defaultStoryboard(baseColors, 'extended', context('counts-a', 'bottom', 'women')).length, 20);
  assert.equal(defaultStoryboard(baseColors, 'extended', context('counts-b', 'bottom', 'men')).length, 21);
  assert.equal(defaultStoryboard(baseColors, 'extended', context('counts-c', 'top', 'women')).length, 19);

  const oneStylingSet = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })[0];
  const originalIncludes = Array.prototype.includes;
  Array.prototype.includes = function mockedIncludes(value, ...rest) {
    if (value === 'forced-single') return this === oneStylingSet.setApplicableClothingTypes;
    return originalIncludes.call(this, value, ...rest);
  };
  try {
    const fallback = defaultStoryboard(baseColors, 'basic', context('fallback', 'forced-single', 'women'));
    assert.equal(fallback.length, 13);
    // 스타일링 세트 1개만 — 호리존은 의류 메타 불일치로 낱장 트리오 폴백(그룹 없음).
    assert.equal(new Set(fallback.filter((block) => block.spaceGroupId)
      .map((block) => block.spaceGroupId)).size, 1);
  } finally {
    Array.prototype.includes = originalIncludes;
  }
});

test('every seeded space group keeps the complete catalog member run', () => {
  const seeded = defaultStoryboard(baseColors, 'extended', context('atomic', 'outer', 'women'));
  const groups = new Map();
  seeded.forEach((block, index) => {
    if (!block.spaceGroupId) return;
    if (!groups.has(block.spaceGroupId)) groups.set(block.spaceGroupId, []);
    groups.get(block.spaceGroupId).push({ block, index });
  });

  for (const [groupId, members] of groups) {
    const set = storyboardSpaceSetById(spaceSetIdFromGroupId(groupId));
    assert.equal(members.length, set.members.length);
    assert.deepEqual(members.map(({ block }) => block.spaceSetMemberOrder), set.members.map((member) => member.order));
    assert.equal(members.at(-1).index - members[0].index + 1, members.length);
    assert.ok(members.every(({ block }) => (
      block.exampleSelectionOrigin === 'auto'
      && block.setSelectionOrigin === 'auto'
      && block.refScope === 'pose'
    )));
  }
});

test('HTTP and mock entry paths pass project ids and share the default builder', () => {
  const httpSource = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const mockApiSource = readFileSync(new URL('../../src/mock/api.js', import.meta.url), 'utf8');
  const mockDbSource = readFileSync(new URL('../../src/mock/db.js', import.meta.url), 'utf8');
  assert.match(httpSource, /const storyboardContext = \{\s*projectId,/);
  assert.match(mockApiSource, /projectId: DB\.project\.id/);
  assert.match(mockDbSource, /projectId: project\.id/);
  assert.match(mockDbSource, /return defaultStoryboard\(colors, mode, context\)/);
});
