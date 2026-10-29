import test from 'node:test';
import assert from 'node:assert/strict';
import { validateDefaults, salesDefaultsValues, salesDefaultsPatch, validateSalesDefaults } from '../../src/lib/mallDefaults.js';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as logic from '../../src/lib/mallDefaults.js';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const field = (tree, name) => walk(tree).find((node) => node.props?.name === name);
const initial = { revision: 0, shipping: {}, returns: {}, disclosure: {} };
async function setup(api = {}, defaults = initial) {
  const runtime = hooks(), changes = [];
  const { SalesDefaultsCard } = await componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['SalesDefaultsCard'], {
    ...runtime, ...logic, api, styles: {}, Button: 'button',
  });
  const props = { defaults, onSaved: (result) => changes.push(result) };
  return { runtime, changes, render: () => runtime.render(SalesDefaultsCard, props) };
}

test('shipping validation rejects invalid amounts and requires positive free threshold', () => {
  for (const value of [-2, 1.5, '1.5', '1e2', true, 2147483648]) {
    assert.equal(validateDefaults('returns', { totalFee: value, partialFee: 0, exchangeFee: 0 }).ok, false);
  }
  assert.equal(validateDefaults('shipping', { feeType: 'CONDITIONAL_FREE', baseFee: 0, jejuFee: 0, isolatedFee: 0, conditionalAmount: 0 }).ok, false);
  assert.equal(validateDefaults('shipping', { feeType: 'FREE', jejuFee: 0, isolatedFee: 0 }).ok, true);
});

test('incomplete sales defaults save without inventing zero fees or approved warranty text', () => {
  const values = salesDefaultsValues(initial);
  assert.equal(validateSalesDefaults(values).ok, true);
  assert.deepEqual(salesDefaultsPatch(values).shipping, { feeType: 'CHARGED' });
  assert.deepEqual(salesDefaultsPatch(values).returns, {});
  assert.equal(salesDefaultsPatch(values).disclosure.warrantyText, '');
  assert.equal(validateSalesDefaults({ ...values, shippingDays: 31 }).ok, false);
});

test('changing shipping policy discards hidden fees and conditional threshold', () => {
  for (const policy of ['FREE', 'CHARGED']) {
    const values = salesDefaultsValues({ shipping: { feeType: policy, baseFee: policy === 'FREE' ? -1 : 3000, jejuFee: 0, isolatedFee: 0, conditionalAmount: -1 } });
    assert.equal(validateSalesDefaults(values).ok, true);
    assert.deepEqual(salesDefaultsPatch(values).shipping, { feeType: policy, baseFee: policy === 'FREE' ? 0 : 3000, jejuFee: 0, isolatedFee: 0 });
  }
});

test('validation summary blocks saving and links the invalid field', async () => {
  let calls = 0;
  const ui = await setup({ saveSalesDefaults: () => calls++ });
  field(ui.render(), 'shippingDays').props.onChange('31');
  await ui.render().props.onSubmit({ preventDefault() {} });
  const html = renderToStaticMarkup(ui.render());
  assert.equal(calls, 0); assert.match(html, /role="alert"/); assert.match(html, /href="#sales-shippingDays"/); assert.match(html, /aria-invalid="true"/);
});

test('late save from an unmounted account never updates replacement defaults', async () => {
  let finish;
  const ui = await setup({ saveSalesDefaults: () => new Promise((resolve) => { finish = resolve; }) });
  const tree = ui.render(), cleanups = ui.runtime.flushEffects();
  const saving = tree.props.onSubmit({ preventDefault() {} });
  cleanups.forEach((cleanup) => cleanup?.()); finish({ revision: 1 }); await saving;
  assert.deepEqual(ui.changes, []);
});

test('a failed revision reload retains the draft and blocks save until reloaded', async () => {
  let calls = 0, loads = 0;
  const ui = await setup({
    saveSalesDefaults: async () => { calls++; throw Object.assign(new Error('conflict'), { code: 'defaults_conflict' }); },
    getSalesDefaults: async () => { if (++loads === 1) throw new Error('offline'); return { revision: 9 }; },
  });
  field(ui.render(), 'manufacturer').props.onChange('내 입력');
  await ui.render().props.onSubmit({ preventDefault() {} });
  await ui.render().props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 1); assert.equal(field(ui.render(), 'manufacturer').props.value, '내 입력');
  const retry = walk(ui.render()).find((node) => node.props?.children === '최신 정보 다시 확인');
  await retry.props.onClick(); await ui.render().props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 2);
});

test('clearing a stored fee cannot silently report the previous amount as saved', async () => {
  let calls = 0;
  const ui = await setup({ saveSalesDefaults: async () => { calls++; return { revision: 2 }; } }, { ...initial, revision: 1, shipping: { feeType: 'CHARGED', baseFee: 3000 } });
  field(ui.render(), 'baseFee').props.onChange('');
  await ui.render().props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 0); assert.match(renderToStaticMarkup(ui.render()), /href="#sales-shipping-baseFee"/);
});
