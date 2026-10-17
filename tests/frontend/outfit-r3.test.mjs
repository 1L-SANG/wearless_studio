import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import * as outfit from '../../src/lib/outfitSelections.js';
import { reconcileMatchCompatibility } from '../../src/lib/api/matchingItems.js';
import { outfitHarness, drain } from './helpers/outfitHarness.mjs';

const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const choice = (itemId, origin = 'ai') => ({ itemId, origin });
const initial = (kind = 'top') => {
  const role = outfit.complementaryOutfitSlot(kind);
  return { clothingType: kind, targetGenders: ['women'], subCategory: 'knit',
    matchClothing: [{ id: role, clothingType: role, selected: true }], matchIds: [role],
    outfitSelections: { version: 1, slots: { [role]: choice(role), footwear: choice(null, 'user') } },
    outfitRecommendation: { contextKey: 'old-context', status: 'pending', fallbackReady: true,
      sets: [{ slots: { [role]: role } }], candidates: { [role]: [{ id: role, clothingType: role }] } } };
};
const customPatch = (analysis) => ({
  matchClothing: [...analysis.matchClothing.map(item => ({ ...item, selected: false })),
    { id: 'custom', isCustom: true, clothingType: 'bottom', selected: true, selOrder: 1, thumb: '/custom.png' }],
  matchIds: ['custom'], outfitSelections: { ...analysis.outfitSelections,
    slots: { ...analysis.outfitSelections.slots, bottom: choice('custom', 'user') } },
});

// Execute the real clothing chip handler, replacing unrelated form boundaries only.
const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
function changeType(a, kind) {
  const handler = source.slice(source.indexOf('  const changeType ='), source.indexOf('  const measurementValues ='));
  let patch;
  const bindings = { a, ...outfit, reconcileMatchCompatibility, setMeasurementDraft() {},
    normalizeTargetGendersForClothingType: (_kind, genders) => genders,
    subCategoriesFor: () => [], createMeasurementFields: () => [], withFitProfile: value => value,
    onChange(value) { patch = value; } };
  new Function(...Object.keys(bindings), `${handler}; changeType(${JSON.stringify(kind)});`)(...Object.values(bindings));
  return patch;
}

for (const [beforeKind, afterKind] of [['top', 'bottom'], ['bottom', 'top'], ['outer', 'top']]) {
  test(`종류 칩 ${beforeKind}에서 ${afterKind} 변경은 AI가 채울 수 있는 선택을 저장한다`, () => {
    const a = initial(beforeKind);
    const patch = changeType(a, afterKind);
    assert.ok(patch.outfitSelections, '호환 정리와 직접 해제를 구별하는 정규화 선택을 함께 저장한다');
    const role = outfit.complementaryOutfitSlot(afterKind);
    assert.equal(patch.outfitSelections.slots[role].origin, 'ai');
    assert.deepEqual(patch.outfitSelections.slots.footwear, choice(null, 'user'));
    assert.equal('inner' in patch.outfitSelections.slots, false);
    if (beforeKind !== 'outer') assert.equal(patch.outfitSelections.slots[role].itemId, null);
  });
}

for (const id of [null, 'bottom']) test(`호환되는 사용자 선택 ${id}은 종류 전환과 같은 종류에서 보존한다`, () => {
  const a = initial('outer'); a.outfitSelections.slots.bottom = choice(id, 'user');
  const patch = changeType(a, 'top');
  assert.ok(patch.outfitSelections);
  assert.deepEqual(patch.outfitSelections.slots.bottom, choice(id, 'user'));
  assert.deepEqual(outfit.normalizeOutfitSelections(patch.outfitSelections, 'top').slots.bottom, choice(id, 'user'));
});

for (const [from, to] of [['outer', 'top'], ['top', 'outer'], ['top', 'bottom']]) {
  test(`종류 칩은 옛 선택 구조에서도 ${from}에서 ${to} 변경 시 유효한 선택만 이월한다`, () => {
    const a = initial(from); delete a.outfitSelections;
    const patch = changeType(a, to);
    if (to === 'bottom') {
      assert.deepEqual(patch.outfitSelections.slots.top, choice(null));
      assert.equal('bottom' in patch.outfitSelections.slots, false);
    } else assert.deepEqual(patch.outfitSelections.slots.bottom, choice('bottom', 'user'));
  });
}

let vite, api, mock, remote, savePublicAnalysisEdit;
before(async () => {
  vite = await createServer({ configFile: false, logLevel: 'silent',
    define: { 'import.meta.env.VITE_API_MODE': '"http"' },
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ httpAdapter: remote } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ savePublicAnalysisEdit } = await vite.ssrLoadModule('/src/lib/api/publicAnalysis.js'));
});
after(async () => { await vite?.close(); });

test('공개 초안은 옛 하의 선택을 상의 종류 변경 뒤에도 사용자 선택으로 보존한다', async () => {
  const a = initial('outer'); delete a.outfitSelections;
  await mock.restoreInputDraft({ clothingType: 'outer', colors: [] }, a);
  const saved = await mock.saveAnalysis(null, { clothingType: 'top', matchClothing: a.matchClothing }, { preserveCatalog: true });
  assert.deepEqual(saved.outfitSelections?.slots.bottom, choice('bottom', 'user'));
});

for (const [from, to] of [['top', 'bottom'], ['bottom', 'top'], ['outer', 'top']]) {
  test(`공개 저장 ${from}에서 ${to} 변경은 새 추천 요청에 user null을 만들지 않는다`, async () => {
    const a = initial(from), role = outfit.complementaryOutfitSlot(to);
    await mock.restoreInputDraft({ clothingType: from, colors: [] }, a);
    let calls = 0;
    const saved = await savePublicAnalysisEdit({ clothingType: to,
      matchClothing: reconcileMatchCompatibility(a.matchClothing, to) }, { local: mock,
      remote: { async publicRecommendOutfit(_product, analysis) {
        calls++;
        assert.notDeepEqual(analysis.outfitSelections.slots[role], choice(null, 'user'));
        return { outfitRecommendation: { ...a.outfitRecommendation, contextKey: 'new-context', status: 'fallback' } };
      } } });
    assert.equal(calls, 1);
    assert.equal(saved.outfitRecommendation.contextKey, 'new-context');
  });
}

test('공개 추천 조회 저장 사이에 내 옷을 골라도 저장본과 확정 요청에 남는다', async (t) => {
  const old = initial();
  await mock.restoreInputDraft({ clothingType: 'top', colors: [] }, old);
  const entered = deferred(), release = deferred(), writes = [];
  t.mock.method(remote, 'getOutfitRecommendation', async () => ({ ...old,
    outfitRecommendation: { ...old.outfitRecommendation, status: 'ready' },
    outfitRecommendationHandoff: { signature: 'ready' } }));
  const save = mock.saveAnalysis.bind(mock);
  t.mock.method(mock, 'saveAnalysis', async (...args) => {
    writes.push(args[1]); entered.resolve(); await release.promise; return save(...args);
  });
  const poll = api.getOutfitRecommendation(null, { handoff: { signature: 'pending' } });
  await entered.promise;
  await mock.restoreInputDraft({ clothingType: 'top', colors: [] }, { ...old, ...customPatch(old) }, {
    uploads: [{ blob: new Blob(['photo']), filename: 'custom.png', mime: 'image/png' }] });
  t.after(() => mock.clearCustomMatchDraft());
  release.resolve(); await poll;
  const stored = await mock.getAnalysis(null);
  assert.deepEqual(stored.outfitSelections.slots.bottom, choice('custom', 'user'));
  assert.deepEqual(stored.matchClothing.filter(item => item.selected).map(item => item.id), ['custom']);
  assert.deepEqual(stored.matchIds, ['custom']);
  assert.deepEqual(Object.keys(writes[0]).sort(), ['outfitRecommendation', 'outfitRecommendationHandoff']);
  const { promoteCustomMatch } = await vite.ssrLoadModule('/src/lib/customMatchPromotion.js');
  const confirmation = [];
  const customDraft = mock.getCustomMatchDraft();
  assert.equal(customDraft.selected, true);
  assert.equal(customDraft.localId, 'custom');
  await promoteCustomMatch({ uploadPhoto: async () => ({ assetId: 'upload' }),
    addCustomMatchItem: async () => ({ analysis: { matchClothing: [{ id: 'server-custom', isCustom: true }] } }),
    saveAnalysis: async (_id, patch) => confirmation.push(patch), clearCustomMatchDraft() {} }, 'project', customDraft);
  assert.deepEqual(confirmation, [{ matchClothing: [{ id: 'server-custom', selected: true, selOrder: 1 }] }]);
});

for (const terminal of ['pending', 'ready', 'fallback']) for (const parent of ['replace', 'change']) {
  test(`폴링 ${terminal}, ${parent} 경로는 내 옷과 선택 필드를 갱신하지 않는다`, async (t) => {
    const waiting = deferred(); const h = await outfitHarness({ getOutfitRecommendation: () => waiting.promise });
    t.after(() => h.close());
    const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
    const changes = [];
    const props = { analysis: initial(), onChange(patch) {
      changes.push(patch);
      props.analysis = { ...props.analysis, ...patch };
    } };
    if (parent === 'replace') props.onRecommendation = fn => { props.analysis = fn(props.analysis); };
    h.render(useOutfitRecommendation, props);
    const stale = structuredClone(props.analysis);
    // Upload selection happens outside the catalog's markEdited click handler.
    props.analysis = { ...props.analysis, ...customPatch(props.analysis) };
    const selected = props.analysis;
    h.render(useOutfitRecommendation, props);
    waiting.resolve({ ...stale, outfitRecommendation: { ...stale.outfitRecommendation, status: terminal } });
    await drain(); h.render(useOutfitRecommendation, props);
    for (const patch of changes) assert.ok(Object.keys(patch).every(key => ['outfitRecommendation', 'outfitRecommendationHandoff'].includes(key)));
    assert.deepEqual(props.analysis.outfitSelections, selected.outfitSelections);
    assert.deepEqual(props.analysis.matchClothing, selected.matchClothing);
    assert.deepEqual(props.analysis.matchIds, ['custom']);
  });
}

test('늦은 ready는 내 옷을 유지하고 명시적으로 AI 코디를 적용할 때만 바꾼다', async (t) => {
  let now = 0; t.mock.method(Date, 'now', () => now);
  const waiting = deferred(), changes = [];
  const h = await outfitHarness({ getOutfitRecommendation: () => waiting.promise }); t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis: initial(), onRecommendation(fn) { props.analysis = fn(props.analysis); },
    onChange(patch) { changes.push(patch); props.analysis = { ...props.analysis, ...patch }; } };
  h.render(useOutfitRecommendation, props);
  const stale = structuredClone(props.analysis);
  props.analysis = { ...props.analysis, ...customPatch(props.analysis) };
  h.render(useOutfitRecommendation, props);
  now = 2000;
  waiting.resolve({ ...stale, outfitRecommendation: { ...stale.outfitRecommendation, status: 'ready' } });
  await drain(); const state = h.render(useOutfitRecommendation, props);
  assert.equal(state.lateRecommendation.status, 'ready');
  assert.deepEqual(props.analysis.outfitSelections.slots.bottom, choice('custom', 'user'));
  assert.equal(changes.length, 0);
  state.acceptLate();
  assert.equal(changes.length, 1);
  assert.equal(props.analysis.outfitSelections.slots.bottom.itemId, 'bottom');
  assert.deepEqual(props.analysis.outfitSelections.slots.footwear, choice(null, 'user'));
  assert.equal(props.analysis.matchClothing.some(item => item.id === 'custom'), true);
});

test('공개 폴링 저장 대기 중 문맥이 바뀌면 이전 추천을 쓰지 않는다', async (t) => {
  const old = initial(), entered = deferred(), release = deferred();
  await mock.restoreInputDraft({ clothingType: 'top', colors: [] }, old);
  t.mock.method(remote, 'getOutfitRecommendation', async () => ({ ...old,
    outfitRecommendation: { ...old.outfitRecommendation, status: 'ready' } }));
  const save = mock.saveAnalysis.bind(mock);
  t.mock.method(mock, 'saveAnalysis', async (...args) => { entered.resolve(); await release.promise; return save(...args); });
  const polling = api.getOutfitRecommendation(null);
  await entered.promise;
  const newer = { ...initial('bottom'), outfitRecommendation: { ...initial('bottom').outfitRecommendation, contextKey: 'new-context' } };
  await mock.restoreInputDraft({ clothingType: 'bottom', colors: [] }, newer);
  release.resolve(); await polling;
  const actual = await mock.getAnalysis(null);
  assert.deepEqual(actual.outfitRecommendation, newer.outfitRecommendation);
  assert.deepEqual(actual.outfitSelections, newer.outfitSelections);
});

for (const [from, to] of [['top', 'bottom'], ['bottom', 'top'], ['outer', 'top']]) {
  test(`인증 저장 ${from}에서 ${to} 변경 후 새 문맥 추천을 요청한다`, async (t) => {
    const { supabase } = await vite.ssrLoadModule('/src/lib/supabase.js');
    t.mock.method(supabase.auth, 'getSession', async () => ({ data: { session: { access_token: 'test' } } }));
    const old = initial(from), patch = changeType(old, to), calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
      calls.push({ url: String(url), ...options });
      const body = options.method === 'PATCH' ? { ...old, ...patch }
        : options.method === 'POST' ? { ...old, ...patch, outfitRecommendation: {
          ...old.outfitRecommendation, status: 'fallback', contextKey: 'new-context' } } : old;
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const result = await remote.saveAnalysis('type-switch', patch);
    assert.deepEqual(calls.filter(call => ['PATCH', 'POST'].includes(call.method)).map(call => call.method), ['PATCH', 'POST']);
    const body = JSON.parse(calls.at(-1).body), role = outfit.complementaryOutfitSlot(to);
    assert.equal(body.clothingType, to);
    assert.equal(body.outfitSelections.slots[role].origin, 'ai');
    assert.equal(result.outfitRecommendation.contextKey, 'new-context');
  });
}
