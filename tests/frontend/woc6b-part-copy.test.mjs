import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { defaultInfoFor } from '../../src/features/editor/presets/infoPresets.js';

let vite, api, DB, withMockPartDescriptions;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ withMockPartDescriptions } = await vite.ssrLoadModule('/src/mock/partDescriptionCopy.js'));
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
      const { events } = await api.getJobEvents(result.jobId);
      assert.ok(events.every(event => event.payload?.phase !== 'copy' && event.payload?.status !== 'copy_ready'));
      assert.deepEqual(events.filter(event => event.type === 'progress').map(event => event.payload.progress), [15, 40, 60, 80, 85]);
      assert.equal(job.progress, 100);
      blocks = job.result.data;
    }
    assert.deepEqual(DB.analysis.partDescriptionCopy.map(item => item.point), ['기존 부위', '단추', '어깨선']);
    for (const row of DB.analysis.partDescriptionCopy.slice(1)) {
      const lines = row.desc.split('\n');
      assert.equal(lines.length, 2);
      assert.ok(lines.every(line => [...line].length <= 22));
      assert.match(lines[0], /[로라]$/u);
      assert.match(lines[1], /(?:어요|아요|해요)\.?$/u);
      assert.doesNotMatch(row.desc, /확인할 수 있습니다|완벽|통기성|보온|방수/u);
    }
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

test('mock same-label seller copy stays independent and unsupported labels are not invented', () => {
  seed();
  DB.analysis.featureCopy = [{ point: '단추', desc: '단추 디테일을 확인할 수 있습니다.' }];
  DB.storyboard[1].pointItems[1].label = '알 수 없는 부위';
  const result = withMockPartDescriptions(DB.storyboard, DB.analysis, true);
  assert.deepEqual(result.featureCopy, DB.analysis.featureCopy);
  assert.equal(result.partDescriptionCopy.find(item => item.point === '단추').desc,
    '단추가 더해진 여밈으로\n차분한 옷차림을 완성해요');
  assert.equal(result.partDescriptionCopy.some(item => item.point === '알 수 없는 부위'), false);
});

test('mock fixture part labels all receive separate natural two-line descriptions', () => {
  seed();
  const labels = ['목선', '소맷단', '밑단', '골지 원단', '어깨선', '기장',
    '목선이 앉는 모양', '입었을 때 기장', '입었을 때 실루엣', '입었을 때 허리선'];
  DB.storyboard[1].pointItems = labels.map(label => ({ kind: 'wornCrop', sourceBlockId: 'worn', label }));
  const result = withMockPartDescriptions(DB.storyboard, DB.analysis, true);
  const descriptions = labels.map(label => result.partDescriptionCopy.find(item => item.point === label)?.desc);
  assert.equal(new Set(descriptions).size, labels.length);
  for (const desc of descriptions) {
    const lines = desc.split('\n');
    assert.equal(lines.length, 2);
    assert.ok(lines.every(line => [...line].length <= 22));
    assert.match(lines[0], /[로라]$/u);
    assert.match(lines[1], /요\.?$/u);
    assert.doesNotMatch(desc, /확인할 수 있습니다|완벽|통기성|보온|방수/u);
  }
});
