import test from 'node:test';
import assert from 'node:assert/strict';

import {
  dissolveSpaceSet,
  groupConsecutiveSpaceRuns,
  moveBlockWithSpaceMembership,
  replaceSpaceSetRun,
} from '../../src/lib/storyboardSpaceSets.js';
import { storyboardSpaceSetById } from '../../src/lib/storyboardSpaceSetCatalog.js';

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
  const next = replaceSpaceSetRun(source, 'space-1', storyboardSpaceSetById('studio'), {
    spaceGroupId: 'space-2',
    makeId: (_member, index) => `new-${index}`,
  });
  assert.notEqual(next, source);
  assert.deepEqual(source.map((item) => item.id), ['before', 'old-a', 'old-b', 'after']);
  assert.deepEqual(next.slice(1, 4).map((item) => [
    item.spaceGroupId, item.cutType, item.direction, item.shot, item.refScope, item.exampleId,
  ]), [
    ['space-2', 'horizon', 'front', 'full', 'pose', null],
    ['space-2', 'horizon', 'side', 'full', 'pose', null],
    ['space-2', 'horizon', 'back', 'full', 'pose', null],
  ]);
  assert.deepEqual(next.map((item) => item.id), ['before', 'old-a', 'old-b', 'new-2', 'after']);
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
