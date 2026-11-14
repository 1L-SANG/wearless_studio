import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { WEARLESS_LEGAL_URLS } from '../../src/lib/legalLinks.js';
import { hookHarness, nodes, textOf, componentStyles } from './helpers/storyboardComponents.mjs';

const file = path => new URL(`../../${path}`, import.meta.url);
const read = path => readFileSync(file(path), 'utf8');

async function load(path, name, bindings = {}) {
  assert.ok(existsSync(file(path)), `${path} is available`);
  const source = read(path).replace(/^import[\s\S]*?;\n/gm, '')
    .replace(/^export default [^;]+;?$/gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, path, { jsx: 'transform' });
  return new Function('React', ...Object.keys(bindings), `${code}; return ${name};`)(React, ...Object.values(bindings));
}

async function sharedFooterBindings() {
  const COMPANY_INFO_ROWS = await load('src/lib/companyInfo.js', 'COMPANY_INFO_ROWS', {
    companyInfo: JSON.parse(read('src/lib/companyInfo.json')),
  });
  const CompanyInfoRows = await load('src/components/CompanyInfoRows.jsx', 'CompanyInfoRows', { COMPANY_INFO_ROWS });
  return { CompanyInfoRows, WEARLESS_LEGAL_URLS, styles: componentStyles };
}

async function footerHarness() {
  const h = hookHarness();
  const listeners = new Map();
  let activeElement = null;
  const trigger = { contains: node => node === trigger, focus() { activeElement = trigger; } };
  const panelChild = {};
  const panel = { contains: node => node === panel || node === panelChild, focus() { activeElement = panel; } };
  const document = {
    addEventListener(type, handler) { listeners.set(type, handler); },
    removeEventListener(type, handler) { if (listeners.get(type) === handler) listeners.delete(type); },
  };
  const Component = await load('src/features/shell/StoryboardFooter.jsx', 'StoryboardFooter', {
    ...await sharedFooterBindings(), ...h.hooks,
    useCallback: (fn, deps) => h.hooks.useMemo(() => fn, deps), useId: () => 'footer-business', document,
  });
  return {
    render() {
      return h.render(Component, {}, tree => {
        for (const node of nodes(tree)) {
          if (node.ref && node.props['aria-haspopup'] === 'dialog') node.ref.current = trigger;
          if (node.ref && node.props.role === 'dialog') node.ref.current = panel;
        }
      });
    },
    fire(type, event) { listeners.get(type)?.(event); },
    get activeElement() { return activeElement; },
    trigger, panel, panelChild, listeners, close: h.close,
  };
}

const businessButton = tree => nodes(tree).find(node => node.type === 'button' && textOf(node) === '사업자 정보');
const dialog = tree => nodes(tree).find(node => node.props?.role === 'dialog');

test('storyboard footer keeps legal destinations and discloses business information without navigating', async () => {
  const h = await footerHarness();
  try {
    let tree = h.render();
    const nav = nodes(tree).find(node => node.type === 'nav');
    assert.equal(nav.props['aria-label'], '법적 고지');
    assert.deepEqual(nodes(nav).filter(node => node.type === 'a').map(node => [textOf(node), node.props.href]), [
      ['이용약관', 'https://wearless.kr/terms'],
      ['개인정보처리방침', 'https://wearless.kr/privacy'],
    ]);
    const trigger = businessButton(tree);
    assert.equal(trigger.props.type, 'button');
    assert.equal(trigger.props.href, undefined);
    assert.equal(trigger.props['aria-expanded'], false);
    assert.equal(dialog(tree), undefined);

    trigger.props.onClick();
    tree = h.render();
    const panel = dialog(tree);
    assert.ok(panel);
    assert.equal(businessButton(tree).props['aria-expanded'], true);
    assert.equal(businessButton(tree).props['aria-controls'], panel.props.id);
    assert.equal(panel.props['aria-label'], '사업자 정보');
    assert.equal(panel.props['aria-modal'], undefined, 'reading company details does not block the work area');
    assert.equal(h.activeElement, h.panel, 'keyboard focus enters the disclosed information');

    const bindings = await sharedFooterBindings();
    const SiteFooter = await load('src/features/shell/SiteFooter.jsx', 'SiteFooter', bindings);
    const companyRows = html => [...html.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/g)].map(match => match[1]);
    const existing = renderToStaticMarkup(React.createElement(SiteFooter));
    const company = existing.slice(existing.indexOf('class="site-footer__company"'));
    assert.deepEqual(companyRows(renderToStaticMarkup(panel)), companyRows(company));
    assert.equal(companyRows(company).length, 8);
  } finally { h.close(); }
});

test('business popover ignores inside interaction and returns focus after Escape, outside press, or close', async () => {
  assert.match(read('src/features/shell/StoryboardFooter.jsx'), /addEventListener\('keydown', onKeyDown, true\)/, 'the topmost popover handles Escape before the underlying drawer');
  const h = await footerHarness();
  try {
    let tree = h.render();
    const open = () => { businessButton(tree).props.onClick(); tree = h.render(); assert.ok(dialog(tree)); };
    const expectClosed = () => {
      tree = h.render();
      assert.equal(dialog(tree), undefined);
      assert.equal(businessButton(tree).props['aria-expanded'], false);
      assert.equal(h.activeElement, h.trigger);
      assert.equal(h.listeners.size, 0, 'closing removes document listeners');
    };
    open();
    h.fire('pointerdown', { target: h.panelChild });
    tree = h.render();
    assert.ok(dialog(tree));
    let stopped = false;
    dialog(tree).props.onClick({ stopPropagation() { stopped = true; } });
    assert.equal(stopped, true, 'reading the top popover must not dismiss a drawer behind it');
    h.fire('pointerdown', { target: h.trigger });
    tree = h.render();
    assert.ok(dialog(tree), 'the trigger owns its toggle, without outside-listener races');
    let prevented = false;
    h.fire('keydown', { key: 'Escape', preventDefault() { prevented = true; } });
    expectClosed();
    assert.equal(prevented, true);

    open();
    h.fire('pointerdown', { target: {} });
    expectClosed();
    open();
    const close = nodes(dialog(tree)).find(node => node.type === 'button' && /닫기/.test(node.props['aria-label'] || textOf(node)));
    assert.ok(close);
    close.props.onClick();
    expectClosed();
    open();
    businessButton(tree).props.onClick();
    expectClosed();
    open();
    h.close();
    assert.equal(h.listeners.size, 0, 'unmounting an open footer removes its listeners');
  } finally { h.close(); }
});

test('only the exact storyboard route receives the compact footer and viewport scope', async () => {
  const shared = await sharedFooterBindings();
  const StoryboardFooter = await load('src/features/shell/StoryboardFooter.jsx', 'StoryboardFooter', shared);
  const SiteFooter = await load('src/features/shell/SiteFooter.jsx', 'SiteFooter', shared);
  const location = { pathname: '/create/storyboard' };
  const ChromeLayout = await load('src/features/shell/ChromeLayout.jsx', 'ChromeLayout', {
    isMannequinStageEnabled: () => true,
    document: { documentElement: { getAttribute: () => null } },
    useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
    useRef: () => ({ current: null }), useEffect() {}, useLayoutEffect() {},
    useLocation: () => location, useNavigate: () => () => {}, useAuth: () => ({ session: null }),
    useAppStore: select => select({ loadAccount() {}, loadCatalogs() {} }), useToast: () => ({ push() {} }),
    TopNav: 'nav', Outlet: 'main', SiteFooter, StoryboardFooter,
  });
  for (const [pathname, compact] of [
    ['/create/storyboard', true], ['/create/storyboard/', false], ['/create/storyboard/history', false],
    ['/create/input', false], ['/create/mannequin', false], ['/create/generating', false],
    ['/library', false], ['/pricing', false], ['/credits', false], ['/credits/history', false],
    ['/subscription', false], ['/subscription/payment', false], ['/payment/success', false], ['/payment/fail', false],
  ]) {
    location.pathname = pathname;
    const tree = ChromeLayout({});
    assert.equal(tree.props['data-storyboard-layout'], compact ? 'true' : undefined, pathname);
    const footers = nodes(tree).filter(node => [SiteFooter, StoryboardFooter].includes(node.type));
    assert.equal(footers.length, 1, pathname);
    assert.equal(footers[0].type, compact ? StoryboardFooter : SiteFooter, pathname);
  }
});

test('business popup lifts only the open footer above the CTA and drawer then restores its ordinary layer', async () => {
  const h = await footerHarness();
  try {
    let tree = h.render();
    assert.equal(tree.props['data-business-open'], undefined);
    businessButton(tree).props.onClick();
    tree = h.render();
    assert.equal(tree.props['data-business-open'], 'true');

    const css = read('src/features/shell/StoryboardFooter.module.css');
    const ordinaryLayer = Number(css.match(/\.footer\s*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
    const openLayer = Number(css.match(/\.footer\[data-business-open="true"\]\s*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
    const continueLayer = Number(read('src/features/storyboard/StoryboardLayout.module.css').match(/\.continueBar\s*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
    const drawerLayer = Number(read('src/features/storyboard/StoryboardPanels.module.css').match(/\.drawer[^{}]*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
    assert.equal(ordinaryLayer, 2);
    assert.equal(openLayer, 250);
    assert.ok(ordinaryLayer < continueLayer && ordinaryLayer < drawerLayer);
    assert.ok(openLayer > continueLayer && openLayer > drawerLayer, 'neither portal can paint over business information');

    const outsideDrawerControl = { dataset: { drawerControl: true } };
    h.fire('pointerdown', { target: outsideDrawerControl });
    tree = h.render();
    assert.equal(tree.props['data-business-open'], undefined, 'pressing an uncovered drawer control restores normal stacking');
    assert.equal(dialog(tree), undefined);
  } finally { h.close(); }
});

test('the compact legal links and business popup remain left of an open drawer', () => {
  const css = read('src/features/shell/StoryboardFooter.module.css');
  assert.match(css, /:global\(body.storyboard-drawer-open\) \.footer \{ justify-content: flex-start/);
  assert.match(css, /:global\(body.storyboard-drawer-open\) \.popup \{ left: var\(--sp-16\); transform: none/);
});
