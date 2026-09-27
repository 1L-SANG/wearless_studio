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
const photos = [{ slot: 'Front' }, { slot: 'Back' }];
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: photos }, { id: 'blue', images: photos }] };
const member = colorId => ({ cutType: 'horizon', source: 'ai', colorId });
const evidence = { version: 1, clothingType: 'top', colors: [{ colorId: 'base', status: 'ready', backgroundStatus: 'ready', backgroundPolicyVersion: policy.version }, { colorId: 'blue', status: 'ready', backgroundStatus: 'ready', backgroundPolicyVersion: policy.version }] };

const pendingResult = { state: 'pending', available: true, reason: null };
const measuredFailure = '사진에서 옷 색을 확인하지 못해 기존 배경을 사용해요.';

test('all current group colors ready is ready; null means base; a missing row is still pending', () => {
  assert.deepEqual(horizonBackgroundAvailability([member(null), member('blue')], product, evidence), { state: 'ready', available: true, reason: null });
  assert.deepEqual(horizonBackgroundAvailability([member(null), member('blue')], product, { ...evidence, colors: evidence.colors.slice(0, 1) }), pendingResult);
  const failed = horizonBackgroundAvailability([member('base')], product, { ...evidence, colors: [{ ...evidence.colors[0], status: 'unavailable', backgroundStatus: 'reference' }] });
  assert.deepEqual(failed, { state: 'unavailable', available: false, reason: measuredFailure });
});
test('not yet measured or measured for other inputs stays selectable as pending', () => {
  assert.deepEqual(horizonBackgroundAvailability([member('base')], { ...product, colors: [{ id: 'base', swatchId: 'black', hex: '#15141a', images: photos }] }, null), pendingResult);
  assert.deepEqual(horizonBackgroundAvailability([member('base')], product, { ...evidence, clothingType: 'bottom' }), pendingResult);
  assert.deepEqual(horizonBackgroundAvailability([member('base')], product, { ...evidence, version: 2 }), pendingResult);
  assert.deepEqual(horizonBackgroundAvailability([member('base')], product, { ...evidence, colors: [{ ...evidence.colors[0], backgroundPolicyVersion: policy.version - 1 }] }), pendingResult);
});
test('a color the server can never measure is unavailable at once, but an unloaded product stays pending', () => {
  const noPhoto = { ...product, colors: [...product.colors, { id: 'red', images: [{ slot: 'Detail' }] }] };
  const photoMissing = { state: 'unavailable', available: false, reason: '이 색상은 앞면이나 뒷면 사진이 없어 기존 배경을 사용해요.' };
  assert.deepEqual(horizonBackgroundAvailability([member('base'), member('red')], noPhoto, null), photoMissing);
  assert.deepEqual(horizonBackgroundAvailability([member('gone')], product, evidence), photoMissing);
  // 슬롯 없는 사진은 서버가 앞면으로 본다.
  assert.equal(horizonBackgroundAvailability([member('red')], { ...product, colors: [...product.colors, { id: 'red', images: [{}] }] }, null).state, 'pending');
  assert.deepEqual(horizonBackgroundAvailability([member('base')], { clothingType: 'top' }, null), pendingResult);
  const blocks = [{ ...member('red'), spaceGroupId: 'g', horizonBackgroundMode: 'garment-tone' }];
  assert.equal(reconcileHorizonGroupBackground(blocks, 'g', noPhoto, null, horizonSetId)[0].horizonBackgroundMode, 'reference');
});
test('a missing row wins over a failed row because the server measures every needed color again', () => {
  const partial = { ...evidence, colors: [{ ...evidence.colors[0], status: 'unavailable', backgroundStatus: 'reference' }] };
  assert.equal(horizonBackgroundAvailability([member('base'), member('blue')], product, partial).state, 'pending');
});
test('unrelated recipes and own images do not participate; empty groups stay unavailable', () => {
  assert.equal(horizonBackgroundAvailability([member('base'), { cutType: 'styling', colorId: 'missing' }, { ...member('missing'), source: 'mine' }], product, evidence).state, 'ready');
  assert.deepEqual(horizonBackgroundAvailability([], product, evidence), { state: 'unavailable', available: false, reason: '이 세트는 기존 배경을 사용해요.' });
  assert.equal(horizonBackgroundAvailability([{ ...member('base'), source: 'mine' }], product, null).state, 'unavailable');
});

test('measured colors still require a current ready background calculation', () => {
  const ambiguous = { ...evidence, colors: [{ ...evidence.colors[0], backgroundStatus: 'reference', backgroundReason: 'ambiguous-middle-lightness' }] };
  const result = horizonBackgroundAvailability([member('base')], product, ambiguous);
  assert.equal(result.state, 'unavailable');
  assert.equal(result.available, false);
  assert.equal(result.reason, '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.');
});
test('all published horizon sets enable garment tone, while styling sets do not', () => {
  const serverIds = serverCatalog.sets.filter(set => set.setType.startsWith('horizon')).map(set => set.setId);
  const frontendIds = frontendCatalog.sets.filter(set => set.setType.startsWith('horizon')).map(set => set.setId);
  assert.deepEqual([...frontendIds].sort(), [...serverIds].sort());
  assert.deepEqual([...horizonSetIds].sort(), serverIds.filter(id => !serverIds.includes(replacements[id])).sort());
  assert.equal(Object.hasOwn(policy, 'allowedSetIds'), false, 'a second allowlist would drift from the published catalog');
  assert.equal(resolveAvailability([member('base')], product, evidence).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, stylingSetId).available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, null, stylingSetId).state, 'unavailable');
  for (const id of serverIds) assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, id).available, true, id);
});

test('replacing a selected real horizon set keeps a pending tone and drops a measured-unavailable one', () => {
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
  const pending = reconcileHorizonGroupBackground(copied, nextGroup, product, null, nextSet.id);
  assert.equal(pending, copied);
  assert.equal(effectiveHorizonBackgroundMode(pending[0], { state: 'pending', available: true }), 'garment-tone');
  const failed = { ...evidence, colors: [{ ...evidence.colors[0], status: 'unavailable', backgroundStatus: 'reference' }] };
  const fallback = reconcileHorizonGroupBackground(copied, nextGroup, product, failed, nextSet.id);
  assert.ok(fallback.every(block => block.horizonBackgroundMode === 'reference'));
  assert.equal(effectiveHorizonBackgroundMode(fallback[0], { state: 'unavailable', available: false }), 'reference');
  const available = reconcileHorizonGroupBackground(copied, nextGroup, product, evidence, nextSet.id);
  assert.ok(available.every(block => block.horizonBackgroundMode === 'garment-tone'));
  assert.equal(effectiveHorizonBackgroundMode(available[0], { state: 'ready', available: true }), 'garment-tone');
});

test('a saved tone request shows garment tone while pending or ready, and the reference once unavailable', () => {
  const block = { ...member('base'), spaceGroupId: 'current', horizonBackgroundMode: 'garment-tone' };
  assert.equal(effectiveHorizonBackgroundMode(block, { state: 'ready', available: true }), 'garment-tone');
  assert.equal(effectiveHorizonBackgroundMode(block, { state: 'pending', available: true }), 'garment-tone');
  assert.equal(effectiveHorizonBackgroundMode(block, { state: 'unavailable', available: false }), 'reference');
  assert.equal(effectiveHorizonBackgroundMode(block, null), 'reference');
  assert.equal(effectiveHorizonBackgroundMode({ ...block, horizonBackgroundMode: 'reference' }, { state: 'pending', available: true }), 'reference');
});

test('replacing a set drops tone only for a measured-unavailable color and preserves undo source', () => {
  const old = { ...member('base'), id: 'old', spaceGroupId: 'old-group', horizonBackgroundMode: 'garment-tone' };
  const nextGroup = [
    { ...old, id: 'next-a', spaceGroupId: 'new-group' },
    { ...old, id: 'next-b', colorId: 'blue', spaceGroupId: 'new-group' },
  ];
  const blocks = [old, ...nextGroup];
  assert.equal(reconcileHorizonGroupBackground(blocks, 'new-group', product, evidence, horizonSetId), blocks);
  const missingBlue = { ...evidence, colors: evidence.colors.slice(0, 1) };
  assert.equal(reconcileHorizonGroupBackground(blocks, 'new-group', product, missingBlue, horizonSetId), blocks);
  const failedBlue = { ...evidence, colors: [evidence.colors[0], { ...evidence.colors[1], status: 'unavailable', backgroundStatus: 'reference' }] };
  const downgraded = reconcileHorizonGroupBackground(blocks, 'new-group', product, failedBlue, horizonSetId);
  assert.notEqual(downgraded, blocks);
  assert.equal(downgraded[0], old);
  assert.ok(downgraded.slice(1).every(block => block.horizonBackgroundMode === 'reference'));
  assert.ok(blocks.slice(1).every(block => block.horizonBackgroundMode === 'garment-tone'));
  assert.equal(reconcileHorizonGroupBackground(blocks, 'new-group', product, evidence, stylingSetId)[1].horizonBackgroundMode, 'reference');
});
