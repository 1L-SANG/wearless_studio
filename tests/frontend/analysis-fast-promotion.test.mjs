import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
let vite, api, sync, features;
before(async () => {
  vite = await createServer({ configFile: false, define: { 'import.meta.env.VITE_ANALYSIS_FAST_ENTRY': '"1"' },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
  features = await vite.ssrLoadModule('/src/lib/featureRun.js');
});
after(async () => { await vite?.close(); });
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const drain = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
function setup(t) {
  let analysis = { sellingPoints: ['예비 핏'], aiSuggestedPoints: ['예비 핏'], confirmedGptProductEvidenceHandoff: 'evidence' };
  const response = deferred(), saved = [], events = [], details = [];
  let time = 0, timer;
  const run = features.createFeatureRun({ now: () => time, random: () => 0,
    setTimer: (fn) => { timer = fn; return 1; }, clearTimer: () => { timer = null; }, readAnalysis: () => analysis,
    saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; },
    requestFeatures: () => response.promise, requestMerge: async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] }),
    onEvent: (code) => events.push(code), onLateDetails: async (pid, handoff) => details.push([pid, handoff]),
  }); features.setActiveFeatureRun(run);
  const original = {};
  const replacement = { createProject: async () => ({ id: 'project' }), uploadPhoto: async () => ({ assetId: 'remote', url: '/photo' }),
    saveProduct: async () => saved.push('product'), saveAnalysis: async (_pid, data) => saved.push(data),
    promoteDetailRecommendations: async (_pid, handoff) => saved.push(['detail', handoff]), promoteConfirmedGptEvidence: async () => {},
    patchProject: async () => {}, getCustomMatchDraft: () => null };
  for (const [key, value] of Object.entries(replacement)) { original[key] = api[key]; api[key] = value; }
  t.after(() => { for (const [key, value] of Object.entries(original)) api[key] = value; sync.resetDraftSyncSingleFlight(); features.setActiveFeatureRun(null); });
  return { run, response, saved, events, details, get analysis() { return analysis; }, deadline() { time = 30000; timer?.(); },
    draft() { return { product: { name: '옷', colors: [] }, analysis: structuredClone(analysis), photos: [{ imageId: 'local', slot: 'Front', mime: 'image/jpeg', blob: new Blob(['photo']) }] }; } };
}
async function begin(f) { await f.run.start({ runId: 'test_run_123', startedAt: 0, photoSignature: 'signature' }); await f.run.receiveCore({ sellingPoints: ['예비 핏'], aiSuggestedPoints: ['예비 핏'], pointsTicket: 'ticket' }); }
const ready = () => ({ points: { status: 'ready', items: ['정확한 핏'] }, featureTicket: 'f', detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: 'detail' });
test('사진과 상품을 먼저 저장하고 특징 정착 후 최신 분석과 인계를 함께 승격한다', async (t) => {
  const f = setup(t); await begin(f); const saving = sync.promoteDraftToProject(f.draft()); await drain();
  assert.deepEqual(f.saved, ['product']);
  f.response.resolve(ready()); await saving;
  assert.equal(f.saved[1].sellingPoints[0], '정확한 핏'); assert.equal('featureRun' in f.saved[1], false); assert.deepEqual(f.saved[2], ['detail', 'detail']);
  assert.equal(f.run.getRecord().stage, 'committed');
});
test('기한 초과는 예비 문구를 고정하고 늦은 특징은 디테일만 넘긴다', async (t) => {
  const f = setup(t); await begin(f); const saving = sync.promoteDraftToProject(f.draft()); await drain(); f.deadline(); await saving;
  assert.equal(f.saved[1].sellingPoints[0], '예비 핏'); assert.equal(f.events.includes('feature_deadline_exceeded'), true);
  f.response.resolve(ready()); await drain(); assert.deepEqual(f.details, [['project', 'detail']]); assert.equal(f.analysis.sellingPoints[0], '예비 핏');
});

test('분석 저장 뒤 단계 실패와 재시도는 고정 사본을 그대로 사용한다', async (t) => {
  const f = setup(t); await begin(f); f.response.resolve(ready()); await drain();
  const originalPatch = api.patchProject; let failed = false;
  api.patchProject = async () => { if (!failed) { failed = true; throw new Error('project save failed'); } return originalPatch(); };
  await assert.rejects(sync.promoteDraftToProject(f.draft()), /project save failed/);
  sync.retryDraftPromotion('project');
  await sync.promoteDraftToProject(f.draft(), { projectId: 'project' });
  const savedAnalyses = f.saved.filter((value) => value && typeof value === 'object' && !Array.isArray(value));
  assert.equal(savedAnalyses.length, 2); assert.deepEqual(savedAnalyses[0], savedAnalyses[1]);
});
for (const stage of ['requested', 'received', 'decided']) test(`로그인 복귀 ${stage} 복원은 새 네트워크 대기를 추가하지 않는다`, async () => {
  let analysis = { sellingPoints: ['예비 핏'], aiSuggestedPoints: ['예비 핏'] }, requested = 0;
  const run = features.createFeatureRun({ readAnalysis: () => analysis, saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; },
    requestFeatures: async () => { requested++; throw new Error('must not call'); }, requestMerge: async () => { requested++; throw new Error('must not call'); } });
  await run.resume({ runId: 'restore_run_123', photoSignature: 'sig', stage, startedAt: 0, deadlineAt: 30000, coreReceived: true, early: ['예비 핏'],
    decisions: [{ late: 0, action: 'refine', early: 0 }], features: ready() }, {}, { promotion: true });
  assert.equal(requested, 0);
  assert.equal(analysis.sellingPoints[0], stage === 'decided' ? '정확한 핏' : '예비 핏');
  if (stage === 'received') assert.equal(analysis.detailRecommendationsHandoff, 'detail');
});
