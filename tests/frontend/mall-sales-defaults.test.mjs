import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import { readFileSync } from 'node:fs';
import * as logic from '../../src/lib/mallDefaults.js';

const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const field = (tree, name) => walk(tree).find((n) => n.props?.name === name);
const empty = { revision: 0, shipping: {}, returns: {}, disclosure: {}, taxType: 'TAX', productOrigin: 'domestic', shippingDays: 2, listingMode: 'HIDDEN', missingItems: ['shipping', 'returns', 'disclosure'] };
async function card(runtime = hooks(), api = {}) {
  return componentModule('src/features/settings/MallDefaultsChecklist.jsx', ['MallDefaultsForm'], {
    ...runtime, ...logic, api, useAuth: () => ({ session: { user: { id: 'local' } } }), useQuery: () => ({ data: [{ id: 'mock-shipping', name: '창고', address: '서울' }] }), styles: {}, Button: 'button', Icon: () => null,
  });
}
async function connections(runtime = hooks(), api = {}, bindings = {}) {
  return componentModule('src/features/settings/MallConnections.jsx', ['MallConnections', 'MallSettings', 'Cafe24Connection'], {
    ...runtime, ...logic, api, useAuth: () => ({ session: { user: { id: 'local' } } }), useQuery: () => ({ data: [{ id: 'mock-shipping', name: '창고', address: '서울' }] }), styles: {}, Button: 'button', Icon: () => null, Modal: ({ children }) => children,
    ExportMenu: () => null, Link: 'a', MallDefaultsForm: () => null, seoulDate: () => '',
    useAuth: () => ({ session: { user: { id: 'local-user' } } }), useLocation: () => ({ search: '' }),
    useQueryClient: () => ({ setQueryData() {}, invalidateQueries() {} }),
    useQuery: () => ({ data: [{ id: 'mock-shipping', name: '성수 창고', address: '성수동' }], isPending: false }), ...bindings,
  });
}

test('sales defaults card shows split fees shipping days and hidden default', async () => {
  const runtime = hooks(), calls = [], { MallDefaultsForm } = await card(runtime, { saveMallDefaults: async (mall, input) => { calls.push(input); return { ...empty, revision: 1 }; } });
  const props = { mall: 'zigzag', defaults: empty };
  let tree = runtime.render(MallDefaultsForm, props);
  const html = renderToStaticMarkup(tree);
  for (const text of ['판매 정보', '무료', '고정', '조건부 무료', '제주 추가', '도서산간 추가', '전체 반품', '부분 반품', '교환', '발송 소요일', '품질보증기준', '반품지']) assert.ok(html.includes(text), text);
  assert.equal(field(tree, 'shippingDays').props.value, 2);
  assert.equal(field(tree, 'listingMode').props.value, 'HIDDEN');
  field(tree, 'feeType').props.onChange('CONDITIONAL_FREE');
  tree = runtime.render(MallDefaultsForm, props);
  assert.ok(field(tree, 'conditionalAmount'));
  field(tree, 'conditionalAmount').props.onChange('50000');
  tree = runtime.render(MallDefaultsForm, props);
  field(tree, 'feeType').props.onChange('FREE');
  tree = runtime.render(MallDefaultsForm, props);
  assert.equal(field(tree, 'conditionalAmount'), undefined);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0].baseRevision, 0); assert.deepEqual(calls[0].shipping, { feeType: 'FREE', baseFee: 0 });
  assert.equal(calls[0].listingMode, 'HIDDEN'); assert.equal(calls[0].disclosure.warrantyText, '');
  assert.deepEqual(calls[0].addresses, { returnAddressId: '' });
});

test('each mall form renders only its own fields and saves one return address', async () => {
  const runtime = hooks(), calls = [], { MallDefaultsForm } = await card(runtime, { saveMallDefaults: async (...args) => { calls.push(args); return { revision: 3 }; } });
  const props = { mall: 'zigzag', defaults: { revision: 2, addresses: {} } };
  let tree = runtime.render(MallDefaultsForm, props);
  assert.equal(field(tree, 'shippingAddressId'), undefined);
  field(tree, 'returnAddressId').props.onChange({ target: { value: 'mock-shipping' } });
  tree = runtime.render(MallDefaultsForm, props);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0][0], 'zigzag'); assert.deepEqual(calls[0][1].addresses, { returnAddressId: 'mock-shipping' });
  const cafeRuntime = hooks(), cafe = await card(cafeRuntime);
  const cafeTree = cafeRuntime.render(cafe.MallDefaultsForm, { mall: 'cafe24', defaults: {} });
  const html = renderToStaticMarkup(cafeTree);
  for (const name of ['shippingDays', 'feeType', 'manufacturer', 'productOrigin', 'returnAddressId']) assert.equal(field(cafeTree, name), undefined, name);
  for (const name of ['country', 'taxType', 'listingMode']) assert.ok(field(cafeTree, name), name);
  assert.doesNotMatch(html, /모든 몰|지그재그|카페24는 몰 설정/);
  assert.deepEqual(logic.mallDefaultsPatch('cafe24', logic.mallDefaultsValues('cafe24', {})), { disclosure: { country: '' }, taxType: 'TAX', listingMode: 'HIDDEN' });
});

test('defaults conflict keeps entered values and reloads current revision', async () => {
  const runtime = hooks(), calls = []; let loads = 0;
  const { MallDefaultsForm } = await card(runtime, {
    saveMallDefaults: async (mall, input) => { calls.push(input); if (calls.length === 1) throw Object.assign(new Error('충돌'), { code: 'defaults_conflict', status: 409 }); return { ...empty, revision: 9 }; },
    getMallDefaults: async () => { loads++; return { ...empty, revision: 8, disclosure: { manufacturer: '다른 창' } }; },
  });
  const props = { mall: 'zigzag', defaults: { ...empty, revision: 3 } };
  let tree = runtime.render(MallDefaultsForm, props);
  field(tree, 'manufacturer').props.onChange('내 입력'); tree = runtime.render(MallDefaultsForm, props);
  await tree.props.onSubmit({ preventDefault() {} }); tree = runtime.render(MallDefaultsForm, props);
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

test('list links connected malls to their own form and preserves editor return state', async () => {
  const runtime = hooks(), returnTo = '/editor/local?mode=edit';
  const { MallConnectionRow, MallConnections } = await componentModule('src/features/settings/MallConnections.jsx', ['MallConnectionRow', 'MallConnections'], {
    ...runtime, styles: {}, api: {}, Button: 'button', Icon: () => null, ExportMenu: () => null, Link: 'a',
    useAuth: () => ({ session: { user: { id: 'local' } } }), useLocation: () => ({ state: { returnTo }, search: '' }),
    useQueryClient: () => ({ invalidateQueries() {} }), useQuery: () => ({ data: { items: [] } }),
  });
  for (const mall of ['cafe24', 'zigzag']) {
    const tree = MallConnectionRow({ mall, state: 'connected', returnTo, connection: { defaultsMissingItems: ['disclosure.country'] } });
    const link = walk(tree).find((node) => node.type === 'a');
    assert.equal(link.props.to, `/settings/malls/${mall}`); assert.deepEqual(link.props.state, { returnTo });
    assert.match(renderToStaticMarkup(tree), /판매 정보 빈칸 1개/);
    const disconnected = MallConnectionRow({ mall, state: 'none' });
    assert.doesNotMatch(renderToStaticMarkup(disconnected), /판매 정보/);
  }
  const list = runtime.render(MallConnections);
  assert.doesNotMatch(renderToStaticMarkup(list), /판매 기본 정보|<input[^>]*name="(shippingDays|country)"/);
  const app = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
  assert.match(app, /path="settings\/malls\/:mall" element={<MallSettings \/>}/);
});

test('detail redirects unsupported or disconnected malls and keeps returnTo through navigation', async () => {
  const returnTo = '/editor/local';
  for (const [mall, status] of [['unknown', 'connected'], ['ably', 'connected'], ['cafe24', 'none'], ['zigzag', 'connected']]) {
    const runtime = hooks();
    const { MallSettings, ZigzagConnection, MallDefaultsForm } = await componentModule('src/features/settings/MallConnections.jsx', ['MallSettings', 'ZigzagConnection', 'MallDefaultsForm'], {
      ...runtime, styles: {}, api: {}, Button: 'button', Icon: () => null, ExportMenu: () => null, Link: 'a', Navigate: 'redirect', MallDefaultsForm: 'mall-form',
      useAuth: () => ({ session: { user: { id: 'local' } } }), useLocation: () => ({ state: { returnTo } }), useParams: () => ({ mall }),
      useQueryClient: () => ({ invalidateQueries() {}, setQueryData() {} }),
      useQuery: ({ queryKey }) => ({ data: queryKey[0] === 'mall-connections' ? { items: [{ mall, status, shopName: '내 상점' }] } : { revision: 2 } }),
    });
    const tree = runtime.render(MallSettings);
    if (mall !== 'zigzag') {
      assert.equal(tree.type, 'redirect'); assert.equal(tree.props.to, '/settings/malls'); assert.deepEqual(tree.props.state, { returnTo });
    } else {
      const back = walk(tree).find((node) => node.type === 'a' && node.props.to === '/settings/malls');
      assert.deepEqual(back.props.state, { returnTo });
      assert.ok(walk(tree).find((node) => node.type === ZigzagConnection && node.props.detail));
      assert.equal(walk(tree).find((node) => node.type === MallDefaultsForm).props.mall, 'zigzag');
    }
  }
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
