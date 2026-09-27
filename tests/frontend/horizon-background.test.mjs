import test from 'node:test';
import assert from 'node:assert/strict';
import { horizonBackgroundMode, needsGarmentColorMeasurement, restoreHorizonGroupBackground, updateHorizonGroupBackground } from '../../src/lib/horizonBackground.js';
import { createSpaceSetMembers, detachSpaceMembership, nextSpaceSetMemberReservation } from '../../src/lib/storyboardSpaceSets.js';
import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';

const horizon = (id, group = 'set:instance-a', extra = {}) => ({
  id, source: 'ai', cutType: 'horizon', spaceGroupId: group, ...extra,
});

test('changing one placed set changes all its horizon cuts but no other set or recipe', () => {
  const before = [horizon('a'), horizon('b'), horizon('c', 'set:instance-b'),
    horizon('d', 'set:instance-a', { cutType: 'styling' }),
    horizon('e', 'set:instance-a', { source: 'mine' })];
  const after = updateHorizonGroupBackground(before, 'set:instance-a', 'garment-tone');
  assert.deepEqual(after.slice(0, 2).map(horizonBackgroundMode), ['garment-tone', 'garment-tone']);
  after.slice(2).forEach((block, index) => assert.equal(block, before[index + 2]));
  assert.equal(before[0].horizonBackgroundMode, undefined, 'undo snapshot is preserved');
  assert.equal(updateHorizonGroupBackground(after, 'set:instance-a', 'garment-tone'), after);
  assert.equal(updateHorizonGroupBackground(after, 'set:instance-a', 'invented'), after);
  assert.equal(updateHorizonGroupBackground(after, null, 'garment-tone'), after);
});

test('detaching removes group setting; another recipe cannot activate it', () => {
  const block = horizon('a', 'set:instance-a', { horizonBackgroundMode: 'garment-tone' });
  const detached = detachSpaceMembership(block);
  assert.equal(detached.horizonBackgroundMode, undefined);
  assert.equal(horizonBackgroundMode(detached), 'reference');
  assert.equal(horizonBackgroundMode({ ...block, cutType: 'styling' }), 'reference');
});

test('deleting, changing the remaining set, then undoing deletion restores the current group mode', () => {
  const deleted = horizon('a', 'set:instance-a', { horizonBackgroundMode: 'reference' });
  const remaining = [horizon('b'), horizon('c', 'set:instance-b')];
  const changed = updateHorizonGroupBackground(remaining, 'set:instance-a', 'garment-tone');
  const restored = restoreHorizonGroupBackground(deleted, changed);
  assert.equal(restored.horizonBackgroundMode, 'garment-tone');
  assert.equal(deleted.horizonBackgroundMode, 'reference', 'original undo snapshot remains intact');
  assert.equal(changed[1], remaining[1], 'another instance is unaffected');
  assert.equal(restoreHorizonGroupBackground(deleted, [remaining[1]]), deleted, 'a sole restored member keeps its saved mode');
});

test('new sets start unchanged; replacement and an additional member retain chosen group setting', () => {
  const set = { members: [1, 2, 3].map(order => ({ order, cutType: 'horizon', shot: 'full', direction: 'front', exampleId: `ex-${order}` })) };
  const host = horizon('a', 'set:instance-a', { exampleId: 'ex-1', spaceSetMemberOrder: 1, horizonBackgroundMode: 'garment-tone' });
  assert.equal(nextSpaceSetMemberReservation(set, [host]).blockPatch.horizonBackgroundMode, 'garment-tone');
  const inserted = createSpaceSetMembers(set, host, { spaceGroupId: 'set:instance-b' });
  assert.ok(inserted.every(block => horizonBackgroundMode(block) === 'reference'));
  const replaced = createSpaceSetMembers(set, host, { spaceGroupId: 'set:instance-c', previousMembers: [host] });
  assert.ok(replaced.every(block => horizonBackgroundMode(block) === 'garment-tone'));
});

test('user background edits change the seed fingerprint; legacy absence equals reference', () => {
  const colors = [{ id: 'base', isBase: true, images: [] }];
  const context = { projectId: 'background', clothingType: 'top', targetGenders: ['women'] };
  const before = defaultStoryboard(colors, 'basic', context);
  const placed = before.find(block => block.cutType === 'horizon' && block.spaceGroupId);
  assert.ok(placed, 'real default board has a placed horizon set');
  assert.equal(isDefaultStoryboardForMode(before, colors, 'basic', context), true);
  assert.equal(isDefaultStoryboardForMode(before.map(block => block.cutType === 'horizon'
    ? { ...block, horizonBackgroundMode: 'reference' } : block), colors, 'basic', context), true);
  assert.equal(isDefaultStoryboardForMode(updateHorizonGroupBackground(before, placed.spaceGroupId, 'garment-tone'), colors, 'basic', context), false);
});

test('failed-cut retry preserves horizon group selection without copying a client palette', () => {
  const block = horizon('failed', 'set:instance-a', { horizonBackgroundMode: 'garment-tone',
    spaceVariation: 'fixed', spaceSetMemberOrder: 2, _horizonBackground: { wallHex: '#000000' } });
  const retry = buildFailedCutRetry([block], 'failed');
  assert.equal(retry.request.horizonBackgroundMode, 'garment-tone');
  assert.equal(retry.request.retryBlockId, 'failed');
  assert.equal(retry.request.spaceGroupId, block.spaceGroupId);
  assert.equal(retry.request.spaceVariation, 'fixed');
  assert.equal(retry.request.spaceSetMemberOrder, 2);
  assert.equal(retry.request._horizonBackground, undefined);
  assert.equal(buildFailedCutRetry([{ ...block, cutType: 'styling' }], 'failed').request.horizonBackgroundMode, undefined);
});

test('leaving the storyboard asks for a garment color measurement only for AI horizon set cuts on garment tone', () => {
  const tone = { horizonBackgroundMode: 'garment-tone' };
  assert.equal(needsGarmentColorMeasurement([horizon('a', 'set:instance-a', tone)]), true);
  assert.equal(needsGarmentColorMeasurement([horizon('a'), horizon('b', 'set:instance-b', tone)]), true);
  assert.equal(needsGarmentColorMeasurement([horizon('a')]), false);
  assert.equal(needsGarmentColorMeasurement([horizon('a', 'set:instance-a', { ...tone, source: 'mine' })]), false);
  assert.equal(needsGarmentColorMeasurement([horizon('a', 'set:instance-a', { ...tone, cutType: 'styling' })]), false);
  assert.equal(needsGarmentColorMeasurement([horizon('a', null, tone)]), false);
  assert.equal(needsGarmentColorMeasurement([]), false);
  assert.equal(needsGarmentColorMeasurement(null), false);
});
