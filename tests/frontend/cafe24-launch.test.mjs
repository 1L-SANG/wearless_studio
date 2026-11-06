import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as launch from '../../src/lib/cafe24Launch.js';

const appUrl = 'https://app.test/settings/malls?auth_config=%7B%22Fabc1%22%3A%22T%22%7D&mall_id=myshop&timestamp=42&user_name=%EB%B6%80+%EC%9A%B4%EC%98%81%EC%9E%90&hmac=signed%2Bvalue%3D';
const pendingUrl = 'https://app.test/settings/malls?mall=cafe24&result=pending&returnTo=%2Feditor%2Fproject';
const tick = () => new Promise(setImmediate);
function browser(t, href = appUrl) {
  const previous = globalThis.window, stored = new Map(), assigned = [], replaced = [];
  const location = { href, search: new URL(href).search, assign: (url) => assigned.push(url), replace: (url) => replaced.push(url) };
  const visit = (url) => { location.href = new URL(url, location.href).href; location.search = new URL(location.href).search; };
  globalThis.window = { location, history: { state: {}, replaceState: (_state, _title, url) => visit(url) },
    sessionStorage: { getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) } };
  t.after(() => { globalThis.window = previous; });
  return { stored, assigned, replaced, location, visit };
}
async function gate(runtime, auth, api, bindings = {}) {
  return componentModule('src/features/settings/Cafe24LaunchGate.jsx', ['Cafe24LaunchGate'], {
    ...runtime, ...launch, api, useAuth: () => auth, useLocation: () => ({ search: window.location.search }),
    RequireAuth: 'auth-guard', Button: 'button', styles: {}, isMockMode: false,
    readPostLogin: () => null, forgetPostLogin() {}, ...bindings,
  });
}
const allNodes = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(allNodes)];

for (const session of [null, { user: { id: 'seller-one' } }]) {
  test(`launch opens consent before auth bootstrap, session ${!!session}`, async (t) => {
    const page = browser(t), calls = [], auth = { session, loading: true, openLogin: () => assert.fail('login preceded consent') };
    const runtime = hooks(), { Cafe24LaunchGate } = await gate(runtime, auth, {
      launchCafe24: async (body) => { calls.push(body); return { startUrl: 'https://myshop.cafe24api.com/api/v2/oauth/authorize' }; },
    });
    runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    await tick();
    assert.deepEqual(calls, [{ query: new URL(appUrl).search.slice(1), returnTo: undefined }]);
    assert.deepEqual(page.assigned, ['https://myshop.cafe24api.com/api/v2/oauth/authorize']);
    assert.equal(page.location.search, ''); assert.equal(launch.readCafe24Launch(), null);
  });
}

test('consent callback opens login then claims once for the current authenticated user', async (t) => {
  const page = browser(t, pendingUrl), opens = [], claims = [], auth = { session: null, loading: false, openLogin: (path) => opens.push(path) };
  const runtime = hooks(), { Cafe24LaunchGate } = await gate(runtime, auth, {
    claimCafe24Pending: async (body) => { claims.push(body); return { connected: true }; },
    launchCafe24: () => assert.fail('callback must not repeat consent'),
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  assert.deepEqual(opens, ['/settings/malls?mall=cafe24&result=pending&returnTo=%2Feditor%2Fproject']);
  assert.equal(claims.length, 0);
  const prompt = runtime.render(Cafe24LaunchGate);
  assert.ok(allNodes(prompt).some((node) => node.props?.children === '로그인 또는 가입'));
  auth.session = { user: { id: 'seller-two' } };
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  await tick();
  assert.deepEqual(claims, [{ expectedUserId: 'seller-two' }]);
  assert.deepEqual(page.replaced, ['/settings/malls?mall=cafe24&result=connected&returnTo=%2Feditor%2Fproject']);
});

test('OAuth login return retains pending installation without retaining the launch query', async (t) => {
  const page = browser(t, pendingUrl), auth = { session: { user: { id: 'seller-one' } }, loading: false }, calls = [];
  const runtime = hooks(), { Cafe24LaunchGate } = await gate(runtime, auth, {
    claimCafe24Pending: async (body) => { calls.push(body); },
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  assert.equal(calls.length, 1); assert.equal(page.stored.size, 0); assert.equal(page.replaced.length, 1);
});

for (const code of ['state_invalid', 'account_closed', 'cafe24_not_allowed']) {
  test(`claim failure ${code} shows a recovery message without reopening consent`, async (t) => {
    const page = browser(t, pendingUrl), runtime = hooks();
    const { Cafe24LaunchGate } = await gate(runtime, { session: { user: { id: 'seller' } }, loading: false }, {
      claimCafe24Pending: async () => { throw { code }; },
    });
    runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
    const alert = allNodes(runtime.render(Cafe24LaunchGate)).find((node) => node.props?.role === 'alert');
    assert.equal(alert.props.children, launch.CAFE24_INSTALL_COPY[code]);
    assert.deepEqual(page.assigned, []); assert.deepEqual(page.replaced, []);
  });
}

test('invalid signature shows recovery and never opens service login', async (t) => {
  browser(t); const runtime = hooks();
  const { Cafe24LaunchGate } = await gate(runtime, { session: null, loading: false, openLogin: () => assert.fail('invalid launch opened login') }, {
    launchCafe24: async () => ({ hmacValid: false }),
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  assert.ok(allNodes(runtime.render(Cafe24LaunchGate)).some((node) => node.props?.role === 'alert'));
});

test('storage blocked still forwards untouched location.search once', async (t) => {
  const page = browser(t); window.sessionStorage = { getItem() { throw Error(); }, setItem() { throw Error(); }, removeItem() { throw Error(); } };
  const runtime = hooks(), calls = [];
  const { Cafe24LaunchGate } = await gate(runtime, { loading: true }, { launchCafe24: async (body) => { calls.push(body); return { startUrl: '/authorize' }; } });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  assert.equal(calls.length, 1); assert.equal(calls[0].query, new URL(appUrl).search.slice(1)); assert.deepEqual(page.assigned, ['/authorize']);
});

test('ordinary settings visits retain existing auth guard', async (t) => {
  browser(t, 'https://app.test/settings/malls'); const runtime = hooks();
  const { Cafe24LaunchGate } = await gate(runtime, { session: null, loading: false, openLogin: () => assert.fail() }, {});
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  assert.equal(runtime.render(Cafe24LaunchGate).type, 'auth-guard');
});

test('a launch without hmac is still verified and rejected before service login', async (t) => {
  browser(t, 'https://app.test/settings/malls?mall_id=myshop&timestamp=42');
  const runtime = hooks(), queries = [];
  const { Cafe24LaunchGate } = await gate(runtime, { loading: false, session: null, openLogin: () => assert.fail('unsigned launch opened login') }, {
    launchCafe24: async ({ query }) => { queries.push(query); return { hmacValid: false }; },
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  assert.deepEqual(queries, ['mall_id=myshop&timestamp=42']);
  assert.ok(allNodes(runtime.render(Cafe24LaunchGate)).some((node) => node.props?.role === 'alert'));
});

test('rate-limited launch tells the seller when to retry without opening login', async (t) => {
  browser(t);
  const runtime = hooks();
  const { Cafe24LaunchGate } = await gate(runtime, { loading: false, session: null,
    openLogin: () => assert.fail('rate-limited launch opened login') }, {
    launchCafe24: async () => { throw Object.assign(new Error(), { code: 'rate_limited' }); },
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  const alert = allNodes(runtime.render(Cafe24LaunchGate)).find(node => node.props?.role === 'alert');
  assert.equal(alert?.props.children, '설치 시도가 많아요. 1시간 뒤에 다시 시도해 주세요');
});

test('reused launch tells the seller to reopen the app in Cafe24', async (t) => {
  const page = browser(t), runtime = hooks();
  const { Cafe24LaunchGate } = await gate(runtime, { loading: false, session: null,
    openLogin: () => assert.fail('reused launch opened login') }, {
    launchCafe24: async () => { throw Object.assign(new Error(), { code: 'launch_reused' }); },
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects(); await tick();
  const alert = allNodes(runtime.render(Cafe24LaunchGate)).find(node => node.props?.role === 'alert');
  assert.equal(alert?.props.children, '이미 쓴 실행 주소예요. 카페24에서 앱을 다시 열어 주세요');
  assert.deepEqual(page.assigned, []); assert.deepEqual(page.replaced, []);
});

for (const code of ['cancelled', 'state_invalid', 'missing_permission', 'not_allowed', 'mall_mismatch', 'token_exchange_failed']) {
  test(`anonymous callback ${code} displays the failure before optional login`, async (t) => {
    browser(t, `https://app.test/settings/malls?mall=cafe24&result=${code}`);
    const runtime = hooks(), opened = [];
    const { Cafe24LaunchGate } = await gate(runtime, { session: null, loading: false, openLogin: (path) => opened.push(path) }, {});
    runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    const tree = runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    const nodes = allNodes(tree), alert = nodes.find((node) => node.props?.role === 'alert');
    assert.equal(alert?.props.children, launch.CAFE24_INSTALL_COPY[code]);
    assert.equal(opened.length, 0, 'a failed callback must not automatically open login');
    const buttons = nodes.filter((node) => node.type === 'button');
    assert.equal(buttons.length, 1);
    assert.equal(buttons[0].props.children, '로그인');
    assert.ok(nodes.some((node) => String(node.props?.className).includes('modal-actions')));
    buttons[0].props.onClick();
    assert.equal(opened.length, 1);
  });
  test(`authenticated callback ${code} retains the settings result handler`, async (t) => {
    browser(t, `https://app.test/settings/malls?mall=cafe24&result=${code}`);
    const runtime = hooks();
    const { Cafe24LaunchGate } = await gate(runtime, { session: { user: { id: 'seller-one' } }, loading: false, openLogin: () => assert.fail('unexpected login') }, {});
    runtime.render(Cafe24LaunchGate); runtime.flushEffects();
    assert.equal(runtime.render(Cafe24LaunchGate).type, 'auth-guard');
  });
}
