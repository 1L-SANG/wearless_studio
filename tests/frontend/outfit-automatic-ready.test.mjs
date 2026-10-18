import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, drain } from './helpers/outfitHarness.mjs';

const choice = (itemId, origin = 'ai') => ({ itemId, origin });
const initial = () => ({ clothingType: 'top',
  matchClothing: ['fallback', 'ready', 'custom'].map(id => ({ id, clothingType: 'bottom', selected: id === 'fallback' })),
  outfitSelections: { version: 1, setIndex: 0, optional: { bag: false, accessory: false },
    slots: { bottom: choice('fallback'), footwear: choice('old-shoes'), bag: choice('remembered-bag'), accessory: choice(null, 'user') } },
  outfitRecommendation: { contextKey: 'context', status: 'pending', fallbackReady: true,
    sets: [{ slots: { bottom: 'fallback', footwear: 'old-shoes' } }] } });
const ready = () => ({ outfitRecommendation: { contextKey: 'context', status: 'ready',
  sets: [{ slots: { bottom: 'ready', footwear: 'new-shoes', bag: 'new-bag', accessory: 'new-accessory' } }] } });

for (const source of ['poll', 'props']) for (const parent of ['replace', 'change']) {
  test(`1.5초 안 ${source} ready는 ${parent} 경로로 세트 0 실제 선택을 저장한다`, async t => {
    let now = 0, resolve;
    t.mock.method(Date, 'now', () => now);
    const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(done => { resolve = done; }) });
    t.after(() => h.close());
    const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
    const patches = [];
    const props = { analysis: initial(), onChange(patch) { patches.push(patch); props.analysis = { ...props.analysis, ...patch }; } };
    if (parent === 'replace') props.onRecommendation = fn => { props.analysis = fn(props.analysis); };
    h.render(useOutfitRecommendation, props);
    now = 1499;
    if (source === 'poll') { resolve(ready()); await drain(); }
    else props.analysis = { ...props.analysis, ...ready() };
    h.render(useOutfitRecommendation, props);
    const state = h.render(useOutfitRecommendation, props);
    assert.equal(state.skeleton, false);
    assert.equal(state.lateRecommendation, null);
    assert.deepEqual(props.analysis.outfitSelections.slots.bottom, choice('ready'));
    assert.deepEqual(props.analysis.outfitSelections.slots.footwear, choice('new-shoes'));
    assert.deepEqual(props.analysis.outfitSelections.slots.bag, choice('remembered-bag'));
    assert.deepEqual(props.analysis.outfitSelections.slots.accessory, choice(null, 'user'));
    assert.deepEqual(props.analysis.matchClothing.filter(item => item.selected).map(item => item.id), ['ready']);
    const selections = patches.filter(patch => patch.outfitSelections);
    assert.equal(selections.length, 1);
    assert.equal('outfitSetIndex' in selections[0], false, '서버 세트 재적용 대신 화면의 실제 선택을 저장한다');
    assert.deepEqual(selections[0].outfitSelections, props.analysis.outfitSelections);
  });
}

test('자동 세트 적용은 기존 origin user 선택과 비운 칸을 보존한다', async t => {
  const h = await outfitHarness({ getOutfitRecommendation: async () => ready() }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis: initial(), onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  props.analysis.outfitSelections.slots.bottom = choice('custom', 'user');
  h.render(useOutfitRecommendation, props); await drain(); h.render(useOutfitRecommendation, props);
  assert.deepEqual(props.analysis.outfitSelections.slots.bottom, choice('custom', 'user'));
  assert.deepEqual(props.analysis.outfitSelections.slots.footwear, choice('new-shoes'));
});

for (const scenario of ['deadline', 'late', 'edited-before-render', 'external-edit']) {
  test(`${scenario} ready는 현재 선택을 보존하고 AI 코디 보기에 남긴다`, async t => {
    let now = 0, resolve;
    t.mock.method(Date, 'now', () => now);
    const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(done => { resolve = done; }) }); t.after(() => h.close());
    const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
    const patches = [];
    const props = { analysis: initial(), onRecommendation(fn) { props.analysis = fn(props.analysis); },
      onChange(patch) { patches.push(patch); props.analysis = { ...props.analysis, ...patch }; } };
    const state = h.render(useOutfitRecommendation, props);
    now = scenario === 'deadline' ? 1500 : scenario === 'late' ? 2000 : 100;
    if (scenario === 'edited-before-render') state.markEdited();
    if (scenario === 'external-edit') {
      props.analysis = { ...props.analysis, outfitSelections: { ...props.analysis.outfitSelections,
        slots: { ...props.analysis.outfitSelections.slots, bottom: choice('custom', 'user') } } };
      h.render(useOutfitRecommendation, props);
    }
    const selected = structuredClone(props.analysis.outfitSelections);
    resolve(ready()); await drain();
    const actual = h.render(useOutfitRecommendation, props);
    assert.deepEqual(props.analysis.outfitSelections, selected);
    assert.equal(patches.length, 0);
    assert.equal(actual.lateRecommendation?.status, 'ready');
  });
}

test('문맥 변경 렌더 뒤 effect 정리 전 온 옛 ready는 새 상품 선택을 바꾸지 않는다', async t => {
  let resolve;
  const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(done => { resolve = done; }) });
  t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const patches = [];
  const props = { analysis: initial(), onRecommendation(fn) { props.analysis = fn(props.analysis); },
    onChange(patch) { patches.push(patch); props.analysis = { ...props.analysis, ...patch }; } };
  h.render(useOutfitRecommendation, props);
  props.analysis = { ...initial(), outfitRecommendation: { ...initial().outfitRecommendation, contextKey: 'new-context' } };
  const before = structuredClone(props.analysis);
  // React can commit new props before passive effects cancel the old request.
  h.runtime.index = 0; h.runtime.effects = [];
  useOutfitRecommendation(props);
  resolve(ready()); await drain();
  assert.deepEqual(props.analysis, before);
  assert.equal(patches.length, 0);
  h.runtime.effects.splice(0).forEach(effect => effect());
});
