import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import * as limits from '../../src/lib/limits.js';
import * as actions from '../../src/features/editor/canvasAiActions.js';

let vite, Layer, useEditorCreditCosts;
before(async () => {
  vite = await createServer({ configFile: false, logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, esbuild: { jsx: 'automatic' },
    plugins: [{ name: 'pricing-hook-state', enforce: 'pre', transform(source, id) {
      if (!id.endsWith('/CanvasAiLayer.jsx') && !id.endsWith('/useEditorCreditCosts.js')) return;
      source = source.replace(/import \{ api, isMockMode \} from '[^']+';/,
        'const isMockMode = false, api = { getCreditQuote: (...args) => globalThis.__pricingApi(...args) };');
      return source.replace(/import \{[^}]+\} from 'react';/,
        'const useEffect = (...args) => globalThis.__pricingHooks.useEffect(...args), useLayoutEffect = () => {}, useRef = () => ({ current: null }), useState = (...args) => globalThis.__pricingHooks.useState(...args);');
    } }],
  });
  // Only the React lifecycle is substituted; the actual menu handlers and JSX run.
  globalThis.__pricingHooks = { useEffect() {}, useLayoutEffect() {}, useRef: () => ({ current: null }),
    useState: (initial) => [initial, () => {}] };
  ({ CanvasAiLayer: Layer } = await vite.ssrLoadModule('/src/features/editor/CanvasAiLayer.jsx'));
  ({ useEditorCreditCosts } = await vite.ssrLoadModule('/src/features/editor/useEditorCreditCosts.js'));
});
after(async () => { delete globalThis.__pricingHooks; delete globalThis.__pricingApi; await vite?.close(); });

const prices = { editorImage: 23, editorEdit: 7, editorSimilar: 11 };
for (const [kind, action, cost] of [
  ['worn', 'color', 7], ['upload', 'closure', 7], ['worn', 'similar', 11],
  ['ghost', 'color', 23], ['ghost', 'direction', 23], ['detail', 'reshoot', 23],
  ['detail', 'detail', 23], ['planned', 'retry', 23], ['unplanned', 'fill', 23],
]) test(`${kind} ${action} shows its server price and falls back to the old image price`, () => {
  assert.equal(actions.canvasAiActionCreditCost(kind, action, prices), cost);
  assert.equal(actions.canvasAiActionCreditCost(kind, action, { editorImage: 29 }), 29);
});

test('HTTP prices wait for server keys instead of taking new local defaults', () => {
  assert.deepEqual(limits.editorCreditCostsFromQuote(null, 23), { editorImage: 23 });
  assert.deepEqual(limits.editorCreditCostsFromQuote({ editorImage: 29 }, 23), { editorImage: 29 });
  assert.deepEqual(limits.editorCreditCostsFromQuote(prices, 19), prices);
  assert.equal(actions.canvasAiActionCreditCost('worn', 'color', limits.editorCreditCostsFromQuote(null, 19)), 19);
});

for (const quote of [{ editorImage: 29 }, prices]) {
  test(`editor loads server prices without mixing local discounts: ${JSON.stringify(quote)}`, async () => {
    let state = null, effect;
    Object.assign(globalThis.__pricingHooks, {
      useState: () => [state, (value) => { state = value; }], useEffect: (setup) => { effect = setup; },
    });
    globalThis.__pricingApi = async (id) => { assert.equal(id, 'project'); return quote; };
    assert.deepEqual(useEditorCreditCosts('project', limits.CREDIT_COSTS), { editorImage: 19 });
    const cleanup = effect(); await Promise.resolve();
    assert.deepEqual(useEditorCreditCosts('project', limits.CREDIT_COSTS), quote);
    // A previous project's response cannot lend its prices to the next project.
    assert.deepEqual(useEditorCreditCosts('other', limits.CREDIT_COSTS), { editorImage: 19 });
    cleanup();
  });
}

test('fill and blank retries keep the image price even when their internal request is similar', () => {
  for (const request of [
    { mode: 'fill' }, { mode: 'new', variationMode: 'color' },
    { mode: 'vary', variationMode: 'similar', retryBlockId: 'blank' },
    { mode: 'vary', variationMode: 'similar', _contextFill: { slotKey: 'b:e' } },
    { mode: 'vary' },
  ]) assert.equal(limits.editorImageCreditCost(request, prices), 23);
});

function find(tree, predicate) {
  if (!tree || typeof tree !== 'object') return null;
  if (predicate(tree)) return tree;
  for (const child of [tree.props?.children].flat(Infinity)) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}
function harness(props) {
  const state = []; let index;
  Object.assign(globalThis.__pricingHooks, { useEffect() {}, useState(initial) {
    const key = index++;
    if (!(key in state)) state[key] = initial;
    return [state[key], (value) => { state[key] = value; }];
  } });
  const render = () => { index = 0; return Layer(props); };
  return { render, price: () => find(render(), (node) => node.type === 'small')?.props.children };
}

for (const [kind, action, cost] of [
  ['worn', 'color', 5], ['upload', 'closure', 5], ['worn', 'similar', 8],
  ['ghost', 'color', 19], ['ghost', 'direction', 19], ['detail', 'reshoot', 19], ['unplanned', 'fill', 19],
]) test(`real orb ${kind} ${action} hover shows ${cost} credits`, () => {
  const direct = ['similar', 'reshoot', 'fill'].includes(action);
  const menu = { kind, branches: [{ id: action, label: action, direct, description: direct ? '생성해요' : null,
    options: direct ? [] : [{ value: 'choice', label: '선택', description: '선택으로 생성해요' }] }] };
  const h = harness({ block: {}, element: { id: 'e' }, menu, visible: true, creditCosts: { editorImage: 19, editorEdit: 5, editorSimilar: 8 } });
  find(h.render(), (node) => node.props?.className === 'canvas-ai-orb').props.onClick();
  const branch = find(h.render(), (node) => node.props?.role === 'menuitem');
  if (direct) branch.props.onMouseEnter();
  else {
    const saved = globalThis.requestAnimationFrame;
    globalThis.requestAnimationFrame = () => {};
    try { branch.props.onClick(); } finally { globalThis.requestAnimationFrame = saved; }
    find(h.render(), (node) => node.type === 'button' && node.props?.className?.startsWith('canvas-ai-option')).props.onMouseEnter();
  }
  assert.equal(h.price(), `${cost} 크레딧`);
});

test('result frame uses settled credits when server and screen prices differ', () => {
  const h = harness({ block: {}, element: { id: 'e' }, menu: { kind: 'worn', branches: [] }, visible: true,
    creditCosts: { editorImage: 19, editorEdit: 5, editorSimilar: 8 },
    job: { status: 'arrived', action: 'color', request: { mode: 'vary', variationMode: 'color' },
      result: { src: '/result.jpg' }, response: { creditsCharged: 19 } } });
  find(h.render(), (node) => node.props?.className === 'canvas-ai-frame').props.onMouseEnter();
  assert.equal(h.price(), '19 크레딧');
});
