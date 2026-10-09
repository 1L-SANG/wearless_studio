import { applyHookStyle } from '../../src/lib/storyboardHookFrame.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createSimilarCutBlock, inheritSimilarCutRecipes, canPlanSimilarCut, reconcileSimilarCutMutation } from '../../src/lib/storyboardSimilarCuts.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import { normalizeBoard } from '../../src/lib/sections.js';
const source = { id: 'a', source: 'ai', cutType: 'horizon', contentRole: 'fit', sectionRole: 'studio', taxonomyVersion: 3, sectionId: 'studio', sectionLayout: 'stack', exampleId: 'example', shot: 'full', colorId: 'navy', modelId: 'mA', thumb: 'example.jpg', spaceGroupId: 'set', hookFrameId: 'frame' };

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
  assert.equal(result[0].sectionId, 'other');
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

test('shrinking a hooking frame removes follow-ups whose original slot was removed', () => {
  let n = 0;
  const template = { ...source, id: 'first', sectionRole: 'hooking', contentRole: 'hero', sectionId: 'hook', cutType: 'styling', spaceGroupId: undefined, hookFrameId: undefined };
  const make = slot => ({ ...template, ...slot, id: `cut${++n}` });
  let grid = applyHookStyle([template], 'moodGrid', { colors: [], createBlock: make });
  grid = grid.map(block => ({ ...block, exampleId: `example-${block.id}` }));
  const original = grid.at(-1);
  const before = [...grid, createSimilarCutBlock(original, 'child')];
  const fitted = applyHookStyle(before, 'pair', { colors: [], createBlock: make });
  assert.ok(!fitted.some(block => block.id === original.id));
  assert.ok(fitted.some(block => block.id === 'child'));
  const saved = reconcileSimilarCutMutation(before, fitted);
  assert.ok(!saved.some(block => block.id === 'child'));
  assert.equal(reconcileSimilarCutMutation(before, saved), saved);
});
