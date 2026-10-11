import test from 'node:test';
import assert from 'node:assert/strict';
import * as jobEvents from '../../src/lib/detailPageJobEvents.js';
import { applyDetailJobEvents } from '../../src/lib/detailPageJobEvents.js';
import { mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { canvasAiHookHarness, editorCanvasAiHandlers } from './helpers/editorCanvasAiHarness.mjs';
import { buildCanvasAiMenu } from '../../src/features/editor/canvasAiActions.js';

const failure = { id: 'image', type: 'image', sourceBlockId: 'failed', src: null,
  genFailed: true, genFailReason: 'no_garment_photo', genFailRetryable: false };

test('완료 병합은 실패 이벤트가 없어도 서버가 저장한 실패 이유를 보존한다', () => {
  const server = [{ id: 'page', elements: [failure] }];
  const local = [{ id: 'page', elements: [{ ...failure, genFailed: undefined, genFailReason: undefined, genFailRetryable: undefined }] }];
  for (const current of [local, []]) {
    const element = mergeServerBlocks(current, server, new Set())[0].elements[0];
    assert.equal(element.genFailed, true);
    assert.equal(element.genFailReason, 'no_garment_photo');
    assert.equal(element.genFailRetryable, false);
  }
});

test('컷 실패 이벤트의 이유와 다시 만들기 가능 여부는 새로고침 재생에도 남는다', () => {
  const job = { cuts: {}, copy: {}, live: ['failed'], failedCuts: [] };
  const result = applyDetailJobEvents(job, [{ type: 'step', payload: {
    blockId: 'failed', status: 'cut_failed', reason: 'provider_error', retryable: true,
  } }]);
  assert.deepEqual(result.failedCutReasons?.failed, { reason: 'provider_error', retryable: true });
  assert.deepEqual(result.failedCuts, ['failed']);
});

test('모든 종류의 실패 칸 다시 만들기는 서버 복원용 블록 id를 보낸다', () => {
  for (const cutType of ['product', 'styling', 'mirror', 'horizon']) {
    const result = buildFailedCutRetry([{ id: 'failed', source: 'ai', cutType }], 'failed');
    assert.equal(result.request.retryBlockId, 'failed');
  }
});


test('완료 상태와 완료 알림은 서버 partial의 실제 개수를 사용한다', () => {
  const partial = { requestedCuts: 3, completedCuts: 2, failedCuts: [{ blockId: 'failed', reason: 'provider_error', retryable: true }], creditsCharged: 76, creditsNotCharged: 19 };
  assert.equal(typeof jobEvents.detailPageDonePatch, 'function');
  assert.equal(typeof jobEvents.detailPageCompletionMessage, 'function');
  assert.deepEqual(jobEvents.detailPageDonePatch({ result: { partial } }).partial, partial);
  assert.equal(jobEvents.detailPageCompletionMessage(partial), '3장 중 2장을 만들었어요. 못 만든 1장 값은 받지 않았어요.');
});

test('옛 작업의 이유 없는 실패 칸도 실제 builder에서 다시 만들기를 허용한다', () => {
  const retry = buildFailedCutRetry([{ id: 'old', source: 'ai', cutType: 'product' }], 'old', null, null,
    { failure: { genFailed: true } });
  assert.equal(retry.retryable, true);
  assert.equal(retry.request.retryBlockId, 'old');
});

test('옛 셀러 조치 실패 칸도 저장된 false 대신 다시 만들기를 반환한다', () => {
  const result = buildFailedCutRetry([{ id: 'failed', source: 'ai', cutType: 'product' }], 'failed', null, null,
    { failure: { genFailReason: 'no_garment_photo', genFailRetryable: false } });
  assert.equal(result.retryable, true);
  assert.equal(result.guidance, '저희 쪽에서 다시 만들 수 있어요');
  assert.equal(result.request.retryBlockId, 'failed');
});

test('비슷한 컷 후속 칸은 원본 사진이 채워진 뒤에만 그 사진으로 변형한다', () => {
  const storyboard = [{ id: 'root', source: 'ai', cutType: 'styling' },
    { id: 'child', source: 'ai', cutType: 'styling', similarSourceBlockId: 'root' }];
  const blocked = buildFailedCutRetry(storyboard, 'child');
  assert.equal(blocked.retryable, false);
  assert.equal(blocked.guidance, '원본 컷을 먼저 만들어 주세요');
  const ready = buildFailedCutRetry(storyboard, 'child', null, null, { editorBlocks: [{ elements: [
    { type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file', cutType: 'styling' },
  ] }] });
  assert.equal(ready.retryable, true);
  assert.equal(ready.request.mode, 'vary');
  assert.equal(ready.request.variationMode, 'similar');
  assert.equal(ready.request.source.src, '/v1/assets/root/file');
  assert.equal(ready.request.retryBlockId, 'child');
});

test('같은 사진 복제의 실패 칸은 원본 칸이 채워진 뒤 다시 만들 수 있다', () => {
  const storyboard = [{ id: 'root', source: 'ai', cutType: 'styling', direction: 'front', shot: 'full' },
    { id: 'clone', source: 'ai', cutType: 'styling', direction: 'front', shot: 'full' }];
  const failure = { genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false };
  assert.equal(buildFailedCutRetry(storyboard, 'clone', null, null, { failure }).retryable, false);
  const ready = buildFailedCutRetry(storyboard, 'clone', null, null, { failure, editorBlocks: [
    { elements: [{ type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' }] },
  ] });
  assert.equal(ready.retryable, true);
  assert.equal(ready.request, null, '복제 칸은 새 이미지를 생성하거나 추가 과금하지 않는다');
  assert.equal(ready.fillImage.src, '/v1/assets/root/file');
  const differentAngle = [{ ...storyboard[0], direction: 'side', sideStyle: 'profile' },
    { ...storyboard[1], direction: 'side', sideStyle: 'threeQuarter' }];
  assert.equal(buildFailedCutRetry(differentAngle, 'clone', null, null, { failure, editorBlocks: [
    { elements: [{ type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' }] },
  ] }).retryable, false, '다른 옆모습 설정의 사진을 복제 원본으로 쓰지 않는다');
});

const plannedSlot = (sourceBlockId, failure = {}) => ({ id: 'empty', type: 'image', src: null, genFailed: true, sourceBlockId, ...failure });
function plannedHarness({ blocks, storyboard, api, fill }) {
  const handlers = editorCanvasAiHandlers({ blocks, latestBlocks: { current: blocks }, api,
    setSlotImage: fill });
  return canvasAiHookHarness({ blocks, storyboard, selectedId: 'empty', api, handlers });
}

test('막 채운 원본으로 후속을 다시 만들기 전에 서버에 그 칸을 저장한다', async () => {
  const calls = [];
  const source = { src: '/v1/assets/root/file', cutType: 'styling' };
  const blocks = [{ id: 'page', elements: [{ id: 'root-image', type: 'image', sourceBlockId: 'root', ...source }, plannedSlot('child')] }];
  const storyboard = [{ id: 'root', source: 'ai', cutType: 'styling' },
    { id: 'child', source: 'ai', cutType: 'styling', similarSourceBlockId: 'root' }];
  const h = plannedHarness({ blocks, storyboard, api: {
    saveEditorBlocks: async (id, saved) => calls.push(['save', id, saved]),
    generateImage: async (_project, request) => { calls.push(['vary', request.source.src]); return { data: { src: '/v1/assets/child/file' } }; },
  }, fill: (_blockId, _elId, image) => calls.push(['fill', image.src]) });
  h.start('retry'); await h.run();
  assert.deepEqual(calls.map((call) => call[0]), ['save', 'vary', 'fill']);
  assert.equal(calls[0][2][0].elements[0].src, '/v1/assets/root/file');
  assert.equal(calls[1][1], '/v1/assets/root/file');
});

test('복제 칸의 실제 클릭 처리는 생성 호출 없이 같은 원본 사진을 넣는다', async () => {
  const fills = [];
  const storyboard = [{ id: 'root', source: 'ai', cutType: 'styling' }, { id: 'clone', source: 'ai', cutType: 'styling' }];
  const blocks = [{ id: 'page', elements: [{ id: 'root-image', type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' },
    plannedSlot('clone', { genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false })] }];
  const h = plannedHarness({ blocks, storyboard, api: {
    saveEditorBlocks: async () => assert.fail('복제는 생성 전 저장을 요구하지 않는다'),
    generateImage: async () => assert.fail('복제는 새 생성과 결제가 없다'),
  }, fill: (blockId, elId, image) => fills.push([blockId, elId, image.src]) });
  h.start('retry'); await h.run();
  assert.deepEqual(fills, [['page', 'empty', '/v1/assets/root/file']]);
});

test('원본 칸 저장이 거절되면 후속 생성이나 결제를 시작하지 않는다', async () => {
  const blocks = [{ id: 'page', elements: [{ id: 'root-image', type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' }, plannedSlot('child')] }];
  const storyboard = [{ id: 'root', source: 'ai', cutType: 'styling' },
    { id: 'child', source: 'ai', cutType: 'styling', similarSourceBlockId: 'root' }];
  const h = plannedHarness({ blocks, storyboard, api: {
    saveEditorBlocks: async () => { throw new Error('다른 창에서 더 최근에 저장했어요'); },
    generateImage: async () => assert.fail('저장 실패 뒤에는 생성하지 않는다'),
  }, fill: () => assert.fail('저장 실패 뒤에는 빈 칸을 바꾸지 않는다') });
  h.start('retry'); const job = await h.run();
  assert.equal(job.status, 'failed');
  assert.equal(job.error.message, '다른 창에서 더 최근에 저장했어요');
});

test('빈 칸 메뉴는 독립 실패의 다시 만들기와 원본이 필요한 칸을 구분한다', () => {
  const storyboard = [{ id: 'failed', source: 'ai', cutType: 'product' }];
  const retryMenu = (board, element, blocks = [{ id: 'page', elements: [element] }]) => {
    const retry = buildFailedCutRetry(board, element.sourceBlockId, null, null, { failure: element, editorBlocks: blocks });
    const menu = buildCanvasAiMenu({ block: blocks[0], element, storyboard: board, failedCutRetry: retry });
    return { retry, branch: menu.branches.find((branch) => branch.id === 'retry') };
  };
  for (const reason of ['no_garment_photo', 'provider_error']) {
    const { retry, branch } = retryMenu(storyboard, plannedSlot('failed', { genFailReason: reason, genFailRetryable: reason === 'provider_error' }));
    assert.equal(retry.retryable, true);
    assert.equal(branch.disabled, false);
    assert.equal(branch.label, '원래 콘티대로 다시');
  }
  const duplicates = [{ id: 'root', source: 'ai', cutType: 'styling' }, { id: 'clone', source: 'ai', cutType: 'styling' }];
  const empty = plannedSlot('clone', { genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false });
  const blocked = retryMenu(duplicates, empty);
  assert.equal(blocked.retry.guidance, '원본 컷을 먼저 만들어 주세요');
  assert.equal(blocked.branch.disabled, true);
  const ready = retryMenu(duplicates, empty, [{ id: 'page', elements: [
    { id: 'root-image', type: 'image', sourceBlockId: 'root', src: '/v1/assets/root/file' }, empty,
  ] }]);
  assert.equal(ready.branch.disabled, false);
  assert.equal(ready.retry.request, null);
  assert.equal(ready.retry.fillImage.src, '/v1/assets/root/file');
});

test('스토어의 실제 done 분기는 partial과 잔액을 보관하고 이벤트의 실패 원본을 유지한다', async () => {
  const partial = { requestedCuts: 3, completedCuts: 2,
    failedCuts: [{ blockId: 'failed', reason: 'duplicate_cut_source_failed', retryable: false }],
    quoteCredits: 95, creditsCharged: 76, creditsNotCharged: 19 };
  const server = await createServer({ root: fileURLToPath(new URL('../..', import.meta.url)),
    configFile: false, esbuild: { jsx: 'automatic' }, server: { middlewareMode: true, ws: false, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    plugins: [{ name: 'partial-job-api', enforce: 'pre',
      resolveId(id) {
        if (id.endsWith('/lib/api/index.js')) return '\0partial-api';
        if (id.endsWith('/lib/api/httpAdapter.js')) return '\0partial-http';
      },
      load(id) {
        if (id === '\0partial-http') return 'export const resetAnalysisCache = () => {};';
        if (id === '\0partial-api') return `export const api = {
          startDetailPage: async () => ({jobId:'job'}),
          getJob: async () => ({status:'done', result:{credits:123, partial:${JSON.stringify(partial)}}}),
          getJobEvents: async () => ({events:[{id:1, type:'step', payload:{blockId:'failed',
            status:'cut_failed', reason:'duplicate_cut_source_failed', retryable:false, sourceBlockId:'root'}}]}),
        };`;
      },
    }],
  });
  try {
    const { useAppStore } = await server.ssrLoadModule('/src/store/useAppStore.js');
    useAppStore.setState({ account: { credits: 199 } });
    await useAppStore.getState().startDetailPageGeneration('project');
    assert.equal(useAppStore.getState().detailPageJob.status, 'done');
    assert.deepEqual(useAppStore.getState().detailPageJob.partial, partial);
    assert.deepEqual(useAppStore.getState().detailPageJob.failedCutReasons.failed, {
      reason: 'duplicate_cut_source_failed', retryable: false, sourceBlockId: 'root',
    });
    assert.equal(useAppStore.getState().account.credits, 123);
  } finally { await server.close(); }
});
