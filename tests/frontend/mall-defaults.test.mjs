import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

async function logic() {
  const url = new URL('../../src/lib/mallDefaults.js', import.meta.url);
  assert.ok(existsSync(url), 'defaults validation is missing');
  return import(url);
}
const saved = {
  shipping: { feeType: 'CONDITIONAL_FREE', baseFee: 3000, jejuFee: 0, isolatedFee: 0, conditionalAmount: 50000 },
  returns: { partialFee: 3000, totalFee: 6000, exchangeFee: 6000 },
  addresses: { shippingAddressId: 'a1', returnAddressId: 'a1' },
  disclosure: { country: '한국', manufacturer: '니트', asManager: '김담당', phone: '02-123-4567' },
  taxType: 'TAX', productOrigin: 'domestic',
};

test('validateDefaults rejects negative fee and missing conditional amount', async () => {
  const { validateDefaults } = await logic();
  const negative = validateDefaults('shipping', { ...saved.shipping, baseFee: -1 });
  assert.equal(negative.ok, false); assert.ok(negative.errors.baseFee);
  const conditional = validateDefaults('shipping', { ...saved.shipping, conditionalAmount: '' });
  assert.equal(conditional.ok, false); assert.ok(conditional.errors.conditionalAmount);
  assert.equal(validateDefaults('shipping', saved.shipping).ok, true);
});

test('completedItems requires every field and accepts zero fees', async () => {
  const { completedItems } = await logic();
  assert.deepEqual(completedItems({}), []);
  assert.deepEqual(completedItems(saved), ['shipping', 'returns', 'addresses', 'disclosure']);
  assert.deepEqual(completedItems({ shipping: { feeType: 'FREE', jejuFee: 0, isolatedFee: 0 } }), ['shipping']);
  assert.deepEqual(completedItems({ returns: { partialFee: 0, totalFee: 0 } }), []);
});

test('validateDefaults rejects decimals blank fees and invalid phones', async () => {
  const { validateDefaults } = await logic();
  for (const partialFee of ['', -2, 1.5, '1.5', '1e2', true]) {
    assert.equal(validateDefaults('returns', { ...saved.returns, partialFee }).ok, false);
  }
  assert.equal(validateDefaults('disclosure', { ...saved.disclosure, phone: '전화' }).ok, false);
  assert.equal(validateDefaults('disclosure', { ...saved.disclosure, country: '  ' }).ok, false);
});

test('changing shipping policy saves only visible fees', async () => {
  const { validateDefaults } = await logic();
  for (const policy of ['FREE', 'CHARGED']) {
    const runtime = hooks(), calls = [];
    const { MallDefaultsSection } = await componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['MallDefaultsSection'],
      { ...runtime, validateDefaults, styles: {}, Button: 'button', Icon: () => null,
        api: { saveZigzagDefaults: async (payload) => { calls.push(payload); return payload; } } });
    const props = { section: 'shipping', defaults: { shipping: { feeType: 'CONDITIONAL_FREE', baseFee: policy === 'FREE' ? -1 : 3000, jejuFee: 0, isolatedFee: 0, conditionalAmount: -1 } }, onSaved() {}, onDone() {} };
    let tree = runtime.render(MallDefaultsSection, props);
    const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
    const radio = walk(tree).find((node) => node.props?.name === 'feeType');
    radio.props.onChange({ ...radio.props.values, feeType: policy });
    tree = runtime.render(MallDefaultsSection, props);
    await tree.props.onSubmit({ preventDefault() {} });
    assert.equal(calls.length, 1, `${policy} save must not be blocked by hidden fields`);
    assert.deepEqual(calls[0], { shipping: { feeType: policy, baseFee: policy === 'FREE' ? 0 : 3000, jejuFee: 0, isolatedFee: 0 } });
  }
});

test('checklist progress counts completed sections', async () => {
  const { completedItems, validateDefaults } = await logic();
  const runtime = hooks();
  const { MallDefaultsChecklist } = await componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['MallDefaultsChecklist'],
    { ...runtime, completedItems, validateDefaults, styles: {}, Icon: () => null,
      Button: ({ children }) => React.createElement('button', null, children) });
  const tree = runtime.render(MallDefaultsChecklist, { defaults: { returns: saved.returns }, onSaved() {} });
  const html = renderToStaticMarkup(tree);
  assert.match(html, /4개 중 1개 끝/);
  assert.match(html, /aria-valuenow="1"/);
  assert.equal((html.match(/aria-expanded="true" aria-controls="mall-section-/g) || []).length, 1);
  const full = hooks();
  const all = await componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['MallDefaultsChecklist'],
    { ...full, completedItems, validateDefaults, styles: {}, Icon: () => null,
      Button: ({ children }) => React.createElement('button', null, children) });
  assert.match(renderToStaticMarkup(full.render(all.MallDefaultsChecklist, { defaults: saved })), /기본 설정 끝/);
});
