import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as logic from '../../src/lib/mallDefaults.js';

const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const field = (tree, name) => walk(tree).find((n) => n.props?.name === name);
const empty = { revision: 0, shipping: {}, returns: {}, disclosure: {}, taxType: 'TAX', productOrigin: 'domestic', shippingDays: 2, listingMode: 'HIDDEN', missingItems: ['shipping', 'returns', 'disclosure'] };
async function card(runtime = hooks(), api = {}) {
  return componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['SalesDefaultsCard'], {
    ...runtime, ...logic, api, styles: {}, Button: 'button', Icon: () => null,
  });
}
async function connections(runtime = hooks(), api = {}, bindings = {}) {
  return componentModule('src/features/settings/MallConnections.jsx', ['MallConnections', 'MallAddressDefaults', 'Cafe24Connection'], {
    ...runtime, ...logic, api, styles: {}, Button: 'button', Icon: () => null, Modal: ({ children }) => children,
    ExportMenu: () => null, Link: 'a', SalesDefaultsCard: () => null, seoulDate: () => '',
    useAuth: () => ({ session: { user: { id: 'local-user' } } }), useLocation: () => ({ search: '' }),
    useQueryClient: () => ({ setQueryData() {}, invalidateQueries() {} }),
    useQuery: () => ({ data: [{ id: 'mock-shipping', name: '성수 창고', address: '성수동' }], isPending: false }), ...bindings,
  });
}

test('sales defaults card shows split fees shipping days and hidden default', async () => {
  const runtime = hooks(), calls = [], { SalesDefaultsCard } = await card(runtime, { saveSalesDefaults: async (input) => { calls.push(input); return { ...empty, revision: 1 }; } });
  const props = { defaults: empty };
  let tree = runtime.render(SalesDefaultsCard, props);
  const html = renderToStaticMarkup(tree);
  for (const text of ['판매 기본 정보', '무료', '고정', '조건부 무료', '제주 추가', '도서산간 추가', '전체 반품', '부분 반품', '교환', '발송 소요일', '품질보증기준', '카페24는 몰 설정']) assert.ok(html.includes(text), text);
  assert.equal(field(tree, 'shippingDays').props.value, 2);
  assert.equal(field(tree, 'listingMode').props.value, 'HIDDEN');
  field(tree, 'feeType').props.onChange('CONDITIONAL_FREE');
  tree = runtime.render(SalesDefaultsCard, props);
  assert.ok(field(tree, 'conditionalAmount'));
  field(tree, 'conditionalAmount').props.onChange('50000');
  tree = runtime.render(SalesDefaultsCard, props);
  field(tree, 'feeType').props.onChange('FREE');
  tree = runtime.render(SalesDefaultsCard, props);
  assert.equal(field(tree, 'conditionalAmount'), undefined);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0].baseRevision, 0); assert.deepEqual(calls[0].shipping, { feeType: 'FREE', baseFee: 0 });
  assert.equal(calls[0].listingMode, 'HIDDEN'); assert.equal(calls[0].disclosure.warrantyText, '');
  assert.equal('addresses' in calls[0], false);
});

test('addresses stay under their own mall connection', async () => {
  const runtime = hooks(), calls = [], { MallAddressDefaults } = await connections(runtime, { saveMallDefaults: async (...args) => { calls.push(args); return { revision: 3, addresses: args[1].addresses }; } });
  const props = { mall: 'zigzag', defaults: { revision: 2, addresses: {} } };
  let tree = runtime.render(MallAddressDefaults, props);
  for (const name of ['shippingAddressId', 'returnAddressId']) {
    field(tree, name).props.onChange({ target: { value: 'mock-shipping' } });
    tree = runtime.render(MallAddressDefaults, props);
  }
  await tree.props.onSubmit({ preventDefault() {} });
  assert.deepEqual(calls, [['zigzag', { baseRevision: 2, addresses: { shippingAddressId: 'mock-shipping', returnAddressId: 'mock-shipping' } }]]);
  assert.doesNotMatch(renderToStaticMarkup(runtime.render(MallAddressDefaults, { mall: 'cafe24', defaults: {} })), /<select/);
});

test('defaults conflict keeps entered values and reloads current revision', async () => {
  const runtime = hooks(), calls = []; let loads = 0;
  const { SalesDefaultsCard } = await card(runtime, {
    saveSalesDefaults: async (input) => { calls.push(input); if (calls.length === 1) throw Object.assign(new Error('충돌'), { code: 'defaults_conflict', status: 409 }); return { ...empty, revision: 9 }; },
    getSalesDefaults: async () => { loads++; return { ...empty, revision: 8, disclosure: { manufacturer: '다른 창' } }; },
  });
  const props = { defaults: { ...empty, revision: 3 } };
  let tree = runtime.render(SalesDefaultsCard, props);
  field(tree, 'manufacturer').props.onChange('내 입력'); tree = runtime.render(SalesDefaultsCard, props);
  await tree.props.onSubmit({ preventDefault() {} }); tree = runtime.render(SalesDefaultsCard, props);
  assert.equal(loads, 1); assert.equal(field(tree, 'manufacturer').props.value, '내 입력');
  assert.match(renderToStaticMarkup(tree), /입력한 값은 남아 있어요/);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0].baseRevision, 3); assert.equal(calls[1].baseRevision, 8);
  assert.equal(calls[1].disclosure.manufacturer, '내 입력');
});

test('preparing malls never expose a working registration or ably connect form', async () => {
  const runtime = hooks(), { MallConnections } = await connections(runtime, {}, {
    useQuery: ({ queryKey }) => ({ data: queryKey[0] === 'mall-connections' ? { items: [{ mall: 'zigzag', status: 'connected', registration: { status: 'preparing' } }, { mall: 'cafe24', status: 'none' }] } : queryKey[0] === 'mall-addresses' ? [] : empty }),
  });
  const tree = runtime.render(MallConnections);
  const html = renderToStaticMarkup(tree);
  assert.match(html, /셀러스 입점만 연결할 수 있어요/); assert.match(html, /파트너스는 연결할 수 없어요/);
  assert.match(html, /등록 준비 중/); assert.doesNotMatch(html, /셀러스 어드민 API 토큰/);
});

test('cafe24 app url mall_id only prefills the mall id field', async () => {
  const previous = globalThis.window;
  try {
    for (const [input, expected] of [['dailymoment2026', 'dailymoment2026'], ['MyShop', 'myshop'], ['bad/other', ''], ['https://myshop.cafe24.com', ''], ['x', ''], ['', '']]) {
      let replaced, starts = 0, userId;
      const stored = new Map(), writes = [];
      globalThis.window = { location: { href: `https://app.test/settings/malls?mall_id=${encodeURIComponent(input)}&hmac=secret&timestamp=42&returnTo=%2Feditor%2Flocal#cafe24` },
        history: { state: { idx: 1 }, replaceState: (...args) => {
          replaced = args;
          globalThis.window.location.href = new URL(args[2], globalThis.window.location.href).href;
        } }, sessionStorage: {
          getItem: (key) => stored.get(key) ?? null,
          setItem: (key, value) => { writes.push([key, value]); stored.set(key, value); },
          removeItem: (key) => stored.delete(key),
        } };
      const parent = hooks(), api = { startCafe24Install: () => starts++ };
      const { MallConnections, Cafe24Connection } = await connections(parent, api, {
        useAuth: () => ({ session: userId ? { user: { id: userId } } : null }),
        useQuery: ({ enabled, queryKey }) => ({ isPending: !enabled,
          data: enabled ? queryKey[0] === 'mall-connections' ? { items: [{ mall: 'cafe24', status: 'none' }] } : empty : undefined }),
      });
      const cafe24Node = () => walk(parent.render(MallConnections)).find((node) => node.type === Cafe24Connection);
      const first = cafe24Node(), mounting = hooks(), initial = await connections(mounting, api);
      mounting.render(initial.Cafe24Connection, first.props);
      const cleanups = mounting.flushEffects();
      assert.equal(first.props.unavailable, true);
      assert.doesNotMatch(replaced[2], /mall_id|hmac|timestamp|secret/); assert.match(replaced[2], /returnTo=/);
      assert.equal(replaced[0].idx, 1);
      userId = 'local-user';
      const ready = cafe24Node();
      assert.notEqual(ready.key, first.key, 'auth bootstrap remounts the account-keyed Cafe24Connection');
      assert.equal(ready.props.unavailable, false);
      cleanups.forEach((cleanup) => cleanup?.());
      const remounted = hooks(), fresh = await connections(remounted, api);
      remounted.render(fresh.Cafe24Connection, ready.props); remounted.flushEffects();
      for (let render = 0; render < 2; render++) {
        const row = React.Children.toArray(remounted.render(fresh.Cafe24Connection, ready.props).props.children)[0];
        assert.equal(row.props.children.props.value, expected, 'the app mall id survives the real key change');
        assert.equal(row.props.state, expected ? 'editing' : 'none');
      }
      assert.equal(starts, 0);
      assert.deepEqual(writes, expected ? [['cafe24-app-mall-id', expected]] : []);
      assert.equal(stored.size, 0, 'the ready connection consumes the one-use prefill');
      const later = hooks(), reopened = await connections(later, api);
      later.render(reopened.Cafe24Connection, ready.props); later.flushEffects();
      const row = React.Children.toArray(later.render(reopened.Cafe24Connection, ready.props).props.children)[0];
      assert.equal(row.props.children.props.value, '', 'a later visit must not reuse the consumed prefill');
    }
  } finally { globalThis.window = previous; }
});
