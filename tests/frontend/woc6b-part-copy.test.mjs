import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { defaultInfoFor } from '../../src/features/editor/presets/infoPresets.js';

let vite, api, DB;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(async () => { await vite?.close(); });

function seed(enabled = true) {
  DB.project.id = `parts-${enabled}-${Math.random()}`;
  DB.project.status = 'draft'; DB.project.copywriting = enabled;
  DB.project.latestJobs = { mannequin: null, detailPage: null };
  DB.product = { name: '셔츠', clothingType: 'top', colors: [] };
  DB.analysis = { sellingPoints: ['판매 특징'],
    featureCopy: [{ point: '판매 특징', desc: '판매 특징에만 쓰이는 설명입니다.' }],
    partDescriptionCopy: [{ point: '기존 부위', desc: '기존에 저장한 설명입니다.' }],
    detailRecommendations: { candidates: [{ id: 'button', label: '단추' }, { id: 'unused', label: '미선택 포켓' }] } };
  DB.storyboard = [
    { id: 'worn', taxonomyVersion: 4, source: 'mine', ownImages: ['/worn.jpg'], sectionId: 'wear',
      pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', pageSectionUsage: 'styling', pageDesignId: 'P02-D01', slotKey: null },
    { id: 'points', taxonomyVersion: 4, source: 'text', sectionId: 'points', pageSectionKey: 'emo.points',
      pageSectionKind: 'points', pageDesignId: 'PT-BELOW-CARD-01', cutType: null, contentRole: null, sectionRole: null, slotKey: null,
      pointItems: [{ kind: 'detail', sourceBlockId: 'detail' },
        { kind: 'wornCrop', sourceBlockId: 'worn', label: '어깨선', bodyPart: 'shoulder', cropRegion: [.1, .1, .4, .2] }] },
    { id: 'detail', taxonomyVersion: 4, source: 'ai', sectionId: 'points', pageSectionKey: 'emo.points',
      pageSectionKind: 'points', pageDesignId: 'PT-BELOW-CARD-01', slotKey: 'points.detail1', cutType: 'product',
      shot: 'detail', direction: 'front', detailTargetId: 'button' },
  ];
}

for (const method of ['generateDetailPage', 'startDetailPage']) {
  test(`mock ${method} persists actual part descriptions without seller-feature leakage`, async () => {
    seed();
    const originalFeatures = structuredClone(DB.analysis.featureCopy);
    const result = await api[method](DB.project.id);
    let blocks = result.data;
    if (result.jobId) {
      let job;
      for (let attempts = 0; attempts < 100; attempts += 1) {
        job = await api.getJob(result.jobId);
        if (job.status === 'done') break;
        await new Promise(resolve => setTimeout(resolve, 1));
      }
      assert.equal(job.status, 'done');
      blocks = job.result.data;
    }
    assert.deepEqual(DB.analysis.partDescriptionCopy.map(item => item.point), ['기존 부위', '단추', '어깨선']);
    assert.deepEqual(DB.analysis.featureCopy, originalFeatures);
    const exposed = await api.getAnalysis(DB.project.id);
    assert.deepEqual(exposed.partDescriptionCopy, DB.analysis.partDescriptionCopy);
    const info = defaultInfoFor('feature_icons', DB.analysis);
    assert.deepEqual(info.items.map(item => item.title).filter(Boolean), ['판매 특징']);
    const descriptions = blocks.flatMap(block => block.elements).filter(el => /^items\.\d+\.desc$/.test(el.copyRole));
    assert.equal(descriptions.length, 2);
    assert.ok(descriptions.every(el => el.text && el.text !== '부위 설명'));
  });
}

test('mock generation with copywriting off preserves saved copy and produces no part description', async () => {
  seed(false);
  const stored = structuredClone(DB.analysis.partDescriptionCopy);
  const { data } = await api.generateDetailPage(DB.project.id);
  assert.deepEqual(DB.analysis.partDescriptionCopy, stored);
  assert.ok(data.flatMap(block => block.elements).filter(el => /^items\.\d+\.desc$/.test(el.copyRole)).every(el => el.text === ''));
});

test('mock analysis null patch cannot delete stored part descriptions', async () => {
  seed();
  const stored = structuredClone(DB.analysis.partDescriptionCopy);
  await api.saveAnalysis(DB.project.id, { partDescriptionCopy: null });
  assert.deepEqual(DB.analysis.partDescriptionCopy, stored);
});
