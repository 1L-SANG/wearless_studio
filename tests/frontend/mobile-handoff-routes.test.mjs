import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { Routes, Route, Outlet, Navigate, useLocation } from 'react-router-dom';
import { transformWithEsbuild } from 'vite';
import { useDocumentEntry } from '../../src/features/shell/useDocumentEntry.js';

const source = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
const guardStart = source.indexOf('function MobileHandoffGuard()');
const guardSource = guardStart < 0 ? '' : source.slice(guardStart, source.indexOf('function RequireProject()'));
const appSource = source.slice(source.indexOf('export default function App()')).replace('export default ', '');
const pageSource = ['HangTag.jsx', 'AddressCopyChip.jsx', 'PcOnlyPage.jsx'].map((file) =>
  readFileSync(new URL(`../../src/features/shell/${file}`, import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const { code } = await transformWithEsbuild(`${pageSource}\n${guardSource}\n${appSource}`, 'MobileRoutes.jsx', { jsx: 'transform' });

function harness(mobile, pathname, entry = null) {
  const calls = [];
  const effects = [];
  const layoutEffects = [];
  const attributes = new Map(entry ? [['data-entry', entry]] : []);
  const timers = new Map();
  const listeners = new Map();
  let nextTimer = 0;
  let bootRemoved = false;
  const document = { title: 'Wearless Studio', documentElement: {
    getAttribute: (key) => attributes.get(key), removeAttribute: (key) => attributes.delete(key),
  } };
  const href = `https://preview.wearless.test${pathname}?view=saved#page`;
  const bindings = {
    React, Routes, Route, Outlet, Navigate, useLocation, useDocumentEntry, Suspense: React.Suspense,
    useEffect: (effect) => effects.push(effect), document,
    useLayoutEffect: (effect) => layoutEffects.push(effect),
    removeSellerBootBackground: () => { bootRemoved = true; },
    s: new Proxy({}, { get: (_, key) => key }), p: new Proxy({}, { get: (_, key) => key }),
    draftSlot: { remove() { calls.push(['remove']); }, removeForNewFlow() { calls.push(['remove']); } },
    IS_FACEMARKET: false, isSupabaseConfigured: true, domainRouteRedirect: () => null,
    isMobileDevice: () => mobile,
    useNavigate: () => (path) => calls.push(['navigate', path]),
    useToast: () => ({ push: (message) => calls.push(['toast', message]) }),
    copyHandoffAddress: async (pushToast) => { pushToast('주소를 복사했어요'); },
    Icon: ({ name, size }) => React.createElement('svg', { 'data-icon': name, width: size }),
    window: {
      location: { href, origin: 'https://preview.wearless.test', host: 'preview.wearless.test' },
      setTimeout: (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
      clearTimeout: (id) => timers.delete(id),
      addEventListener: (name, callback) => listeners.set(name, callback),
      removeEventListener: (name, callback) => { if (listeners.get(name) === callback) listeners.delete(name); },
    },
    Button: ({ children, ...props }) => React.createElement('button', props, children),
    WEARLESS_LEGAL_URLS: {},
  };
  for (const name of ['ResumeTracker', 'SignupCompletion', 'LoginQueryGate']) bindings[name] = () => null;
  for (const name of ['ChromeLayout', 'RequireAuth', 'RequireProject', 'RequireEditorProject']) {
    bindings[name] = () => { calls.push(['mount', name]); return React.createElement(Outlet); };
  }
  for (const name of ['RootRedirect', 'LegalRedirect', 'Pricing', 'CreditsHistory', 'PaymentSuccess', 'PaymentFail',
    'SubscriptionManage', 'SubscriptionSuccess', 'SubscriptionFail', 'Library', 'ProductInputRoute', 'Mannequin',
    'Storyboard', 'Generating', 'LazyEditor', 'PublicVerify', 'PublicVerifyPublication', 'KakaoCallback', 'NotFound']) {
    bindings[name] = () => { calls.push(['mount', name]); return React.createElement('div', null, name); };
  }
  const components = new Function(...Object.keys(bindings), `${code}; return { App, guard: typeof MobileHandoffGuard === 'function' ? MobileHandoffGuard : null };`)(...Object.values(bindings));
  const html = renderToString(React.createElement(StaticRouter, { location: pathname }, React.createElement(components.App)));
  return { calls, html, href, effects, layoutEffects, document, attributes, timers, listeners,
    get bootRemoved() { return bootRemoved; }, page() {
    const directBindings = { ...bindings, useLocation: () => ({ pathname }) };
    return new Function(...Object.keys(directBindings), `${code}; return PcOnlyPage;`)(...Object.values(directBindings))();
  } };
}

function find(tree, text) {
  if (!tree || typeof tree !== 'object') return null;
  if (typeof tree.type === 'function') return find(tree.type(tree.props), text);
  if (tree.props?.['aria-label'] === text) return tree;
  const children = React.Children.toArray(tree.props?.children);
  if (children.includes(text)) return tree;
  return children.map((child) => find(child, text)).find(Boolean);
}

for (const [path, component] of [
  ['/library', 'Library'],
  ['/create/storyboard', 'Storyboard'], ['/create/mannequin', 'Mannequin'],
  ['/create/generating', 'Generating'], ['/editor/project-id', 'LazyEditor'],
]) {
  test(`mobile ${path} prevents protected mounts and offers non-destructive navigation and address copying`, async () => {
    const h = harness(true, path);
    assert.ok(h.html.includes('이 단계는 PC에서 이어서 만들어요'));
    for (const copy of ['PC ONLY', 'preview.wearless.test', 'CONTINUE ON PC', 'WEARLESS', 'PC에서 같은 계정으로 로그인하면 바로 이어져요.']) assert.ok(h.html.includes(copy), copy);
    assert.match(h.html, /aria-label="주소 복사"/);
    assert.ok(!h.html.includes('PC로 링크 보내기'));
    assert.equal((h.html.match(/<button /g) || []).length, 2);
    assert.deepEqual(h.calls.filter((call) => call[0] === 'mount' && call[1] !== 'ChromeLayout'), []);
    const guard = h.page();
    await find(guard, '주소 복사').props.onClick();
    const primary = find(guard, '사진 업로드 화면으로');
    assert.equal(primary.props.className, 'btn btn-primary btn-block');
    const tag = find(guard, 'CONTINUE ON PC 라고 적힌 옷 태그');
    assert.equal(tag.props.className, 'art');
    assert.equal(tag.props.viewBox, '0 0 280 230');
    const sway = React.Children.toArray(tag.props.children)[0];
    assert.equal(sway.props.className, 'sway');
    const care = React.Children.toArray(sway.props.children).find((child) => child.props?.className === 'stroke care');
    assert.equal(React.Children.count(care.props.children), 6);
    assert.equal(React.Children.toArray(sway.props.children).filter((child) => child.type === 'rect' && child.props.y === '198').length, 12);
    primary.props.onClick();
    assert.deepEqual(h.calls.filter((call) => call[0] !== 'mount'), [
      ['toast', '주소를 복사했어요'],
      ['navigate', '/create/input'],
    ]);
  });
  test(`desktop ${path} retains the original route element`, () => {
    const h = harness(false, path);
    assert.ok(h.html.includes(component));
    assert.ok(h.calls.some((call) => call[1] === component));
    assert.ok(!h.html.includes('이 단계는 PC에서 이어서 만들어요'));
  });
}
for (const [path, component] of [
  ['/pricing', 'Pricing'], ['/credits/history', 'CreditsHistory'],
  ['/payments/success', 'PaymentSuccess'], ['/payments/fail', 'PaymentFail'],
  ['/subscription', 'SubscriptionManage'], ['/subscription/success', 'SubscriptionSuccess'],
  ['/subscription/fail', 'SubscriptionFail'],
]) {
  test(`mobile keeps ${path} reachable`, () => {
    assert.ok(harness(true, path).html.includes(component));
  });
}


test('PC-only page sets and restores the document title on mount and unmount', () => {
  const h = harness(true, '/create/storyboard');
  const cleanup = h.effects[0]();
  assert.equal(h.document.title, 'PC에서 이어서 만들어요');
  cleanup();
  assert.equal(h.document.title, 'Wearless Studio');
  assert.equal(harness(false, '/create/storyboard').effects.length, 0);
});

test('크롬 밖 공개 확인은 2초 안전장치로 부트 배경을 제거한다(첫 렌더에서 바로 지우면 빛이 빈다)', () => {
  const h = harness(false, '/verify/license');
  assert.ok(h.html.includes('PublicVerify'));
  const cleanup = h.layoutEffects[0]();
  assert.equal(h.bootRemoved, false);
  const timers = [...h.timers.values()];
  assert.deepEqual(timers.map((t) => t.delay), [2000]);
  timers[0].callback();
  assert.equal(h.bootRemoved, true);
  cleanup();
  assert.equal(h.timers.size, 0);
});

test('랜딩 도착 표식은 600ms에 끝나고, 부트 사본은 ChromeLayout 또는 2초 안전장치가 지운다', () => {
  const h = harness(false, '/pricing', 'landing');
  const cleanup = h.layoutEffects[0]();
  assert.equal(h.bootRemoved, false);
  assert.equal(h.attributes.get('data-entry'), 'landing');
  const timers = [...h.timers.values()];
  assert.deepEqual(timers.map((t) => t.delay).sort((a, b) => a - b), [600, 2000]);
  timers.find((t) => t.delay === 600).callback();
  assert.equal(h.attributes.has('data-entry'), false);
  timers.find((t) => t.delay === 2000).callback();
  assert.equal(h.bootRemoved, true);
  cleanup();
  assert.equal(h.timers.size, 0);
  assert.equal(h.listeners.size, 0);
});

test('StrictMode 재설정은 도착을 취소하지 않고 bfcache 복귀는 표식을 끝낸다', () => {
  const h = harness(false, '/pricing', 'landing');
  h.layoutEffects[0]()();
  assert.equal(h.attributes.get('data-entry'), 'landing');
  assert.equal(h.timers.size, 0);
  const cleanup = h.layoutEffects[0]();
  assert.equal(h.timers.size, 2);
  h.listeners.get('pageshow')({ persisted: false });
  assert.equal(h.attributes.get('data-entry'), 'landing');
  h.listeners.get('pageshow')({ persisted: true });
  assert.equal(h.attributes.has('data-entry'), false);
  cleanup();
});
