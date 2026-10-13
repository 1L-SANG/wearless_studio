import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { createPortal } from 'react-dom';
import { transformWithEsbuild } from 'vite';
import { CREDIT_COSTS, editorImageCreditCost } from '../../src/lib/limits.js';
import { canvasAiActionCreditCost } from '../../src/features/editor/canvasAiActions.js';
import * as placementRules from '../../src/features/editor/canvasAiPlacement.js';
const { calculateCanvasAiPlacement } = placementRules;
import { canvasAiProgressSize, canvasAiProgressVisible } from '../../src/features/editor/canvasAiApply.js';

const read = (path) => existsSync(new URL(path, import.meta.url))
  ? readFileSync(new URL(path, import.meta.url), 'utf8') : '';
const layer = read('../../src/features/editor/CanvasAiLayer.jsx');
const hook = read('../../src/features/editor/useCanvasAi.js');
const editor = read('../../src/features/editor/Editor.jsx');
const css = read('../../src/styles/features.css');
const transformed = await transformWithEsbuild(layer.replace(/^import .*;\n/gm, '').replace(/^export /gm, ''), 'CanvasAiLayer.jsx', { jsx: 'transform' });

// Render the real JSX with a selected-photo state snapshot, without a browser or API.
function renderLayer({ open = false, job = null, visible = true, fixed = false,
  hover = null, placed = true, nodes = {}, viewportWidth = 1280 } = {}) {
  let index = 0, refIndex = 0;
  const states = [open, null, hover, fixed, false, placed], changes = [], refs = [], layoutEffects = [];
  const scope = { React, createPortal, useEffect() {}, useLayoutEffect: (setup) => layoutEffects.push(setup),
    useRef: () => refs[refIndex++] ||= { current: null },
    useState: () => { const current = index++; return [states[current], (value) => { states[current] = changes[current] = value; }]; },
    document: { body: { nodeType: 1 }, createRange() {
      let node;
      return { selectNodeContents(value) { node = value; }, getClientRects: () => node.lineWidths().map((width) => ({ width })) };
    } }, window: { innerWidth: viewportWidth, addEventListener() {}, removeEventListener() {} },
    getComputedStyle: () => ({ lineHeight: '17px', paddingLeft: '11px', paddingRight: '11px' }),
    ResizeObserver: class { observe() {} disconnect() {} }, requestAnimationFrame() {}, cancelAnimationFrame() {},
    CREDIT_COSTS, editorImageCreditCost, canvasAiActionCreditCost, calculateCanvasAiPlacement, calculateCanvasAiReadPlacement: placementRules.calculateCanvasAiReadPlacement, canvasAiProgressSize, canvasAiProgressVisible };
  const CanvasAiLayer = new Function(...Object.keys(scope), `${transformed.code}; return CanvasAiLayer;`)(...Object.values(scope));
  const connectRefs = (node) => {
    if (node?.$$typeof === Symbol.for('react.portal')) { connectRefs(node.children); return; }
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
const portal = (tree) => tree && React.Children.toArray(tree.props.children).find((child) => child?.$$typeof === Symbol.for('react.portal'));
const description = (tree) => portal(tree)?.children;

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
    { name: 'wide margin', fixed: false },
    { name: 'narrow margin', fixed: false },
    { name: 'fixed', fixed: true },
  ]) {
    test(`one named trigger in ${placement.name} while ${state.name}`, () => {
      const { tree, changes } = renderLayer({ ...state, ...placement });
      const buttons = React.Children.toArray(tree.props.children).filter((child) => child.type === 'button');
      assert.equal(buttons.length, 1);
      const button = buttons[0], children = React.Children.toArray(button.props.children);
      assert.equal(button.props['aria-label'], 'AI 생성');
      assert.equal(label(tree)?.props.children, 'AI 생성');
      assert.equal(children.find((child) => child.props.className === 'canvas-ai-orb-hit')?.props['aria-hidden'], 'true');
      assert.equal(button.props['aria-expanded'], state.open);
      button.props.onClick();
      assert.equal(changes[0], !state.open);
    });
  }
}

for (const fixed of [false, true]) {
  test(`labelled orb does not duplicate its description (fixed=${fixed})`, () => {
    for (const event of ['onMouseEnter', 'onFocus']) {
      const view = renderLayer({ fixed });
      trigger(view.tree).props[event]?.();
      assert.equal(description(view.render()), undefined);
    }
  });
}

test('menu description and price are portalled above the canvas', () => {
  const tree = renderLayer({ open: true, hover: { description: '선택한 컷', price: '8 크레딧' } }).tree;
  assert.equal(portal(tree)?.containerInfo.nodeType, 1);
  const children = React.Children.toArray(description(tree).props.children);
  assert.equal(children[0].props.children, '선택한 컷');
  assert.equal(children[1].props.children, '8 크레딧');
});

test('Escape restores orb focus and removes the portalled description', () => {
  let focused = false;
  const view = renderLayer({ open: true, hover: { description: '선택한 컷' }, nodes: { 'canvas-ai-orb': { focus() { focused = true; } } } });
  const menu = React.Children.toArray(view.tree.props.children).find((child) => child.props?.role === 'menu');
  assert.ok(description(view.tree));
  menu.props.onKeyDown({ key: 'Escape', stopPropagation() {}, preventDefault() {} });
  const tree = view.render();
  assert.equal(focused, true);
  assert.equal(trigger(tree).props['aria-expanded'], false);
  assert.equal(description(tree), undefined);
});

test('closing the menu while busy also removes its description', () => {
  const view = renderLayer({ open: true, job: { status: 'busy' }, hover: { description: '지금 만들고 있어요' } });
  trigger(view.tree).props.onClick();
  assert.equal(description(view.render()), undefined);
});

function placementNodes({ blockLeft = 520, capsuleLeft = 401, descriptionHeight = () => 34,
  readWidth = (properties) => Number.parseFloat(properties.get('--canvas-ai-read-max-width')),
  lineWidths, priceWidth = 50 } = {}) {
  const properties = new Map(), readProperties = new Map(), readClasses = new Set();
  const text = { getBoundingClientRect: () => ({ height: descriptionHeight(readProperties) }),
    lineWidths: () => lineWidths ? lineWidths(readProperties) : [readWidth(readProperties) - 22] };
  const price = { lineWidths: () => [priceWidth] };
  let width = 107;
  const photo = { dataset: { elid: 'photo' }, getBoundingClientRect: () => ({ left: 780, top: 166, width: 172, height: 258 }) };
  const blockNode = { querySelectorAll: () => [photo], getBoundingClientRect: () => ({ left: blockLeft, top: 140, width: 400, height: 568 }) };
  const nodes = {
    'canvas-ai-layer': { closest: () => blockNode, querySelectorAll: () => [], style: { setProperty: (key, value) => properties.set(key, value) } },
    'canvas-ai-orb': { getBoundingClientRect: () => ({ left: capsuleLeft, top: 143, width, height: 46 }) },
    'canvas-ai-trigger-measure': { getBoundingClientRect: () => ({ width }) },
    'canvas-ai-label': { getBoundingClientRect: () => ({ width: width - 63 }) },
    'canvas-ai-read': { style: { setProperty: (key, value) => readProperties.set(key, value) },
      getBoundingClientRect: () => ({ width: parseFloat(readProperties.get('--canvas-ai-read-width')) || readWidth(readProperties) }),
      classList: { toggle: (name, value) => value ? readClasses.add(name) : readClasses.delete(name) },
      querySelector: () => text, querySelectorAll: () => [text, price] },
    wrap: { getBoundingClientRect: () => ({ left: 320, top: 60, width: 832, height: 740 }), addEventListener() {}, removeEventListener() {} },
  };
  return { nodes, properties, readProperties, readClasses, setWidth(value) { width = value; } };
}

test('first placement and font remeasurement preserve the complete labelled capsule', () => {
  const fixture = placementNodes();
  const view = renderLayer({ placed: false, nodes: fixture.nodes });
  assert.doesNotMatch(view.tree.props.className, /is-placed/);
  for (const width of [107, 93, 107]) {
    fixture.setWidth(width);
    const cleanup = view.layoutEffects[0]();
    const tree = view.render();
    assert.match(tree.props.className, /is-placed/);
    assert.doesNotMatch(tree.props.className, /is-fixed/);
    assert.equal(label(tree)?.props.children, 'AI 생성');
    assert.equal(fixture.properties.get('--canvas-ai-right'), '1030px');
    cleanup();
  }
});

test('portal uses capsule screen coordinates outside the canvas scroll area', () => {
  const fixture = placementNodes();
  const view = renderLayer({ open: true, hover: { description: '지금 이미지와 비슷한 컷을 생성해요', price: '8 크레딧' }, nodes: fixture.nodes });
  const cleanup = view.layoutEffects[0]();
  assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), '225px');
  assert.equal(fixture.readProperties.get('--canvas-ai-read-top'), '166px');
  assert.equal(fixture.readProperties.get('--canvas-ai-read-max-width'), '168px');
  assert.ok(fixture.readClasses.has('is-placed'));
  assert.ok(!fixture.readClasses.has('is-right'));
  cleanup();
});

for (const fixed of [false, true]) {
  test(`balanced two-line glass fits rendered lines and keeps its edge (fixed=${fixed})`, () => {
    const fixture = placementNodes({ blockLeft: fixed ? 400 : 520, lineWidths: () => [110, 104] });
    const view = renderLayer({ open: true, hover: { description: '지금 이미지와 비슷한 컷을 생성해요', price: '8 크레딧' }, nodes: fixture.nodes });
    const cleanup = view.layoutEffects[0]();
    assert.equal(fixture.readProperties.get('--canvas-ai-read-width'), '132px');
    assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), fixed ? '516px' : '261px');
    assert.equal(fixture.readClasses.has('is-right'), fixed);
    cleanup();
  });

  test(`portal measures a short description before aligning it (fixed=${fixed})`, () => {
    const fixture = placementNodes({ blockLeft: fixed ? 400 : 520, readWidth: () => 96 });
    const view = renderLayer({ open: true, hover: { description: '뒷면을 생성해요', price: '19 크레딧' }, nodes: fixture.nodes });
    const cleanup = view.layoutEffects[0]();
    assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), fixed ? '516px' : '297px');
    assert.equal(fixture.readProperties.get('--canvas-ai-read-top'), '166px');
    assert.equal(fixture.readClasses.has('is-right'), fixed);
    cleanup();
  });
}

test('price width and fresh balanced lines are measured on every placement update', () => {
  let lines = [60, 55];
  const fixture = placementNodes({ priceWidth: 90, lineWidths: (properties) => {
    assert.equal(properties.get('--canvas-ai-read-width'), 'max-content');
    return lines;
  } });
  const view = renderLayer({ open: true, hover: { description: '두 줄 설명', price: '19 크레딧' }, nodes: fixture.nodes });
  let cleanup = view.layoutEffects[0]();
  assert.equal(fixture.readProperties.get('--canvas-ai-read-width'), '112px');
  assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), '281px');
  cleanup();
  lines = [120.25, 115];
  cleanup = view.layoutEffects[0]();
  assert.equal(fixture.readProperties.get('--canvas-ai-read-width'), '143px');
  assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), '250px');
  cleanup();
});

test('three-line text on the left moves right and regains its two-line width', () => {
  const fixture = placementNodes({ capsuleLeft: 140, descriptionHeight: (properties) =>
    properties.get('--canvas-ai-read-max-width') === '168px' ? 34 : 51 });
  const view = renderLayer({ open: true, hover: { description: '지금 이미지와 비슷한 컷을 생성해요' }, nodes: fixture.nodes });
  const cleanup = view.layoutEffects[0]();
  assert.equal(fixture.readProperties.get('--canvas-ai-read-left'), '255px');
  assert.equal(fixture.readProperties.get('--canvas-ai-read-max-width'), '168px');
  assert.ok(fixture.readClasses.has('is-right'));
  cleanup();
});

test('description disappears when its capsule is hidden or its result is dismissed', () => {
  assert.equal(renderLayer({ visible: false, open: true, hover: { description: '선택한 컷' } }).tree, null);
  assert.equal(description(renderLayer({ job: { status: 'arrived', dismissed: true }, hover: { description: 'AI로 생성한 새 컷' } }).tree), undefined);
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
  assert.match(css, /\.canvas-ai-pill\.btn-glowring[^}]*color: var\(--fg-1\)/);
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

test('keyboard focus uses the hover and open glass without an extra halo or outline', () => {
  const focus = css.match(/\.canvas-ai-orb:focus-visible\s*\{([^}]+)\}/)?.[1];
  assert.match(focus, /outline: none/);
  assert.doesNotMatch(focus, /box-shadow/);
  assert.match(css, /\.canvas-ai-orb:is\(:hover, :focus-visible, \[aria-expanded="true"\]\)[^{]*\{[^}]*background: var\(--canvas-ai-glass\)[^}]*box-shadow: var\(--canvas-ai-glass-shadow\)/);
  assert.doesNotMatch(css, /\.canvas-ai-orb:focus-visible \.canvas-ai-core[^}]*outline/);
  assert.match(css, /\.canvas-ai-read\s*\{[^}]*position: fixed[^}]*z-index: 60/);
  assert.match(css, /\.canvas-ai-read small\s*\{[^}]*white-space: nowrap/);
});
