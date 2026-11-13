import test from 'node:test';
import assert from 'node:assert/strict';
import { createSimilarCutBlock, inheritSimilarCutRecipes, canPlanSimilarCut, reconcileSimilarCutMutation, keepSimilarFamiliesAfterSources } from '../../src/lib/storyboardSimilarCuts.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { createSpaceSetMembers, moveBlockWithSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
import { normalizeBoard } from '../../src/lib/sections.js';
const source = { id: 'a', source: 'ai', cutType: 'horizon', contentRole: 'fit', sectionRole: 'studio', taxonomyVersion: 4, pageSectionKey: 'show.wear_fit', pageSectionKind: 'wear', pageSectionUsage: 'fit', pageDesignId: 'P04-D01', slotKey: null, sectionId: 'studio', sectionLayout: 'stack', exampleId: 'example', shot: 'full', colorId: 'navy', modelId: 'mA', thumb: 'example.jpg', spaceGroupId: 'set', hookFrameId: 'frame' };

test('new set members do not inherit a follow-up template relationship', () => {
  const original = { ...source, spaceGroupId: undefined, hookFrameId: undefined };
  const followup = createSimilarCutBlock(original, 'followup');
  const set = { spaceVariation: 'fixed', members: [
    { cutType: 'horizon', direction: 'front', shot: 'full', exampleId: 'chosen-set-example', thumb: 'set.jpg' },
  ] };
  const members = createSpaceSetMembers(set, followup, { spaceGroupId: 'new-set', makeId: () => 'member' });
  const member = normalizeBoard([followup, original, ...members]).find(block => block.id === 'member');
  assert.equal(member.similarSourceBlockId, undefined);
  assert.equal(member.exampleId, 'chosen-set-example');
  assert.equal(member.spaceGroupId, 'new-set');
});
test('similar planning uses a worn original and leaves its example unchanged', () => {
  const child = createSimilarCutBlock(source, 'b');
  assert.equal(child.similarSourceBlockId, 'a');
  assert.equal(child.exampleId, source.exampleId);
  assert.equal(child.thumb, source.thumb);
  assert.equal(child.spaceGroupId, source.spaceGroupId);
  assert.equal(child.hookFrameId, undefined);
  assert.equal(canPlanSimilarCut(child), false);
  assert.equal(canPlanSimilarCut({ ...source, cutType: 'product' }), false);
  assert.equal(canPlanSimilarCut({ ...source, source: 'mine' }), false);
});
test('a moved follow-up retains its placement and follows source edits by ID', () => {
  const child = { ...createSimilarCutBlock(source, 'b'), sectionId: 'other', layoutRowId: 'row' };
  const result = inheritSimilarCutRecipes([child, { ...source, colorId: 'ivory', exampleId: 'updated', thumb: 'updated.jpg' }]);
  assert.equal(result[0].colorId, 'ivory');
  assert.equal(result[0].exampleId, 'updated');
  assert.equal(result[0].sectionId, source.sectionId);
  assert.equal(result[0].layoutRowId, 'row');
  assert.equal(inheritSimilarCutRecipes(result), result);
});
test('missing original remains an explicit dependency, never an ordinary example generation', () => {
  const child = createSimilarCutBlock(source, 'b');
  assert.equal(inheritSimilarCutRecipes([child])[0].similarSourceBlockId, 'a');
});
test('planned follow-ups count separately while ordinary identical copies still reuse one image', () => {
  const plain = { ...source, spaceGroupId: undefined, hookFrameId: undefined };
  assert.equal(uniqueGenerationCutCount([plain, { ...plain, id: 'duplicate' }]), 1);
  assert.equal(uniqueGenerationCutCount([plain, createSimilarCutBlock(plain, 'b'), createSimilarCutBlock(plain, 'c')]), 3);
});

test('source deletion and conversion produce the same cleaned save and undo state', () => {
  const original = { ...source, spaceGroupId: undefined, sectionLayout: 'twoColumn', layoutRowId: 'row' };
  const child = { ...createSimilarCutBlock(original, 'b'), sectionLayout: 'twoColumn', layoutRowId: 'row' };
  const before = [original, child];
  const deleted = reconcileSimilarCutMutation(before, [child]);
  assert.deepEqual(deleted, []);
  assert.equal(reconcileSimilarCutMutation(before, deleted), deleted);
  assert.equal(reconcileSimilarCutMutation(deleted, before), before);
  assert.equal(before[1].layoutRowId, 'row');
  const converted = reconcileSimilarCutMutation(before, [{ ...original, cutType: 'product' }, child]);
  assert.deepEqual(converted.map(block => block.id), ['a']);
  assert.equal(reconcileSimilarCutMutation(before, converted), converted);
});


test('moving a standalone original carries its follow-ups without adding a generation card', () => {
  const root = { ...source, spaceGroupId: undefined };
  const child = createSimilarCutBlock(root, 'child');
  const other = { ...root, id: 'other' };
  const moved = moveBlockWithSpaceMembership([root, child, other], root.id, 3);
  assert.deepEqual(moved.map(b => b.id), ['other', root.id, child.id]);
  assert.equal(moved.length, 3);
});
test('swapping originals carries both families and unrelated insertions preserve manual order', () => {
  const root = { ...source, spaceGroupId: undefined };
  const other = { ...root, id: 'other' };
  const child = createSimilarCutBlock(root, 'child'), second = createSimilarCutBlock(other, 'second');
  const before = [root, child, other, second];
  const swapped = keepSimilarFamiliesAfterSources(before, [other, child, root, second]);
  assert.deepEqual(swapped.map(b => b.id), ['other','second',root.id,'child']);
  const inserted = [{ ...root, id: 'new' }, ...before];
  assert.equal(keepSimilarFamiliesAfterSources(before, inserted), inserted);
});
