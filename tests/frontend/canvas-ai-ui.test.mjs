import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import { CREDIT_COSTS, editorImageCreditCost } from '../../src/lib/limits.js';
import { canvasAiActionCreditCost } from '../../src/features/editor/canvasAiActions.js';
import { calculateCanvasAiPlacement } from '../../src/features/editor/canvasAiPlacement.js';
import { canvasAiProgressSize, canvasAiProgressVisible } from '../../src/features/editor/canvasAiApply.js';

const read = (path) => existsSync(new URL(path, import.meta.url))
  ? readFileSync(new URL(path, import.meta.url), 'utf8') : '';
const layer = read('../../src/features/editor/CanvasAiLayer.jsx');
const hook = read('../../src/features/editor/useCanvasAi.js');
const editor = read('../../src/features/editor/Editor.jsx');
const css = read('../../src/styles/features.css');
const transformed = await transformWithEsbuild(layer.replace(/^import .*;\n/gm, '').replace(/^export /gm, ''), 'CanvasAiLayer.jsx', { jsx: 'transform' });

// Render the real JSX with a selected-photo state snapshot, without a browser or API.
function renderLayer({ open = false, job = null, visible = true, fixed = false, showLabel = true,
  hover = null, placed = true, nodes = {} } = {}) {
  let index = 0, refIndex = 0;
  const states = [open, null, hover, fixed, false, placed, showLabel], changes = [], refs = [], layoutEffects = [];
  const scope = { React, useEffect() {}, useLayoutEffect: (setup) => layoutEffects.push(setup),
    useRef: () => refs[refIndex++] ||= { current: null },
    useState: () => { const current = index++; return [states[current], (value) => { states[current] = changes[current] = value; }]; },
    document: {}, window: { addEventListener() {}, removeEventListener() {} },
    ResizeObserver: class { observe() {} disconnect() {} }, requestAnimationFrame() {}, cancelAnimationFrame() {},
    CREDIT_COSTS, editorImageCreditCost, canvasAiActionCreditCost, calculateCanvasAiPlacement, canvasAiProgressSize, canvasAiProgressVisible };
  const CanvasAiLayer = new Function(...Object.keys(scope), `${transformed.code}; return CanvasAiLayer;`)(...Object.values(scope));
  const connectRefs = (node) => {
    if (!React.isValidElement(node)) return;
    if (node.ref) node.ref.current = nodes[node.props.className?.split(' ')[0]] || null;
    React.Children.toArray(node.props.children).forEach(connectRefs);
  };
  const render = () => {
    index = refIndex = 0; layoutEffects.length = 0;
    const tree = CanvasAiLayer({ block: { id: 'block' }, element: { id: 'photo', src: '/selected.jpg', w: 300, h: 450 },
      scale: 0.4, wrapRef: { current: nodes.wrap || null }, menu: { kind: 'worn', branches: [] }, visible, job });
    connectRefs(tree);
    return tree;
  };
  return { tree: render(), render, changes, layoutEffects };
}

const trigger = (tree) => React.Children.toArray(tree.props.children).find((child) => child.type === 'button');
const label = (tree) => React.Children.toArray(trigger(tree).props.children).find((child) => child.props.className === 'canvas-ai-label');
const description = (tree) => React.Children.toArray(tree.props.children).find((child) => child.props.className?.startsWith('canvas-ai-read'));

test('오브는 메뉴 버튼이며 방향키와 Esc가 초점을 돌려준다', () => {
  assert.match(layer, /aria-haspopup="menu"/);
  assert.match(layer, /aria-expanded=\{open\}/);
  assert.match(layer, /role="menu"/);
  assert.match(layer, /ArrowDown/);
  assert.match(layer, /ArrowUp/);
  assert.match(layer, /Escape/);
  assert.match(layer, /orbRef\.current\?\.focus\(\)/);
});

for (const state of [
  { name: 'closed', open: false },
  { name: 'menu open', open: true },
  { name: 'generating', open: false, job: { status: 'busy' } },
  { name: 'result arrived', open: false, job: { status: 'arrived', result: { src: '/result.jpg' } } },
]) {
  for (const placement of [
    { name: 'wide margin', showLabel: true, fixed: false },
    { name: 'narrow margin', showLabel: false, fixed: false },
    { name: 'fixed', showLabel: false, fixed: true },
  ]) {
    test(`one named trigger in ${placement.name} while ${state.name}`, () => {
      const { tree, changes } = renderLayer({ ...state, ...placement });
      const buttons = React.Children.toArray(tree.props.children).filter((child) => child.type === 'button');
      assert.equal(buttons.length, 1);
      const button = buttons[0], children = React.Children.toArray(button.props.children);
      assert.equal(button.props['aria-label'], 'AI 생성');
      assert.equal(label(tree)?.props.children, placement.showLabel ? 'AI 생성' : undefined);
      assert.equal(children.find((child) => child.props.className === 'canvas-ai-orb-hit')?.props['aria-hidden'], 'true');
      assert.equal(button.props['aria-expanded'], state.open);
      button.props.onClick();
      assert.equal(changes[0], !state.open);
    });
  }
}

for (const placement of [
  { name: 'wide margin', showLabel: true, fixed: false, read: false },
  { name: 'narrow margin', showLabel: false, fixed: false, read: true },
  { name: 'fixed', showLabel: false, fixed: true, read: true },
]) {
  test(`orb hover and keyboard focus describe only an unlabelled trigger in ${placement.name}`, () => {
    for (const event of ['onMouseEnter', 'onFocus']) {
      const view = renderLayer(placement);
      trigger(view.tree).props[event]();
      assert.equal(Boolean(description(view.render())), placement.read);
    }
  });
}

test('menu descriptions and credit amounts remain visible with the label', () => {
  const tree = renderLayer({ open: true, hover: { description: '선택한 컷', price: '8 크레딧' } }).tree;
  const children = React.Children.toArray(description(tree).props.children);
  assert.equal(children[0].props.children, '선택한 컷');
  assert.equal(children[1].props.children, '8 크레딧');
});

test('Escape restores orb focus without showing a duplicate description beside the label', () => {
  for (const showLabel of [true, false]) {
    let focused = false;
    const view = renderLayer({ open: true, showLabel, nodes: { 'canvas-ai-orb': { focus() { focused = true; } } } });
    const menu = React.Children.toArray(view.tree.props.children).find((child) => child.props.role === 'menu');
    menu.props.onKeyDown({ key: 'Escape', stopPropagation() {}, preventDefault() {} });
    const tree = view.render();
    assert.equal(focused, true);
    assert.equal(trigger(tree).props['aria-expanded'], false);
    assert.equal(Boolean(description(tree)), !showLabel);
  }
});

test('the first visible placement uses a measured label even when the trigger starts at 40px', () => {
  const properties = new Map();
  let labelWidth = 44;
  const photo = { dataset: { elid: 'photo' }, getBoundingClientRect: () => ({ left: 780, top: 166, width: 172, height: 258 }) };
  const blockNode = { querySelectorAll: () => [photo], getBoundingClientRect: () => ({ left: 520, top: 140, width: 400, height: 568 }) };
  const nodes = {
    'canvas-ai-layer': { closest: () => blockNode, querySelectorAll: () => [], style: { setProperty: (key, value) => properties.set(key, value) } },
    'canvas-ai-orb': { getBoundingClientRect: () => ({ width: 40 }) },
    'canvas-ai-label': { getBoundingClientRect: () => ({ width: labelWidth }) },
    wrap: { getBoundingClientRect: () => ({ left: 320, top: 60, width: 832, height: 740 }), addEventListener() {}, removeEventListener() {} },
  };
  const view = renderLayer({ showLabel: false, placed: false, nodes });
  assert.doesNotMatch(view.tree.props.className, /is-placed/);
  for (const row of [
    { labelWidth: 44, showLabel: false, readWidth: '135px' },
    { labelWidth: 30, showLabel: true, readWidth: '97px' },
    { labelWidth: 44, showLabel: false, readWidth: '135px' },
  ]) {
    labelWidth = row.labelWidth;
    const cleanup = view.layoutEffects[0]();
    const tree = view.render();
    assert.match(tree.props.className, /is-placed/);
    assert.doesNotMatch(tree.props.className, /is-fixed/);
    assert.equal(Boolean(label(tree)), row.showLabel);
    assert.equal(properties.get('--canvas-ai-right'), '1030px');
    assert.equal(properties.get('--canvas-ai-read-width'), row.readWidth);
    cleanup();
  }
});

test('orb and label disappear together when the photo tools are hidden', () => {
  assert.equal(renderLayer({ visible: false }).tree, null);
});

test('선택이 바뀌면 메뉴를 닫고 몸짓과 편집 중에는 숨긴다', () => {
  assert.match(layer, /\[element\.id, element\.src\]/);
  assert.match(editor, /const \[canvasGesture, setCanvasGesture\] = useState\(false\)/);
  assert.match(editor, /!canvasGesture && !cropping && !marquee && !spaceDown && !editEl && !preview/);
  assert.match(editor, /gesturing\.current = true; setCanvasGesture\(true\)/);
  assert.match(editor, /gesturing\.current = false; setCanvasGesture\(false\)/);
});

test('생성 결과는 의류에 남고 현재 탭을 바꾸지 않는다', () => {
  assert.match(hook, /api\.getEditorAssetInfo/);
  assert.match(hook, /api\.fillEmptySlot/);
  assert.match(hook, /api\.generateImage/);
  assert.match(hook, /onResult/);
  assert.doesNotMatch(hook, /setTab/);
  assert.match(layer, /현재 이미지와 교체/);
  assert.match(layer, /새 블록에 추가/);
  assert.match(layer, /크레딧은 나가지 않았어요/);
});

test('새 UI는 CSS 변수로 배치하고 내보내기에서 제외한다', () => {
  assert.doesNotMatch(layer, /style=\{/);
  assert.match(layer, /style\.setProperty/);
  const exportSource = read('../../src/features/editor/editorExport.js');
  assert.match(exportSource, /'\.canvas-ai-layer'/);
  assert.match(exportSource, /'\.canvas-ai-slot-progress'/);
  assert.match(css, /\.ed-body\s*\{[^}]*position: relative/);
});

test('오브와 로고 채움은 확정한 크기와 속도이며 동작 줄이기를 존중한다', () => {
  assert.match(css, /\.canvas-ai-orb\s*\{[^}]*--d: 30px/);
  assert.match(css, /canvasAiGlassA 12s/);
  assert.match(css, /canvasAiGlassB 17s/);
  assert.match(css, /canvasAiFill 2\.4s ease-in-out/);
  assert.match(css, /canvasAiDrain 2s linear/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /\.canvas-ai-pill\.btn-glowring[^}]*color: var\(--bg-1\)/);
  assert.doesNotMatch(layer, /className="spin"/);
});

test('갈래 너비는 확정 시안처럼 실제 글자를 재고 선택지는 같은 폭으로 늘린다', () => {
  assert.match(layer, /canvas-ai-measure/);
  assert.match(layer, /--canvas-ai-branch-width/);
  assert.match(layer, /getBoundingClientRect\(\)\.width/);
  assert.match(css, /\.canvas-ai-options\.is-closure[^}]*display: grid/);
  assert.doesNotMatch(css.slice(css.indexOf('/* ADR-0020:')), /min-width: 132px|width: 152px/);
});

test('낮은 가로 사진의 취소 버튼도 로고 옆에 보여 준다', () => {
  assert.match(layer, /classList\.toggle\('is-compact'/);
  assert.match(css, /\.canvas-ai-progress\.is-compact[^}]*flex-direction: row/);
});
