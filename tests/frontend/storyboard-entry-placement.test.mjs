import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import {
  applyOpeningRow,
  entryStylingMembers,
  hashSeed,
  hasFullAndMediumMembers,
  hasOpeningRow,
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

test('entry placement combines the untouched opening seed into one medium two-column row', () => {
  const seeded = defaultStoryboard(baseColors, 'basic', context('opening-row'));
  assert.deepEqual(seeded.slice(0, 2).map((block) => block.shot), ['full', 'medium']);

  const placed = applyOpeningRow(seeded);
  const [hero, benefit] = placed;
  assert.equal(hasOpeningRow(placed), true);
  assert.deepEqual([hero.shot, benefit.shot], ['medium', 'medium']);
  assert.equal(hero.sectionId, benefit.sectionId);
  assert.equal(hero.sectionLayout, 'twoColumn');
  assert.equal(benefit.sectionLayout, 'twoColumn');
  assert.equal(hero.layoutRowId, benefit.layoutRowId);
  assert.equal(hero.layoutRowVersion, 1);
  assert.equal(benefit.layoutRowVersion, 1);
  assert.equal(isDefaultStoryboardForMode(placed, baseColors, 'basic', context('opening-row')), true);
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

test('entry styling sets seed exactly one full and one medium member in catalog order', () => {
  for (const [mode, seedContext, expectedSetCount] of [
    ['basic', context('two-cut-basic', 'top', 'women'), 2],
    ['extended', context('two-cut-extended', 'bottom', 'women'), 3],
  ]) {
    const seeded = defaultStoryboard(baseColors, mode, seedContext);
    const groups = new Map();
    for (const block of seeded.filter((item) => item.spaceGroupId && item.cutType === 'styling')) {
      if (!groups.has(block.spaceGroupId)) groups.set(block.spaceGroupId, []);
      groups.get(block.spaceGroupId).push(block);
    }
    assert.equal(groups.size, expectedSetCount);
    for (const members of groups.values()) {
      assert.equal(members.length, 2);
      assert.deepEqual(new Set(members.map((member) => member.shot)), new Set(['full', 'medium']));
      assert.ok(members[0].spaceSetMemberOrder < members[1].spaceSetMemberOrder);
    }
  }
});

test('all-full styling sets still seed two members and prefer different directions', () => {
  const allFullSet = {
    members: [
      { order: 1, shot: 'full', direction: 'front', exampleId: 'front-a' },
      { order: 2, shot: 'full', direction: 'front', exampleId: 'front-b' },
      { order: 3, shot: 'full', direction: 'back', exampleId: 'back' },
    ],
  };
  assert.deepEqual(
    entryStylingMembers(allFullSet).map((member) => member.exampleId),
    ['front-a', 'back'],
  );
  assert.deepEqual(
    entryStylingMembers({ members: allFullSet.members.slice(0, 2) }).map((member) => member.exampleId),
    ['front-a', 'front-b'],
  );
});

test('women bottom soft preference falls back to all-full sets to preserve a fourth place', () => {
  const picked = pickEntrySets({
    gender: 'women', clothingType: 'bottom', projectId: 'soft-place-fallback', stylingCount: 4,
  }).stylingSets;
  const places = picked.map((set) => normalizePlaceType(set.placeType, set.setType));
  assert.equal(picked.filter(Boolean).length, 4);
  assert.equal(new Set(places).size, 4);
  assert.equal(picked.filter(hasFullAndMediumMembers).length, 3);
  assert.equal(picked.some((set) => !hasFullAndMediumMembers(set)), true);
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
  const standaloneStyling = (blocks) => blocks.filter((block) => (
    block.sectionRole === 'styling'
    && block.cutType === 'styling'
    && block.shot === 'full'
    && !block.spaceGroupId
  ));

  assert.equal(women.filter((block) => block.cutType === 'mirror').length, 1);
  assert.equal(women.find((block) => block.cutType === 'mirror').faceExposure, 'hide');
  assert.equal(men.some((block) => block.cutType === 'mirror'), false);
  assert.equal(standaloneStyling(men).at(-1).direction, 'back');
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
  assert.equal(basic.length, 12);
  // 확장형 기대치는 실제 추첨(pickEntrySets)에서 유도 — 카탈로그가 자라도 테스트가 낡지 않게.
  for (const [pid, clothing, gender] of [
    ['counts-a', 'bottom', 'women'], ['counts-b', 'bottom', 'men'], ['counts-c', 'top', 'women'],
  ]) {
    const picked = pickEntrySets({ gender, clothingType: clothing, projectId: pid, stylingCount: 3 });
    const stylingCuts = picked.stylingSets.reduce((s, set) => s + (set ? entryStylingMembers(set).length : 2), 0);
    const horizonCuts = (picked.sequenceSet || picked.rotationSet)?.members.length ?? 3;
    assert.equal(
      defaultStoryboard(baseColors, 'extended', context(pid, clothing, gender)).length,
      2 + stylingCuts + horizonCuts + 1 + 4,
      `${gender}/${clothing} extended`,
    );
  }

  // 미지의 의류(후보 0)에서도 시드는 추첨 결과와 정확히 정합하며 낱장 폴백으로 채운다(fail-closed).
  const forced = context('fallback', 'forced-single', 'women');
  const fPicked = pickEntrySets({
    gender: 'women', clothingType: 'forced-single', projectId: 'fallback', stylingCount: 2,
  });
  const fallback = defaultStoryboard(baseColors, 'basic', forced);
  const fStyling = fPicked.stylingSets.reduce((s, set) => s + (set ? entryStylingMembers(set).length : 2), 0);
  const fHorizon = fPicked.rotationSet?.members.length ?? 3;
  assert.equal(fallback.length, 2 + fStyling + fHorizon + 1 + 2);
  assert.equal(
    new Set(fallback.filter((block) => block.spaceGroupId).map((block) => block.spaceGroupId)).size,
    fPicked.stylingSets.filter(Boolean).length + (fPicked.rotationSet ? 1 : 0),
  );
});

test('seeded styling groups keep two entry members while horizon groups stay complete', () => {
  const seeded = defaultStoryboard(baseColors, 'extended', context('atomic', 'outer', 'women'));
  const groups = new Map();
  seeded.forEach((block, index) => {
    if (!block.spaceGroupId) return;
    if (!groups.has(block.spaceGroupId)) groups.set(block.spaceGroupId, []);
    groups.get(block.spaceGroupId).push({ block, index });
  });

  for (const [groupId, members] of groups) {
    const set = storyboardSpaceSetById(spaceSetIdFromGroupId(groupId));
    const expectedMembers = set.setType === 'styling' ? entryStylingMembers(set) : set.members;
    assert.equal(members.length, expectedMembers.length);
    assert.deepEqual(members.map(({ block }) => block.spaceSetMemberOrder), expectedMembers.map((member) => member.order));
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

// ---------- 2026-08-07 슬롯 개편: 디테일 컷 상시 제공 ----------

test('기본 콘티는 디테일 사진이 없어도 디테일 컷을 포함한다', () => {
  const colors = [{ id: 'col1', isBase: true, images: [
    { slot: 'Front', id: 'f1' }, { slot: 'Back', id: 'b1' },
  ] }];
  const basic = defaultStoryboard(colors, 'basic', { clothingType: 'top', projectId: 'p-detail' });
  assert.ok(basic.some((b) => b.cutType === 'product' && b.shot === 'detail'));
  const extended = defaultStoryboard(colors, 'extended', { clothingType: 'top', projectId: 'p-detail' });
  assert.ok(extended.some((b) => b.cutType === 'product' && b.shot === 'detail'));
});

test('디테일 블록의 색상은 앞면 디테일 보유 색을 우선한다', () => {
  const colors = [
    { id: 'col1', isBase: true, images: [{ slot: 'Front', id: 'f1' }, { slot: 'Back', id: 'b1' }] },
    { id: 'col2', images: [{ slot: 'Detail', id: 'd2' }] },
  ];
  const blocks = defaultStoryboard(colors, 'basic', { clothingType: 'top', projectId: 'p-detail2' });
  const detail = blocks.find((b) => b.cutType === 'product' && b.shot === 'detail');
  assert.equal(detail.colorId, 'col2');
});

test('개편 전(디테일 없음→ghost 대체) 기본 시드도 무수정 기본값으로 인정한다', () => {
  const colors = [{ id: 'col1', isBase: true, images: [
    { slot: 'Front', id: 'f1' }, { slot: 'Back', id: 'b1' },
  ] }];
  const ctx = { clothingType: 'top', projectId: 'p-legacy' };
  for (const mode of ['basic', 'extended']) {
    const legacySeed = defaultStoryboard(colors, mode, ctx, { legacyDetailGhostFallback: true });
    assert.ok(!legacySeed.some((b) => b.shot === 'detail'), '레거시 시드엔 디테일 블록이 없어야 전제 성립');
    assert.equal(isDefaultStoryboardForMode(legacySeed, colors, mode, ctx), true,
      `${mode}: 레거시 기본 시드가 사용자 편집본으로 오인되면 사진 양 변경이 무시된다`);
  }
});
