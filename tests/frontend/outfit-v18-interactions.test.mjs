import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, drain, textOf } from './helpers/outfitHarness.mjs';
import { applyOutfitSet, syncOutfitLegacySelection } from '../../src/lib/outfitSelections.js';
const item = (id, extras = {}) => ({ id, name: id, thumb: `/${id}.png`, isCompatible: true, ...extras });
const rec = (status = 'ready') => ({ version: 1, contextKey: 'top-1', status, fallbackReady: true,
  sets: [{ slots: { bottom: 'pants-1', footwear: 'shoe-1', bag: 'bag-1' } }, { slots: { bottom: 'pants-2', footwear: 'shoe-2', bag: 'bag-2' } }],
  candidates: { bottom: [item('pants-1'), item('pants-2')], footwear: [item('shoe-1'), item('shoe-2')], bag: [item('bag-1'), item('bag-2')] }, roleStatus: { accessory: 'unavailable' } });
const initial = (status) => syncOutfitLegacySelection({ clothingType: 'top', selectedModelId: 'mA',
  matchClothing: [item('pants-1', { clothingType: 'bottom' }), item('pants-2', { clothingType: 'bottom' })], outfitRecommendation: rec(status),
  outfitSelections: { version: 1, setIndex: 0, optional: { bag: false, accessory: false }, slots: { bottom: { itemId: 'pants-1', origin: 'ai' }, footwear: { itemId: 'shoe-1', origin: 'ai' } } } });
const byName = (tree, name) => nodes(tree).filter(node => node.type?.name === name);
function rafClock(t) {
  let now = 0, id = 0; const frames = new Map();
  t.mock.method(performance, 'now', () => now);
  t.mock.method(globalThis, 'requestAnimationFrame', fn => { frames.set(++id, fn); return id; });
  t.mock.method(globalThis, 'cancelAnimationFrame', id => frames.delete(id));
  return (time) => { now = time; const current = [...frames.values()]; frames.clear(); current.forEach(fn => fn(time)); };
}
// Node has no requestAnimationFrame; only the deterministic callback queue is installed.
if (!globalThis.requestAnimationFrame) globalThis.requestAnimationFrame = () => 0;
if (!globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame = () => {};

test('처음에는 모두 접히고 모델이 첫 줄이며 한 줄만 펼친다', async (t) => {
  rafClock(t); const h = await outfitHarness(); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const props = { analysis: initial(), models: [{ id: 'mA', displayName: 'Mia', thumb: '/model.png' }], onChange() {} };
  let tree = h.render(OutfitSelection, props);
  const rows = () => nodes(tree).filter(node => node.type === 'section' && node.props['aria-label']?.endsWith('선택'));
  assert.deepEqual(rows().map(node => node.props['aria-label']), ['모델 선택', '하의 선택', '신발 선택', '가방 선택']);
  assert.equal(nodes(tree).filter(node => node.props['aria-expanded'] === true).length, 0);
  nodes(rows()[1]).find(node => node.props['aria-controls']).props.onClick(); tree = h.render(OutfitSelection, props);
  assert.equal(rows()[1].props['data-open'], true);
  nodes(rows()[2]).find(node => node.props['aria-controls']).props.onClick(); tree = h.render(OutfitSelection, props);
  assert.equal(rows()[1].props['data-open'], false); assert.equal(rows()[2].props['data-open'], true);
});

test('서랍에서 고른 뒤에도 열려 있고 닫으면 펼친 줄을 유지한다', async (t) => {
  rafClock(t); const h = await outfitHarness(); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const props = { analysis: initial(), onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  let tree = h.render(OutfitSelection, props);
  nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-bottom').props.onClick(); tree = h.render(OutfitSelection, props);
  byName(tree, 'OutfitMore')[0].props.onClick(); tree = h.render(OutfitSelection, props);
  byName(tree, 'OutfitDrawer')[0].props.onSelect('pants-2'); tree = h.render(OutfitSelection, props);
  assert.equal(byName(tree, 'OutfitDrawer').length, 1);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'pants-2');
  assert.deepEqual(props.analysis.matchClothing.filter(item => item.selected).map(item => item.id), ['pants-2']);
  byName(tree, 'OutfitDrawer')[0].props.onClose(); tree = h.render(OutfitSelection, props);
  assert.equal(byName(tree, 'OutfitDrawer').length, 0);
  assert.equal(nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-bottom').props['aria-expanded'], true);
});

test('소품을 켜면 판이 열리고 끄면 선택을 기억한 채 닫힌다', async (t) => {
  rafClock(t); const h = await outfitHarness(); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const props = { analysis: initial(), onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  let tree = h.render(OutfitSelection, props);
  const toggle = () => nodes(tree).find(node => node.props.role === 'switch');
  assert.equal(toggle().props['aria-checked'], false);
  toggle().props.onClick(); tree = h.render(OutfitSelection, props);
  assert.equal(toggle().props['aria-checked'], true); assert.equal(props.analysis.outfitSelections.slots.bag.itemId, 'bag-1');
  assert.equal(byName(tree, 'OutfitTile').length, 2);
  byName(tree, 'OutfitTile')[1].props.onSelect('bag-2'); tree = h.render(OutfitSelection, props);
  toggle().props.onClick(); tree = h.render(OutfitSelection, props);
  assert.equal(byName(tree, 'OutfitTile').length, 0);
  assert.equal(props.analysis.outfitSelections.slots.bag.itemId, 'bag-2');
});

test('색을 바꿔도 내 옷 자리는 격자의 마지막 칸이다', async (t) => {
  rafClock(t); const h = await outfitHarness(); t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  const props = { analysis: initial(), onChange() {}, onUpload() {} }; let tree = h.render(OutfitSelection, props);
  nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-bottom').props.onClick(); tree = h.render(OutfitSelection, props);
  byName(tree, 'OutfitColorChips')[0].props.onChange('black'); tree = h.render(OutfitSelection, props);
  assert.equal(byName(tree, 'OutfitUploadTile').length, 1);
  assert.equal(byName(tree, 'OutfitTile').length, 0);
});

test('폴백 화면은 서버의 늦은 세트를 자동 적용하는 outfitSetIndex 명령을 보내지 않는다', async (t) => {
  const h = await outfitHarness(); t.after(() => h.close()); const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  let patch; const tree = h.render(OutfitSelection, { analysis: initial('fallback'), onChange(value) { patch = value; } });
  nodes(tree).find(node => node.type === 'button' && textOf(node).includes('AI 추천')).props.onClick();
  assert.equal(patch.outfitSelections.setIndex, 1); assert.equal('outfitSetIndex' in patch, false);
});

test('늦은 ready가 저장 응답으로 오면 이전 pending 폴링을 적용하지 않는다', async (t) => {
  let now = 0; t.mock.method(Date, 'now', () => now);
  const callbacks = [];
  t.mock.method(globalThis, 'setTimeout', fn => { callbacks.push(fn); return callbacks.length; });
  t.mock.method(globalThis, 'clearTimeout', () => {});
  const pending = initial('pending');
  const h = await outfitHarness({ getOutfitRecommendation: async () => pending }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  callbacks.length = 0;
  const props = { analysis: pending, onRecommendation(fn) { props.analysis = fn(props.analysis); }, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  h.render(useOutfitRecommendation, props); await drain(); h.render(useOutfitRecommendation, props);
  now = 1600; callbacks[0](); h.render(useOutfitRecommendation, props);
  const ready = rec('ready'); ready.sets[0].slots.bottom = 'pants-2';
  props.analysis = { ...props.analysis, outfitRecommendation: ready };
  h.render(useOutfitRecommendation, props); let state = h.render(useOutfitRecommendation, props);
  assert.equal(state.lateRecommendation.status, 'ready'); assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'pants-1');
  state.acceptLate(); state = h.render(useOutfitRecommendation, props);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'pants-2'); assert.equal(state.recommendation.status, 'ready');
});

for (const changed of [false, true]) test(`내 옷 정리 완료는 ${changed ? '중간 선택을 보존한다' : '정돈본을 한 번만 선택한다'}`, async (t) => {
  const frame = rafClock(t); t.mock.method(URL, 'createObjectURL', () => 'blob:original'); t.mock.method(URL, 'revokeObjectURL', () => {});
  const h = await outfitHarness(); t.after(() => h.close()); const { useCustomOutfitUpload } = await h.load('/src/features/analysis/useCustomOutfitUpload.js');
  const calls = []; const props = { analysis: initial(), onChange(patch) { calls.push(patch); props.analysis = { ...props.analysis, ...patch }; }, onRefresh(value) { props.analysis = value; } };
  let state = h.render(useCustomOutfitUpload, props); state.begin({}); state = h.render(useCustomOutfitUpload, props);
  const custom = item('custom', { isCustom: true, clothingType: 'bottom', cutoutStatus: 'processing', thumb: '/original.jpg' });
  state.receive({ ...props.analysis, matchClothing: [...props.analysis.matchClothing, custom] }); state = h.render(useCustomOutfitUpload, props);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'pants-1');
  if (changed) { state.noteSelection(); props.analysis = { ...props.analysis, ...applyOutfitSet(props.analysis, 1) }; }
  frame(7000);
  props.analysis = { ...props.analysis, matchClothing: props.analysis.matchClothing.map(item => item.id === 'custom' ? { ...item, cutoutStatus: 'ready', thumb: '/tidy.png' } : item) };
  h.render(useCustomOutfitUpload, props); state = h.render(useCustomOutfitUpload, props);
  assert.equal(calls.length, 0, '고리가 다 찰 때까지 선택을 그대로 둔다');
  frame(9500); state = h.render(useCustomOutfitUpload, props); frame(11000);
  assert.equal(calls.length, changed ? 0 : 1); assert.equal(state.upload, null);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, changed ? 'pants-2' : 'custom');
});

for (const terminal of ['ready', 'fallback']) test(`첫 1.5초 안에 고른 옷은 pending 왕복 뒤 ${terminal} 도착에도 유지한다`, async (t) => {
  let now = 0; t.mock.method(Date, 'now', () => now);
  const timers = []; t.mock.method(globalThis, 'setTimeout', fn => { timers.push(fn); return timers.length; }); t.mock.method(globalThis, 'clearTimeout', () => {});
  let response = initial('pending'); const h = await outfitHarness({ getOutfitRecommendation: async () => response }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  timers.length = 0;
  let replacements = 0;
  const props = { analysis: initial('pending'), onRecommendation(fn) { replacements++; props.analysis = fn(props.analysis); }, onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  h.render(useOutfitRecommendation, props); await drain(); let state = h.render(useOutfitRecommendation, props);
  now = 100; state.markEdited(); props.analysis.outfitSelections.slots.bottom = { itemId: 'pants-2', origin: 'user' };
  state = h.render(useOutfitRecommendation, props);
  props.analysis = { ...props.analysis, outfitRecommendation: rec('pending') };
  h.render(useOutfitRecommendation, props); state = h.render(useOutfitRecommendation, props);
  assert.equal(state.recommendation.status, 'fallback', 'pending 저장 응답이 고르기 시작한 표시를 되돌리지 않는다');
  response = initial(terminal); now = 1000; await timers[1](); await drain(); state = h.render(useOutfitRecommendation, props);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'pants-2');
  if (terminal === 'ready') { assert.equal(state.lateRecommendation.status, 'ready'); assert.equal(replacements, 0); }
});
