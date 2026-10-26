import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

let vite, Icon, Button, Publish;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ Icon, Button } = await vite.ssrLoadModule('/src/components/ui.jsx'));
  ({ EditorPublishModal: Publish } = await vite.ssrLoadModule('/src/features/editor/EditorPublishModal.jsx'));
});
after(() => vite?.close());
const markup = (tree) => renderToStaticMarkup(tree);
async function load(runtime = hooks()) {
  assert.ok(existsSync(new URL('../../src/features/editor/ExportMenu.jsx', import.meta.url)), 'export menu is missing');
  return componentModule('src/features/editor/ExportMenu.jsx', ['ExportMenu', 'ExportSplitButton', 'buildExportMenuItems'],
    { useRef: runtime.useRef, useEffect: runtime.useEffect, Icon, Button, styles: {} });
}
const options = (extra = {}) => ({ malls: { items: [], disabled: false }, publishEnabled: true, gifCount: 1,
  onDownload() {}, onPublish() {}, onConnect() {}, onGifs() {}, ...extra });

test('split button renders main and caret with aria', async () => {
  const { ExportSplitButton } = await load();
  let opened = false, downloads = 0;
  const tree = ExportSplitButton({ open: false, anchorRef: { current: null }, items: [],
    onDownload: () => downloads++, onToggle: () => { opened = true; }, onClose() {} });
  const html = markup(tree);
  assert.match(html, /다운로드<\/button>/);
  assert.match(html, /aria-label="다운로드와 쇼핑몰 등록 메뉴"[^>]*aria-haspopup="menu"[^>]*aria-expanded="false"/);
  const children = React.Children.toArray(tree.props.children);
  children[0].props.onClick();
  children[1].props.onKeyDown({ key: 'ArrowDown', preventDefault() {}, stopPropagation() {} });
  assert.equal(downloads, 1); assert.equal(opened, true);
});

test('menu lists fixed order and shows mall items as 준비 중 when connect api is disabled', async () => {
  const { buildExportMenuItems } = await load();
  assert.deepEqual(buildExportMenuItems(options()).map((item) => item.label),
    ['이미지로 다운로드', '몰에 붙여넣기', '지그재그 연결하기', '에이블리에 상품 등록', 'GIF 따로 받기']);
  const disabled = buildExportMenuItems(options({ malls: { items: [], disabled: true } }));
  assert.deepEqual(disabled.map((item) => item.label),
    ['이미지로 다운로드', '몰에 붙여넣기', '지그재그에 상품 등록', '에이블리에 상품 등록', 'GIF 따로 받기']);
  assert.deepEqual(disabled.filter((item) => ['zigzag', 'ably'].includes(item.id))
    .map(({ id, label, kind, disabled, badge }) => ({ id, label, kind, disabled, badge })), [
    { id: 'zigzag', label: '지그재그에 상품 등록', kind: 'action', disabled: true, badge: '준비 중' },
    { id: 'ably', label: '에이블리에 상품 등록', kind: 'action', disabled: true, badge: '준비 중' },
  ]);
  assert.deepEqual(buildExportMenuItems(options({ malls: { disabled: true }, publishEnabled: false, gifCount: 0 })).map((item) => item.label),
    ['이미지로 다운로드', '지그재그에 상품 등록', '에이블리에 상품 등록']);
});

test('zigzag item is connect link when not connected and disabled badge when connected', async () => {
  const { ExportMenu, buildExportMenuItems } = await load();
  let navigated = false;
  const items = buildExportMenuItems(options({ onConnect: () => { navigated = true; } }));
  items[2].onSelect();
  assert.equal(navigated, true); assert.equal(items[2].kind, 'go');
  const connected = buildExportMenuItems(options({ malls: { items: [{ mall: 'zigzag', status: 'connected' }] } }));
  assert.equal(connected[2].badge, '준비 중'); assert.equal(connected[2].disabled, true);
  const html = markup(ExportMenu({ open: true, items: connected, onClose() {}, anchorRef: { current: null } }));
  assert.match(html, /role="menu"/); assert.match(html, /role="menuitem"/);
  assert.match(html, /zigzag-48.png/); assert.match(html, /zigzag-96.png 2x/); assert.match(html, /alt=""/);
});

test('paste item shows download-only badge for real model', async () => {
  const { ExportMenu, buildExportMenuItems } = await load();
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('items={buildExportMenuItems(') + 'items={'.length;
  const end = source.indexOf('} />', start);
  const menu = new Function('buildExportMenuItems', 'realModelBlocked', 'mallInfo', 'publicationInfo', 'pubScope', 'gifImages',
    `return (${source.slice(start, end)});`);
  for (const blocked of [true, false]) {
    const paste = menu(buildExportMenuItems, blocked, { disabled: true }, { scope: 'p', disabled: false }, 'p', [])
      .find((item) => item.id === 'paste');
    assert.equal(paste.disabled, blocked);
    assert.equal(paste.badge, blocked ? '다운로드만 가능' : undefined);
    const html = markup(ExportMenu({ open: true, items: [paste], onClose() {}, anchorRef: {} }));
    if (blocked) assert.match(html, /disabled=""[^>]*aria-disabled="true"[\s\S]*다운로드만 가능/);
    else assert.doesNotMatch(html, /다운로드만 가능/);
  }
});

test('menu items without icons render text only', async () => {
  const { ExportMenu } = await load();
  const html = markup(ExportMenu({ open: true, onClose() {}, anchorRef: {},
    items: [{ id: 'rotate', label: '인증정보 변경' }, { id: 'revoke', label: '연결 해제' }] }));
  assert.match(html, /인증정보 변경/); assert.match(html, /연결 해제/);
  assert.doesNotMatch(html, /<svg|<img/);
});

test('menu closes on escape and restores focus', async () => {
  const runtime = hooks();
  const { ExportMenu } = await load(runtime);
  let closed = 0, focused = '';
  const tree = runtime.render(ExportMenu, { open: true, items: [], onClose: () => closed++,
    anchorRef: { current: { focus: () => { focused = 'caret'; } } } });
  tree.props.onKeyDown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.equal(closed, 1); assert.equal(focused, 'caret');
});

test('menu keeps focus on current item when parent rerenders', async () => {
  const runtime = hooks();
  const { ExportMenu } = await load(runtime);
  const previousDocument = globalThis.document;
  let active, outside, closed = 0, restored = false;
  globalThis.document = { addEventListener: (_, handler) => { outside = handler; }, removeEventListener() {} };
  try {
    const buttons = [0, 1].map(() => ({ focus() { active = this; } }));
    const props = { open: true, items: [], onClose() {}, anchorRef: { current: null } };
    const tree = runtime.render(ExportMenu, props);
    tree.ref.current = { querySelector: () => buttons[0], contains: () => false };
    runtime.flushEffects();
    assert.equal(active, buttons[0]);
    buttons[1].focus();
    runtime.render(ExportMenu, { ...props, onClose: () => closed++,
      anchorRef: { current: { contains: () => false, focus: () => { restored = true; } } } });
    runtime.flushEffects();
    assert.equal(active, buttons[1]);
    outside({ target: {} });
    assert.equal(closed, 1); assert.equal(restored, true);
  } finally { globalThis.document = previousDocument; }
});

test('opening export menu with arrow keys never moves selected editor image', async () => {
  const { ExportMenu, ExportSplitButton } = await load();
  const { clampDragDelta, getBlockRenderHeight } = await vite.ssrLoadModule('/src/features/editor/editorGeometry.js');
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const start = source.indexOf("      if (selEls.length && e.key.startsWith('Arrow')) {");
  const end = source.indexOf('\n    };\n    window.addEventListener', start);
  assert.ok(start > 0 && end > start);
  let blocks = [{ id: 'b', h: 1000, elements: [{ id: 'photo', type: 'image', x: 100, y: 100, w: 200, h: 200 }] }];
  const nudge = new Function('selEls', 'kb', 'setBlocks', 'clampDragDelta', 'getBlockRenderHeight',
    `return (e) => {${source.slice(start, end)}};`)(['photo'], { current: {} }, (update) => { blocks = update(blocks); }, clampDragDelta, getBlockRenderHeight);
  for (const key of ['ArrowDown', 'ArrowUp']) {
    let opened = false, stopped = false;
    const tree = ExportSplitButton({ open: false, onToggle: () => { opened = true; }, onClose() {}, anchorRef: {}, items: [] });
    const event = { key, target: { tagName: 'BUTTON' }, preventDefault() {}, stopPropagation() { stopped = true; } };
    React.Children.toArray(tree.props.children)[1].props.onKeyDown(event);
    if (!stopped) nudge(event);
    assert.equal(opened, true);
    assert.deepEqual([blocks[0].elements[0].x, blocks[0].elements[0].y], [100, 100]);
  }
  for (const key of ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Delete']) {
    let stopped = false;
    const tree = ExportMenu({ open: true, onClose() {}, anchorRef: {}, items: [] });
    const event = { key, target: { tagName: 'BUTTON' },
      currentTarget: { querySelectorAll: () => [], ownerDocument: {} },
      preventDefault() {}, stopPropagation() { stopped = true; } };
    tree.props.onKeyDown(event);
    if (!stopped) nudge(event);
    assert.equal(stopped, true, key);
    assert.deepEqual([blocks[0].elements[0].x, blocks[0].elements[0].y], [100, 100]);
  }
});

test('tab closes menu without preventing focus traversal', async () => {
  const { ExportMenu } = await load();
  let closed = false, prevented = false, stopped = false;
  ExportMenu({ open: true, items: [], onClose: () => { closed = true; }, anchorRef: {} }).props.onKeyDown({
    key: 'Tab', preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
  assert.equal(closed, true); assert.equal(prevented, false); assert.equal(stopped, true);
});

test('menu arrow keys and home end skip disabled entries', async () => {
  const { ExportMenu } = await load();
  let active;
  const buttons = [0, 1, 2].map((i) => ({ focus() { active = this; }, ownerDocument: { get activeElement() { return active; } } }));
  const tree = ExportMenu({ open: true, items: [], onClose() {}, anchorRef: {} });
  const press = (key) => tree.props.onKeyDown({ key, currentTarget: { querySelectorAll: () => buttons, ownerDocument: { get activeElement() { return active; } } }, preventDefault() {}, stopPropagation() {} });
  press('Home'); assert.equal(active, buttons[0]);
  press('ArrowDown'); assert.equal(active, buttons[1]);
  press('End'); assert.equal(active, buttons[2]);
  press('ArrowDown'); assert.equal(active, buttons[0]);
  press('ArrowUp'); assert.equal(active, buttons[2]);
});

test('publish modal copy renamed', () => {
  const props = { settings: { preset: 'smartstore' }, summary: { fileCount: 1, approxBytes: 1 }, busy: false, previousCount: 0 };
  const html = markup(React.createElement(Publish, props));
  assert.match(html, /붙여넣을 코드 만들기/); assert.match(html, /코드 만들기<\/button>/);
  assert.match(markup(React.createElement(Publish, { ...props, result: { files: [] } })), /코드를 만들었어요. 쇼핑몰 HTML 입력칸에 붙여 넣어 주세요/);
});
