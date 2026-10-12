import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
let vite, api, store, runner, attempts, promotion, originalLocation;
before(async () => {
  originalLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { pathname: '/create/storyboard' } });
  vite = await createServer({ configFile: false, define: { 'import.meta.env.VITE_ANALYSIS_FAST_ENTRY': '"1"' },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  ({ useAppStore: store } = await vite.ssrLoadModule('/src/store/useAppStore.js'));
  runner = await vite.ssrLoadModule('/src/features/mannequin/generationRunner.js');
  ({ initialGenerationAttempts: attempts } = await vite.ssrLoadModule('/src/features/mannequin/initialGenerationAttempts.js'));
  promotion = await vite.ssrLoadModule('/src/lib/productPhotoPromotion.js');
});
after(async () => {
  await vite?.close();
  if (originalLocation) Object.defineProperty(globalThis, 'location', originalLocation);
  else delete globalThis.location;
});
const drain = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
test('공용 러너는 승격 중 최초 생성 기록과 크레딧 접수를 모두 미룬다', async (t) => {
  const pid = 'boundary-wait'; let resolve, generations = 0;
  const gate = new Promise((yes) => { resolve = yes; });
  store.setState({ projectId: pid }); const original = api.generateMannequins;
  api.generateMannequins = async () => { generations++; return { data: [] }; };
  t.after(() => { api.generateMannequins = original; promotion.clearProductPhotoPromotionTask(pid); });
  const task = promotion.startProductPhotoPromotion(pid, () => gate);
  const request = runner.requestMannequinGeneration(pid, { isActive: () => true }); await drain();
  assert.equal(generations, 0); assert.equal(attempts.read(pid), null); assert.notEqual(store.getState().mannequinJob.status, 'running');
  resolve(); await task.promise; await request; assert.equal(generations, 1);
});
test('공용 러너는 기다리는 동안 화면이 닫히면 접수하지 않는다', async (t) => {
  const pid = 'boundary-leave'; let resolve, active = true, generations = 0;
  const gate = new Promise((yes) => { resolve = yes; }); store.setState({ projectId: pid }); const original = api.generateMannequins;
  api.generateMannequins = async () => { generations++; return { data: [] }; };
  t.after(() => { api.generateMannequins = original; promotion.clearProductPhotoPromotionTask(pid); });
  const task = promotion.startProductPhotoPromotion(pid, () => gate);
  const request = runner.requestMannequinGeneration(pid, { isActive: () => active }); await drain(); active = false; resolve(); await task.promise;
  assert.equal(await request, null); assert.equal(generations, 0); assert.equal(attempts.read(pid), null);
});


test('화면 cleanup 직전 주소가 바뀌어도 첫 생성을 접수하지 않는다', async (t) => {
  const pid = 'boundary-route'; let resolve, generations = 0;
  const gate = new Promise((yes) => { resolve = yes; }); store.setState({ projectId: pid }); const original = api.generateMannequins;
  api.generateMannequins = async () => { generations++; return { data: [] }; };
  t.after(() => { api.generateMannequins = original; promotion.clearProductPhotoPromotionTask(pid); globalThis.location.pathname = '/create/storyboard'; });
  const task = promotion.startProductPhotoPromotion(pid, () => gate);
  const request = runner.requestMannequinGeneration(pid, { isActive: () => true }); await drain();
  globalThis.location.pathname = '/library'; resolve(); await task.promise;
  assert.equal(await request, null); assert.equal(generations, 0);
});
