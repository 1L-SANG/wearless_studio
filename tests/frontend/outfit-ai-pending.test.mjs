import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, textOf, drain } from './helpers/outfitHarness.mjs';

const notice = 'AI 코디가 준비됐어요. AI 추천을 눌러 확인해 보세요';
const choice = itemId => ({ itemId, origin: 'ai' });
const recommendation = (status, count = 2, contextKey = 'context') => ({ status, contextKey, fallbackReady: true,
  sets: Array.from({ length: count }, (_, i) => ({ slots: { bottom: `${status}-${i}`, footwear: `shoe-${i}` } })),
  candidates: { bottom: ['pending-0', 'pending-1', 'ready-0', 'ready-1', 'manual'].map(id => ({ id, name: id, clothingType: 'bottom' })) },
});
const initial = (status = 'pending', count = 2) => ({ clothingType: 'top',
  matchClothing: recommendation(status).candidates.bottom.map(item => ({ ...item, selected: item.id === 'pending-0' })),
  outfitSelections: { version: 1, setIndex: 0, optional: { bag: false, accessory: false },
    slots: { bottom: choice('pending-0'), footwear: choice('shoe-0') } },
  outfitRecommendation: recommendation(status, count),
});
const button = tree => nodes(tree).find(node => node.type === 'button' && textOf(node).startsWith('AI 추천'));

async function setup(t, analysis = initial()) {
  let now = 0;
  const requests = [];
  const h = await outfitHarness({ getOutfitRecommendation: (_projectId, { signal }) =>
    new Promise((resolve, reject) => { requests.push({ resolve, reject, signal }); }) });
  t.after(() => h.close());
  const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
  t.mock.method(Date, 'now', () => now);
  const timers = [];
  t.mock.method(globalThis, 'setTimeout', (fn, ms) => { timers.push({ fn, ms }); return timers.length; });
  t.mock.method(globalThis, 'clearTimeout', id => { if (timers[id - 1]) timers[id - 1].fn = null; });
  t.mock.method(globalThis, 'requestAnimationFrame', () => 0);
  t.mock.method(globalThis, 'cancelAnimationFrame', () => {});
  const patches = [];
  const props = { analysis, onRecommendation(fn) { props.analysis = fn(props.analysis); },
    onChange(patch) { patches.push(patch); props.analysis = { ...props.analysis, ...patch }; } };
  const render = () => { h.render(OutfitSelection, props); return h.render(OutfitSelection, props); };
  return { h, props, patches, render, requests, setNow: value => { now = value; },
    expire() { now = 1500; timers.find(timer => timer.ms === 1500).fn(); },
    runTimer(ms) { timers.find(timer => timer.ms === ms && timer.fn)?.fn(); },
    async deliver(rec, source = 'poll') {
      if (source === 'poll') { requests.at(-1).resolve({ outfitRecommendation: rec }); await drain(); }
      else props.analysis = { ...props.analysis, outfitRecommendation: rec };
      return render();
    } };
}

test('35초 동안 조회가 끝나지 않으면 대체 세트를 넘길 수 있다', async t => {
  const s = await setup(t); s.render(); s.expire(); s.render();
  s.setNow(34_999);
  assert.equal(button(s.render()).props.disabled, true);
  s.setNow(35_000); s.runTimer(35_000);
  let tree = s.render();
  assert.equal(button(tree).props.disabled, false);
  assert.equal(s.requests[0].signal.aborted, true, '시간이 지난 조회는 취소한다');
  tree = await s.deliver(recommendation('pending'), 'props');
  assert.equal(button(tree).props.disabled, false, '같은 추천의 이전 pending 응답으로 다시 잠기지 않는다');
  button(tree).props.onClick();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'pending-1');
  assert.deepEqual(s.h.runtime.toasts, []);
});

test('만료된 추천 조회는 바로 대기를 풀고 재조회하지 않는다', async t => {
  const s = await setup(t); s.render(); s.setNow(100);
  s.requests[0].reject(Object.assign(new Error('만료된 추천 조회'), { code: 'invalid_analysis_handoff' }));
  await drain();
  let tree = s.render();
  assert.equal(button(tree).props.disabled, false);
  s.runTimer(1000); await drain();
  assert.equal(s.requests.length, 1, '만료된 정보로 같은 상태를 다시 조회하지 않는다');
  tree = s.render();
  button(tree).props.onClick();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'pending-1');
  assert.deepEqual(s.h.runtime.toasts, []);
});

if (!globalThis.requestAnimationFrame) globalThis.requestAnimationFrame = () => 0;
if (!globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame = () => {};

for (const count of [0, 1, 2]) test(`pending 세트 ${count}개: 뼈대 종료·직접 선택 뒤에도 버튼이 잠기고 숫자는 숨긴다`, async t => {
  const s = await setup(t, initial('pending', count));
  let tree = s.render();
  assert.ok(button(tree), '세트가 없어도 추천 버튼을 표시한다');
  assert.equal(button(tree).props.disabled, true);
  assert.equal(button(tree).props['data-pending'], true);
  assert.equal(textOf(button(tree)), 'AI 추천');
  s.expire(); tree = s.render();
  assert.equal(button(tree).props.disabled, true);
  assert.equal(textOf(button(tree)), 'AI 추천');
  const status = nodes(tree).find(node => node.props.role === 'status' && textOf(node) === 'AI 코디를 준비하고 있어요');
  assert.ok(status, '뼈대가 끝나도 보조 기술에 대기 상태를 알린다');
  assert.equal(button(tree).props['aria-describedby'], status.props.id);
  const before = structuredClone(s.props.analysis.outfitSelections);
  button(tree).props.onClick();
  assert.deepEqual(s.props.analysis.outfitSelections, before, '대기 중 대체 세트 넘기기도 차단한다');
  nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-bottom').props.onClick();
  tree = s.render();
  nodes(tree).find(node => node.type?.name === 'OutfitTile' && node.props.item.id === 'manual').props.onSelect('manual');
  tree = s.render();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'manual');
  assert.equal(button(tree).props.disabled, true);
  assert.equal(textOf(button(tree)), 'AI 추천');
  assert.deepEqual(s.h.runtime.toasts, []);
});

for (const source of ['poll', 'props']) test(`늦은 ${source} ready: 알림 한 번, 첫 클릭은 AI 첫 세트, 그 다음은 다음 세트`, async t => {
  const s = await setup(t);
  let tree = s.render();
  nodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-bottom').props.onClick();
  tree = s.render();
  nodes(tree).find(node => node.type?.name === 'OutfitTile' && node.props.item.id === 'manual').props.onSelect('manual');
  s.render(); s.setNow(2000);
  tree = await s.deliver(recommendation('ready'), source);
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'manual');
  assert.equal(button(tree).props.disabled, false);
  assert.equal(button(tree).props['data-pending'], undefined);
  assert.equal(textOf(button(tree)), 'AI 추천');
  assert.deepEqual(s.h.runtime.toasts, [[notice]]);
  for (let i = 0; i < 3; i++) tree = await s.deliver(recommendation('ready'), 'props');
  assert.deepEqual(s.h.runtime.toasts, [[notice]], '같은 contextKey 응답은 한 번만 알린다');
  button(tree).props.onClick(); tree = s.render();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'ready-0');
  assert.equal(s.props.analysis.outfitSelections.setIndex, 0);
  assert.equal(s.patches.at(-1).outfitSetIndex, 0);
  assert.equal(textOf(button(tree)), 'AI 추천1 / 2');
  button(tree).props.onClick(); tree = s.render();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'ready-1');
  assert.equal(s.props.analysis.outfitSelections.setIndex, 1);
  assert.equal(textOf(button(tree)), 'AI 추천2 / 2');
  assert.deepEqual(s.h.runtime.toasts, [[notice]]);
});

test('fallback 세트가 없어도 늦은 AI 단일 세트는 한 번 적용할 수 있다', async t => {
  const s = await setup(t, initial('pending', 0)); s.render(); s.setNow(2000);
  let tree = await s.deliver(recommendation('ready', 1));
  assert.equal(button(tree).props.disabled, false);
  button(tree).props.onClick(); tree = s.render();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'ready-0');
  assert.equal(button(tree).props.disabled, true);
  assert.equal(textOf(button(tree)), 'AI 추천1 / 1');
});

test('편집하지 않은 채 1.5초가 지난 ready도 선택을 유지하고 알린다', async t => {
  const s = await setup(t); s.render(); s.expire(); s.render();
  const tree = await s.deliver(recommendation('ready'));
  assert.equal(button(tree).props.disabled, false);
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'pending-0');
  assert.deepEqual(s.h.runtime.toasts, [[notice]]);
});

for (const source of ['poll', 'props']) test(`1.5초 안 ${source} automaticReady는 알림 없이 적용한다`, async t => {
  const s = await setup(t); s.render(); s.setNow(1499);
  const tree = await s.deliver(recommendation('ready'), source);
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'ready-0');
  assert.equal(button(tree).props.disabled, false);
  assert.equal(textOf(button(tree)), 'AI 추천1 / 2');
  assert.deepEqual(s.h.runtime.toasts, []);
});

test('진입 전 ready와 재진입한 ready는 알림 없이 기존 다음 세트 동작을 쓴다', async t => {
  const s = await setup(t, initial('ready')); let tree = s.render();
  assert.deepEqual(s.h.runtime.toasts, []);
  button(tree).props.onClick(); tree = s.render();
  assert.equal(s.props.analysis.outfitSelections.slots.bottom.itemId, 'ready-1');
  s.h.unmount(); s.render();
  assert.deepEqual(s.h.runtime.toasts, []);
});

for (const count of [0, 1, 2]) test(`실패 후 fallback ${count}개: 알림 없이 기존 활성 조건으로 돌아간다`, async t => {
  const s = await setup(t, initial('pending', count)); s.render(); s.setNow(2000);
  const tree = await s.deliver(recommendation('fallback', count));
  if (count) {
    assert.equal(button(tree).props.disabled, count < 2);
    assert.equal(textOf(button(tree)), `AI 추천1 / ${count}`);
  } else assert.equal(button(tree), undefined);
  assert.deepEqual(s.h.runtime.toasts, []);
});

test('같은 contextKey는 수락 전 재진입·새 pending 왕복에도 다시 알리지 않는다', async t => {
  const s = await setup(t); s.render(); s.setNow(2000);
  await s.deliver(recommendation('ready'));
  assert.deepEqual(s.h.runtime.toasts, [[notice]]);
  s.h.unmount(); s.render(); s.setNow(4000);
  await s.deliver(recommendation('ready'));
  assert.deepEqual(s.h.runtime.toasts, [[notice]]);
  s.props.analysis = { ...s.props.analysis, outfitRecommendation: recommendation('pending', 2, 'next') };
  s.render(); s.setNow(6000);
  await s.deliver(recommendation('ready', 2, 'next'), 'props');
  assert.deepEqual(s.h.runtime.toasts, [[notice], [notice]], '새 문맥은 별도로 한 번 알린다');
  s.props.analysis = { ...s.props.analysis, outfitRecommendation: recommendation('pending') };
  s.render(); s.setNow(8000);
  await s.deliver(recommendation('ready'), 'props');
  assert.deepEqual(s.h.runtime.toasts, [[notice], [notice]]);
});

test('새 contextKey에 도착한 옛 응답은 버튼을 풀거나 알리지 않는다', async t => {
  const s = await setup(t); s.render();
  s.props.analysis = { ...s.props.analysis, outfitRecommendation: recommendation('pending', 0, 'new') };
  // 새 props 렌더와 이전 폴링 effect 정리 사이의 응답도 막아야 한다.
  s.h.runtime.index = 0; s.h.runtime.effects = [];
  const { OutfitSelection } = await s.h.load('/src/features/analysis/OutfitSelection.jsx');
  OutfitSelection(s.props);
  s.setNow(2000);
  await s.deliver(recommendation('ready'));
  const tree = s.render();
  assert.equal(button(tree).props.disabled, true);
  assert.equal(textOf(button(tree)), 'AI 추천');
  assert.deepEqual(s.h.runtime.toasts, []);
});

test('소품만 pending이면 세트 추천 버튼을 잠그지 않는다', async t => {
  const analysis = initial('ready');
  analysis.outfitRecommendation.props = { bag: { status: 'pending', picks: [] } };
  const s = await setup(t, analysis); const tree = s.render();
  assert.equal(button(tree).props.disabled, false);
  assert.equal(textOf(button(tree)), 'AI 추천1 / 2');
  assert.deepEqual(s.h.runtime.toasts, []);
});
