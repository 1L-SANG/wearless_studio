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
    const draft = { product: { colors: [{ images: [{ id: 'asset', src: 'https://test/photo.jpg' }] }] }, analysis: { detailRecommendationsHandoff: handoff, confirmedGptProductEvidenceHandoff: { opaque: true } } };
    const httpError = (code, status) => Object.assign(new Error(code), { code, status });
    api.promoteConfirmedGptEvidence = async () => { events.push('evidence'); };

    // 서명·유효기간 검증 실패만 추천 없이 확정을 끝까지 보낸다. 뒤따르는 증거 승격·프로젝트 저장도 돈다.
    events.length = 0;
    api.promoteDetailRecommendations = async () => { events.push('promote'); throw httpError('invalid_analysis_handoff', 400); };
    const warn = console.warn;
    const warned = [];
    console.warn = (...args) => warned.push(args);
    try {
      const result = await module.promoteDraftToProject(draft);
      assert.equal(result.projectId, 'p');
    } finally {
      console.warn = warn;
    }
    assert.deepEqual(events, ['create', 'product', 'analysis', 'promote', 'evidence', 'project']);
    assert.equal(warned.length, 1);

    // 원본 불일치·저장 충돌·일시 오류는 지금처럼 확정을 막는다(재시도하면 다시 승격한다).
    for (const error of [httpError('analysis_handoff_source_drift', 400), httpError('analysis_handoff_source_missing', 400),
      httpError('analysis_handoff_conflict', 409), httpError('internal_error', 500), httpError('browser_offline')]) {
      events.length = 0;
      api.promoteDetailRecommendations = async () => { events.push('promote'); throw error; };
      await assert.rejects(module.promoteDraftToProject(draft), (err) => err === error);
      assert.deepEqual(events, ['create', 'product', 'analysis', 'promote'], error.code);
    }

    // 증거 승격 실패도 지금처럼 확정을 막는다(이번 변경 범위 밖).
    events.length = 0;
    api.promoteDetailRecommendations = async () => { events.push('promote'); };
    api.promoteConfirmedGptEvidence = async () => { events.push('evidence'); throw new Error('evidence_drift'); };
    await assert.rejects(module.promoteDraftToProject(draft), /evidence_drift/);
    assert.deepEqual(events, ['create', 'product', 'analysis', 'promote', 'evidence']);
  } finally {
    delete globalThis.__detailPromotionHarness;
  }
});
