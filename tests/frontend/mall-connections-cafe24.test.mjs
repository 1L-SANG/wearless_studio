import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

const Button = ({ children, ...props }) => React.createElement('button', props, children);
const connection = { mall: 'cafe24', status: 'connected', mallId: 'myshop', shopName: '니트 스토어', permissions: ['상품 조회와 등록'] };
const html = renderToStaticMarkup;
const rowOf = (tree) => React.Children.toArray(tree.props.children)[0];
async function load(runtime = hooks(), api = {}, bindings = {}) {
  return componentModule('src/features/settings/MallConnections.jsx',
    ['MallConnections', 'MallConnectionRow', 'Cafe24Connection', 'MallActionDialog', 'MallIdField'], {
      ...runtime, api, Button, Icon: () => null, Modal: ({ children }) => children,
      styles: {}, seoulDate: () => '2026.10.26', ExportMenu: () => null, MallDefaultsChecklist: () => null,
      ...bindings,
    });
}

test('zigzag row keeps existing five states after generalization', async () => {
  const { MallConnectionRow } = await load();
  for (const [state, copy] of [['none', '연결하기'], ['editing', 'Access Key'], ['verifying', '확인 중'], ['connected', '인증키 끝자리'], ['failed', '다시 확인']]) {
    const markup = html(React.createElement(MallConnectionRow, { state, values: {}, connection }));
    assert.match(markup, /지그재그/); assert.ok(markup.includes(copy));
  }
});

test('cafe24 row renders none editing starting connected failed reconnect states', async () => {
  const { MallConnectionRow, MallIdField } = await load();
  for (const [state, copy] of [['none', '연결하기'], ['editing', '몰 아이디'], ['starting', '설치 시작 중'], ['connected', '몰 아이디 myshop'], ['failed', '몰 아이디가 달라요'], ['reconnect_required', '다시 연결']]) {
    const markup = html(React.createElement(MallConnectionRow, { mall: 'cafe24', title: '카페24', state, connection,
      error: state === 'failed' ? '몰 아이디가 달라요' : '', submitLabel: '카페24에서 앱 설치',
      children: React.createElement(MallIdField, { value: 'myshop' }) }));
    assert.ok(markup.includes(copy), state); assert.doesNotMatch(markup, /Access Key/);
  }
});

test('connected cafe24 metadata hides permissions while zigzag keeps them', async () => {
  const { MallConnectionRow } = await load();
  for (const mall of ['cafe24', 'zigzag']) {
    const markup = html(React.createElement(MallConnectionRow, { mall, state: 'connected',
      connection: { ...connection, mall, connectedAt: '2026-10-26', lastRegisteredAt: '2026-10-26', keyHint: '12345678' } }));
    assert.match(markup, /연결한 날 2026.10.26/);
    assert.match(markup, /마지막 등록 2026.10.26/);
    if (mall === 'cafe24') {
      assert.match(markup, /몰 아이디 myshop/);
      assert.doesNotMatch(markup, /권한|상품 조회와 등록|인증키 끝자리/);
    } else {
      assert.match(markup, /권한 상품 조회와 등록/);
      assert.match(markup, /인증키 끝자리 12345678/);
    }
  }
});

test('cafe24 start button disabled while starting', async () => {
  const runtime = hooks(); let resolve, starts = 0, assigned;
  const previous = globalThis.window;
  globalThis.window = { location: { assign: (url) => { assigned = url; } }, sessionStorage: { getItem() {}, setItem() {} } };
  try {
    const { Cafe24Connection, MallConnectionRow } = await load(runtime, { startCafe24Install: () => { starts++; return new Promise((r) => { resolve = r; }); } });
    const props = { connection: { status: 'none' } };
    let row = rowOf(runtime.render(Cafe24Connection, props)); row.props.onConnect();
    row = rowOf(runtime.render(Cafe24Connection, props)); row.props.onChange('myshop');
    row = rowOf(runtime.render(Cafe24Connection, props));
    const first = row.props.onSubmit({ preventDefault() {} });
    await row.props.onSubmit({ preventDefault() {} });
    const starting = rowOf(runtime.render(Cafe24Connection, props));
    assert.equal(starting.props.state, 'starting');
    assert.match(html(React.createElement(MallConnectionRow, starting.props)), /disabled=""[^>]*>[\s\S]*설치 시작 중/);
    assert.equal(starts, 1); resolve({ startUrl: '/install/start' }); await first;
    assert.equal(assigned, '/install/start');
  } finally { globalThis.window = previous; }
});

test('cafe24 input strips url parts and lowercases', async () => {
  const { MallIdField } = await load();
  let value;
  const field = MallIdField({ value: '', onChange: (next) => { value = next; } });
  const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
  const input = walk(field).find((node) => node.type === 'input');
  for (const raw of ['  MyShop  ', 'HTTPS://MyShop.cafe24.com/admin?x=1', 'myShop.cafe24api.com/#x']) {
    input.props.onChange({ target: { value: raw } }); assert.equal(value, 'myshop');
  }
  assert.equal(input.props.autoCapitalize, 'none');
});

test('reconnect prefills mall id from response', async () => {
  const runtime = hooks(); const { Cafe24Connection } = await load(runtime);
  const props = { connection: { ...connection, status: 'reconnect_required' } };
  let row = rowOf(runtime.render(Cafe24Connection, props));
  assert.equal(row.props.state, 'reconnect_required'); row.props.onConnect();
  row = rowOf(runtime.render(Cafe24Connection, props));
  assert.equal(row.props.state, 'editing'); assert.equal(row.props.children.props.value, 'myshop');
});

test('callback result query shows seller copy and clears query', async () => {
  const previous = globalThis.window; let replaced, changed = 0;
  globalThis.window = { location: { href: 'https://app.test/settings/malls?mall=cafe24&result=mall_mismatch&keep=yes#row' },
    history: { state: { idx: 1 }, replaceState: (...args) => { replaced = args; } },
    sessionStorage: { getItem: () => 'myshop', removeItem() {} } };
  try {
    const runtime = hooks(); const { Cafe24Connection } = await load(runtime);
    const props = { connection: { status: 'none' }, onChanged: () => changed++ };
    runtime.render(Cafe24Connection, props); runtime.flushEffects();
    const row = rowOf(runtime.render(Cafe24Connection, props));
    assert.equal(row.props.state, 'failed');
    assert.equal(row.props.error, '입력한 몰 아이디와 로그인한 몰이 달라요. 몰 아이디를 확인해 주세요');
    assert.equal(row.props.children.props.value, 'myshop');
    assert.equal(replaced[0].idx, 1); assert.match(replaced[2], /keep=yes/); assert.doesNotMatch(replaced[2], /result=|mall=/);
    globalThis.window.location.href = 'https://app.test/settings/malls?mall=cafe24&result=connected';
    const connectedRuntime = hooks(); const module = await load(connectedRuntime);
    connectedRuntime.render(module.Cafe24Connection, props); connectedRuntime.flushEffects();
    assert.equal(rowOf(connectedRuntime.render(module.Cafe24Connection, props)).props.state, 'none'); assert.equal(changed, 1);
    globalThis.window.location.href = 'https://app.test/settings/malls?mall=cafe24&result=state_invalid';
    const expiredRuntime = hooks(); const expired = await load(expiredRuntime);
    expiredRuntime.render(expired.Cafe24Connection, props); expiredRuntime.flushEffects();
    const expiredRow = rowOf(expiredRuntime.render(expired.Cafe24Connection, props));
    assert.equal(expiredRow.props.state, 'failed');
    assert.equal(expiredRow.props.error, '설치 확인이 만료됐어요. 같은 브라우저에서 다시 시작해 주세요');
  } finally { globalThis.window = previous; }
});

test('successful install callback follows refreshed connection status', async () => {
  const previous = globalThis.window; let changed = 0;
  globalThis.window = { location: { href: 'https://app.test/settings/malls?mall=cafe24&result=connected' },
    history: { replaceState() {} }, sessionStorage: { getItem: () => 'myshop' } };
  try {
    const runtime = hooks(), { Cafe24Connection, MallConnectionRow } = await load(runtime);
    const props = { connection, onChanged: () => changed++ };
    runtime.render(Cafe24Connection, props); runtime.flushEffects();
    assert.equal(changed, 1);
    assert.equal(rowOf(runtime.render(Cafe24Connection, props)).props.state, 'connected');
    for (const status of ['none', 'reconnect_required']) {
      props.connection = { ...connection, status };
      const row = rowOf(runtime.render(Cafe24Connection, props));
      assert.equal(row.props.state, status);
      assert.ok(html(React.createElement(MallConnectionRow, row.props)).includes(status === 'none' ? '연결하기' : '다시 연결'));
    }
  } finally { globalThis.window = previous; }
});

test('account switch gives Cafe24 a fresh form and ignores the old install response', async () => {
  const previous = globalThis.window;
  let userId = 'account-one', finishInstall, assigned = 0;
  globalThis.window = { location: { href: 'https://app.test/settings/malls', assign: () => assigned++ },
    sessionStorage: { setItem() {} } };
  try {
    const parent = hooks();
    const { MallConnections, Cafe24Connection } = await load(parent, {}, {
      useAuth: () => ({ session: { user: { id: userId } } }), useLocation: () => ({ search: '' }),
      useQueryClient: () => ({}), useQuery: () => ({ data: { items: [{ mall: 'cafe24', status: 'none' }] } }),
    });
    const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
    const first = walk(parent.render(MallConnections)).find((node) => node.type === Cafe24Connection);
    const oldRuntime = hooks(), old = await load(oldRuntime, {
      startCafe24Install: () => new Promise((resolve) => { finishInstall = resolve; }),
    });
    let oldRow = rowOf(oldRuntime.render(old.Cafe24Connection, first.props));
    const cleanups = oldRuntime.flushEffects();
    oldRow.props.onConnect(); oldRow = rowOf(oldRuntime.render(old.Cafe24Connection, first.props));
    oldRow.props.onChange('old-account-shop'); oldRow = rowOf(oldRuntime.render(old.Cafe24Connection, first.props));
    const pending = oldRow.props.onSubmit({ preventDefault() {} });
    userId = 'account-two';
    const next = walk(parent.render(MallConnections)).find((node) => node.type === Cafe24Connection);
    assert.notEqual(next.key, first.key);
    assert.notEqual(next.props.storageKey, first.props.storageKey);
    cleanups.forEach((cleanup) => cleanup?.());
    const freshRuntime = hooks(), fresh = await load(freshRuntime);
    const freshRow = rowOf(freshRuntime.render(fresh.Cafe24Connection, next.props));
    assert.equal(freshRow.props.state, 'none'); assert.equal(freshRow.props.children.props.value, '');
    finishInstall({ startUrl: '/old-account-install' }); await pending;
    assert.equal(assigned, 0);
  } finally { globalThis.window = previous; }
});

test('revoke dialog copy for cafe24', async () => {
  const { MallActionDialog } = await load();
  const markup = html(React.createElement(MallActionDialog, { mall: 'cafe24', kind: 'revoke', anchorRef: {} }));
  assert.match(markup, /카페24 연결을 해제할까요\?/);
  assert.match(markup, /해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요<\/p>/);
  assert.doesNotMatch(markup, /자사몰과의 연결이 끊겨요/);
  assert.match(markup, /등록한 상품과 게시한 이미지는 그대로예요/); assert.doesNotMatch(markup, /기본 설정은 남아요/);
  const zigzag = html(React.createElement(MallActionDialog, { kind: 'revoke', anchorRef: {} }));
  assert.match(zigzag, /스토어와의 연결이 끊겨요. 해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요. 기본 설정은 남아요/);
});
