import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { draftStoreFixture, drainDraftTasks } from './helpers/draft-store-fixture.mjs';
import { preparePublicAnalysis } from '../../src/lib/api/publicAnalysisRequest.js';
import { analyzePublicDraft } from '../../src/lib/api/publicAnalysis.js';
let vite, api, runtime, features, sync;
before(async () => {
  vite = await createServer({ configFile: false, define: { 'import.meta.env.VITE_ANALYSIS_FAST_ENTRY': '"1"' },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  runtime = await vite.ssrLoadModule('/src/lib/featureRunDraft.js');
  features = await vite.ssrLoadModule('/src/lib/featureRun.js');
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
});
after(async () => { await vite?.close(); });
const product = (src = 'blob:old') => ({ colors: [{ id: 'base', isBase: true, images: [{ id: 'photo', slot: 'Front', src }] }] });
let draftId = 0;
async function savedDraft(stage = 'requested') {
  const blob = new Blob(['original'], { type: 'image/jpeg' });
  const prepared = await preparePublicAnalysis(product(), { fetchBlob: async () => blob });
  return { product: product(), photos: [{ imageId: 'photo', slot: 'Front', blob, mime: 'image/jpeg' }], updatedAt: 'revision',
    analysis: { sellingPoints: ['예비 핏'], aiSuggestedPoints: ['예비 핏'], featureRun: {
      runId: `restore_run_${++draftId}`, photoSignature: prepared.photoSignature, stage, startedAt: Date.now(), deadlineAt: Date.now() + 30000,
      attempts: 1, coreReceived: true, early: ['예비 핏'], pointsTicket: 'p',
    } } };
}
function replace(t, methods) {
  const old = {}; for (const [name, fn] of Object.entries(methods)) { old[name] = api[name]; api[name] = fn; }
  t.after(() => { for (const [name, fn] of Object.entries(old)) api[name] = fn; sync.resetDraftSyncSingleFlight(); features.setActiveFeatureRun(null); });
}

test('실행 조정자의 핵심 응답에 이전 상품 사진이 섞여도 현재 원본과 Blob을 보존한다', async (t) => {
  const draft = await savedDraft();
  draft.analysis.featureRun.coreReceived = false;
  const storage = draftStoreFixture(t, draft);
  const colors = structuredClone(draft.product.colors);
  const originalBlob = draft.photos[0].blob;
  replace(t, {
    analyzePublicFeatures: () => new Promise(() => {}),
    analyzeProduct: async () => ({ suggestedName: '새 상품 분석', clothingType: 'top',
      sellingPoints: ['이번 상품 특징'], aiSuggestedPoints: ['이번 상품 특징'], pointsTicket: 'p',
      colors: [{ id: 'base', images: [{ id: 'old-product-photo', slot: 'Front', src: '/old-product-photo' }] }] }),
  });
  let updatedProduct;
  const run = runtime.createDraftFeatureRun({ product: draft.product, analysis: draft.analysis,
    onProduct: (next) => { updatedProduct = next; } });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] });
  await run.requestCore({ product: draft.product, photos: [] });
  assert.deepEqual(updatedProduct.colors, colors);
  assert.deepEqual(storage.saved.product.colors, colors);
  assert.equal(storage.saved.photos[0].blob, originalBlob);
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['이번 상품 특징']);
});

test('로그인 복귀 requested 승격은 기한 뒤 재요청을 기다리지 않고 끝내고 늦은 디테일만 한 번 승격한다', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 35000 });
  const draft = await savedDraft(); draft.analysis.featureRun.startedAt = 0; draft.analysis.featureRun.deadlineAt = 30000;
  const storage = draftStoreFixture(t, draft); let resolve, mergeCalls = 0;
  const response = new Promise((yes) => { resolve = yes; }); const requests = [], saved = [], promoted = [];
  replace(t, { analyzePublicFeatures: (input) => { requests.push(input); return response; },
    mergePublicPoints: async () => { mergeCalls++; throw new Error('must not merge'); },
    createProject: async () => ({ id: 'restored-project' }), uploadPhoto: async () => ({ assetId: 'remote', url: '/photo' }),
    saveProduct: async () => {}, saveAnalysis: async (_pid, analysis) => { saved.push(analysis); },
    promoteDetailRecommendations: async (pid, handoff) => { promoted.push([pid, handoff]); },
    patchProject: async () => {}, getCustomMatchDraft: () => null });
  const result = await sync.promoteDraftToProject(draft, { restoreFeatures: true });
  assert.equal(result.projectId, 'restored-project');
  await drainDraftTasks();
  assert.equal(requests.length, 1); assert.equal(requests[0].attempt, 2);
  assert.equal(requests[0].runId, draft.analysis.featureRun.runId); assert.equal(requests[0].photos[0].blob, draft.photos[0].blob);
  const run = runtime.getDraftFeatureRun(draft); assert.equal(run.getRecord().stage, 'committed');
  assert.equal(run.getRecord().deadlineAt, 30000);
  assert.deepEqual(saved[0].sellingPoints, ['예비 핏']); assert.deepEqual(promoted, []);
  resolve({ points: { status: 'ready', items: ['정확한 핏'] }, featureTicket: 'f',
    detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'late' } });
  await drainDraftTasks();
  assert.deepEqual(promoted, [['restored-project', { signature: 'late' }]]);
  assert.deepEqual(run.getAnalysis().sellingPoints, ['예비 핏']);
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['예비 핏']); assert.equal(mergeCalls, 0);
  run.committed(); await run.setEditing(false); await drainDraftTasks(); assert.equal(promoted.length, 1);
});

test('새로고침의 새 objectURL에서도 동일 Blob 서명을 확인하고 요청과 적용을 복원한다', async (t) => {
  const draft = await savedDraft(); const storage = draftStoreFixture(t, draft); let analysis = draft.analysis;
  replace(t, { analyzePublicFeatures: async () => ({ points: { status: 'ready', items: ['정확한 핏'] }, featureTicket: 'f' }), mergePublicPoints: async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] }) });
  const run = await runtime.restoreDraftFeatureRun(draft, { readProduct: () => product('blob:new'), readAnalysis: () => analysis, onAnalysis: (next) => { analysis = next; } });
  await drainDraftTasks();
  assert.equal(run.getRecord().stage, 'applied'); assert.equal(storage.saved.analysis.sellingPoints[0], '정확한 핏');
  assert.equal(storage.saved.updatedAt, 'revision');
});
test('저장된 사진 바이트가 바뀌면 received 단계도 로컬 적용 전에 거부한다', async (t) => {
  const draft = await savedDraft('received'); draft.photos[0].blob = new Blob(['replacement'], { type: 'image/jpeg' });
  draft.analysis.featureRun.features = { points: { status: 'ready', items: ['옛 문구'] }, featureTicket: 'f' };
  const storage = draftStoreFixture(t, draft); let requests = 0;
  replace(t, { mergePublicPoints: async () => { requests++; return { decisions: [{ late: 0, action: 'refine', early: 0 }] }; } });
  const run = await runtime.restoreDraftFeatureRun(draft); await drainDraftTasks();
  assert.equal(run, null); assert.equal(requests, 0); assert.equal(storage.writes.length, 0);
});

test('초안을 지우고 새 제작을 시작해도 고정한 옛 프로젝트의 늦은 추천은 보존한다', async (t) => {
  const draft = await savedDraft(); draftStoreFixture(t, draft); let resolve, sameDraftGeneration = true;
  const response = new Promise((yes) => { resolve = yes; }); const promoted = [];
  replace(t, { analyzePublicFeatures: () => response,
    promoteDetailRecommendations: async (pid, handoff) => { promoted.push([pid, handoff]); } });
  const run = runtime.createDraftFeatureRun({ product: draft.product, analysis: draft.analysis, isCurrent: () => sameDraftGeneration });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] }); await drainDraftTasks();
  run.freeze('original-project'); run.committed();
  const draftStore = await vite.ssrLoadModule('/src/lib/draftStore.js'); await draftStore.clearDraft();
  sameDraftGeneration = false;
  features.setActiveFeatureRun(features.createFeatureRun({ readAnalysis: () => ({}), saveAnalysis: async () => {}, requestFeatures: async () => {}, requestMerge: async () => {} }));
  resolve({ points: { status: 'ready', items: ['늦은 특징'] }, featureTicket: 'f', detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'late' } });
  await drainDraftTasks(); assert.deepEqual(promoted, [['original-project', { signature: 'late' }]]);
});

test('핵심 저장 뒤 받은 특징 기록이 저장된 coreReceived를 되돌리지 않는다', async (t) => {
  const draft = await savedDraft(); const storage = draftStoreFixture(t, draft);
  let featureResolve, mergeResolve;
  const response = new Promise((yes) => { featureResolve = yes; }); const merge = new Promise((yes) => { mergeResolve = yes; });
  replace(t, { analyzePublicFeatures: () => response, mergePublicPoints: () => merge });
  const run = runtime.createDraftFeatureRun({ product: draft.product, analysis: draft.analysis });
  await run.resume({ ...draft.analysis.featureRun, coreReceived: false }, { product: draft.product, photos: [] }); await drainDraftTasks();
  storage.pauseWrites();
  const saving = run.receiveCore({ sellingPoints: ['예비 핏'], aiSuggestedPoints: ['예비 핏'], pointsTicket: 'p' });
  await drainDraftTasks();
  featureResolve({ points: { status: 'ready', items: ['정확한 핏'] }, featureTicket: 'f' }); await drainDraftTasks();
  storage.releaseWrites(); await saving; await drainDraftTasks();
  assert.equal(storage.saved.analysis.featureRun.stage, 'received');
  assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
  mergeResolve({ decisions: [{ late: 0, action: 'refine', early: 0 }] }); await drainDraftTasks();
});

const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const readyFeatures = (point) => ({ points: { status: 'ready', items: [point] }, featureTicket: 'f' });

for (const phase of ['flush', 'hash', 'prepared']) test(`분석 준비 ${phase} 중 재진입은 같은 실행에 합류하고 원본을 보존한다`, async (t) => {
  const draft = await savedDraft(); draft.analysis = null;
  const originalSrc = URL.createObjectURL(draft.photos[0].blob);
  const returnedSrc = URL.createObjectURL(draft.photos[0].blob);
  t.after(() => { URL.revokeObjectURL(originalSrc); URL.revokeObjectURL(returnedSrc); });
  draft.product = product(originalSrc);
  const storage = draftStoreFixture(t, draft), hashing = deferred(), core = deferred(); let coreCalls = 0, featureCalls = 0;
  if (phase === 'flush') storage.pauseWrites();
  else {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    t.mock.method(crypto.subtle, 'digest', async (...args) => { await hashing.promise; return digest(...args); });
  }
  replace(t, { analyzePublicFeatures: () => { featureCalls++; return new Promise(() => {}); },
    analyzeProduct: () => { coreCalls++; return core.promise; } });
  let currentProduct = draft.product, analysis = null;
  const bindings = () => ({ readProduct: () => currentProduct, readAnalysis: () => analysis,
    onAnalysis: (next) => { analysis = next; }, onProduct: (next) => { currentProduct = next; } });
  const run = runtime.createDraftFeatureRun({ product: currentProduct, analysis, ...bindings() });
  const startedAt = Date.now();
  const requesting = run.startAnalysis({ runId: 'preparing_run_123', startedAt, composeMode: 'basic' });
  await drainDraftTasks();
  assert.equal(run.getRecord().stage, 'preparing'); assert.equal(coreCalls, 0);
  storage.releaseWrites();
  const store = await vite.ssrLoadModule('/src/lib/draftStore.js');
  await store.flushProductDraftSave();
  const snapshot = await store.loadDraft();
  assert.equal(snapshot.analysis.featureRun.stage, 'preparing');
  if (phase === 'prepared') { hashing.resolve(); await drainDraftTasks(); assert.equal(coreCalls, 1); }
  URL.revokeObjectURL(originalSrc); currentProduct = product(returnedSrc); analysis = snapshot.analysis;
  const returning = runtime.restoreDraftFeatureRun(snapshot, bindings()); await drainDraftTasks();
  hashing.resolve(); await drainDraftTasks();
  assert.equal(coreCalls, 1); assert.equal(featureCalls, 1); assert.equal(features.getActiveFeatureRun(), run);
  core.resolve({ suggestedName: '준비를 이어간 상품', sellingPoints: ['특징'], aiSuggestedPoints: ['특징'], pointsTicket: 'p' });
  await requesting; assert.equal(await returning, run);
  assert.equal(currentProduct.colors[0].images[0].src, returnedSrc);
  assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
  assert.equal(storage.saved.analysis.featureRun.runId, 'preparing_run_123');
  assert.equal(storage.saved.analysis.featureRun.deadlineAt, startedAt + 30000);
  assert.deepEqual(await storage.saved.photos[0].blob.arrayBuffer(), await draft.photos[0].blob.arrayBuffer());
});

for (const returnBeforeCore of [false, true]) test(`다른 화면에 머물러도 핵심 분석과 상품 저장을 마치고 같은 요청에 합류한다 (응답 전 복귀 ${returnBeforeCore})`, async (t) => {
  const draft = await savedDraft(); draft.analysis.featureRun.coreReceived = false;
  draft.analysis.featureRun.early = []; draft.analysis.sellingPoints = []; draft.analysis.aiSuggestedPoints = [];
  const storage = draftStoreFixture(t, draft), core = deferred(); let calls = 0;
  replace(t, { analyzePublicFeatures: () => new Promise(() => {}), analyzeProduct: () => { calls++; return core.promise; } });
  let currentProduct = draft.product, analysis = draft.analysis, visible = true, uiUpdates = 0;
  const bindings = () => ({ readProduct: () => currentProduct, readAnalysis: () => analysis,
    onProduct: (next) => { currentProduct = next; if (visible) uiUpdates++; },
    onAnalysis: (next) => { analysis = next; if (visible) uiUpdates++; } });
  const run = runtime.createDraftFeatureRun({ product: currentProduct, analysis, ...bindings() });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] }); await drainDraftTasks();
  const requesting = run.requestCore({ product: draft.product, photos: [] });
  const joined = run.requestCore();
  visible = false; const previousUpdates = uiUpdates;
  let returning;
  if (returnBeforeCore) returning = runtime.restoreDraftFeatureRun(storage.saved, bindings());
  await drainDraftTasks(); assert.equal(calls, 1);
  const inputConsistency = { offending: [{ slot: 'Back', reason: '다른 의류' }] };
  core.resolve({ suggestedName: '분석한 니트', clothingType: 'knit', sellingPoints: ['핵심 특징'], aiSuggestedPoints: ['핵심 특징'], inputConsistency,
    swatchSuggestions: [{ colorGroupId: 'base', swatchId: 'ivory', colorName: '크림' }], pointsTicket: 'p' });
  assert.deepEqual((await requesting).inputConsistency, inputConsistency);
  assert.deepEqual((await joined).inputConsistency, inputConsistency);
  await drainDraftTasks();
  assert.equal(uiUpdates, previousUpdates);
  assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
  assert.deepEqual(storage.saved.analysis.inputConsistency, inputConsistency);
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['핵심 특징']);
  assert.equal(storage.saved.product.name, '분석한 니트'); assert.equal(storage.saved.product.clothingType, 'knit');
  assert.equal(storage.saved.product.colors[0].name, '크림'); assert.equal(storage.saved.product.colors[0].swatchId, 'ivory');
  assert.equal(storage.saved.photos[0].blob, draft.photos[0].blob);
  if (!returning) {
    analysis = draft.analysis; currentProduct = product('blob:returned');
    returning = runtime.restoreDraftFeatureRun(draft, bindings());
  }
  assert.equal(await returning, run); assert.equal(calls, 1);
  assert.equal(analysis.featureRun.coreReceived, true); assert.deepEqual(analysis.sellingPoints, ['핵심 특징']);
  assert.equal(currentProduct.name, '분석한 니트');
  if (!returnBeforeCore) assert.equal(currentProduct.colors[0].images[0].src, 'blob:returned');
});

for (const change of ['photo', 'run']) test(`화면 밖 핵심 응답도 다른 ${change}에는 저장하지 않는다`, async (t) => {
  const draft = await savedDraft(); draft.analysis.featureRun.coreReceived = false;
  const storage = draftStoreFixture(t, draft), core = deferred(); let currentProduct = draft.product;
  replace(t, { analyzePublicFeatures: () => new Promise(() => {}), analyzeProduct: () => core.promise });
  const run = runtime.createDraftFeatureRun({ product: currentProduct, analysis: draft.analysis, readProduct: () => currentProduct });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] }); await drainDraftTasks();
  const requesting = run.requestCore({ product: draft.product, photos: [] }); await drainDraftTasks();
  if (change === 'photo') currentProduct = product('blob:replacement');
  else runtime.createDraftFeatureRun({ product: product('blob:new-run'), analysis: null });
  const writes = storage.writes.length;
  core.resolve({ suggestedName: '옛 상품', sellingPoints: ['옛 특징'], aiSuggestedPoints: ['옛 특징'] });
  assert.equal(await requesting, null); await drainDraftTasks();
  assert.equal(storage.writes.length, writes); assert.equal(storage.saved.analysis.featureRun.coreReceived, false);
});
test('핵심 상품 저장 중 재진입해도 새 사진 주소를 폐기된 이전 주소로 되돌리지 않는다', async (t) => {
  const draft = await savedDraft(); draft.analysis.featureRun.coreReceived = false;
  const storage = draftStoreFixture(t, draft); let currentProduct = draft.product, analysis = draft.analysis;
  replace(t, { analyzePublicFeatures: () => new Promise(() => {}), analyzeProduct: async () => ({
    suggestedName: '저장 중인 상품', sellingPoints: ['핵심 특징'], aiSuggestedPoints: ['핵심 특징'], pointsTicket: 'p' }) });
  const bindings = () => ({ readProduct: () => currentProduct, readAnalysis: () => analysis,
    onProduct: (next) => { currentProduct = next; }, onAnalysis: (next) => { analysis = next; } });
  const run = runtime.createDraftFeatureRun({ product: currentProduct, analysis, ...bindings() });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] }); await drainDraftTasks();
  storage.pauseWrites();
  const requesting = run.requestCore({ product: draft.product, photos: [] }); await drainDraftTasks();
  assert.equal(storage.waitingWrites, 1);
  currentProduct = product('blob:returned');
  const returning = runtime.restoreDraftFeatureRun(storage.saved, bindings()); await drainDraftTasks();
  storage.releaseWrites(); await requesting; assert.equal(await returning, run);
  assert.equal(currentProduct.colors[0].images[0].src, 'blob:returned');
  assert.equal(currentProduct.name, '저장 중인 상품');
  assert.equal(storage.saved.photos[0].blob, draft.photos[0].blob);
  assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
});

test('화면 밖 핵심 분석 실패를 저장하고 재진입은 대기를 끝내 새 분석으로 재시도한다', async (t) => {
  const draft = await savedDraft(); draft.analysis.featureRun.coreReceived = false;
  const storage = draftStoreFixture(t, draft); let calls = 0;
  replace(t, { analyzePublicFeatures: () => new Promise(() => {}), analyzeProduct: async () => {
    if (++calls === 1) throw new Error('core failed');
    return { suggestedName: '다시 분석한 상품', sellingPoints: ['재시도 특징'], aiSuggestedPoints: ['재시도 특징'], pointsTicket: 'p' };
  } });
  const run = runtime.createDraftFeatureRun({ product: draft.product, analysis: draft.analysis });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] });
  await assert.rejects(run.requestCore({ product: draft.product, photos: [] }), /core failed/);
  assert.equal(storage.saved.analysis.featureRun.coreFailed, true); assert.equal(storage.saved.analysis.featureRun.stage, 'failed');
  assert.equal(await runtime.restoreDraftFeatureRun(storage.saved), null); assert.equal(calls, 1);
  const retry = runtime.createDraftFeatureRun({ product: draft.product, analysis: null });
  await retry.start({ runId: 'retry_core_run', photoSignature: draft.analysis.featureRun.photoSignature, product: draft.product, photos: [] });
  await retry.requestCore({ product: draft.product, photos: [] });
  assert.equal(calls, 2); assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['재시도 특징']);
});
for (const changed of ['account', 'identity']) test(`계정 ${changed} 세대가 바뀌면 옛 핵심 저장을 막고 새 실행으로 초안을 복원한다`, async (t) => {
  const draft = await savedDraft(); draft.analysis.featureRun.coreReceived = false;
  const storage = draftStoreFixture(t, draft), response = deferred(); let requests = 0, accountGeneration = 1;
  const { clientEvents } = await vite.ssrLoadModule('/src/lib/clientEvents.js');
  const { draftSlot } = await vite.ssrLoadModule('/src/lib/draftSlot.js');
  t.mock.method(clientEvents, 'getAccountGeneration', () => accountGeneration);
  draftSlot.setUserId('account-a'); t.after(() => draftSlot.resetIdentity());
  replace(t, { analyzePublicFeatures: () => new Promise(() => {}), analyzeProduct: () => ++requests === 1 ? response.promise
    : Promise.resolve({ suggestedName: '새 계정 상품', sellingPoints: ['새 계정 특징'], aiSuggestedPoints: ['새 계정 특징'], pointsTicket: 'p' }) });
  const run = runtime.createDraftFeatureRun({ product: draft.product, analysis: draft.analysis });
  await run.resume(draft.analysis.featureRun, { product: draft.product, photos: [] }); await drainDraftTasks();
  const requesting = run.requestCore({ product: draft.product, photos: [] }); await drainDraftTasks();
  const writes = storage.writes.length;
  if (changed === 'account') accountGeneration++;
  else draftSlot.setUserId('account-b');
  response.resolve({ suggestedName: '이전 계정 상품', sellingPoints: ['이전 계정 특징'], aiSuggestedPoints: ['이전 계정 특징'] });
  assert.equal(await requesting, null); assert.equal(storage.writes.length, writes);
  assert.equal(runtime.getDraftFeatureRun(storage.saved), null);
  const restored = await runtime.restoreDraftFeatureRun(storage.saved); await drainDraftTasks();
  assert.notEqual(restored, run); assert.equal(requests, 2);
  assert.equal(storage.saved.analysis.featureRun.coreReceived, true);
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['새 계정 특징']);
  assert.equal('accountGeneration' in storage.saved.analysis.featureRun, false);
  assert.equal('identityEpoch' in storage.saved.analysis.featureRun, false);
});

for (const sellerEdited of [false, true]) for (const coreFirst of [false, true]) test(`A 사진 승격 중 B 분석은 실행을 유지하고 각 상품 결과를 저장한다, B 핵심 먼저 ${coreFirst}, 셀러 편집 ${sellerEdited}`, { timeout: 5000 }, async (t) => {
  const draft = await savedDraft(sellerEdited ? 'applied' : 'requested'); const storage = draftStoreFixture(t, draft);
  const upload = deferred(), aFeatures = deferred(), bFeatures = deferred(), bCore = deferred();
  const saved = []; let aAnalysis = draft.analysis, aScreen = true;
  replace(t, { analyzePublicFeatures: ({ runId }) => runId === draft.analysis.featureRun.runId ? aFeatures.promise : bFeatures.promise,
    mergePublicPoints: async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] }),
    createProject: async () => ({ id: 'project-a' }), uploadPhoto: () => upload.promise,
    saveProduct: async () => {}, saveAnalysis: async (pid, analysis) => { saved.push([pid, analysis]); },
    patchProject: async () => {}, getCustomMatchDraft: () => null });
  const runA = await runtime.restoreDraftFeatureRun(draft, { readAnalysis: () => aAnalysis, isCurrent: () => aScreen,
    onAnalysis: (next) => { aAnalysis = next; } });
  await drainDraftTasks();
  if (sellerEdited) {
    aAnalysis = { ...aAnalysis, sellingPoints: ['A 셀러 특징'], aiSuggestedPoints: [], dismissedAiPoints: ['예비 핏'] };
    draft.analysis = aAnalysis;
  }
  const promotion = sync.promoteDraftToProject(draft);
  await drainDraftTasks();
  aScreen = false;
  const draftStore = await vite.ssrLoadModule('/src/lib/draftStore.js');
  const blob = new Blob(['product B'], { type: 'image/jpeg' });
  const src = URL.createObjectURL(blob); t.after(() => URL.revokeObjectURL(src));
  const productB = product(src); productB.colors[0].images[0].id = 'photo-b';
  draftStore.queueProductDraftSave(productB, null, 'basic', 'revision-b'); await draftStore.flushProductDraftSave();
  let analysisB = null;
  const runB = runtime.createDraftFeatureRun({ product: productB, readAnalysis: () => analysisB, onAnalysis: (next) => { analysisB = next; } });
  const prepared = await preparePublicAnalysis(productB, { fetchBlob: async () => blob });
  await runB.start({ ...prepared, product: productB, runId: 'product_b_run' });
  const core = analyzePublicDraft(productB, { isCurrent: () => features.getActiveFeatureRun() === runB }, {
    remote: { publicAnalyze: () => bCore.promise }, local: { saveAnalysis: async () => ({}) },
  }).then(async (analysis) => { assert.ok(analysis, 'B 핵심 응답이 활성 실행 검사에서 버려지지 않는다'); await runB.receiveCore(analysis); });
  const receiveB = async () => {
    bCore.resolve({ sellingPoints: ['B 예비'], aiSuggestedPoints: ['B 예비'], pointsTicket: 'p-b' });
    await core; bFeatures.resolve(readyFeatures('B 정확한 특징')); await drainDraftTasks();
  };
  if (coreFirst) await receiveB();
  aFeatures.resolve(readyFeatures('A 정확한 특징')); await drainDraftTasks();
  upload.resolve({ assetId: 'asset-a', url: '/photo-a' }); await promotion; await drainDraftTasks();
  assert.equal(features.getActiveFeatureRun(), runB);
  if (!coreFirst) await receiveB();
  assert.equal(runB.getRecord().coreReceived, true);
  assert.equal(runB.getRecord().stage, 'applied');
  assert.deepEqual(analysisB.sellingPoints, ['B 정확한 특징']);
  assert.deepEqual(saved.map(([pid, analysis]) => [pid, analysis.sellingPoints]), [['project-a', [sellerEdited ? 'A 셀러 특징' : 'A 정확한 특징']]]);
  assert.equal(runA.getRecord().stage, 'committed');
  assert.equal(storage.saved.product.colors[0].images[0].id, 'photo-b');
  assert.equal(storage.saved.analysis.featureRun.runId, 'product_b_run');
  assert.deepEqual(storage.saved.analysis.sellingPoints, ['B 정확한 특징']);
});

test('승격 전용 복원은 다른 상품의 활성 실행을 바꾸지 않는다', async (t) => {
  const draft = await savedDraft('applied'); draftStoreFixture(t, draft);
  replace(t, {});
  const active = features.setActiveFeatureRun(features.createFeatureRun({ readAnalysis: () => ({}), saveAnalysis: async () => {},
    requestFeatures: () => new Promise(() => {}), requestMerge: async () => ({ decisions: [] }) }));
  await active.start({ runId: 'other_run_123', photoSignature: 'other-signature' });
  const restored = await runtime.restoreDraftFeatureRun(draft, { promotion: true });
  assert.notEqual(restored, active);
  assert.equal(features.getActiveFeatureRun(), active);
  const settled = await runtime.settleDraftFeatureRun(draft, 'project-a', { restoreFeatures: true });
  assert.equal(features.getActiveFeatureRun(), active);
  assert.deepEqual(settled.analysis.sellingPoints, ['예비 핏']);
});

for (const stage of ['requested', 'applied']) test(`같은 실행의 ${stage} 초안을 새 화면에 복원하면 새 칩 편집과 콜백으로 승격한다`, async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 35000 });
  const draft = await savedDraft(stage); const storage = draftStoreFixture(t, draft);
  draft.analysis.featureRun.startedAt = 0; draft.analysis.featureRun.deadlineAt = 30000;
  const response = deferred();
  replace(t, { analyzePublicFeatures: () => response.promise,
    mergePublicPoints: async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] }) });
  let screenA = true, analysisA = draft.analysis, analysisB = draft.analysis, callsA = 0, callsB = 0;
  const run = await runtime.restoreDraftFeatureRun(draft, { readProduct: () => product(), readAnalysis: () => analysisA,
    isCurrent: () => screenA, onAnalysis: (next) => { analysisA = next; callsA++; } });
  await drainDraftTasks(); screenA = false;
  const previousCallsA = callsA;
  const restored = await runtime.restoreDraftFeatureRun(storage.saved, { readProduct: () => product('blob:new-screen'), readAnalysis: () => analysisB,
    isCurrent: () => true, onAnalysis: (next) => { analysisB = next; callsB++; } });
  assert.equal(restored, run);
  analysisB = { ...analysisB, sellingPoints: ['새 화면에서 고친 핏'], aiSuggestedPoints: [], dismissedAiPoints: ['예비 핏'] };
  if (stage === 'requested') { response.resolve(readyFeatures('AI가 다듬은 핏')); await drainDraftTasks(); }
  const settled = await runtime.settleDraftFeatureRun(storage.saved, 'restored-project');
  assert.deepEqual(settled.analysis.sellingPoints, ['새 화면에서 고친 핏']);
  assert.deepEqual(settled.analysis.dismissedAiPoints, undefined);
  assert.equal(callsA, previousCallsA);
  if (stage === 'requested') { assert.ok(callsB > 0); assert.deepEqual(storage.saved.analysis.sellingPoints, ['새 화면에서 고친 핏']); }
});
