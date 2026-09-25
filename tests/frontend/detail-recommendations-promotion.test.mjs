import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { withUploadedSrcs } from '../../src/lib/draftPromotionProduct.js';

// Execute the actual promotion orchestration with boundary doubles; no network or auth session.
test('signed detail handoff is promoted only after source upload, product and analysis save', async () => {
  const events = [];
  const handoff = { payload: 'signed-server-payload', signature: 'opaque' };
  const api = {
    createProject: async () => { events.push('create'); return { id: 'p' }; },
    uploadPhoto: async () => { events.push('upload'); return { assetId: 'asset', url: 'https://test/photo.jpg' }; },
    saveProduct: async (_id, product) => { events.push('product'); assert.equal(product.colors[0].images[0].id, 'asset'); },
    saveAnalysis: async (_id, analysis) => { events.push('analysis'); assert.equal('detailRecommendations' in analysis, false); assert.equal('detailRecommendationsHandoff' in analysis, false); },
    promoteDetailRecommendations: async (id, value) => { events.push('promote'); assert.equal(id, 'p'); assert.deepEqual(value, handoff); },
    patchProject: async () => { events.push('project'); },
  };
  globalThis.__detailPromotionHarness = {
    api, withUploadedSrcs, isUploadablePhotoMime: () => true,
    createDraftSyncSingleFlight: (sync) => ({ sync }),
    draftPromotionSession: { read: () => ({}), rememberProject() {}, rememberAsset() {} },
    startCustomMatchPromotion: () => null, stripLocalCustomMatch: (analysis) => analysis,
    invalidateStoryboardEntryPrefetch() {},
  };
  try {
    const source = readFileSync(new URL('../../src/lib/draftSync.js', import.meta.url), 'utf8')
      .replace(/^import .+;\r?$/gm, '');
    const declarations = `const { ${Object.keys(globalThis.__detailPromotionHarness).join(', ')} } = globalThis.__detailPromotionHarness;\n`;
    const module = await import(`data:text/javascript;base64,${Buffer.from(declarations + source).toString('base64')}`);
    await module.promoteDraftToProject({
      product: { colors: [{ images: [{ id: 'local', src: 'blob:photo' }] }] },
      photos: [{ imageId: 'local', mime: 'image/jpeg' }],
      analysis: { detailRecommendations: { status: 'ready', candidates: [{ id: 'cannot-promote-unsigned' }] }, detailRecommendationsHandoff: handoff },
    });
    assert.deepEqual(events, ['create', 'upload', 'product', 'analysis', 'promote', 'project']);
    events.length = 0;
    api.promoteDetailRecommendations = async () => { events.push('promote'); throw new Error('stale_source'); };
    await assert.rejects(module.promoteDraftToProject({ product: { colors: [{ images: [{ id: 'asset', src: 'https://test/photo.jpg' }] }] }, analysis: { detailRecommendationsHandoff: handoff } }), /stale_source/);
    assert.deepEqual(events, ['create', 'product', 'analysis', 'promote']);
  } finally {
    delete globalThis.__detailPromotionHarness;
  }
});
