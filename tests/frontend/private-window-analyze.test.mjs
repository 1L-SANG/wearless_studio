import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { draftStoreFixture, drainDraftTasks } from './helpers/draft-store-fixture.mjs';

const cache = mkdtempSync(join(tmpdir(), 'private-window-analyze-'));
after(() => rmSync(cache, { recursive: true, force: true }));
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
async function setup(t) {
  const vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, cacheDir: cache,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  const store = await vite.ssrLoadModule('/src/lib/draftStore.js');
  const { api } = await vite.ssrLoadModule('/src/lib/api/index.js');
  const runtime = await vite.ssrLoadModule('/src/lib/featureRunDraft.js');
  const features = await vite.ssrLoadModule('/src/lib/featureRun.js');
  const { draftSlot } = await vite.ssrLoadModule('/src/lib/draftSlot.js');
  const storage = draftStoreFixture(t, null, store);
  const blob = new Blob(['local photo'], { type: 'image/jpeg' });
  const src = URL.createObjectURL(blob);
  let product = { colors: [{ id: 'base', isBase: true, images: [
    { id: 'front', slot: 'Front', src, type: 'image/jpeg' },
    { id: 'back', slot: 'Back', src, type: 'image/jpeg' },
  ] }] }, analysis = null;
  const core = deferred(), late = deferred();
  const requests = [];
  api.getCustomMatchDraft = () => null;
  api.analyzeProduct = async (_id, input) => { requests.push(['core', input]); return core.promise; };
  api.analyzePublicFeatures = async (input) => { requests.push(['features', input]); return late.promise; };
  api.mergePublicPoints = async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] });
  const run = runtime.createDraftFeatureRun({ product, analysis, readProduct: () => product, readAnalysis: () => analysis,
    onProduct: (next) => { product = next; }, onAnalysis: (next) => { analysis = next; } });
  t.after(async () => { run.invalidate(); features.setActiveFeatureRun(null); URL.revokeObjectURL(src); await vite.close(); });
  const start = () => run.startAnalysis({ runId: 'private_run_123', startedAt: Date.now() });
  const resolveCore = () => core.resolve({ suggestedName: '분석한 상품', clothingType: 'top',
    sellingPoints: ['편안한 핏'], aiSuggestedPoints: ['편안한 핏'], pointsTicket: 'core-ticket' });
  const resolveLate = () => late.resolve({ points: { status: 'ready', items: ['여유로운 핏'] }, featureTicket: 'feature-ticket',
    detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'detail' } });
  return { store, storage, runtime, run, features, draftSlot, requests, start, resolveCore, resolveLate,
    get analysis() { return analysis; }, get product() { return product; } };
}

for (const failure of ['open', 'put', 'readback']) test(`IndexedDB ${failure} 실패에도 사진 분석과 늦은 특징을 화면 상태에 반영한다`, async (t) => {
  const f = await setup(t);
  if (failure === 'open') f.storage.failOpen(new Error('IndexedDB blocked'));
  if (failure === 'put') f.storage.failWrites(new DOMException('Blob write blocked', 'QuotaExceededError'));
  if (failure === 'readback') f.storage.dropWrites();
  f.resolveCore();
  const result = await f.start();
  assert.equal(result.featureRun.coreReceived, true);
  assert.equal(f.run.isMemoryOnly(), true);
  assert.equal(f.product.name, '분석한 상품');
  assert.deepEqual(f.analysis.sellingPoints, ['편안한 핏']);
  assert.deepEqual(f.requests.map(([part]) => part).sort(), ['core', 'features']);
  for (const [, input] of f.requests) {
    assert.deepEqual(input.photos.map((photo) => photo.slot), ['Front', 'Back']);
    assert.equal(await input.photos[0].blob.text(), 'local photo');
  }
  f.resolveLate(); await f.run.wait(); await drainDraftTasks();
  assert.deepEqual(f.analysis.sellingPoints, ['여유로운 핏']);
  assert.equal(f.storage.saved, null);
  assert.equal(f.storage.writes.length, 0);
});

test('실패 신호 없이 초안만 없으면 메모리 분석으로 간주하지 않는다', async (t) => {
  const f = await setup(t);
  assert.equal(await f.store.saveFeatureRunDraft({ sellingPoints: ['옛 결과'] }), false);
});

test('사진 Blob이 손상된 저장을 재시도해도 성공한 초안으로 간주하지 않는다', async (t) => {
  const f = await setup(t); f.storage.corruptWrites();
  f.store.queueProductDraftSave(f.product);
  await assert.rejects(f.store.flushProductDraftSave(), /보관하지 못했어요/);
  f.resolveCore(); await f.start();
  assert.equal(f.run.isMemoryOnly(), true);
  assert.deepEqual(f.analysis.sellingPoints, ['편안한 핏']);
  assert.equal(f.storage.saved.analysis.featureRun.coreReceived, false);
});

test('체크포인트는 보관 불가와 현재 실행이 아닌 경우를 구분한다', async (t) => {
  const f = await setup(t);
  f.storage.failWrites(new Error('storage blocked'));
  f.store.queueProductDraftSave(f.product);
  await assert.rejects(f.store.flushProductDraftSave(), /storage blocked/);
  assert.equal(await f.store.saveFeatureRunDraft({}, { isCurrent: () => false }), false);
  assert.equal(await f.store.saveFeatureRunDraft({}), 'unavailable');
});

test('한 번 저장한 탭의 후속 쓰기 실패는 메모리 분석으로 전환하지 않는다', async (t) => {
  const f = await setup(t);
  await f.store.saveProductDraft(f.product);
  f.storage.failWrites(new Error('later write failure'));
  await assert.rejects(f.start(), /later write failure/);
  assert.equal(f.requests.length, 0);
});

test('이미 복원한 정상 초안의 후속 쓰기 실패도 메모리 분석으로 전환하지 않는다', async (t) => {
  const f = await setup(t);
  const blob = new Blob(['valid prior photo'], { type: 'image/jpeg' });
  f.storage.replaceDraft({ product: f.product, analysis: null, photos: [{ imageId: 'front', slot: 'Front', blob, mime: 'image/jpeg' }], updatedAt: 'prior-draft' });
  assert.equal(await (await f.store.loadDraft()).photos[0].blob.text(), 'valid prior photo');
  f.storage.failWrites(new Error('later write failure'));
  f.resolveCore();
  await assert.rejects(f.start(), /later write failure/);
  assert.equal(f.requests.length, 0);
});

test('정상 저장소에서는 분석 결과와 원본을 저장하고 같은 실행으로 복원한다', async (t) => {
  const f = await setup(t); f.resolveCore();
  await f.start(); f.resolveLate(); await f.run.wait(); await drainDraftTasks();
  assert.equal(f.run.isMemoryOnly(), false);
  assert.equal(f.storage.saved.product.name, '분석한 상품');
  assert.deepEqual(f.storage.saved.analysis.sellingPoints, ['여유로운 핏']);
  assert.equal(f.storage.saved.photos.length, 2);
  assert.equal(await f.storage.saved.photos[0].blob.text(), 'local photo');
  assert.deepEqual((await f.store.loadDraft()).analysis, f.analysis);
});

test('구조화 복제한 undefined, Date, 중첩 객체와 File을 저장 실패로 오인하지 않는다', async (t) => {
  const f = await setup(t); f.storage.cloneWrites();
  const file = new File(['original upload'], 'upload.jpg', { type: 'image/jpeg' });
  file.preview = 'local preview';
  const metadata = { unset: undefined, createdAt: new Date('2026-10-20T00:00:00Z'), nested: { z: 1, a: { b: 2, a: undefined } } };
  await f.store.saveProductDraft({ ...f.product, metadata }, null, 'basic', '2026-10-20T00:00:00Z', { uploads: [{ file }] });
  assert.deepEqual(f.storage.saved.product.metadata, metadata);
  assert.equal(f.storage.saved.customMatch.uploads[0].file.size, 15);
  assert.equal(f.storage.saved.customMatch.uploads[0].file.type, 'image/jpeg');
  assert.equal(f.storage.saved.customMatch.uploads[0].file.preview, undefined);
  assert.equal(f.store.isDraftStorageUnavailable(), false);
});

for (const mismatch of ['updatedAt', 'photoCount', 'photoType', 'photoId']) test(`읽기 검증은 ${mismatch} 불일치를 계속 거부한다`, async (t) => {
  const f = await setup(t);
  f.storage.cloneWrites((value) => {
    const copy = structuredClone(value);
    if (mismatch === 'updatedAt') copy.updatedAt = 'other-revision';
    if (mismatch === 'photoCount') copy.photos.pop();
    if (mismatch === 'photoType') copy.photos[0].blob = new Blob(['local photo'], { type: 'image/png' });
    if (mismatch === 'photoId') copy.photos[0].imageId = 'other-photo';
    return copy;
  });
  await assert.rejects(f.store.saveProductDraft(f.product), /보관하지 못했어요/);
  assert.equal(f.store.isDraftStorageUnavailable(), true);
});

test('다른 탭의 실행으로 바뀐 초안에는 핵심 분석 결과를 쓰지 않는다', async (t) => {
  const f = await setup(t);
  const starting = f.start();
  await drainDraftTasks();
  assert.equal(f.requests.some(([part]) => part === 'core'), true);
  const replacement = { ...f.storage.saved, analysis: { featureRun: { runId: 'other-tab', photoSignature: 'other-photo' } } };
  f.storage.replaceDraft(replacement); f.resolveCore();
  await assert.rejects(starting);
  assert.deepEqual(f.storage.saved, replacement);
  assert.equal(f.analysis.featureRun.coreReceived, false);
});

test('복원한 실행의 첫 체크포인트도 다른 탭이 바꾼 초안을 덮어쓰지 않는다', async (t) => {
  const f = await setup(t);
  const analysis = { featureRun: { runId: 'restored-run', photoSignature: 'restored-photo',
    stage: 'requested', attempts: 0, coreReceived: false, early: [], deadlineAt: Date.now() + 30000 } };
  await f.store.saveProductDraft(f.product, analysis);
  const restored = f.runtime.createDraftFeatureRun({ product: f.product, analysis });
  t.after(() => restored.invalidate());
  const replacement = { ...f.storage.saved, analysis: { featureRun: { runId: 'other-tab', photoSignature: 'other-photo' } } };
  f.storage.replaceDraft(replacement);
  await restored.resume(analysis.featureRun, { product: f.product, photos: [] }); await drainDraftTasks();
  assert.deepEqual(f.storage.saved, replacement);
  assert.equal(f.requests.length, 0);
});

for (const change of ['run', 'identity']) test(`메모리 분석도 ${change} 교체 뒤 늦은 결과를 버린다`, async (t) => {
  const f = await setup(t); f.storage.failWrites(new Error('storage blocked')); f.resolveCore();
  await f.start();
  const before = structuredClone(f.analysis);
  if (change === 'run') f.features.setActiveFeatureRun(null);
  else f.draftSlot.resetIdentity();
  f.resolveLate(); await drainDraftTasks();
  assert.deepEqual(f.analysis, before);
  assert.equal(f.storage.saved, null);
});

for (const change of ['run', 'identity', 'photo']) test(`메모리 핵심 분석도 ${change} 교체 뒤에는 화면에 적용하지 않는다`, async (t) => {
  const f = await setup(t); f.storage.failWrites(new Error('storage blocked'));
  const starting = f.start(); await drainDraftTasks();
  assert.equal(f.requests.some(([part]) => part === 'core'), true);
  const before = structuredClone(f.analysis);
  if (change === 'run') f.features.setActiveFeatureRun(null);
  else if (change === 'identity') f.draftSlot.resetIdentity();
  else f.product.colors[0].images[0].src = 'blob:replacement';
  f.resolveCore(); f.resolveLate();
  assert.equal(await starting, null); await drainDraftTasks();
  assert.deepEqual(f.analysis, before);
  assert.equal(f.storage.saved, null);
});
