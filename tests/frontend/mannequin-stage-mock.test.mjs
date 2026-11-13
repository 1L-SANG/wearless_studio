import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
let vite, api, DB, defaultPageStyle;
before(async () => {
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, ws: false } });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ defaultPageStyle } = await vite.ssrLoadModule('/src/lib/pageTemplates/pageStyle.js'));
});
after(() => vite?.close());
async function fresh({ plan = 'free', model = 'mF', preview = true, ai = true } = {}) {
  await api.createProject();
  DB.account.plan = plan; DB.account.credits = 1000;
  DB.analysis.selectedModelId = model;
  DB.mannequins = preview ? [{ id: 'preview', previewOnly: true, imageUrl: '/preview.png' }] : [];
  DB.storyboard = DB.storyboard.filter(block => block.pageSectionKind === 'first').slice(0, 1);
  if (!ai) DB.storyboard = DB.storyboard.map(block => ({ ...block, source: 'mine', contentRole: 'custom', cutType: null, sectionRole: null,
    exampleId: null, exampleThumb: null }));
  return DB.project.id;
}
async function finish(jobId) {
  for (let attempt = 0; attempt < 200; attempt++) {
    const job = await api.getJob(jobId);
    if (job.status === 'done') return job;
    await new Promise(resolve => setTimeout(resolve, 1));
  }
  assert.fail('mock detail job did not finish');
}

test('미리보기 마네킹만 있으면 확장 모델 요금을 견적에 붙인다', async () => {
  const id = await fresh();
  assert.ok(DB.mannequins.length > 0 && DB.mannequins.every(cut => cut.previewOnly));
  const quote = await api.getCreditQuote(id);
  assert.deepEqual(quote.detailPageGenerate, { extensionModelFee: 19, selectedModelId: 'mF' });
  assert.equal(quote.mannequinGenerate.total, 64);
  assert.deepEqual((await api.getCreditQuote(id, { selectedModelId: 'mA' })).detailPageGenerate, { extensionModelFee: 0, selectedModelId: 'mA' });
});

for (const [plan, model, fee] of [['free', 'mF', 19], ['starter', 'mF', 19], ['seller', 'mF', 10], ['pro', 'mF', 0], ['free', 'mA', 0], ['free', 'real-model', 0]]) {
  test(`마네킹 없는 상세페이지는 ${plan} ${model} 모델 요금 ${fee}를 한번만 차감한다`, async () => {
    const id = await fresh({ plan, model });
    assert.equal((await api.getCreditQuote(id)).detailPageGenerate.extensionModelFee, fee);
    const first = await api.generateDetailPage(id);
    assert.equal(first.credits, 1000 - 95 - fee);
    assert.equal(DB.detailPageExtensionFeeCharged, fee);
    assert.equal((await api.getCreditQuote(id)).detailPageGenerate.extensionModelFee, 0);
    assert.equal((await api.generateDetailPage(id)).credits, first.credits);
    DB.project.status = 'draft';
    assert.equal((await api.generateDetailPage(id)).credits, 1000 - 190 - fee);
  });
}

test('마네킹 먼저 만든 기존 흐름은 상세페이지에서 컷 값만 차감한다', async () => {
  const id = await fresh({ preview: false });
  assert.equal((await api.generateMannequins(id)).credits, 936);
  assert.deepEqual((await api.getCreditQuote(id)).detailPageGenerate, { extensionModelFee: 0, selectedModelId: 'mF' });
  assert.equal((await api.generateDetailPage(id)).credits, 841);
});

test('대기와 실패와 취소 마네킹 작업도 모델 요금 중복을 막는다', async () => {
  for (const status of ['pending', 'running', 'error', 'cancelled', 'done']) {
    const id = await fresh();
    DB.project.latestJobs.mannequin = { id: 'old-mannequin', status };
    assert.equal((await api.getCreditQuote(id)).detailPageGenerate.extensionModelFee, 0);
  }
});

test('비동기 생성도 시작할 때 요금을 고정하고 합류와 완료 재호출은 중복 차감하지 않는다', async () => {
  const id = await fresh();
  const { jobId } = await api.startDetailPage(id);
  assert.equal((await api.startDetailPage(id)).jobId, jobId);
  DB.account.plan = 'pro';
  const job = await finish(jobId);
  assert.equal(job.result.credits, 886);
  assert.equal(DB.detailPageExtensionFeeCharged, 19);
  assert.equal((await api.startDetailPage(id)).credits, 886);
});

test('AI 컷이 없는 상세페이지는 확장 모델 요금도 차감하지 않는다', async () => {
  const id = await fresh({ ai: false });
  assert.equal((await api.generateDetailPage(id)).credits, 1000);
  assert.equal(DB.detailPageExtensionFeeCharged, 0);
});

test('상세페이지 작업은 실패해도 템플릿 변경만 잠그고 마네킹 문구를 우선한다', async () => {
  for (const status of ['pending', 'running', 'error', 'cancelled', 'done']) {
    const id = await fresh();
    DB.project.latestJobs.detailPage = { id: 'detail', status };
    await assert.rejects(api.saveAnalysis(id, { pageStyle: { ...defaultPageStyle(), templateId: 'show' } }),
      error => error.status === 409 && error.code === 'page_style_locked' && error.message === '상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요.');
    await api.saveAnalysis(id, { pageStyle: { ...defaultPageStyle(), fontStyle: 'serif' } });
    DB.project.latestJobs.mannequin = { id: 'mannequin', status: 'error' };
    await assert.rejects(api.saveAnalysis(id, { pageStyle: { ...defaultPageStyle(), templateId: 'show' } }),
      error => error.message === '마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요.');
  }
});

test('두 생성 방식 모두 작업을 남겨 생성 시작 후 템플릿 변경을 막는다', async () => {
  for (const method of ['generateDetailPage', 'startDetailPage']) {
    const id = await fresh();
    const result = await api[method](id);
    assert.ok(DB.project.latestJobs.detailPage?.id);
    await assert.rejects(api.saveAnalysis(id, { pageStyle: { ...defaultPageStyle(), templateId: 'show' } }),
      error => error.status === 409 && error.code === 'page_style_locked');
    if (result.jobId) await finish(result.jobId);
  }
});

test('마네킹 없는 새 콘티를 자동 저장하면 만드는 중 보관함에 나타난다', async () => {
  const id = await fresh();
  assert.equal((await api.getInProgressLibrary()).some(row => row.id === id), false);
  await api.saveStoryboard(id, DB.storyboard, { autoAssignment: true });
  assert.equal(DB.storyboardDirty, false);
  assert.equal((await api.getInProgressLibrary()).some(row => row.id === id), true);
});
