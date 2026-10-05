import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { Routes, Route, Outlet, Navigate, useLocation } from 'react-router-dom';
import { transformWithEsbuild } from 'vite';

const source = readFileSync(new URL('../../src/apps/seller/App.jsx', import.meta.url), 'utf8');
const guardStart = source.indexOf('function MobileHandoffGuard()');
const guardSource = guardStart < 0 ? '' : source.slice(guardStart, source.indexOf('function RequireProject()'));
const appSource = source.slice(source.indexOf('export default function App()')).replace('export default ', '');
const pageSource = ['HangTag.jsx', 'AddressCopyChip.jsx', 'PcOnlyPage.jsx'].map((file) =>
  readFileSync(new URL(`../../src/features/shell/${file}`, import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const { code } = await transformWithEsbuild(`${pageSource}\n${guardSource}\n${appSource}`, 'MobileRoutes.jsx', { jsx: 'transform' });

function harness(mobile, pathname) {
  const calls = [];
  const effects = [];
  const document = { title: 'Wearless Studio' };
  const href = `https://preview.wearless.test${pathname}?view=saved#page`;
  const bindings = {
    React, Routes, Route, Outlet, Navigate, useLocation, Suspense: React.Suspense,
    useEffect: (effect) => effects.push(effect), document,
    s: new Proxy({}, { get: (_, key) => key }), p: new Proxy({}, { get: (_, key) => key }),
    draftSlot: { remove() { calls.push(['remove']); }, removeForNewFlow() { calls.push(['remove']); } },
    IS_FACEMARKET: false, isSupabaseConfigured: true, domainRouteRedirect: () => null,
    isMobileDevice: () => mobile,
    useNavigate: () => (path) => calls.push(['navigate', path]),
    useToast: () => ({ push: (message) => calls.push(['toast', message]) }),
    copyHandoffAddress: async (pushToast) => { pushToast('주소를 복사했어요'); },
    Icon: ({ name, size }) => React.createElement('svg', { 'data-icon': name, width: size }),
    window: { location: { href, origin: 'https://preview.wearless.test', host: 'preview.wearless.test' } },
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
  return { calls, html, href, effects, document, page() {
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
    const primary = find(guard, '사진 담기 화면으로');
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
  ['/library', 'Library'], ['/pricing', 'Pricing'], ['/credits/history', 'CreditsHistory'],
  ['/payments/success', 'PaymentSuccess'], ['/payments/fail', 'PaymentFail'],
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
