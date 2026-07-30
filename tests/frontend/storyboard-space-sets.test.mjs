import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSpaceSetMembers,
  dissolveSpaceSet,
  groupConsecutiveSpaceRuns,
  moveBlockWithSpaceMembership,
  replaceSpaceSetRun,
} from '../../src/lib/storyboardSpaceSets.js';
import {
  inferStoryboardSpaceSet,
  isStoryboardSpaceSetEligible,
  normalizeStoryboardSpaceSetRelease,
  spaceSetGroupId,
  spaceSetIdFromGroupId,
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

test('consecutive spaceGroupId runs become bands without joining separated runs', () => {
  const groups = groupConsecutiveSpaceRuns([
    block('a'),
    block('b', { spaceGroupId: 'space-1' }),
    block('c', { spaceGroupId: 'space-1' }),
    block('d'),
    block('e', { spaceGroupId: 'space-1' }),
  ]);
  assert.deepEqual(groups.map((group) => [group.kind, group.items.map((item) => item.id)]), [
    ['block', ['a']],
    ['space', ['b', 'c']],
    ['block', ['d']],
    ['space', ['e']],
  ]);
});

test('dragging into and out of a band transitions spaceGroupId and refScope', () => {
  const source = [
    block('outside', { exampleId: 'all-only', refScope: 'all', baseThumb: 'base.png', thumb: 'example.png' }),
    block('inside-a', { spaceGroupId: 'space-1', refScope: 'pose' }),
    block('inside-b', { spaceGroupId: 'space-1', refScope: 'pose' }),
  ];
  const entered = moveBlockWithSpaceMembership(source, 'outside', 3, {
    targetSpaceGroupId: 'space-1',
    isPoseCompatible: () => false,
  });
  const joined = entered.find((item) => item.id === 'outside');
  assert.equal(joined.spaceGroupId, 'space-1');
  assert.equal(joined.refScope, 'pose');
  assert.equal(joined.exampleId, null);
  assert.equal(joined.thumb, 'base.png');

  const left = moveBlockWithSpaceMembership(entered, 'outside', 0);
  assert.equal(left.find((item) => item.id === 'outside').spaceGroupId, undefined);
  assert.equal(left.find((item) => item.id === 'inside-a').spaceGroupId, 'space-1');
  assert.equal(left.find((item) => item.id === 'inside-b').spaceGroupId, 'space-1');
});

test('space set replacement swaps the whole composition in one immutable board result', () => {
  const source = [
    block('before'),
    block('old-a', { spaceGroupId: 'space-1', exampleId: 'pose-a', refScope: 'pose' }),
    block('old-b', { spaceGroupId: 'space-1', exampleId: 'pose-b', refScope: 'pose' }),
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
  const next = replaceSpaceSetRun(source, 'space-1', studioSet, {
    spaceGroupId: 'space-2',
    makeId: (_member, index) => `new-${index}`,
  });
  assert.notEqual(next, source);
  assert.deepEqual(source.map((item) => item.id), ['before', 'old-a', 'old-b', 'after']);
  assert.deepEqual(next.slice(1, 4).map((item) => [
    item.spaceGroupId, item.cutType, item.direction, item.shot, item.refScope,
    item.exampleId, item.exampleSelectionOrigin, item.thumb,
  ]), [
    ['space-2', 'horizon', 'front', 'full', 'pose', 'front', 'user', 'front.webp'],
    ['space-2', 'horizon', 'side', 'full', 'pose', 'side', 'user', 'side.webp'],
    ['space-2', 'horizon', 'back', 'full', 'pose', 'back', 'user', 'back.webp'],
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
      placeType: 'indoor',
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
    placeType: 'indoor',
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
    members: base.members.map((member) => ({ ...member, direction: null })),
  }).length, 0);
  assert.equal(normalize({ ...base, setId: 'bad__id' }).length, 0);
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
    applicableClothingTypes: ['top', 'outer'],
  };
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'women', clothingType: 'top' }), true);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: null, clothingType: 'top' }), false);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'men', clothingType: 'top' }), false);
  assert.equal(isStoryboardSpaceSetEligible(released, { gender: 'women', clothingType: 'bottom' }), false);
  for (const set of storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })) {
    assert.ok(set.gender == null || set.gender === 'women');
    assert.ok(set.applicableClothingTypes.includes('top'));
  }
});

test('creating released members preserves exact ordered example choices as user selections', () => {
  const members = createSpaceSetMembers({
    id: 'released',
    spaceVariation: 'related-views',
    members: [
      { exampleId: 'exact-full', order: 1, cutType: 'styling', shot: 'full', direction: 'front', thumb: 'full.webp' },
      { exampleId: 'exact-medium', order: 2, cutType: 'styling', shot: 'medium', direction: 'side', thumb: 'medium.webp' },
    ],
  }, block('template'), { spaceGroupId: 'ssg1__released__1' });
  assert.deepEqual(members.map((member) => ({
    exampleId: member.exampleId,
    origin: member.exampleSelectionOrigin,
    thumb: member.thumb,
    order: member.spaceSetMemberOrder,
    variation: member.spaceVariation,
  })), [
    { exampleId: 'exact-full', origin: 'user', thumb: 'full.webp', order: 1, variation: 'related-views' },
    { exampleId: 'exact-medium', origin: 'user', thumb: 'medium.webp', order: 2, variation: 'related-views' },
  ]);
});

test('unknown persisted set ids use a neutral label rather than guessing a cafe', () => {
  const inferred = inferStoryboardSpaceSet('sgset__removed-release-id__legacy', [
    block('a'), block('b'),
  ]);
  assert.equal(inferred.name, '저장된 촬영 묶음');
  assert.equal(inferred.tone, 'default');
  assert.equal(inferred.compositionLabel, '2컷 구성');
});

test('dragging a member out keeps its content and keeps the remaining set intact', () => {
  const source = [
    block('a', { spaceGroupId: 'space-1', refScope: 'pose', exampleId: 'ex-1' }),
    block('b', { spaceGroupId: 'space-1', refScope: 'pose', exampleId: 'ex-2' }),
    block('c', {}),
  ];
  const moved = moveBlockWithSpaceMembership(source, 'b', 3, { targetSpaceGroupId: null });
  const out = moved.find((item) => item.id === 'b');
  assert.equal(out.spaceGroupId, undefined);
  assert.equal(out.exampleId, 'ex-2');
  const remaining = moved.find((item) => item.id === 'a');
  assert.equal(remaining.spaceGroupId, 'space-1');
  const remainingRun = groupConsecutiveSpaceRuns(moved)
    .find((group) => group.spaceGroupId === 'space-1');
  assert.equal(remainingRun.kind, 'space');
  assert.deepEqual(remainingRun.items.map((item) => item.id), ['a']);
});
