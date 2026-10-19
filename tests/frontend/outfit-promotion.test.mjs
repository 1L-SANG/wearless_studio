import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { outfitHarness, drain } from './helpers/outfitHarness.mjs';
let vite, api, sync, adapter, supabase;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
});
after(async () => { await vite?.close(); });
test('확정 승격은 후보를 클라이언트 저장에서 제외하고 사진 저장 뒤 서명과 선택을 검증한다', async (t) => {
  const events = [], handoff = { signature: 'signed' };
  const choices = { version: 1, slots: { footwear: { itemId: null, origin: 'user' } } };
  const replacement = {
    createProject: async () => ({ id: 'project' }), uploadPhoto: async () => ({ assetId: 'asset', url: '/photo' }),
    saveProduct: async () => events.push('product'),
    saveAnalysis: async (_id, data, options) => {
      assert.equal(options?.recommendOutfit, false);
      events.push('analysis');
      assert.equal('outfitRecommendation' in data, false);
      assert.equal('outfitRecommendationHandoff' in data, false);
      assert.deepEqual(data.outfitSelections, choices);
    },
    promoteOutfitRecommendation: async (id, value, selections) => {
      events.push('outfit'); assert.equal(id, 'project'); assert.deepEqual(value, handoff); assert.deepEqual(selections, choices);
    },
    patchProject: async () => events.push('project'), getCustomMatchDraft: () => null,
  };
  for (const [key, value] of Object.entries(replacement)) t.mock.method(api, key, value);
  t.after(() => sync.resetDraftSyncSingleFlight());
  await sync.promoteDraftToProject({ product: { colors: [] }, photos: [], analysis: { clothingType: 'dress',
    outfitSelections: choices, outfitRecommendation: { candidates: { footwear: [] } }, outfitRecommendationHandoff: handoff } });
  assert.deepEqual(events, ['product', 'analysis', 'outfit', 'project']);
});

for (const timing of ['fast', 'late', 'edited']) test(`${timing} 추천 뒤 확정 요청의 선택은 훅이 표시한 선택과 같다`, async t => {
  let now = 0, resolve;
  t.mock.method(Date, 'now', () => now);
  const h = await outfitHarness({ getOutfitRecommendation: () => new Promise(done => { resolve = done; }) });
  t.after(() => h.close());
  const { useOutfitRecommendation } = await h.load('/src/features/analysis/useOutfitRecommendation.js');
  const props = { analysis: { clothingType: 'dress', outfitSelections: { version: 1,
    slots: { footwear: { itemId: 'fallback-shoes', origin: 'ai' } } },
    outfitRecommendationHandoff: { signature: 'pending' }, outfitRecommendation: {
      contextKey: 'context', status: 'pending', sets: [{ slots: { footwear: 'fallback-shoes' } }] } },
    onRecommendation(fn) { props.analysis = fn(props.analysis); },
    onChange(patch) { props.analysis = { ...props.analysis, ...patch }; } };
  const state = h.render(useOutfitRecommendation, props);
  now = timing === 'late' ? 1600 : 100;
  if (timing === 'edited') state.markEdited();
  resolve({ outfitRecommendationHandoff: { signature: 'ready' }, outfitRecommendation: {
    contextKey: 'context', status: 'ready', sets: [{ slots: { footwear: 'ready-shoes' } }] } });
  await drain(); h.render(useOutfitRecommendation, props);
  assert.equal(props.analysis.outfitSelections.slots.footwear.itemId, timing === 'fast' ? 'ready-shoes' : 'fallback-shoes');
  const displayed = structuredClone(props.analysis.outfitSelections);
  const confirmations = [];
  const replacement = {
    createProject: async () => ({ id: 'project' }), saveProduct: async () => {},
    saveAnalysis: async (_id, data) => confirmations.push(data.outfitSelections),
    promoteOutfitRecommendation: async (_id, _handoff, selections) => confirmations.push(selections),
    patchProject: async () => {}, getCustomMatchDraft: () => null,
  };
  for (const [key, value] of Object.entries(replacement)) t.mock.method(api, key, value);
  t.after(() => sync.resetDraftSyncSingleFlight());
  await sync.promoteDraftToProject({ product: { colors: [] }, photos: [], analysis: props.analysis });
  assert.deepEqual(confirmations, [displayed, displayed]);
});

for (const truncation of ['save', 'promotion']) test(`saved multi-accessory draft cannot confirm after ${truncation} loses extras`, async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const choices = { version: 1, optional: { accessory: true }, slots: {
    accessory: { itemId: 'ear', extraIds: ['neck', 'hat'], origin: 'user' },
  } };
  const draft = { product: { colors: [] }, photos: [], analysis: { clothingType: 'top',
    outfitSelections: choices, outfitRecommendationHandoff: { signature: 'signed' } } };
  const original = structuredClone(draft), events = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    const promotion = String(url).endsWith(':promote');
    if (options.method === 'PATCH') events.push('save');
    if (promotion) events.push('promotion');
    const outfitSelections = truncation === 'promotion' && !promotion ? choices
      : { ...choices, slots: { accessory: { itemId: 'ear', origin: 'user' } } };
    return new Response(JSON.stringify({ clothingType: 'top', outfitSelections, outfitRecommendation: { contextKey: 'saved' } }),
      { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  for (const [key, value] of Object.entries({ createProject: async () => ({ id: 'multi-draft' }),
    saveProduct: async () => {}, saveAnalysis: adapter.saveAnalysis.bind(adapter),
    promoteOutfitRecommendation: adapter.promoteOutfitRecommendation.bind(adapter),
    patchProject: async () => events.push('confirmed'), getCustomMatchDraft: () => null })) t.mock.method(api, key, value);
  t.after(() => sync.resetDraftSyncSingleFlight());
  await assert.rejects(sync.promoteDraftToProject(draft), { code: 'outfit_selection_not_saved', projectId: 'multi-draft' });
  assert.deepEqual(events, truncation === 'save' ? ['save'] : ['save', 'promotion']);
  assert.deepEqual(draft, original);
});
