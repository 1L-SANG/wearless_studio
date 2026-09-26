import test from 'node:test';
import assert from 'node:assert/strict';
import { effectiveHorizonBackgroundMode, horizonBackgroundAvailability as resolveAvailability,
  reconcileHorizonGroupBackground } from '../../src/lib/horizonBackgroundAvailability.js';
import { STORYBOARD_SPACE_SETS } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { replaceSpaceSetRun } from '../../src/lib/storyboardSpaceSets.js';
import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import serverCatalog from '../../server/app/data/space_set_assets.json' with { type: 'json' };
import frontendCatalog from '../../src/data/storyboardSpaceSets.json' with { type: 'json' };
import replacements from '../../data/storyboardSpaceSetReplacements.json' with { type: 'json' };
const horizonSetIds = STORYBOARD_SPACE_SETS.filter(set => set.setType.startsWith('horizon')).map(set => set.id);
const stylingSetId = STORYBOARD_SPACE_SETS.find(set => set.setType === 'styling').id;
const horizonSetId = horizonSetIds[0];
const horizonBackgroundAvailability = (members, product, evidence, setId = horizonSetId) => resolveAvailability(members, product, evidence, setId);
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }, { id: 'blue' }] };
const member = colorId => ({ cutType: 'horizon', source: 'ai', colorId });
const evidence = { version: 1, clothingType: 'top', colors: [{ colorId: 'base', status: 'ready', backgroundStatus: 'ready', backgroundPolicyVersion: policy.version }, { colorId: 'blue', status: 'ready', backgroundStatus: 'ready', backgroundPolicyVersion: policy.version }] };

test('all current group colors need saved garment-photo evidence; null means base', () => {
  assert.equal(horizonBackgroundAvailability([member(null), member('blue')], product, evidence).available, true);
  assert.equal(horizonBackgroundAvailability([member(null), member('blue')], product, { ...evidence, colors: evidence.colors.slice(0, 1) }).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, { ...evidence, colors: [{ colorId: 'base', status: 'unavailable' }] }).available, false);
});
test('swatches and mismatched clothing analysis never enable garment tone', () => {
  assert.equal(horizonBackgroundAvailability([member('base')], { ...product, colors: [{ id: 'base', swatchId: 'black', hex: '#15141a' }] }, null).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, { ...evidence, clothingType: 'bottom' }).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, { ...evidence, version: 2 }).available, false);
  assert.equal(horizonBackgroundAvailability([member('gone')], product, { ...evidence, colors: [{ colorId: 'gone', status: 'ready' }] }).available, false);
});
test('unrelated recipes and own images do not participate; empty groups stay unavailable', () => {
  assert.equal(horizonBackgroundAvailability([member('base'), { cutType: 'styling', colorId: 'missing' }, { ...member('missing'), source: 'mine' }], product, evidence).available, true);
  assert.equal(horizonBackgroundAvailability([], product, evidence).available, false);
});

test('measured colors still require a current ready background calculation', () => {
  const ambiguous = { ...evidence, colors: [{ ...evidence.colors[0], backgroundStatus: 'reference', backgroundReason: 'ambiguous-middle-lightness' }] };
  const result = horizonBackgroundAvailability([member('base')], product, ambiguous);
  assert.equal(result.available, false);
  assert.equal(result.reason, '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.');
  assert.equal(horizonBackgroundAvailability([member('base')], product, { ...evidence, colors: [{ colorId: 'base', status: 'ready' }] }).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, { ...evidence, colors: [{ ...evidence.colors[0], backgroundPolicyVersion: policy.version - 1 }] }).available, false);
});
test('all published horizon sets enable garment tone, while styling sets do not', () => {
  const serverIds = serverCatalog.sets.filter(set => set.setType.startsWith('horizon')).map(set => set.setId);
  const frontendIds = frontendCatalog.sets.filter(set => set.setType.startsWith('horizon')).map(set => set.setId);
  assert.deepEqual([...frontendIds].sort(), [...serverIds].sort());
  assert.deepEqual([...horizonSetIds].sort(), serverIds.filter(id => !serverIds.includes(replacements[id])).sort());
  assert.equal(Object.hasOwn(policy, 'allowedSetIds'), false, 'a second allowlist would drift from the published catalog');
  assert.equal(resolveAvailability([member('base')], product, evidence).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, stylingSetId).available, false);
  for (const id of serverIds) assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, id).available, true, id);
});

test('replacing a selected real horizon set cannot leave a false garment-tone label', () => {
  const [oldSet, nextSet] = STORYBOARD_SPACE_SETS.filter(set => set.setType.startsWith('horizon')
    && set.gender === 'women' && set.applicableClothingTypes.includes('top'));
  assert.ok(oldSet && nextSet);
  const oldGroup = `ssg1__${oldSet.id}__old`;
  const nextGroup = `ssg1__${nextSet.id}__next`;
  const placed = oldSet.members.slice(0, 2).map((item, index) => ({
    id: `old-${index}`, source: 'ai', sectionId: 'studio-section', sectionRole: 'studio',
    cutType: 'horizon', colorId: 'base', spaceGroupId: oldGroup,
    spaceSetMemberOrder: item.order, exampleId: item.exampleId,
    horizonBackgroundMode: 'garment-tone',
  }));
  const copied = replaceSpaceSetRun(placed, oldGroup, nextSet, { spaceGroupId: nextGroup });
  assert.ok(copied.length >= 2 && copied.every(block => block.horizonBackgroundMode === 'garment-tone'));
  const fallback = reconcileHorizonGroupBackground(copied, nextGroup, product, null, nextSet.id);
  assert.ok(fallback.every(block => block.horizonBackgroundMode === 'reference'));
  assert.equal(effectiveHorizonBackgroundMode(fallback[0], { available: false }), 'reference');
  const available = reconcileHorizonGroupBackground(copied, nextGroup, product, evidence, nextSet.id);
  assert.ok(available.every(block => block.horizonBackgroundMode === 'garment-tone'));
  assert.equal(effectiveHorizonBackgroundMode(available[0], { available: true }), 'garment-tone');
});

test('a saved tone request shows the background actually used for stale or missing evidence', () => {
  const block = { ...member('base'), spaceGroupId: 'current', horizonBackgroundMode: 'garment-tone' };
  assert.equal(effectiveHorizonBackgroundMode(block, { available: true }), 'garment-tone');
  assert.equal(effectiveHorizonBackgroundMode(block, { available: false }), 'reference');
  assert.equal(effectiveHorizonBackgroundMode(block, null), 'reference');
});

test('replacing a set keeps tone only if every target color is ready and preserves undo source', () => {
  const old = { ...member('base'), id: 'old', spaceGroupId: 'old-group', horizonBackgroundMode: 'garment-tone' };
  const nextGroup = [
    { ...old, id: 'next-a', spaceGroupId: 'new-group' },
    { ...old, id: 'next-b', colorId: 'blue', spaceGroupId: 'new-group' },
  ];
  const blocks = [old, ...nextGroup];
  assert.equal(reconcileHorizonGroupBackground(blocks, 'new-group', product, evidence, horizonSetId), blocks);
  const missingBlue = { ...evidence, colors: evidence.colors.slice(0, 1) };
  const downgraded = reconcileHorizonGroupBackground(blocks, 'new-group', product, missingBlue, horizonSetId);
  assert.notEqual(downgraded, blocks);
  assert.equal(downgraded[0], old);
  assert.ok(downgraded.slice(1).every(block => block.horizonBackgroundMode === 'reference'));
  assert.ok(blocks.slice(1).every(block => block.horizonBackgroundMode === 'garment-tone'));
  assert.equal(reconcileHorizonGroupBackground(blocks, 'new-group', product, evidence, stylingSetId)[1].horizonBackgroundMode, 'reference');
});
