import test from 'node:test';
import assert from 'node:assert/strict';
import * as selections from '../../src/lib/outfitSelections.js';
import { buildOutfitBoardLayout, filterOutfitItems } from '../../src/lib/outfitPresentation.js';
import { outfitHarness, drain, nodes, textOf } from './helpers/outfitHarness.mjs';

const choice = (itemId, origin = 'ai', extraIds) => ({ itemId, origin, ...(extraIds ? { extraIds } : {}) });
const accessories = [
  { id: 'ear1', category: '귀걸이', name: '첫 귀걸이' }, { id: 'ear2', category: '귀걸이' },
  { id: 'neck', category: '목걸이' }, { id: 'hat', category: '모자' }, { id: 'glasses', category: '안경' },
].map(item => ({ ...item, role: 'accessory', isCompatible: true, thumb: `${item.id}.png` }));
const initial = () => ({ clothingType: 'top', matchClothing: [],
  outfitSelections: { version: 1, setIndex: 1, optional: { bag: false, accessory: true }, slots: { accessory: choice('ear1') } },
  outfitRecommendation: { contextKey: 'props-context', status: 'ready', candidates: { accessory: accessories.map(item => ({ ...item })), bag: [{ id: 'bag' }] },
    sets: [{ slots: {} }, { slots: {} }], props: { accessory: { status: 'ready', picks: ['neck', 'hat', 'ear1'] } } } });

test('accessory normalization keeps at most three unique ids and drops extras without a primary id', () => {
  const value = initial(); value.outfitSelections.slots.accessory = choice('ear1', 'user', ['ear1', 'neck', 'hat', 'glasses']);
  assert.deepEqual(selections.normalizeOutfitSelections(value.outfitSelections, 'top').slots.accessory, choice('ear1', 'user', ['neck', 'hat']));
  value.outfitSelections.slots.accessory.itemId = null;
  assert.deepEqual(selections.normalizeOutfitSelections(value.outfitSelections, 'top').slots.accessory, choice(null, 'user'));
});

test('accessory picks toggle, replace within category, preserve primary order and reject a fourth category', () => {
  let analysis = initial();
  const pick = id => { const patch = selections.toggleOutfitAccessorySelection(analysis, id); if (patch) analysis = { ...analysis, ...patch }; return patch; };
  pick('neck'); pick('hat');
  assert.deepEqual(analysis.outfitSelections.slots.accessory, choice('ear1', 'user', ['neck', 'hat']));
  assert.equal(pick('glasses'), null);
  pick('ear2');
  assert.deepEqual(analysis.outfitSelections.slots.accessory, choice('ear2', 'user', ['neck', 'hat']));
  pick('ear2');
  assert.deepEqual(analysis.outfitSelections.slots.accessory, choice('neck', 'user', ['hat']));
  pick('hat'); pick('neck');
  assert.deepEqual(analysis.outfitSelections.slots.accessory, choice(null, 'user'));
});

test('optional toggle uses props at current index and preserves prior user choice and explicit null', () => {
  const analysis = initial();
  assert.equal(selections.toggleOutfitOptional(analysis, 'accessory', true).outfitSelections.slots.accessory.itemId, 'hat');
  analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['neck']);
  assert.deepEqual(selections.toggleOutfitOptional(analysis, 'accessory', true).outfitSelections.slots.accessory, choice('ear1', 'user', ['neck']));
  analysis.outfitSelections.slots.accessory = choice(null, 'user');
  assert.deepEqual(selections.toggleOutfitOptional(analysis, 'accessory', true).outfitSelections.slots.accessory, choice(null, 'user'));
});

test('set application cycles enabled props, removes accessory extras, and ignores obsolete set props', () => {
  const analysis = initial(); analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['neck']);
  analysis.outfitRecommendation.sets[1].slots.accessory = 'glasses';
  assert.deepEqual(selections.applyOutfitSet(analysis, 1).outfitSelections.slots.accessory, choice('hat'));
  analysis.outfitSelections.optional.accessory = false;
  assert.deepEqual(selections.applyOutfitSet(analysis, 1).outfitSelections.slots.accessory, choice('ear1', 'user', ['neck']));
});

test('accessory filtering combines actual category, color and name', () => {
  const items = accessories.map(item => ({ ...item, colorGroup: 'black' }));
  assert.deepEqual(filterOutfitItems(items, { role: 'accessory', category: '모자', color: 'black' }).map(x => x.id), ['hat']);
});

test('board stacks all accessory circles at the right, within common bounds and without collisions', () => {
  const layout = buildOutfitBoardLayout({ clothingType: 'top', width: 180, maxHeight: 210,
    product: { cm: 64, ar: 2.3 }, items: { bottom: { cm: 20, ar: 4 }, footwear: { ar: 4 },
      bag: { src: 'bag.png' }, accessory: accessories.slice(0, 3).map(item => ({ ...item, src: item.thumb })) } });
  const right = layout.accessories.filter(item => item.slot === 'accessory');
  assert.equal(right.length, 3);
  for (const item of [...layout.pieces, ...layout.accessories]) {
    assert.ok(item.x >= 10 - 1e-6 && item.x + item.width <= 170 + 1e-6);
    assert.ok(item.y >= 10 - 1e-6 && item.y + item.height <= 200 + 1e-6);
  }
  for (let i = 1; i < right.length; i++) assert.ok(right[i].y >= right[i - 1].y + right[i - 1].height + 10 - 1e-6);
  const bag = layout.accessories.find(item => item.slot === 'bag');
  for (const circle of layout.accessories) for (const garment of layout.pieces) {
    assert.ok(circle.x + circle.width <= garment.x - 10 + 1e-6 || circle.x >= garment.x + garment.width + 10 - 1e-6);
  }
  assert.ok(bag.x < right[0].x);
});

function globals(t) {
  t.mock.method(globalThis, 'requestAnimationFrame', () => 1);
  t.mock.method(globalThis, 'cancelAnimationFrame', () => {});
}
for (const name of ['requestAnimationFrame', 'cancelAnimationFrame']) globalThis[name] ??= () => {};

test('pending accessory row shows skeleton and status while multiple names use first name and count', async t => {
  globals(t); const h = await outfitHarness({}); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const analysis = initial(); analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['neck', 'hat']);
  const props = { analysis, onChange() {}, models: [] };
  let tree = h.render(OutfitSelection, props);
  assert.match(textOf(tree), /첫 귀걸이 외 2개/);
  analysis.outfitRecommendation = { ...analysis.outfitRecommendation, contextKey: 'new-pending-context', props: { accessory: { status: 'pending', picks: ['ear1'] } } };
  h.render(OutfitSelection, props);
  tree = h.render(OutfitSelection, props);
  assert.ok(nodes(tree).some(node => node.props?.role === 'status' && /액세서리/.test(textOf(node))));
});

for (const [role, label] of [['bag', '가방'], ['accessory', '액세서리']]) {
  test(`${role} pending status announces recommendation preparation with a correct Korean particle`, async t => {
    globals(t); const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(() => {}) });
    t.after(() => h.close());
    const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
    const analysis = initial(); analysis.outfitSelections.optional[role] = true;
    analysis.outfitRecommendation.props[role] = { status: 'pending', picks: [] };
    const tree = h.render(OutfitSelection, { analysis, onChange() {} });
    const row = nodes(tree).find(node => node.props['aria-label'] === `${label} 선택`);
    assert.equal(textOf(nodes(row).find(node => node.props.role === 'status')), `${label} 추천을 준비하고 있어요`);
  });

  for (const terminal of ['ready', 'fallback']) test(`${role} expanded pending grid has three inert skeletons and restores real tiles on ${terminal}`, async t => {
    globals(t); const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(() => {}) });
    t.after(() => h.close());
    const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
    const { default: styles } = await h.load('/src/features/analysis/OutfitSelection.module.css');
    const analysis = initial(); analysis.outfitSelections.optional[role] = false;
    const items = role === 'bag' ? ['bag1', 'bag2', 'bag3'].map(id => ({ id, name: id, thumb: `${id}.png` })) : accessories;
    const picks = role === 'bag' ? ['bag1', 'bag2', 'bag3'] : ['ear1', 'neck', 'hat'];
    analysis.outfitRecommendation.candidates[role] = items;
    analysis.outfitRecommendation.props[role] = { status: 'pending', picks };
    const props = { analysis, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
    let tree = h.render(OutfitSelection, props);
    nodes(tree).find(node => node.props.role === 'switch' && node.props['aria-label'] === `${label} 넣어요`).props.onClick();
    tree = h.render(OutfitSelection, props);
    const panel = () => nodes(tree).find(node => node.props.id === `outfit-panel-${role}`);
    const grid = () => nodes(panel()).find(node => node.props.className === styles.grid);
    const hasClass = (node, name) => (node.props.className || '').split(' ').includes(styles[name]);
    const tiles = () => nodes(grid()).filter(node => node.type?.name === 'OutfitTile');
    assert.equal(tiles().length, 0, 'pending picks must not become actual selectable tiles');
    const placeholders = nodes(grid()).filter(node => hasClass(node, 'tileWrap'));
    assert.equal(placeholders.length, 3);
    for (const placeholder of placeholders) {
      assert.equal(placeholder.props['aria-hidden'], 'true');
      assert.equal(nodes(placeholder).filter(node => hasClass(node, 'tilePicture') && hasClass(node, 'skeleton')).length, 1);
      assert.equal(nodes(placeholder).filter(node => hasClass(node, 'tileName')).length, 1);
      assert.equal(nodes(placeholder).filter(node => hasClass(node, 'skeletonText')).length, 1);
    }
    assert.equal(nodes(grid()).some(node => node.type === 'button' || node.type === 'img' || node.props.onClick || node.props.onSelect
      || node.props['aria-pressed'] !== undefined || hasClass(node, 'selected') || hasClass(node, 'aiBadge')), false);
    const more = nodes(panel()).find(node => node.type?.name === 'OutfitMore');
    assert.deepEqual(more.props.items.map(item => item.id), items.map(item => item.id));
    assert.match(textOf(more.type(more.props)), /전체 .* 보기/);
    assert.equal(typeof more.props.onClick, 'function');

    props.analysis = { ...props.analysis, outfitRecommendation: { ...props.analysis.outfitRecommendation,
      props: { ...props.analysis.outfitRecommendation.props, [role]: { status: terminal, picks } } } };
    h.render(OutfitSelection, props); tree = h.render(OutfitSelection, props);
    assert.equal(tiles().length, 3);
    assert.deepEqual(tiles().map(tile => tile.props.item.id), role === 'bag' ? ['bag2', 'bag1', 'bag3'] : ['neck', 'ear1', 'hat']);
    assert.equal(nodes(grid()).some(node => hasClass(node, 'skeleton') || hasClass(node, 'skeletonText')), false);
    const renderedTiles = tiles().flatMap(tile => nodes(tile.type(tile.props)));
    assert.equal(renderedTiles.filter(node => node.type === 'img').length, 3);
    assert.equal(renderedTiles.filter(node => hasClass(node, 'aiBadge')).length, 3);
    assert.equal(renderedTiles.filter(node => node.props['aria-pressed'] === true).length, 1);
    tiles().find(tile => tile.props.item.id === picks[0]).props.onSelect(picks[0]);
    assert.deepEqual(props.analysis.outfitSelections.slots[role], role === 'bag' ? choice('bag1', 'user') : choice('neck', 'user', ['ear1']));
  });
}

test('props hook requests only absent props, polls despite ready garments, and preserves picks made during pending', async t => {
  let resolve, polls = 0, requests = 0;
  const response = () => ({ outfitRecommendation: { ...initial().outfitRecommendation,
    props: { accessory: { status: 'ready', picks: ['neck', 'hat'] } } } });
  const h = await outfitHarness({ recommendOutfitProps: async (_id, role) => { requests++; assert.equal(role, 'accessory'); return new Promise(done => { resolve = done; }); },
    getOutfitRecommendation: async () => { polls++; return response(); } }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const analysis = initial(); delete analysis.outfitRecommendation.props;
  const props = { analysis, onRecommendation(fn) { props.analysis = fn(props.analysis); }, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  let state = h.render(useOutfitRecommendation, props);
  const request = state.recommendProps('accessory');
  state.recommendProps('accessory'); await drain();
  assert.equal(requests, 1);
  props.analysis = { ...props.analysis, outfitSelections: { ...props.analysis.outfitSelections,
    slots: { ...props.analysis.outfitSelections.slots, accessory: choice('ear1', 'user', ['hat']) } } };
  h.render(useOutfitRecommendation, props);
  resolve(response()); await request; await drain();
  assert.deepEqual(props.analysis.outfitSelections.slots.accessory, choice('ear1', 'user', ['hat']));
  props.analysis = { ...props.analysis, outfitRecommendation: { ...props.analysis.outfitRecommendation,
    props: { accessory: { status: 'pending', picks: ['neck'] } } } };
  h.render(useOutfitRecommendation, props); await drain();
  assert.ok(polls > 0);
});

test('pending props response selects its deterministic current-index pick and a later ready is applied only to AI slots', async t => {
  const analysis = initial(); delete analysis.outfitRecommendation.props; analysis.outfitSelections.slots.accessory = choice(null);
  const rec = status => ({ outfitRecommendation: { ...analysis.outfitRecommendation,
    props: { accessory: { status, picks: status === 'pending' ? ['ear1', 'neck'] : ['hat', 'glasses'] } } } });
  const h = await outfitHarness({ recommendOutfitProps: async () => rec('pending'), getOutfitRecommendation: () => new Promise(() => {}) });
  t.after(() => h.close()); const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis, onRecommendation(fn) { props.analysis = fn(props.analysis); }, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  let state = h.render(useOutfitRecommendation, props); await state.recommendProps('accessory');
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'neck');
  props.analysis = { ...props.analysis, ...rec('ready') };
  state = h.render(useOutfitRecommendation, props); h.render(useOutfitRecommendation, props);
  assert.equal(props.analysis.outfitSelections.slots.accessory.itemId, 'glasses');
});

test('accessory selection rejects inactive and wrong-role candidates', () => {
  const analysis = initial();
  analysis.outfitRecommendation.candidates.accessory.push({ id: 'bad-role', role: 'bag' }, { id: 'inactive', active: false });
  assert.equal(selections.toggleOutfitAccessorySelection(analysis, 'bad-role'), null);
  assert.equal(selections.toggleOutfitAccessorySelection(analysis, 'inactive'), null);
});

for (const rerender of [true, false]) test(`concurrent prop responses preserve completed props and metadata (${rerender ? 'render between' : 'same batch'})`, async t => {
  let bagResolve, accessoryResolve;
  const analysis = initial(); delete analysis.outfitRecommendation.props;
  analysis.outfitSelections.optional.bag = true;
  const response = props => ({ outfitRecommendation: { ...analysis.outfitRecommendation, props,
    propsContextKeys: Object.fromEntries(Object.entries(props).map(([role, value]) => [role, `${role}-${value.status}`])),
    propsExpiresAt: Object.fromEntries(Object.entries(props).map(([role, value]) => [role, value.status === 'ready' ? 200 : 100])) } });
  const h = await outfitHarness({ recommendOutfitProps: (_id, role) => new Promise(done => { if (role === 'bag') bagResolve = done; else accessoryResolve = done; }),
    getOutfitRecommendation: () => new Promise(() => {}) }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis, onRecommendation(fn) { props.analysis = fn(props.analysis); }, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  let state = h.render(useOutfitRecommendation, props);
  const bag = state.recommendProps('bag'); const accessory = state.recommendProps('accessory');
  await drain();
  bagResolve(response({ bag: { status: 'ready', picks: ['bag'] } })); await bag;
  if (rerender) h.render(useOutfitRecommendation, props);
  accessoryResolve(response({ accessory: { status: 'ready', picks: ['hat'] }, bag: { status: 'pending', picks: ['bag'] } })); await accessory;
  state = h.render(useOutfitRecommendation, props);
  assert.equal(state.recommendation.props.bag.status, 'ready');
  assert.equal(state.recommendation.props.accessory.status, 'ready');
  assert.equal(state.recommendation.propsContextKeys.bag, 'bag-ready');
  assert.equal(state.recommendation.propsContextKeys.accessory, 'accessory-ready');
  assert.equal(state.recommendation.propsExpiresAt.bag, 200);
});

test('candidate-aware accessory normalization skips invalid extras before filling the two extra places', () => {
  const analysis = initial();
  analysis.outfitRecommendation.candidates.accessory.push({ id: 'wrong-type', clothingType: 'bag' }, { id: 'disabled', active: false, category: '팔찌' });
  analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['ear2', 'missing', 'wrong-type', 'disabled', 'neck', 'hat']);
  assert.deepEqual(selections.normalizeOutfitSelections(analysis.outfitSelections, 'top', [], analysis.outfitRecommendation.candidates).slots.accessory,
    choice('ear1', 'user', ['neck', 'hat']));
});

test('enabled accessory extras affect generation relevance while disabled remembered extras do not', async () => {
  const { isGenerationRelevantAnalysisPatch } = await import('../../src/lib/generationRelevance.js');
  const analysis = initial(); const changed = structuredClone(analysis.outfitSelections);
  changed.slots.accessory.extraIds = ['hat'];
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitSelections: changed }, analysis), true);
  changed.optional.accessory = false; analysis.outfitSelections.optional.accessory = false;
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitSelections: changed }, analysis), false);
});

test('accessory category chips include only catalog categories and drawer places them before color chips', async t => {
  const h = await outfitHarness({}); t.after(() => h.close());
  const { OutfitCategoryChips } = await h.load('/src/features/analysis/OutfitCatalog.jsx');
  const chips = OutfitCategoryChips({ items: accessories, value: '모자', onChange() {} });
  assert.equal(chips.props['aria-label'], '액세서리 종류');
  assert.deepEqual(nodes(chips).filter(node => node.type === 'button').map(textOf), ['전체', '귀걸이', '목걸이', '모자', '안경']);
  const { OutfitDrawer } = await h.load('/src/features/analysis/OutfitDrawer.jsx');
  const originalDocument = globalThis.document; globalThis.document = { body: {} };
  t.after(() => { globalThis.document = originalDocument; });
  // Render without mounting DOM effects; only verify actual component structure.
  h.runtime.index = 0; h.runtime.effects = [];
  const drawer = OutfitDrawer({ role: 'accessory', label: '액세서리', items: accessories, selectedId: 'ear1', selectedIds: ['ear1', 'hat'], onSelect() {}, onClose() {} });
  const controls = nodes(drawer).filter(node => ['OutfitCategoryChips', 'OutfitColorChips'].includes(node.type?.name));
  assert.deepEqual(controls.map(node => node.type.name), ['OutfitCategoryChips', 'OutfitColorChips']);
});

test('shared server and frontend fixture agrees on filtered extras and props set application', async () => {
  const { readFile } = await import('node:fs/promises');
  const fixture = JSON.parse(await readFile(new URL('../fixtures/outfit-props-contract.json', import.meta.url), 'utf8'));
  const candidates = Object.fromEntries(['accessory', 'bag', 'bottom'].map(role => [role, fixture.items.filter(item => item.clothingType === role)]));
  for (const row of fixture.normalize) assert.deepEqual(
    selections.normalizeOutfitSelections(row.input, 'top', [], candidates).slots.accessory, row.accessory, row.name);
  const applied = selections.applyOutfitSet({ clothingType: 'top', outfitSelections: fixture.apply.input,
    outfitRecommendation: { ...fixture.apply.recommendation, candidates }, matchClothing: candidates.bottom }, fixture.apply.index);
  assert.deepEqual(applied.outfitSelections, fixture.apply.expected);
});

test('fourth accessory category shows the exact brief hint beneath the picker grid', async t => {
  globals(t); const timers = [];
  t.mock.method(globalThis, 'setTimeout', (fn, delay) => { if (delay === 2600) timers.push(fn); return 1; });
  const h = await outfitHarness({}); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const analysis = initial(); analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['neck', 'hat']);
  const props = { analysis, onChange() {} }; let tree = h.render(OutfitSelection, props);
  nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-accessory').props.onClick();
  tree = h.render(OutfitSelection, props);
  nodes(tree).find(node => node.type?.name === 'OutfitMore').props.onClick();
  tree = h.render(OutfitSelection, props);
  nodes(tree).find(node => node.type?.name === 'OutfitDrawer').props.onSelect('glasses');
  tree = h.render(OutfitSelection, props);
  const hint = nodes(tree).find(node => node.type === 'p' && node.props.role === 'status');
  assert.equal(textOf(hint), '액세서리는 3개까지 넣을 수 있어요');
  assert.equal(timers.length, 1); timers[0](); tree = h.render(OutfitSelection, props);
  assert.equal(nodes(tree).some(node => node.type === 'p' && node.props.role === 'status'), false);
});

test('fallback explicit AI set replaces user accessory extras and sends the exact shown selection with its command', async t => {
  globals(t); const h = await outfitHarness({}); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const analysis = initial(); analysis.outfitRecommendation.status = 'fallback'; analysis.outfitSelections.setIndex = 0;
  analysis.outfitSelections.slots.accessory = choice('ear1', 'user', ['neck', 'hat']);
  let saved;
  const tree = h.render(OutfitSelection, { analysis, onChange(patch) { saved = patch; } });
  nodes(tree).find(node => node.type === 'button' && textOf(node).includes('AI 추천')).props.onClick();
  assert.equal(saved.outfitSetIndex, 1);
  assert.deepEqual(saved.outfitSelections.slots.accessory, choice('hat'));
});
