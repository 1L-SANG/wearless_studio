import test from 'node:test';
import assert from 'node:assert/strict';
import { horizonBackgroundAvailability as resolveAvailability } from '../../src/lib/horizonBackgroundAvailability.js';
import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
const allowedSetId = policy.allowedSetIds[0];
const horizonBackgroundAvailability = (members, product, evidence, setId = allowedSetId) => resolveAvailability(members, product, evidence, setId);
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
test('only explicitly supported catalog sets enable garment tone', () => {
  assert.ok(allowedSetId);
  assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, 'horizon-sequence-figma-s14-v2').available, false);
  assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, 'horizon-sequence-figma-s05-v2').available, true);
  assert.equal(resolveAvailability([member('base')], product, evidence).available, false);
  for (const id of policy.allowedSetIds) assert.equal(horizonBackgroundAvailability([member('base')], product, evidence, id).available, true);
});
