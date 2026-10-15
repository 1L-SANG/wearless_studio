import test from 'node:test';
import assert from 'node:assert/strict';
import { ensureSections, normalizeBoard } from '../../src/lib/sections.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { createSpaceSetMembers, detachSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';

const base = { id: 'cut', source: 'ai', cutType: 'styling', shot: 'full', direction: 'front',
  sectionRole: 'styling', contentRole: 'coordination', taxonomyVersion: 3,
  exampleId: 'chosen', thumb: 'existing.png' };

for (const scope of ['pose', 'bg']) {
  test(`old ${scope} selection cannot reactivate a single-cut mode`, () => {
    const saved = { ...base, refScope: scope, ref_scope: scope };
    for (const normalize of [ensureSections, normalizeBoard]) {
      const [result] = normalize([saved]);
      assert.equal(result.refScope, 'all');
      assert.equal(result.ref_scope, undefined);
      assert.equal(result.exampleId, 'chosen');
      assert.equal(result.thumb, 'existing.png');
    }
    const { patch } = generationExampleSelectionPatch(saved,
      { id: 'chosen', cutType: 'styling', direction: 'front' }, { refScope: scope });
    assert.equal(patch.refScope, 'all');
    assert.equal(saved.refScope, scope);
  });
}

test('place series retain internal pose input and detach into full-example reference', () => {
  const [member] = createSpaceSetMembers({ spaceVariation: 'subtle', members: [
    { exampleId: 'chosen', order: 1, cutType: 'styling', shot: 'full', direction: 'front' },
  ] }, base, { spaceGroupId: 'ssg1__set-one__instance-one' });
  assert.equal(ensureSections([member])[0].refScope, 'pose');
  assert.equal(detachSpaceMembership(member).refScope, 'all');
});
