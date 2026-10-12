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
