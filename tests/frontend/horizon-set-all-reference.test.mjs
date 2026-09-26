import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { createSpaceSetMembers, nextSpaceSetMemberReservation, moveBlockWithSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
const setFor = cutType => ({ id: `${cutType}-set`, members: [1, 2].map(order => ({ exampleId: `${cutType}-${order}`, cutType, order, direction: 'front', shot: 'full', thumb: `/${order}.png` })) });
for (const [cutType, expected] of [['horizon','all'],['styling','pose']]) {
  test(`${cutType} grouped creation, reservation, selection and move keep ${expected} scope`, () => {
    const set = setFor(cutType), spaceGroupId = `ssg1__${set.id}__instance`;
    const members = createSpaceSetMembers(set, { sectionRole: cutType === 'horizon' ? 'studio' : 'styling' }, { spaceGroupId });
    assert.ok(members.every(member => member.refScope === expected));
    assert.equal(nextSpaceSetMemberReservation(set, members.slice(0,1)).blockPatch.refScope, expected);
    const chosen = generationExampleSelectionPatch({ ...members[0], refScope:'pose' }, { id:'external-complete',cutType,direction:'front',variants:['all'] }, { refScope:'pose' });
    assert.equal(chosen.patch.refScope, expected);
    const moved = moveBlockWithSpaceMembership(members, members[0].id, members.length, { targetSpaceGroupId:spaceGroupId, nextGroupId:()=>'unused' });
    assert.ok(moved.every(member=>member.refScope===expected));
  });
}
test('pending recipe and reserved addition use the horizon family instead of requiring pose assets', () => {
 const source=readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx',import.meta.url),'utf8');
 assert.match(source,/refScope: pendingInSpace && recipePatch.cutType !== 'horizon' \? 'pose' : 'all'/);
 assert.match(source,/refScope=\{pendingInSpace && pendingRecipe.cutType !== 'horizon' \? 'pose' : 'all'\}/);
 assert.match(source,/targetSpaceGroupId && droppedExample && droppedCutType !== 'horizon'/);
});
