import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
let vite, api, sync;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
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
