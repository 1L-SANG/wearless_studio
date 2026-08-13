import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createSpaceSetMembers,
  detachSpaceMembership,
  dissolveSpaceSet,
  groupConsecutiveSpaceRuns,
  moveBlockWithSpaceMembership,
  nextSpaceSetMemberReservation,
  rekeySeparatedSpaceRuns,
  replaceSpaceSetRun,
} from '../../src/lib/storyboardSpaceSets.js';
import {
  STORYBOARD_SPACE_SET_EXAMPLES,
  distinctPlaceStylingSetsFor,
  inferStoryboardSpaceSet,
  isStoryboardSpaceSetEligible,
  normalizeStoryboardSpaceSetRelease,
  spaceSetGroupId,
  spaceSetIdFromGroupId,
  STORYBOARD_SPACE_SETS,
  STORYBOARD_SPACE_PLACE_TYPES,
  storyboardSpaceSetsFor,
} from '../../src/lib/storyboardSpaceSetCatalog.js';

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
  ...extra,
});

const releasedStylingSet = storyboardSpaceSetsFor({
  gender: 'women',
  clothingType: 'top',
}).find((set) => set.setType === 'styling');
const groupA = spaceSetGroupId(releasedStylingSet.id, 'instance-a');
const groupB = spaceSetGroupId(releasedStylingSet.id, 'instance-b');
const rawSpaceSetRelease = JSON.parse(readFileSync(
  new URL('../../src/data/storyboardSpaceSets.json', import.meta.url),
  'utf8',
));

test('consecutive spaceGroupId runs become bands without joining separated runs', () => {
  const groups = groupConsecutiveSpaceRuns([
    block('a'),
    block('b', { spaceGroupId: groupA }),
    block('c', { spaceGroupId: groupA }),
    block('d'),
    block('e', { spaceGroupId: groupA }),
  ]);
  assert.deepEqual(groups.map((group) => [group.kind, group.items.map((item) => item.id)]), [
    ['block', ['a']],
    ['space', ['b', 'c']],
    ['block', ['d']],
    ['space', ['e']],
  ]);
});

test('[A, A, mine, A] detaches the middle cut and rekeys the separated run before save', () => {
  const detached = [
    block('a', { spaceGroupId: groupA, spaceVariation: 'subtle', spaceSetMemberOrder: 1, refScope: 'pose' }),
    block('b', { spaceGroupId: groupA, spaceVariation: 'subtle', spaceSetMemberOrder: 2, refScope: 'pose' }),
    detachSpaceMembership(block('mine', {
      source: 'mine',
      spaceGroupId: groupA,
      spaceVariation: 'subtle',
      spaceSetMemberOrder: 3,
      refScope: 'pose',
    })),
    block('c', { spaceGroupId: groupA, spaceVariation: 'subtle', spaceSetMemberOrder: 4, refScope: 'pose' }),
  ];
  const rekeyed = rekeySeparatedSpaceRuns(detached, (setId) => spaceSetGroupId(setId, 'split-c'));

  assert.deepEqual(rekeyed.map((item) => item.spaceGroupId), [groupA, groupA, undefined, spaceSetGroupId(releasedStylingSet.id, 'split-c')]);
  assert.equal(rekeyed[2].spaceVariation, undefined);
  assert.equal(rekeyed[2].spaceSetMemberOrder, undefined);
  assert.equal(rekeyed[2].refScope, 'all');
  assert.equal(spaceSetIdFromGroupId(rekeyed[3].spaceGroupId), releasedStylingSet.id);

  const indexesByGroup = new Map();
  rekeyed.forEach((item, index) => {
    if (!item.spaceGroupId) return;
    if (!indexesByGroup.has(item.spaceGroupId)) indexesByGroup.set(item.spaceGroupId, []);
    indexesByGroup.get(item.spaceGroupId).push(index);
  });
  for (const indexes of indexesByGroup.values()) {
    assert.equal(indexes.at(-1) - indexes[0] + 1, indexes.length);
  }
});

test('an existing general cut dropped inside a set stays detached and rekeys the rear run', () => {
  const source = [
    block('outside', { exampleId: 'all-only', refScope: 'all', baseThumb: 'base.png', thumb: 'example.png' }),
    block('inside-a', { spaceGroupId: groupA, refScope: 'pose' }),
    block('inside-b', { spaceGroupId: groupA, refScope: 'pose' }),
  ];
  const entered = moveBlockWithSpaceMembership(source, 'outside', 2, {
    targetSpaceGroupId: groupA,
    nextGroupId: (setId) => spaceSetGroupId(setId, 'drop-split'),
  });
  assert.deepEqual(entered.map((item) => item.id), ['inside-a', 'outside', 'inside-b']);
  const dropped = entered.find((item) => item.id === 'outside');
  assert.equal(dropped.spaceGroupId, undefined);
  assert.equal(dropped.refScope, 'all');
  assert.equal(dropped.exampleId, 'all-only');
  assert.equal(entered[0].spaceGroupId, groupA);
  assert.equal(entered[2].spaceGroupId, spaceSetGroupId(releasedStylingSet.id, 'drop-split'));

  const left = moveBlockWithSpaceMembership(entered, 'outside', 0);
  assert.equal(left.find((item) => item.id === 'outside').spaceGroupId, undefined);
  assert.equal(left.find((item) => item.id === 'inside-a').spaceGroupId, groupA);
  assert.equal(left.find((item) => item.id === 'inside-b').spaceGroupId, spaceSetGroupId(releasedStylingSet.id, 'drop-split'));
});

test('space set replacement swaps the whole composition in one immutable board result', () => {
  const source = [
    block('before'),
    block('old-a', { spaceGroupId: groupA, exampleId: 'pose-a', refScope: 'pose' }),
    block('old-b', { spaceGroupId: groupA, exampleId: 'pose-b', refScope: 'pose' }),
    block('after'),
  ];
  const studioSet = {
    id: 'released-studio',
    spaceVariation: 'fixed',
    members: [
      { exampleId: 'front', order: 1, cutType: 'horizon', direction: 'front', shot: 'full', thumb: 'front.webp' },
      { exampleId: 'side', order: 2, cutType: 'horizon', direction: 'side', shot: 'full', thumb: 'side.webp' },
      { exampleId: 'back', order: 3, cutType: 'horizon', direction: 'back', shot: 'full', thumb: 'back.webp' },
    ],
  };
  const next = replaceSpaceSetRun(source, groupA, studioSet, {
    spaceGroupId: groupB,
    makeId: (_member, index) => `new-${index}`,
  });
  assert.notEqual(next, source);
  assert.deepEqual(source.map((item) => item.id), ['before', 'old-a', 'old-b', 'after']);
  assert.deepEqual(next.slice(1, 4).map((item) => [
    item.spaceGroupId, item.cutType, item.direction, item.shot, item.refScope,
    item.exampleId, item.exampleSelectionOrigin, item.setSelectionOrigin, item.thumb,
  ]), [
    [groupB, 'horizon', 'front', 'full', 'pose', 'front', 'user', 'user', 'front.webp'],
    [groupB, 'horizon', 'side', 'full', 'pose', 'side', 'user', 'user', 'side.webp'],
    [groupB, 'horizon', 'back', 'full', 'pose', 'back', 'user', 'user', 'back.webp'],
  ]);
  assert.deepEqual(next.map((item) => item.id), ['before', 'old-a', 'old-b', 'new-2', 'after']);
});

test('release normalization keeps canonical applicability and thumbnails', () => {
  const [set] = normalizeStoryboardSpaceSetRelease({
    schemaVersion: 1,
    releaseId: 'test-release-1',
    sets: [{
      setId: 'set-women-top',
      name: '밝은 실내',
      setType: 'styling',
      gender: 'women',
      applicableClothingTypes: ['top'],
      placeType: 'building-interior',
      tone: 'daily-snapshot',
      compositionLabel: '풀 1 + 미디움 1',
      spaceVariation: 'subtle',
      platePolicy: 'required',
      representativePlate: { url: 'plate.png' },
      members: [
        {
          exampleId: 'ss_full',
          order: 1,
          cutType: 'styling',
          shot: 'full',
          direction: 'front',
          allUrl: 'full.png',
          thumbUrl: 'full.webp',
        },
        {
          exampleId: 'ss_medium',
          order: 2,
          cutType: 'styling',
          shot: 'medium',
          direction: 'side',
          allUrl: 'medium.png',
          thumbUrl: 'medium.webp',
        },
      ],
    }],
  });
  assert.equal(set.id, 'set-women-top');
  assert.deepEqual(set.applicableClothingTypes, ['top']);
  assert.deepEqual(set.setApplicableClothingTypes, ['top']);
  assert.deepEqual(set.members.map((member) => [member.exampleId, member.thumb]), [
    ['ss_full', 'full.webp'],
    ['ss_medium', 'medium.webp'],
  ]);
});

test('release normalization fails closed instead of widening malformed sets', () => {
  const base = {
    setId: 'set-women-top',
    name: '밝은 실내',
    setType: 'styling',
    gender: 'women',
    applicableClothingTypes: ['top'],
    placeType: 'building-interior',
    tone: 'daily-snapshot',
    compositionLabel: '풀 2',
    spaceVariation: 'subtle',
    platePolicy: 'required',
    representativePlate: { url: 'plate.png' },
    members: [
      {
        exampleId: 'ss_first',
        order: 1,
        cutType: 'styling',
        shot: 'full',
        direction: 'front',
        allUrl: 'first.png',
        thumbUrl: 'first.webp',
      },
      {
        exampleId: 'ss_second',
        order: 2,
        cutType: 'styling',
        shot: 'full',
        direction: 'side',
        allUrl: 'second.png',
        thumbUrl: 'second.webp',
      },
    ],
  };
  const normalize = (set) => normalizeStoryboardSpaceSetRelease({
    schemaVersion: 1,
    releaseId: 'test-release-1',
    sets: [set],
  });
  assert.equal(normalize({ ...base, gender: 'other' }).length, 0);
  assert.equal(normalize({ ...base, applicableClothingTypes: [] }).length, 0);
  assert.equal(normalize({
    ...base,
    gender: 'men',
    applicableClothingTypes: ['dress'],
  }).length, 0);
  assert.equal(normalize({
    ...base,
    setApplicableClothingTypes: ['top', 'bottom', 'outer', 'dress'],
  }).length, 0);
  assert.equal(normalize({
    ...base,
    members: base.members.map((member) => ({ ...member, direction: null })),
  }).length, 0);
  assert.equal(normalize({ ...base, setId: 'bad__id' }).length, 0);
  assert.equal(normalize({ ...base, placeType: 'indoor' }).length, 0);
  assert.equal(normalize({ ...base, placeType: '05. 작은 해변·항구' }).length, 0);
  assert.equal(normalize({ ...base, placeType: ' building-interior ' }).length, 0);
  assert.equal(normalize({ ...base, place: 'waterfront' }).length, 0);
  assert.equal(normalize({
    ...base,
    setType: 'horizon-rotation',
    platePolicy: 'required',
    members: base.members.map((member) => ({
      ...member,
      cutType: 'horizon',
      direction: 'front',
    })),
  }).length, 0);
});

test('published sets use only the shared place vocabulary and matching catalog fields', () => {
  const allowed = new Set(STORYBOARD_SPACE_PLACE_TYPES.map((item) => item.value));
  const released = STORYBOARD_SPACE_SETS;
  assert.ok(released.length > 0);
  assert.equal(released.length, rawSpaceSetRelease.sets.length);
  assert.ok(released.every((set) => allowed.has(set.placeType)));
  assert.ok(released.every((set) => set.place === set.placeType));
});

test('styling-set rotation keeps one candidate per place type and excludes horizon sets', () => {
  const sets = distinctPlaceStylingSetsFor({
    gender: 'women',
    clothingType: 'top',
  });
  assert.ok(sets.length > 1);
  assert.ok(sets.every((set) => set.setType === 'styling'));
  assert.equal(new Set(sets.map((set) => set.placeType)).size, sets.length);
  assert.ok(sets.every((set) => set.placeType !== 'horizon-studio'));
});

test('empty release stays empty and production group ids use the versioned namespace', () => {
  assert.deepEqual(normalizeStoryboardSpaceSetRelease({
    schemaVersion: 1,
    releaseId: null,
    sets: [],
  }), []);
  const unknownGroupId = spaceSetGroupId('released-set', 'sg_123');
  assert.equal(unknownGroupId, 'ssg1__released-set__sg_123');
  assert.equal(spaceSetIdFromGroupId(unknownGroupId), null);
  assert.equal(spaceSetIdFromGroupId('sgset__released-set__sg_123'), null);

  const released = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })[0];
  assert.ok(released);
  const releasedGroupId = spaceSetGroupId(released.id, 'sg_123');
  assert.equal(spaceSetIdFromGroupId(releasedGroupId), released.id);
});

test('production set eligibility filters by gender and clothing type', () => {
  const released = {
    gender: 'women',
    applicableClothingTypes: ['bottom'],
    setApplicableClothingTypes: ['top', 'bottom', 'outer', 'dress'],
  };
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'women', clothingType: 'top' }), true);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: null, clothingType: 'top' }), false);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'men', clothingType: 'top' }), false);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'women', clothingType: 'bottom' }), true);
  for (const set of storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })) {
    assert.ok(set.gender == null || set.gender === 'women');
    assert.ok(set.setApplicableClothingTypes.includes('top'));
  }
});

test('rotation set members and grouped set both cover every supported clothing type', () => {
  const rotation = storyboardSpaceSetsFor({
    gender: 'women',
    clothingType: 'dress',
  }).find((set) => set.setType === 'horizon-rotation');
  assert.ok(rotation);
  assert.deepEqual(
    rotation.applicableClothingTypes,
    ['top', 'bottom', 'outer', 'dress'],
  );
  assert.deepEqual(
    rotation.setApplicableClothingTypes,
    ['top', 'bottom', 'outer', 'dress'],
  );
  const standaloneMembers = STORYBOARD_SPACE_SET_EXAMPLES.filter(
    (example) => example.spaceSetId === rotation.id,
  );
  assert.ok(standaloneMembers.length > 0);
  assert.ok(standaloneMembers.every(
    (example) => (
      example.shot === 'full'
      && example.applicableClothingTypes.includes('dress')
    ),
  ));
});

test('creating released members preserves exact ordered example choices as user selections', () => {
  const members = createSpaceSetMembers({
    id: 'released',
    spaceVariation: 'subtle',
    members: [
      { exampleId: 'exact-full', order: 1, cutType: 'styling', shot: 'full', direction: 'front', thumb: 'full.webp' },
      { exampleId: 'exact-medium', order: 2, cutType: 'styling', shot: 'medium', direction: 'side', thumb: 'medium.webp' },
    ],
  }, block('template'), { spaceGroupId: 'ssg1__released__1' });
  assert.deepEqual(members.map((member) => ({
    exampleId: member.exampleId,
    origin: member.exampleSelectionOrigin,
    setOrigin: member.setSelectionOrigin,
    thumb: member.thumb,
    order: member.spaceSetMemberOrder,
    variation: member.spaceVariation,
  })), [
    { exampleId: 'exact-full', origin: 'user', setOrigin: 'user', thumb: 'full.webp', order: 1, variation: 'subtle' },
    { exampleId: 'exact-medium', origin: 'user', setOrigin: 'user', thumb: 'medium.webp', order: 2, variation: 'subtle' },
  ]);
});

test('creating or replacing a space set never inherits block-local mood photos', () => {
  const set = {
    id: 'released',
    spaceVariation: 'subtle',
    members: [
      { exampleId: 'exact-full', order: 1, cutType: 'styling', shot: 'full', direction: 'front' },
      { exampleId: 'exact-medium', order: 2, cutType: 'styling', shot: 'medium', direction: 'side' },
    ],
  };
  const template = block('template', {
    refImages: [{ url: 'template-mood.webp', assetId: 'template-mood' }],
    refAssetIds: ['template-mood'],
  });
  const previousMembers = [
    block('old-1', { refImages: ['old-1.webp'], refAssetIds: ['old-1'] }),
    block('old-2', { refImages: ['old-2.webp'], refAssetIds: ['old-2'] }),
  ];

  const inserted = createSpaceSetMembers(set, template, {
    spaceGroupId: 'ssg1__released__1',
  });
  const replaced = createSpaceSetMembers(set, template, {
    spaceGroupId: 'ssg1__released__2',
    previousMembers,
  });

  for (const member of [...inserted, ...replaced]) {
    assert.deepEqual(member.refImages, []);
    assert.deepEqual(member.refAssetIds, []);
  }
});

test('a set-internal addzone preserves reservation order and falls back after exhaustion', () => {
  const set = {
    members: [
      { exampleId: 'full', order: 1, cutType: 'styling', shot: 'full', direction: 'front' },
      { exampleId: 'spare', order: 2, cutType: 'styling', shot: 'full', direction: 'side' },
      { exampleId: 'medium', order: 3, cutType: 'styling', shot: 'medium', direction: 'front' },
    ],
  };
  const current = [
    block('full', { exampleId: 'full', spaceSetMemberOrder: 1, spaceGroupId: groupA, refScope: 'pose' }),
    block('medium', { exampleId: 'medium', spaceSetMemberOrder: 3, spaceGroupId: groupA, refScope: 'pose' }),
  ];
  const reservation = nextSpaceSetMemberReservation(set, current);
  const reserved = reservation.member;
  assert.equal(reserved.exampleId, 'spare');
  assert.deepEqual(reservation.blockPatch, {
    spaceGroupId: groupA,
    spaceVariation: 'subtle',
    refScope: 'pose',
    spaceSetMemberOrder: 2,
    setSelectionOrigin: 'user',
  });

  const added = [...current, block('spare', {
    exampleId: reserved.exampleId,
    ...reservation.blockPatch,
  })];
  assert.equal(added.at(-1).spaceGroupId, groupA);
  assert.equal(added.at(-1).refScope, 'pose');
  assert.equal(nextSpaceSetMemberReservation(set, added), null);
});

test('reserved-member lookup uses member order even after its example is changed', () => {
  const set = {
    members: [
      { exampleId: 'first', order: 1 },
      { exampleId: 'second', order: 2 },
      { exampleId: 'third', order: 3 },
    ],
  };
  const current = [
    block('changed-first', { exampleId: 'custom', spaceSetMemberOrder: 1 }),
    block('third', { exampleId: 'third', spaceSetMemberOrder: 3 }),
  ];
  assert.equal(nextSpaceSetMemberReservation(set, current).member.exampleId, 'second');
});

test('one insert control handles empty, terminal, and set-internal additions', () => {
  const storyboardSource = readFileSync(
    new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
    'utf8',
  );
  const cssSource = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  assert.match(storyboardSource, /nextSpaceSetMemberReservation\(set, unit\.items\.map/);
  assert.equal((storyboardSource.match(/<StoryboardInsertControl/g) || []).length, 1);
  assert.match(storyboardSource, /renderUnit\(spaceUnit, group, unit\.spaceGroupId, reservation\)/);
  assert.match(storyboardSource, /insertControl\(lastItem\.index, group, null, null, 'end'\)/);
  assert.match(storyboardSource, /!group\.items\.length && insertControl\(groupSection\.start, group, null, null, 'empty'\)/);
  assert.match(storyboardSource, /addBlock\(idx, section\.id, section\.role, targetSpaceGroupId, group\.key, requestedExample\)/);
  assert.match(storyboardSource, /\.\.\.\(reservation\?\.blockPatch \|\| \{\}\)/);
  const addBlockSource = storyboardSource.slice(
    storyboardSource.indexOf('const addBlock ='),
    storyboardSource.indexOf('const mineBlock ='),
  );
  assert.match(addBlockSource, /effectiveSpaceGroupId = targetSpaceGroupId && \(reservedSpaceMember \|\| droppedExample\)/);
  assert.match(addBlockSource, /const g = explicitGroup/);
  assert.doesNotMatch(addBlockSource, /peers\.every/);
  assert.match(cssSource, /\.sb-addzone\.end \{[^}]*display: grid/);
  assert.match(cssSource, /\.sb-addzone\.empty/);
  assert.doesNotMatch(storyboardSource, /sb-ghost-card|sb-tray-add|sb-tray-add-preview/);
  assert.doesNotMatch(cssSource, /sb-ghost-card|sb-tray-add|sb-tray-add-preview/);
});

test('space runs use continuity-aware units, stable composite keys, and no dissolve menu', () => {
  const storyboardSource = readFileSync(
    new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
    'utf8',
  );
  assert.match(storyboardSource, /kind: 'spaceRun'/);
  assert.match(storyboardSource, /unit\.kind === 'spaceRun' \? renderSpaceRun/);
  assert.match(storyboardSource, /key=\{'spaceRun:' \+ unit\.spaceGroupId \+ ':' \+ unit\.items\[0\]\.block\.id\}/);
  assert.match(storyboardSource, /sb-tray-label[^]*spaceSetDisplayName\(set\)/);
  assert.match(storyboardSource, /className="sb-tray-swap"[^]*장소 세트 변경/);
  assert.doesNotMatch(storyboardSource, /장소 세트 묶음 풀기|dissolveSpaceGroup|sb-tray-more/);
});

test('unknown or pre-release group ids are not inferred as a shooting set', () => {
  assert.equal(inferStoryboardSpaceSet('ssg1__removed-release-id__instance'), null);
  assert.equal(inferStoryboardSpaceSet('sgset__removed-release-id__legacy'), null);
});

test('dragging a member out keeps its content and keeps the remaining set intact', () => {
  const source = [
    block('a', { spaceGroupId: groupA, refScope: 'pose', exampleId: 'ex-1' }),
    block('b', { spaceGroupId: groupA, refScope: 'pose', exampleId: 'ex-2' }),
    block('c', {}),
  ];
  const moved = moveBlockWithSpaceMembership(source, 'b', 3, { targetSpaceGroupId: null });
  const out = moved.find((item) => item.id === 'b');
  assert.equal(out.spaceGroupId, undefined);
  assert.equal(out.exampleId, 'ex-2');
  const remaining = moved.find((item) => item.id === 'a');
  assert.equal(remaining.spaceGroupId, groupA);
  const remainingRun = groupConsecutiveSpaceRuns(moved)
    .find((group) => group.spaceGroupId === groupA);
  assert.equal(remainingRun.kind, 'space');
  assert.deepEqual(remainingRun.items.map((item) => item.id), ['a']);
});

test('converting a middle member to mine keeps its position and separates both space runs', () => {
  const source = [
    block('a', { spaceGroupId: groupA, refScope: 'pose' }),
    block('b', { spaceGroupId: groupA, refScope: 'pose', source: 'mine' }),
    block('c', { spaceGroupId: groupA, refScope: 'pose' }),
    block('outside'),
  ];
  const detached = source.map((item) => item.id === 'b' ? detachSpaceMembership(item) : item);
  const moved = rekeySeparatedSpaceRuns(detached, (setId) => spaceSetGroupId(setId, 'mine-split'));
  assert.deepEqual(moved.map((item) => item.id), ['a', 'b', 'c', 'outside']);
  assert.equal(moved[1].spaceGroupId, undefined);
  assert.equal(moved[1].refScope, 'all');
  assert.equal(moved[0].spaceGroupId, groupA);
  assert.equal(moved[2].spaceGroupId, spaceSetGroupId(releasedStylingSet.id, 'mine-split'));
});

/* 위/아래 한 칸 이동(nudgeBlock)의 인덱스 보정 계약.
   moveBlockWithSpaceMembership 은 targetIndex 를 '원본 배열 기준'으로 받아 자기 자신이 빠진
   만큼 스스로 보정한다. 따라서 아래로 한 칸은 to+1, 위로 한 칸은 to 를 넘겨야 한다.
   이 계약이 깨지면 버튼이 두 칸씩 뛰거나 제자리에 머문다. */
test('한 칸 이동 — 아래는 to+1, 위는 to 를 넘긴다', () => {
  const board = ['a', 'b', 'c', 'd'].map((id) => ({ id, sectionId: 's1' }));
  const ids = (list) => list.map((block) => block.id);
  const at = (id) => board.findIndex((block) => block.id === id);

  // b 를 아래로 한 칸: to = at(b)+1 = 2 → idx = to + 1 = 3
  assert.deepEqual(ids(moveBlockWithSpaceMembership(board, 'b', 3)), ['a', 'c', 'b', 'd']);
  // c 를 위로 한 칸: to = at(c)-1 = 1 → idx = to = 1
  assert.deepEqual(ids(moveBlockWithSpaceMembership(board, 'c', 1)), ['a', 'c', 'b', 'd']);
  // 경계: 첫 카드를 아래로, 마지막 카드를 위로
  assert.deepEqual(ids(moveBlockWithSpaceMembership(board, 'a', at('a') + 2)), ['b', 'a', 'c', 'd']);
  assert.deepEqual(ids(moveBlockWithSpaceMembership(board, 'd', at('d') - 1)), ['a', 'b', 'd', 'c']);
});
