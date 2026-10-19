import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { outfitHarness, drain } from './helpers/outfitHarness.mjs';
let vite, api, sync, adapter, supabase, promotion, clientEvents, createClientEventCollector;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
  promotion = await vite.ssrLoadModule('/src/lib/productPhotoPromotion.js');
  ({ clientEvents, createClientEventCollector } = await vite.ssrLoadModule('/src/lib/clientEvents.js'));
  api.promoteConfirmedGptEvidence ??= async () => {};
});
after(async () => { await vite?.close(); });

for (const code of ['outfit_context_changed', 'internal_error', 'api_network']) test(`추천 승격 ${code}는 기록하고 사진 완료와 후속 확정을 허용한다`, async t => {
  const projectId = `optional-${code}`, events = [], reports = [], failures = [], logged = [];
  const collector = createClientEventCollector({ env: {}, uuid: () => '11111111-1111-4111-8111-111111111111',
    transport: async (_url, { body }) => { logged.push(JSON.parse(body)); return { ok: true }; } });
  const choices = { version: 1, slots: { bottom: { itemId: 'bottom', origin: 'user' } } };
  const error = Object.assign(new Error('추천 승격 실패'), { code, status: code === 'outfit_context_changed' ? 409 : 500 });
  const replacement = {
    saveProduct: async () => events.push('product'),
    saveAnalysis: async (_id, data, options) => {
      assert.deepEqual(data.outfitSelections, choices);
      assert.equal(options.recommendOutfit, false);
      events.push('analysis');
    },
    promoteOutfitRecommendation: async () => { events.push('outfit'); throw error; },
    promoteConfirmedGptEvidence: async () => events.push('evidence'),
    patchProject: async () => events.push('project'), getCustomMatchDraft: () => null,
  };
  for (const [key, value] of Object.entries(replacement)) t.mock.method(api, key, value);
  t.mock.method(clientEvents, 'report', details => { reports.push(details); return collector.report(details); });
  const unsubscribe = promotion.onProductPhotoPromotionFailure((...args) => failures.push(args));
  t.after(() => { unsubscribe(); promotion.clearProductPhotoPromotionTask(projectId); sync.resetDraftSyncSingleFlight(); });
  const task = promotion.startProductPhotoPromotion(projectId, ({ onPhotoProgress }) => sync.promoteDraftToProject({
    product: { colors: [] }, photos: [], analysis: { outfitSelections: choices,
      outfitRecommendationHandoff: { signature: 'signed' }, confirmedGptProductEvidenceHandoff: { signature: 'evidence' } },
  }, { projectId, onPhotoProgress }));
  await promotion.productPhotosReady(projectId);
  assert.equal(task.status, 'settled');
  assert.equal(task.error, null);
  assert.deepEqual(failures, []); // The shell's photo-upload toast only subscribes to failures.
  assert.deepEqual(events, ['product', 'analysis', 'outfit', 'evidence', 'project']);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].error, error);
  assert.equal(reports[0].error.code, code);
  assert.equal(reports[0].messageCode, 'outfit_recommendation_promotion');
  assert.equal(reports[0].blocking, false);
  assert.equal(reports[0].context.projectId, projectId);
  assert.equal(logged[0].error_code, code);
  assert.equal(logged[0].message_code, 'outfit_recommendation_promotion');
  assert.equal(logged[0].blocking, false);
});

for (const stage of ['uploadPhoto', 'saveProduct', 'saveAnalysis']) test(`${stage}의 실제 실패는 사진 작업 failed와 기존 오류 안내 경로를 유지한다`, async t => {
  const projectId = `required-${stage}`, failures = [], reports = [];
  const error = Object.assign(new Error('필수 저장 실패'), { code: 'api_network' });
  const replacement = { uploadPhoto: async () => ({ assetId: 'asset', url: '/photo' }),
    saveProduct: async () => {}, saveAnalysis: async () => {},
    promoteOutfitRecommendation: async () => assert.fail('failed saves cannot promote'),
    patchProject: async () => assert.fail('failed saves cannot confirm'), getCustomMatchDraft: () => null };
  replacement[stage] = async () => { throw error; };
  for (const [key, value] of Object.entries(replacement)) t.mock.method(api, key, value);
  t.mock.method(clientEvents, 'report', details => reports.push(details));
  const unsubscribe = promotion.onProductPhotoPromotionFailure((...args) => failures.push(args));
  t.after(() => { unsubscribe(); promotion.clearProductPhotoPromotionTask(projectId); sync.resetDraftSyncSingleFlight(); });
  const task = promotion.startProductPhotoPromotion(projectId, ({ onPhotoProgress }) => sync.promoteDraftToProject({
    product: { colors: [] }, photos: [{ imageId: `photo-${stage}`, mime: 'image/png', blob: new Blob(['photo']) }],
    analysis: { outfitRecommendationHandoff: { signature: 'signed' } },
  }, { projectId, onPhotoProgress }), { recoverDraftSlot: async () => {} });
  await assert.rejects(promotion.productPhotosReady(projectId), error);
  assert.equal(task.status, 'failed');
  assert.equal(task.error, error);
  assert.deepEqual(failures, [[projectId, error]]);
  assert.deepEqual(reports, []);
});
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

for (const truncation of ['save', 'promotion']) test(`multi-accessory ${truncation} truncation must preserve saved choices before confirmation`, async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const choices = { version: 1, optional: { accessory: true }, slots: {
    accessory: { itemId: 'ear', extraIds: ['neck', 'hat'], origin: 'user' },
  } };
  const draft = { product: { colors: [] }, photos: [], analysis: { clothingType: 'top',
    outfitSelections: choices, outfitRecommendationHandoff: { signature: 'signed' } } };
  const original = structuredClone(draft), events = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    if (String(url).endsWith('/outfit-capabilities')) return new Response(JSON.stringify({ props: true, maxAccessories: 3 }),
      { status: 200, headers: { 'Content-Type': 'application/json' } });
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
  if (truncation === 'save') {
    await assert.rejects(sync.promoteDraftToProject(draft), { code: 'outfit_selection_not_saved', projectId: 'multi-draft' });
    assert.deepEqual(events, ['save']);
  } else {
    await sync.promoteDraftToProject(draft);
    assert.deepEqual(events, ['save', 'promotion', 'save', 'confirmed']);
  }
  assert.deepEqual(draft, original);
});

for (const readiness of ['missing', 'warming']) test(`stale multi draft stays unconfirmed without analysis writes while readiness is ${readiness}`, async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const draft = { product: { colors: [] }, photos: [], analysis: { clothingType: 'top',
    outfitSelections: { version: 1, optional: { accessory: true }, slots: {
      accessory: { itemId: 'ear', extraIds: ['neck', 'hat'], origin: 'user' },
    } }, outfitRecommendationHandoff: { signature: 'signed' } } };
  const original = structuredClone(draft), events = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    const caps = String(url).endsWith('/outfit-capabilities');
    if (['PATCH', 'POST'].includes(options.method)) events.push(options.method);
    const value = caps ? { props: false, maxAccessories: 1 }
      : { clothingType: 'top', outfitSelections: draft.analysis.outfitSelections, outfitRecommendation: { contextKey: 'saved' } };
    return new Response(JSON.stringify(value), { status: caps && readiness === 'missing' ? 404 : 200,
      headers: { 'Content-Type': 'application/json' } });
  });
  for (const [key, value] of Object.entries({ createProject: async () => ({ id: 'stale-multi' }),
    saveProduct: async () => {}, saveAnalysis: adapter.saveAnalysis.bind(adapter),
    promoteOutfitRecommendation: adapter.promoteOutfitRecommendation.bind(adapter),
    patchProject: async () => events.push('confirmed'), getCustomMatchDraft: () => null })) t.mock.method(api, key, value);
  t.after(() => sync.resetDraftSyncSingleFlight());
  await assert.rejects(sync.promoteDraftToProject(draft), { code: 'outfit_selection_not_saved', projectId: 'stale-multi' });
  assert.deepEqual(events, []);
  assert.deepEqual(draft, original);
});
