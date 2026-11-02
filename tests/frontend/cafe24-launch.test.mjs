import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import React from 'react';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as launch from '../../src/lib/cafe24Launch.js';

const appUrl = 'https://app.test/settings/malls?mall_id=myshop&timestamp=42&hmac=signed%2Bvalue%3D';
const rowOf = (tree) => React.Children.toArray(tree.props.children)[0];
const tick = () => new Promise(setImmediate);
const props = { userId: 'seller-one', storageKey: 'install:seller-one', connection: { status: 'none' } };

function browser(t, href = appUrl) {
  const previous = globalThis.window, stored = new Map(), assigned = [];
  const location = { href, search: new URL(href).search, assign: (url) => assigned.push(url) };
  const visit = (url) => { location.href = new URL(url, location.href).href; location.search = new URL(location.href).search; };
  globalThis.window = { location, history: { state: { idx: 1 }, replaceState: (_state, _title, url) => visit(url) },
    sessionStorage: { getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) } };
  t.after(() => { globalThis.window = previous; });
  return { stored, assigned, location, visit };
}

async function connection(runtime, api) {
  return componentModule('src/features/settings/MallConnections.jsx', ['Cafe24Connection'], {
    ...runtime, ...launch, api, styles: {},
  });
}

async function gate(runtime, auth, bindings = {}) {
  return componentModule('src/features/settings/Cafe24LaunchGate.jsx', ['Cafe24LaunchGate'], {
    ...runtime, ...launch, useAuth: () => auth, useLocation: () => ({ search: window.location.search }),
    RequireAuth: 'auth-guard', Button: 'button', styles: {}, isMockMode: false,
    readPostLogin: () => null, forgetPostLogin() {}, ...bindings,
  });
}

test('authenticated signed launch redirects without submitting and cannot repeat after reload or back', async (t) => {
  const page = browser(t), calls = [];
  const runtime = hooks(), { Cafe24Connection } = await connection(runtime, {
    launchCafe24: async (body) => { calls.push(body); return { startUrl: 'https://api.test/install/start' }; },
    startCafe24Install: () => assert.fail('automatic launch must use server signature verification'),
  });
  runtime.render(Cafe24Connection, { ...props, unavailable: true }); runtime.flushEffects();
  assert.equal(calls.length, 0, 'wait for connection loading');
  assert.equal(page.location.search, '');
  runtime.render(Cafe24Connection, props); runtime.flushEffects();
  assert.equal(rowOf(runtime.render(Cafe24Connection, props)).props.state, 'starting');
  await tick();
  assert.deepEqual(page.assigned, ['https://api.test/install/start']);
  assert.deepEqual(calls, [{ query: new URL(appUrl).search.slice(1), returnTo: undefined, expectedUserId: 'seller-one' }]);
  assert.equal(launch.readCafe24Launch(), null);
  for (let visit = 0; visit < 2; visit++) {
    const fresh = hooks(), module = await connection(fresh, { launchCafe24: () => assert.fail('consumed launch repeated') });
    fresh.render(module.Cafe24Connection, props); fresh.flushEffects(); await tick();
  }
});

test('anonymous app launch opens existing login and resumes in the same tab after OAuth return', async (t) => {
  const page = browser(t), opens = [], auth = { session: null, loading: true, openLogin: (path) => opens.push(path) };
  const first = hooks(), { Cafe24LaunchGate } = await gate(first, auth);
  first.render(Cafe24LaunchGate); first.flushEffects();
  assert.equal(opens.length, 0, 'wait for authentication bootstrap');
  auth.loading = false;
  first.render(Cafe24LaunchGate); first.flushEffects();
  first.render(Cafe24LaunchGate); first.flushEffects();
  assert.deepEqual(opens, ['/settings/malls']);
  assert.equal(page.location.search, '');
  assert.equal(launch.readCafe24Launch().mallId, 'myshop');
  // OAuth's existing RootRedirect returns to the path supplied to openLogin.
  page.visit(opens[0]);
  auth.session = { user: { id: props.userId } };
  const returned = hooks(), afterLogin = await gate(returned, auth);
  returned.render(afterLogin.Cafe24LaunchGate); returned.flushEffects();
  assert.equal(returned.render(afterLogin.Cafe24LaunchGate).type, 'auth-guard');
  const ready = hooks(), { Cafe24Connection } = await connection(ready, { launchCafe24: async () => ({ startUrl: '/install/start' }) });
  ready.render(Cafe24Connection, props); ready.flushEffects(); await tick();
  assert.deepEqual(page.assigned, ['/install/start']);
  assert.equal(launch.readCafe24Launch(), null);
});

test('password login preserves editor returnTo and clears only its own login continuation', async (t) => {
  const page = browser(t, `${appUrl}&returnTo=%2Feditor%2Fproject`), opens = [];
  let continuation = null;
  const auth = { session: null, loading: false, openLogin: (path) => { opens.push(path); continuation = path; } };
  const runtime = hooks(), { Cafe24LaunchGate } = await gate(runtime, auth, {
    readPostLogin: () => continuation, forgetPostLogin: () => { continuation = null; },
  });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  assert.equal(opens[0], '/settings/malls?returnTo=%2Feditor%2Fproject');
  assert.match(page.location.search, /returnTo=/);
  auth.session = { user: { id: props.userId } };
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  assert.equal(continuation, null);
  assert.equal(runtime.render(Cafe24LaunchGate).type, 'auth-guard');
});

test('plain malls visits retain the original auth guard without opening login', async (t) => {
  browser(t, 'https://app.test/settings/malls');
  const runtime = hooks(), { Cafe24LaunchGate } = await gate(runtime, { loading: false, session: null, openLogin: () => assert.fail('plain route changed') });
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  runtime.render(Cafe24LaunchGate); runtime.flushEffects();
  assert.equal(runtime.render(Cafe24LaunchGate).type, 'auth-guard');
  const source = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
  assert.match(source, /<Route element=\{<Cafe24LaunchGate \/>\}>\s*<Route path="settings\/malls"/);
});

test('mobile OAuth return keeps only a live Cafe24 continuation and leaves the input policy intact', async (t) => {
  browser(t);
  launch.captureCafe24Launch();
  const source = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
  const root = source.slice(source.indexOf('function RootRedirect()'), source.indexOf('/* 모델 섹션'));
  const { code } = await transformWithEsbuild(root, 'RootRedirect.jsx', { jsx: 'transform', define: { 'import.meta.env.VITE_API_MODE': '"http"' } });
  for (const [target, pending, expected] of [
    ['/settings/malls', true, '/settings/malls'], ['/settings/malls?returnTo=%2Feditor%2Fp', true, '/settings/malls?returnTo=%2Feditor%2Fp'],
    ['/settings/malls', false, '/create/input'], ['/library', true, '/create/input'], ['/create/storyboard', true, '/create/input'],
  ]) {
    const runtime = hooks(), bindings = { ...runtime, React, useAuth: () => ({ session: { user: { id: props.userId } }, loading: false }),
      isMobileDevice: () => true, readPostLogin: () => target, forgetPostLogin() {}, sellerRootTarget: () => '/create/input',
      readCafe24Launch: () => pending ? launch.readCafe24Launch() : null, Navigate: 'navigate' };
    const RootRedirect = new Function(...Object.keys(bindings), `${code}; return RootRedirect;`)(...Object.values(bindings));
    runtime.render(RootRedirect); runtime.flushEffects();
    assert.equal(runtime.render(RootRedirect).props.to, expected);
  }
});

test('missing or rejected signature only prefills and a connected launch keeps the list', async (t) => {
  const page = browser(t);
  for (const outcome of ['unsigned', 'invalid', 'connected']) {
    page.stored.clear(); page.visit(outcome === 'unsigned' ? appUrl.split('&hmac=')[0] : appUrl);
    let starts = 0;
    const runtime = hooks(), { Cafe24Connection } = await connection(runtime, {
      launchCafe24: async () => { starts++; return outcome === 'connected' ? { alreadyConnected: true } : { hmacValid: false }; },
    });
    const input = outcome === 'connected' ? { ...props, connection: { status: 'connected', mallId: 'myshop' } } : props;
    runtime.render(Cafe24Connection, input); runtime.flushEffects(); await tick();
    const row = rowOf(runtime.render(Cafe24Connection, input));
    assert.equal(row.props.children.props.value, 'myshop');
    assert.equal(row.props.state, outcome === 'connected' ? 'connected' : 'editing');
    assert.equal(starts, outcome === 'unsigned' ? 0 : 1);
    assert.equal(launch.readCafe24Launch(), null);
  }
  assert.deepEqual(page.assigned, []);
});

test('pilot rejection uses existing copy and does not retry on remount', async (t) => {
  browser(t);
  const runtime = hooks(), { Cafe24Connection } = await connection(runtime, { launchCafe24: async () => { throw { code: 'cafe24_not_allowed' }; } });
  runtime.render(Cafe24Connection, props); runtime.flushEffects(); await tick();
  const row = rowOf(runtime.render(Cafe24Connection, props));
  assert.equal(row.props.state, 'failed');
  assert.equal(row.props.error, '지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요');
  const fresh = hooks(), next = await connection(fresh, { launchCafe24: () => assert.fail('failed launch retried') });
  fresh.render(next.Cafe24Connection, props); fresh.flushEffects();
});

test('pending launches expire after ten minutes and blocked storage stays manual', async (t) => {
  const page = browser(t);
  t.mock.timers.enable({ apis: ['Date'], now: Date.now() });
  launch.captureCafe24Launch();
  t.mock.timers.tick(10 * 60 * 1000 + 1);
  assert.equal(launch.consumeCafe24Launch(), null);
  assert.equal(page.stored.size, 0);
  page.visit(appUrl);
  Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('blocked'); } });
  const runtime = hooks(), { Cafe24Connection } = await connection(runtime, { launchCafe24: () => assert.fail('unrecoverable launch started') });
  runtime.render(Cafe24Connection, props); runtime.flushEffects();
  const row = rowOf(runtime.render(Cafe24Connection, props));
  assert.equal(row.props.children.props.value, 'myshop');
  assert.equal(row.props.state, 'editing');
  assert.equal(page.location.search, '');
});

test('StrictMode effect replay starts once and account unmount ignores the late response', async (t) => {
  const page = browser(t), effects = [], runtime = hooks();
  let calls = 0, finish;
  const { Cafe24Connection } = await connection({ ...runtime, useEffect: (fn, deps) => { effects.push(fn); runtime.useEffect(fn, deps); } }, {
    launchCafe24: () => { calls++; return new Promise((resolve) => { finish = resolve; }); },
  });
  runtime.render(Cafe24Connection, props);
  const cleanups = runtime.flushEffects();
  cleanups.forEach((cleanup) => cleanup?.());
  const replay = effects.map((effect) => effect());
  assert.equal(calls, 1);
  replay.forEach((cleanup) => cleanup?.());
  finish({ startUrl: '/old-account/start' }); await tick();
  assert.deepEqual(page.assigned, []);
});
