import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, transformWithEsbuild } from 'vite';
import * as closure from '../../src/lib/outerClosure.js';
import { clampDragDelta, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const front = (kind = 'button', range = 'graded', extra = {}) => ({
  version: 1, kind, range, secondary: null, evidenceSlots: ['Front'], note: null, ...extra,
});
const values = ['closed', 'openToChest', 'partial', 'openToWaist', 'open'];
let vite, DB, mockApi, Control, Field, defaultAnalysisShape, httpAdapter;
const cacheRoot = mkdtempSync(join(tmpdir(), 'outer-closure-'));

before(async () => {
  vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false, root: new URL('../..', import.meta.url).pathname,
    cacheDir: cacheRoot, logLevel: 'silent', appType: 'custom',
    esbuild: { jsx: 'automatic' },
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, ws: false },
    plugins: [{
      name: 'outer-closure-auth-boundary', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0outer-closure-auth'; },
      load(id) { if (id === '\0outer-closure-auth') return 'export const supabase={auth:{getSession:async()=>({data:{session:{access_token:"test-token",user:{id:"seller"}}}})}};'; },
    }],
  });
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ api: mockApi } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ default: Control } = await vite.ssrLoadModule('/src/components/OuterClosureControl.jsx'));
  ({ default: Field } = await vite.ssrLoadModule('/src/components/OuterClosureField.jsx'));
  ({ defaultAnalysisShape } = await vite.ssrLoadModule('/src/lib/api/shapes.js'));
  ({ httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
});
after(async () => { await vite?.close(); rmSync(cacheRoot, { recursive: true, force: true }); });

test('normalization preserves all five saved values and defaults invalid values to open', () => {
  assert.deepEqual(closure.OUTER_CLOSURE_VALUES, values);
  for (const value of values) assert.equal(closure.normalizeOuterClosureState(value), value);
  for (const value of ['legacy', null, undefined, '', {}, 0]) {
    assert.equal(closure.normalizeOuterClosureState(value), 'open');
  }
});

test('range follows closure kind overrides and falls back for missing or malformed analysis', () => {
  for (const [kind, range, expected] of [
    ['none', null, 'hidden'], ['button', 'binary', 'binary'], ['snap', 'binary', 'binary'],
    ['zipper', 'binary', 'graded'], ['unknown', 'binary', 'graded'],
    ['belt', 'graded', 'binary'], ['belt', null, 'binary'],
    ['button', 'graded', 'graded'], ['snap', null, 'graded'],
  ]) assert.equal(closure.outerClosureRange(front(kind, range)), expected);
  for (const malformed of [null, undefined, [], 'binary', {}, { kind: 'none' },
    front('invalid', 'binary'), front('none', 'invalid'), front('none', null, { version: 2 })]) {
    assert.equal(closure.outerClosureRange(malformed), 'graded');
  }
});

test('binary display maps every saved value without rewriting it', () => {
  const expected = ['closed', 'closed', 'closed', 'open', 'open'];
  values.forEach((value, i) => {
    assert.equal(closure.outerClosureDisplayValue(value, 'binary'), expected[i]);
    assert.equal(closure.outerClosureDisplayValue(value, 'graded'), value);
  });
  assert.equal(closure.outerClosureDisplayValue(null, 'binary'), 'open');
});

test('card captions use the long chest and waist wording and hide absent closures', () => {
  assert.equal(closure.outerClosureCaption('open', 'hidden', DB.catalogs.outerClosureStates), null);
  assert.equal(closure.outerClosureCaption('openToChest', 'graded'), '가슴까지 열림');
  assert.equal(closure.outerClosureCaption('openToWaist', 'graded'), '배까지 열림');
  assert.equal(closure.outerClosureCaption('partial', 'binary'), '전체 닫힘');
  assert.equal(closure.outerClosureCaption('openToWaist', 'binary'), '전체 열림');
  assert.equal(closure.outerClosureCaption('invalid', 'graded'), '전체 열림');
  assert.equal(closure.outerClosureCaption('partial', 'graded', [{ value: 'partial', label: '짧은 이름', caption: '카드 문구' }]), '카드 문구');
});

test('catalog exposes the five stops in contract order with separate card captions', () => {
  assert.deepEqual(DB.catalogs.outerClosureStates, [
    { value: 'closed', label: '전체 닫힘', caption: '전체 닫힘' },
    { value: 'openToChest', label: '가슴까지', caption: '가슴까지 열림' },
    { value: 'partial', label: '반만 열림', caption: '반만 열림' },
    { value: 'openToWaist', label: '배까지', caption: '배까지 열림' },
    { value: 'open', label: '전체 열림', caption: '전체 열림' },
  ]);
});

test('Field hides none without a label and renders an accessible five or two stop slider', () => {
  assert.equal(renderToStaticMarkup(React.createElement(Field, { value: 'open', frontClosure: front('none', null), id: 'hidden' })), '');
  for (const [range, max, height] of [['graded', 4, 140], ['binary', 1, 68]]) {
    const html = renderToStaticMarkup(React.createElement(Control, { value: 'openToChest', range, labelledBy: 'closure-label' }));
    assert.match(html, /role="slider"/);
    assert.match(html, /aria-orientation="vertical"/);
    assert.match(html, /aria-labelledby="closure-label"/);
    assert.match(html, new RegExp(`aria-valuemax="${max}"`));
    assert.match(html, new RegExp(`viewBox="0 0 36 ${height}"`));
    assert.equal((html.match(/<button/g) || []).length, max + 1);
    assert.doesNotMatch(html, /style=/);
  }
});

// 실제 React 부품의 이벤트를 실행한다. 훅 저장소와 포인터 좌표, 프레임 시계만 대체한다.
async function controlHarness({ value = 'closed', range = 'graded', reduced = false } = {}) {
  const source = read('src/components/OuterClosureControl.jsx').replace(/^import[\s\S]*?;\n/gm, '').replace(/^export default /gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, 'OuterClosureControl.jsx', { jsx: 'transform' });
  const hooks = [], frames = new Map(), changes = [], effects = [];
  let cursor = 0, now = 0, frameId = 0, tree;
  const state = (initial) => {
    const slot = cursor++;
    if (!(slot in hooks)) hooks[slot] = typeof initial === 'function' ? initial() : initial;
    return [hooks[slot], (next) => { hooks[slot] = typeof next === 'function' ? next(hooks[slot]) : next; }];
  };
  const ref = (initial) => { const slot = cursor++; return hooks[slot] ||= { current: initial }; };
  const effect = (callback, deps) => {
    const slot = cursor++, previous = hooks[slot];
    if (!previous || deps.some((item, i) => item !== previous.deps[i])) {
      effects.push(() => { previous?.cleanup?.(); hooks[slot] = { deps, cleanup: callback() }; });
    }
  };
  const context = {
    React, useState: state, useRef: ref, useEffect: effect,
    ...closure, requestAnimationFrame: (callback) => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: (id) => frames.delete(id), performance: { now: () => now },
    matchMedia: () => ({ matches: reduced }),
  };
  const component = new Function(...Object.keys(context), `${code}; return OuterClosureControl;`)(...Object.values(context));
  const walk = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) { const found = walk(child, predicate); if (found) return found; }
    return null;
  };
  const railNode = { focus() {}, setPointerCapture() {}, releasePointerCapture() {}, hasPointerCapture: () => true,
    getBoundingClientRect: () => ({ top: 0, height: range === 'binary' ? 68 : 140 }) };
  const render = () => {
    cursor = 0; tree = component({ value, range, onChange: (next) => changes.push(next), labelledBy: 'label' });
    walk(tree, (node) => { if (node.ref) node.ref.current = railNode; return false; });
    while (effects.length) effects.shift()();
    return tree;
  };
  render();
  const rail = () => walk(render(), (node) => node.props?.role === 'slider');
  return {
    changes, frames, rail,
    stop(index) { return walk(render(), (node) => node.type === 'button' && node.props['data-index'] === index); },
    pull() { return walk(render(), (node) => node.type === 'g').props.transform; },
    key(key, { capture, bubble, shiftKey = false } = {}) {
      const event = { key, shiftKey, target: { tagName: 'DIV', isContentEditable: false },
        defaultPrevented: false, propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; } };
      capture?.(event);
      if (!event.propagationStopped) rail().props.onKeyDown(event);
      if (!event.propagationStopped) bubble?.(event);
      return event;
    },
    click(index) { walk(render(), (node) => node.type === 'button' && node.props['data-index'] === index).props.onClick(); },
    pointer(type, y, id = 1) { rail().props[type]({ button: 0, pointerType: 'mouse', pointerId: id, clientY: y, currentTarget: railNode, preventDefault() {} }); },
    advance(ms) { now += ms; const pending = [...frames.values()]; frames.clear(); pending.forEach((fn) => fn(now)); },
    setValue(next) { value = next; render(); },
    setRange(next) { range = next; render(); },
    unmount() { hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test('keyboard Home, End and arrows stop at endpoints and commit each change once', async () => {
  const h = await controlHarness();
  h.key('Home'); h.key('ArrowUp'); h.click(0);
  assert.deepEqual(h.changes, []);
  h.key('ArrowDown'); h.key('ArrowRight'); h.key('ArrowLeft'); h.key('End'); h.key('ArrowDown'); h.key('End'); h.key('Home');
  assert.deepEqual(h.changes, ['openToChest', 'partial', 'openToChest', 'open', 'closed']);
  assert.equal(h.rail().props['aria-valuenow'], 0);
  h.unmount();
});

test('closure arrows do not move the selected editor image through the global shortcut', async () => {
  const editor = read('src/features/editor/Editor.jsx');
  const start = editor.indexOf("      if (selEls.length && e.key.startsWith('Arrow')) {");
  const end = editor.indexOf('\n    };\n    window.addEventListener', start);
  let blocks = [{ id: 'block', h: 1000, elements: [
    { id: 'photo', type: 'image', x: 100, y: 100, w: 200, h: 200 },
  ] }];
  const nudge = new Function('selEls', 'kb', 'setBlocks', 'clampDragDelta', 'getBlockRenderHeight',
    `return (e) => {${editor.slice(start, end)}};`)(
    ['photo'], { current: { croppingOn: false } }, (update) => { blocks = update(blocks); },
    clampDragDelta, getBlockRenderHeight,
  );
  const h = await controlHarness();
  try {
    for (const [key, index, shiftKey] of [
      ['ArrowDown', 1, false], ['ArrowRight', 2, false],
      ['ArrowUp', 1, false], ['ArrowLeft', 0, true],
    ]) {
      h.key(key, { bubble: nudge, shiftKey });
      assert.equal(h.rail().props['aria-valuenow'], index);
      assert.deepEqual(blocks[0].elements[0],
        { id: 'photo', type: 'image', x: 100, y: 100, w: 200, h: 200 });
    }
    assert.deepEqual(h.changes, ['openToChest', 'partial', 'openToChest', 'closed']);
  } finally { h.unmount(); }
});

async function storyboardKeyboardGuard(saving) {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('<div className={`wizard wide sb-page');
  const end = source.indexOf('>\n      {/* 완료 가드', start);
  const { code } = await transformWithEsbuild(
    `const tree = ${source.slice(start, end + 1)}null</div>;`, 'StoryboardKeyboardGuard.jsx',
    { jsx: 'transform' },
  );
  return new Function('React', 'atomicSaving', 'initialBoardRevealed', `${code}; return tree;`)(
    React, saving, true,
  ).props.onKeyDownCapture;
}

test('atomic storyboard saving keeps closure display at the saved value until editing resumes', async () => {
  const h = await controlHarness({ value: 'open' });
  try {
    const savingGuard = await storyboardKeyboardGuard(true);
    h.key('ArrowUp', { capture: savingGuard });
    h.key('Home', { capture: savingGuard });
    assert.deepEqual(h.changes, []);
    assert.equal(h.rail().props['aria-valuetext'], '전체 열림');
    const tab = h.key('Tab', { capture: savingGuard });
    assert.equal(tab.defaultPrevented, false, 'saving must still allow keyboard focus navigation');
    const escape = h.key('Escape', { capture: savingGuard });
    assert.equal(escape.defaultPrevented, false);
    h.setValue('open');
    assert.equal(h.rail().props['aria-valuetext'], '전체 열림');
    h.key('ArrowUp', { capture: await storyboardKeyboardGuard(false) });
    assert.deepEqual(h.changes, ['openToWaist']);
    assert.equal(h.rail().props['aria-valuetext'], '배까지 열림');
  } finally { h.unmount(); }
});

test('binary middle values are display-only until a different stop is chosen', async () => {
  const h = await controlHarness({ value: 'partial', range: 'binary' });
  h.click(0); h.key('Home');
  assert.deepEqual(h.changes, []);
  h.key('End'); h.key('ArrowRight');
  assert.deepEqual(h.changes, ['open']);
  h.setValue('openToChest');
  assert.equal(h.rail().props['aria-valuenow'], 0);
  assert.deepEqual(h.changes, ['open']);
  h.unmount();
});

test('drag follows the pointer, previews the nearest stop and saves only once on release', async () => {
  const h = await controlHarness();
  h.pointer('onPointerDown', 14); h.pointer('onPointerMove', 85);
  assert.deepEqual(h.changes, []);
  assert.equal(h.pull(), 'translate(18 85)');
  assert.match(h.stop(3).props.className, / near/);
  assert.equal(h.rail().props['data-pointer'], '1');
  h.pointer('onPointerUp', 85); h.pointer('onLostPointerCapture', 85);
  assert.deepEqual(h.changes, ['openToWaist']);
  assert.equal(h.rail().props['aria-valuetext'], '배까지 열림');
  assert.equal(h.frames.size, 1);
  h.setValue('openToWaist');
  assert.equal(h.frames.size, 1, 'controlled value acknowledgment does not interrupt snapping');
  h.advance(90); assert.equal(h.frames.size, 1);
  h.advance(90); assert.equal(h.frames.size, 0);
  assert.equal(h.pull(), 'translate(18 98)');
  h.setValue('openToWaist');
  h.pointer('onPointerDown', 98); h.pointer('onPointerUp', 98);
  assert.deepEqual(h.changes, ['openToWaist']);
  h.unmount();
});

test('unrelated pointers do not move the handle and cancellation commits at most once', async () => {
  const h = await controlHarness();
  h.pointer('onPointerDown', 14); h.pointer('onPointerMove', 126, 2);
  assert.equal(h.pull(), 'translate(18 14)');
  h.pointer('onPointerMove', 126); h.pointer('onPointerCancel', 126); h.pointer('onLostPointerCapture', 126);
  assert.deepEqual(h.changes, ['open']);
  h.key('Home');
  assert.equal(h.rail().props['data-pointer'], undefined);
  assert.ok(h.frames.size > 0);
  h.unmount(); assert.equal(h.frames.size, 0);
});

test('nearest stops clamp both rails and reduced motion snaps without a frame', async () => {
  const { outerClosureNearestIndex } = await vite.ssrLoadModule('/src/components/OuterClosureControl.jsx');
  for (const [range, y, index] of [['graded', -10, 0], ['graded', 27, 0], ['graded', 29, 1], ['graded', 85, 3], ['graded', 999, 4], ['binary', 33, 0], ['binary', 35, 1]]) {
    assert.equal(outerClosureNearestIndex(y, range), index);
  }
  const h = await controlHarness({ reduced: true });
  h.pointer('onPointerDown', 14); h.pointer('onPointerMove', 70); h.pointer('onPointerUp', 70);
  assert.deepEqual(h.changes, ['partial']); assert.equal(h.frames.size, 0);
  h.unmount();
});

test('storyboard and editor pass analysis to Field while preserving the request value', () => {
  const storyboard = read('src/features/storyboard/Storyboard.jsx');
  const panel = read('src/features/editor/EditorPanels.jsx');
  assert.match(storyboard, /<OuterClosureField[\s\S]*?frontClosure=\{catalogs\.frontClosure\}/);
  assert.match(storyboard, /frontClosure: a\?\.frontClosure/);
  assert.match(storyboard, /outerClosureCaption\(block\.outerClosureState/);
  assert.match(storyboard, /normalizeOuterClosureState\(current\.outerClosureState\)/);
  assert.match(panel, /<OuterClosureField[\s\S]*?frontClosure=\{frontClosure\}/);
  assert.match(panel, /outerClosureState: showOuterClosure \? outerClosure : null/);
  assert.match(read('src/features/editor/Editor.jsx'), /frontClosure=\{analysis\?\.frontClosure\}/);
});

test('example gallery has exactly the requested two pixel top gap', () => {
  const rule = read('src/styles/features.css').match(/\.sb-exgallery\s*\{([^}]+)\}/)?.[1];
  assert.match(rule, /margin-top:\s*2px\s*;/);
});

test('actual storyboard cards show long captions and remove hidden or product closure text', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const captionSource = source.slice(source.indexOf('function cardLabels('), source.indexOf('/* 컷 이동 손잡이 아이콘'));
  const { code } = await transformWithEsbuild(captionSource, 'StoryboardCaption.jsx', { jsx: 'transform' });
  const context = { React, useContext: React.useContext, ...closure, WORN_CUT_TYPES: new Set(['styling', 'horizon']) };
  const Caption = new Function(...Object.keys(context), `${code}; return StoryboardCaption;`)(...Object.values(context));
  const render = (value, frontClosure, cutType = 'styling', source = 'ai') => renderToStaticMarkup(React.createElement(Caption, {
    block: { source, cutType, shot: 'full', outerClosureState: value },
    catalogs: { ...DB.catalogs, frontClosure }, colorOpts: [], clothingType: 'outer',
  }));
  assert.match(render('openToChest', front('zipper')), /가슴까지 열림/);
  assert.match(render('openToWaist', front('zipper')), /배까지 열림/);
  assert.match(render('partial', front('button', 'binary')), /전체 닫힘/);
  assert.match(render('openToWaist', front('belt', null), 'horizon'), /전체 열림/);
  assert.doesNotMatch(render('open', front('none', null)), /아우터 열림 정도|전체 열림/);
  assert.doesNotMatch(render('open', front('zipper'), 'product'), /아우터 열림 정도|전체 열림/);
  assert.equal(render('open', front('zipper'), 'styling', 'mine'), '<div class="sb-canvas-caption mine">내 사진</div>');
});

test('actual AI form keeps hidden closure state in worn requests and sends null for product', async () => {
  const panel = read('src/features/editor/EditorPanels.jsx');
  const source = panel.slice(panel.indexOf('const NEW_CUT_DEFAULT_SHOT'), panel.indexOf('/* ---------- 의류 (wardrobe library)'))
    .replace('export function AIPanel', 'function AIPanel');
  const { code } = await transformWithEsbuild(source, 'AIPanel.jsx', { jsx: 'transform' });
  const hooks = []; let cursor, tree;
  const useState = (initial) => {
    const slot = cursor++;
    if (!(slot in hooks)) hooks[slot] = initial;
    return [hooks[slot], (value) => { hooks[slot] = typeof value === 'function' ? value(hooks[slot]) : value; }];
  };
  const context = {
    React, useState, useRef: () => ({ current: null }), OuterClosureField: Field,
    ...await vite.ssrLoadModule('/src/lib/storyboardTaxonomy.js'),
    ...await vite.ssrLoadModule('/src/lib/storyboardExampleSelection.js'),
    ...await vite.ssrLoadModule('/src/lib/detailRecommendations.js'),
    ...await vite.ssrLoadModule('/src/lib/generationExamples.js'),
    ...await vite.ssrLoadModule('/src/features/analysis/modelSelection.js'),
    ...await vite.ssrLoadModule('/src/lib/productSourcePhotos.js'),
  };
  for (const name of ['Button', 'Icon', 'Chips', 'UnderlineTabs', 'ColorDots', 'MoodGuide', 'PanelHead', 'DetailTargetPicker', 'ProductSourcePicker']) context[name] = () => null;
  const AIPanel = new Function(...Object.keys(context), `${code}; return AIPanel;`)(...Object.values(context));
  const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) { const found = find(child, predicate); if (found) return found; }
    return null;
  };
  const requests = [];
  const props = { catalogs: DB.catalogs, clothingType: 'outer', onGenerate: (request) => requests.push(request), frontClosure: front('zipper') };
  const render = () => { cursor = 0; tree = AIPanel(props); return tree; };
  const generate = () => find(render(), (node) => node.type === context.Button && node.props.icon === 'sparkles').props.onClick();
  render();
  const field = () => find(render(), (node) => node.type === Field);
  assert.deepEqual(field().props.frontClosure, front('zipper'));
  field().props.onChange('openToChest'); generate();
  assert.equal(requests.at(-1).outerClosureState, 'openToChest');
  field().props.onChange('openToWaist'); props.frontClosure = front('none', null);
  assert.equal(renderToStaticMarkup(React.createElement(Field, field().props)), '');
  generate(); assert.equal(requests.at(-1).outerClosureState, 'openToWaist');
  find(render(), (node) => node.type === context.UnderlineTabs).props.onChange('product');
  assert.equal(field(), null); generate(); assert.equal(requests.at(-1).outerClosureState, null);
  find(render(), (node) => node.type === context.UnderlineTabs).props.onChange('styling');
  generate(); assert.equal(requests.at(-1).outerClosureState, 'open');
});

test('mock analysis shows graded zipper for outer and unknown for all other garments', async () => {
  assert.deepEqual(DB.analysis.frontClosure, front('unknown'));
  for (const clothingType of ['outer', 'top', 'bottom', 'dress']) {
    const result = await mockApi.analyzeProduct(null, { product: { ...DB.product, clothingType } });
    assert.deepEqual(result.frontClosure, front(clothingType === 'outer' ? 'zipper' : 'unknown'));
  }
});

test('HTTP public, job, saved and custom-match analysis retain frontClosure or default to null', async (t) => {
  assert.equal(defaultAnalysisShape().frontClosure, null);
  const previousFetch = globalThis.fetch;
  let saved = { clothingType: 'outer', frontClosure: front('belt', 'binary'), matchClothing: [] };
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url);
    if (path === 'https://test.invalid/photo') return new Response(new Blob(['photo'], { type: 'image/png' }));
    if (options.method === 'PATCH') { posts.push(JSON.parse(options.body)); return Response.json(saved); }
    if (path.endsWith('/v1/public/analyze')) return Response.json({ data: saved });
    if (path.endsWith('/analyze')) return Response.json({ jobId: 'closure-job' });
    if (path.includes('/v1/jobs/')) return Response.json({ status: 'done', result: { data: saved } });
    if (path.includes('match-candidates')) return Response.json([]);
    if (path.endsWith('/custom-match-item') && options.method === 'POST') return Response.json({ item: {}, analysis: saved });
    if (options.method === 'DELETE') return Response.json({});
    return Response.json(saved);
  };
  t.after(() => { globalThis.fetch = previousFetch; });
  const product = { colors: [{ images: [{ slot: 'Front', src: 'https://test.invalid/photo' }] }] };
  for (const expected of [front('belt', 'binary'), null]) {
    saved = { clothingType: 'outer', matchClothing: [], ...(expected ? { frontClosure: expected } : {}) };
    assert.deepEqual((await httpAdapter.publicAnalyze(product)).frontClosure, expected);
    assert.deepEqual((await httpAdapter.analyzeProduct('closure-project')).frontClosure, expected);
    assert.deepEqual((await httpAdapter.getAnalysis('closure-project')).frontClosure, expected);
    assert.deepEqual((await httpAdapter.saveAnalysis('closure-project', { washCare: '손세탁' })).frontClosure, expected);
    assert.deepEqual((await httpAdapter.addCustomMatchItem('closure-project', { assetIds: ['asset'] })).analysis.frontClosure, expected);
    assert.deepEqual((await httpAdapter.removeCustomMatchItem('closure-project')).analysis.frontClosure, expected);
    assert.deepEqual((await httpAdapter.refreshMatchClothing('closure-project')).frontClosure, expected);
  }
  await httpAdapter.saveAnalysis('closure-project', { frontClosure: front('zipper') });
  assert.deepEqual(posts.at(-1).frontClosure, front('zipper'), 'client does not strip the server-owned field from a patch');
});
