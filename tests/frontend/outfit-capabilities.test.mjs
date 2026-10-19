import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, drain, nodes, textOf } from './helpers/outfitHarness.mjs';

for (const name of ['requestAnimationFrame', 'cancelAnimationFrame']) globalThis[name] ??= () => {};
const capabilities = { props: true, maxAccessories: 3 };
const initial = () => ({ clothingType: 'top', matchClothing: [],
  outfitSelections: { version: 1, setIndex: 0, optional: { bag: false, accessory: false }, slots: { accessory: { itemId: 'ear', origin: 'ai' } } },
  outfitRecommendation: { contextKey: 'context', status: 'ready', candidates: { accessory: [
    { id: 'ear', category: '귀걸이' }, { id: 'neck', category: '목걸이' }, { id: 'hat', category: '모자' },
  ] }, sets: [{ slots: { accessory: 'ear' } }, { slots: { accessory: 'neck' } }] } });
const toggle = tree => nodes(tree).find(node => node.props.role === 'switch');
const tile = (tree, id) => nodes(tree).find(node => node.type?.name === 'OutfitTile' && node.props.item.id === id);
function setup(h, OutfitSelection) {
  const props = { analysis: initial(), onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  return { props, render: () => h.render(OutfitSelection, props) };
}

test('old API toggle keeps a usable single accessory without calling role recommendation', async t => {
  let requests = 0;
  const h = await outfitHarness({ getOutfitCapabilities: async () => ({ props: false, maxAccessories: 1 }),
    recommendOutfitProps: async () => { requests++; return {}; } }); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const { props, render } = setup(h, OutfitSelection);
  render(); await drain(); toggle(render()).props.onClick(); let tree = render(); await drain();
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'ear');
  assert.equal(requests, 0);
  tile(tree, 'neck').props.onSelect('neck'); tree = render();
  assert.deepEqual(props.analysis.outfitSelections.slots.accessory, { itemId: 'neck', origin: 'user' });
  nodes(tree).find(node => node.type === 'button' && textOf(node).includes('AI 추천')).props.onClick();
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'neck');
});

test('pending capability cannot add a second accessory and new support enables three', async t => {
  let resolve;
  const h = await outfitHarness({ getOutfitCapabilities: () => new Promise(done => { resolve = done; }),
    recommendOutfitProps: async () => ({}) }); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const { props, render } = setup(h, OutfitSelection);
  props.analysis.outfitSelections.optional.accessory = true;
  props.analysis.outfitRecommendation.props = { accessory: { status: 'ready', picks: ['ear'] } };
  let tree = render(); nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-accessory').props.onClick();
  tree = render(); tile(tree, 'neck').props.onSelect('neck');
  assert.deepEqual(props.analysis.outfitSelections.slots.accessory, { itemId: 'neck', origin: 'user' });
  resolve(capabilities); await drain(); tree = render(); tile(tree, 'ear').props.onSelect('ear');
  tree = render(); tile(tree, 'hat').props.onSelect('hat');
  assert.deepEqual(props.analysis.outfitSelections.slots.accessory, { itemId: 'neck', extraIds: ['ear', 'hat'], origin: 'user' });
});

test('unmounted capability query is aborted and cannot start a queued props request', async t => {
  let resolve, signal, requests = 0;
  const h = await outfitHarness({ getOutfitCapabilities: options => { signal = options.signal; return new Promise(done => { resolve = done; }); },
    recommendOutfitProps: async () => { requests++; return {}; } });
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const { render } = setup(h, OutfitSelection);
  toggle(render()).props.onClick(); await h.close();
  resolve(capabilities); await drain();
  assert.equal(signal.aborted, true);
  assert.equal(requests, 0);
});

test('a new props endpoint returning no role result clears pending with a usable fallback', async t => {
  const h = await outfitHarness({ getOutfitCapabilities: async () => capabilities,
    recommendOutfitProps: async () => ({ outfitRecommendation: initial().outfitRecommendation }) }); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const { props, render } = setup(h, OutfitSelection);
  render(); await drain(); toggle(render()).props.onClick(); render(); await drain();
  const tree = render();
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'ear');
  assert.equal(nodes(tree).some(node => node.props.role === 'status' && textOf(node).includes('준비')), false);
});

for (const timing of ['fast', 'late']) test(`old API ${timing} garment result keeps enabled optional choices from legacy sets`, async t => {
  let now = 0, resolve;
  t.mock.method(Date, 'now', () => now);
  const h = await outfitHarness({ getOutfitCapabilities: async () => ({ props: false, maxAccessories: 1 }),
    getOutfitRecommendation: () => new Promise(done => { resolve = done; }) }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis: initial(), onChange(patch) { props.analysis = { ...props.analysis, ...patch }; },
    onRecommendation(fn) { props.analysis = fn(props.analysis); } };
  props.analysis.outfitSelections.optional.accessory = true;
  props.analysis.outfitRecommendation.status = 'pending';
  h.render(useOutfitRecommendation, props); await drain(); h.render(useOutfitRecommendation, props);
  now = timing === 'late' ? 1600 : 100;
  resolve({ outfitRecommendation: { ...props.analysis.outfitRecommendation, status: 'ready', sets: [{ slots: { accessory: 'neck' } }] } });
  await drain(); const state = h.render(useOutfitRecommendation, props);
  if (timing === 'late') state.acceptLate();
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'neck');
});

for (const change of ['context', 'disabled']) test(`queued props request is cancelled when its ${change} changes before capability resolves`, async t => {
  let resolve, requests = 0;
  const h = await outfitHarness({ getOutfitCapabilities: () => new Promise(done => { resolve = done; }),
    recommendOutfitProps: async () => { requests++; return {}; } }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis: initial(), onChange() {} };
  props.analysis.outfitSelections.optional.accessory = true;
  const state = h.render(useOutfitRecommendation, props);
  const request = state.recommendProps('accessory');
  props.analysis = structuredClone(props.analysis);
  if (change === 'context') props.analysis.outfitRecommendation.contextKey = 'changed-context';
  else props.analysis.outfitSelections.optional.accessory = false;
  h.render(useOutfitRecommendation, props); resolve(capabilities); await request;
  assert.equal(requests, 0);
});

test('missing capability method keeps legacy single selection and never requests props', async t => {
  let requests = 0;
  const h = await outfitHarness({ getOutfitCapabilities: undefined,
    recommendOutfitProps: async () => { requests++; return {}; } }); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const { props, render } = setup(h, OutfitSelection);
  toggle(render()).props.onClick(); let tree = render(); await drain();
  tile(tree, 'neck').props.onSelect('neck');
  assert.deepEqual(props.analysis.outfitSelections.slots.accessory, { itemId: 'neck', origin: 'user' });
  assert.equal(requests, 0);
});
