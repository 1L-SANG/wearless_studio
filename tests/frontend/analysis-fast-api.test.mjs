import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
let vite, adapter, supabase;
before(async () => {
  vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
  supabase.auth.getSession = async () => ({ data: { session: null } });
});
after(async () => { await vite?.close(); });
const response = (data) => ({ ok: true, status: 200, headers: new Headers(), json: async () => data });
function capture(t, data) {
  const calls = []; const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return response(data); };
  t.after(() => { globalThis.fetch = original; }); return calls;
}
test('합치기는 비회원도 JSON 증표를 보내고 decisions를 읽는다', async (t) => {
  const calls = capture(t, { data: { decisions: [{ late: 0, action: 'new', early: null }] } });
  const body = { runId: 'run_test_123', pointsTicket: { signature: 'core' }, featureTicket: { signature: 'feature' } };
  const result = await adapter.mergePublicPoints(body);
  assert.deepEqual(result.decisions, [{ late: 0, action: 'new', early: null }]);
  assert.deepEqual(JSON.parse(calls[0].body), body);
});
test('핵심 분석과 특징은 준비된 Blob으로 정확한 공개 폼을 보낸다', async (t) => {
  const calls = capture(t, { data: { pointsTicket: { signature: 'core' }, points: { status: 'ready', items: ['문구'] } } });
  const input = { product: { colors: [] }, photos: [{ slot: 'Front', blob: new Blob(['bytes'], { type: 'image/jpeg' }) }], runId: 'run_test_123' };
  const core = await adapter.publicAnalyze(input.product, { ...input, parts: 'core' });
  assert.equal(core.pointsTicket.signature, 'core');
  await adapter.analyzePublicFeatures({ ...input, attempt: 2 });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].body.get('parts'), 'core');
  assert.equal(calls[1].body.get('attempt'), '2');
  assert.equal(calls[1].body.get('parts'), null);
  assert.deepEqual(calls[0].body.getAll('slots'), calls[1].body.getAll('slots'));
});
test('분석 서버 저장은 실행 기록과 버린 문구 목록을 보내지 않는다', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'fake' } } });
  const calls = capture(t, { sellingPoints: ['보존'] });
  await adapter.saveAnalysis('project', { sellingPoints: ['보존'], featureRun: { secret: 'ticket' }, dismissedAiPoints: ['버림'] });
  assert.deepEqual(JSON.parse(calls[0].body), { sellingPoints: ['보존'] });
});

test('목 특징 인계도 확정 뒤 저장되어 개발 흐름이 계속 이어진다', async () => {
  const { mockAdapter } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js');
  const feature = await mockAdapter.analyzePublicFeatures({ runId: 'mock_run_123' });
  await mockAdapter.promoteDetailRecommendations('mock-project', feature.detailRecommendationsHandoff);
  const saved = await mockAdapter.getAnalysis('mock-project');
  assert.equal(saved.detailRecommendations.status, 'ready');
});

test('HTTP analysis mapping preserves the fit profile and its axis sources', async (t) => {
  const fitProfile = { category: 'top', gender: 'women', axes: { length: 'slight_crop' },
    axisSources: { length: 'measure' }, source: 'auto', version: 2 };
  const fitRead = { photoType: 'flat', sleeveType: 'long', topLength: { value: 'basic', confident: true },
    sleeve: { value: 'wrist', confident: false } };
  capture(t, { data: { clothingType: 'top', fit: 'regular', fitProfile, fitRead } });
  const result = await adapter.publicAnalyze({ colors: [] }, { photos: [{ slot: 'Front', blob: new Blob(['bytes'], { type: 'image/jpeg' }) }] });
  assert.deepEqual(result.fitProfile, fitProfile);
  assert.deepEqual(result.fitRead, fitRead);
});

test('mock analysis save and reload preserve per-axis sources', async () => {
  const { mockAdapter } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js');
  const fitProfile = { category: 'top', gender: 'women', axes: { sleeve: 'back_of_hand' },
    axisSources: { sleeve: 'seller' }, source: 'seller', version: 2 };
  const fitRead = { photoType: 'worn', sleeveType: 'long', sleeve: { value: 'wrist', confident: true } };
  await mockAdapter.saveAnalysis('mock-project', { fitProfile, fitRead });
  const result = await mockAdapter.getAnalysis('mock-project');
  assert.deepEqual(result.fitProfile, fitProfile);
  assert.deepEqual(result.fitRead, fitRead);
});

test('HTTP analysis save and reload retain the full per-axis source map', async (t) => {
  const fitProfile = { category: 'outer', gender: 'men', axes: { fit: 'over', sleeve: 'fingers' },
    axisSources: { fit: 'ai', sleeve: 'seller' }, source: 'seller', version: 2 };
  const fitRead = { photoType: 'flat', sleeveType: 'long', sleeve: { value: 'wrist', confident: false } };
  const calls = capture(t, { clothingType: 'outer', targetGenders: ['men'], fitProfile, fitRead });
  const saved = await adapter.saveAnalysis('fit-source-project', { fitProfile, fitRead });
  assert.deepEqual(JSON.parse(calls[0].body), { fitProfile, fitRead });
  assert.deepEqual(saved.fitProfile, fitProfile);
  assert.deepEqual(saved.fitRead, fitRead);
  const result = await adapter.getAnalysis('fit-source-project');
  assert.deepEqual(result.fitProfile, fitProfile);
  assert.deepEqual(result.fitRead, fitRead);
});
