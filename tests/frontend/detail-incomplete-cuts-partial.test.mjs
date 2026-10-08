/* 부분 실패(incomplete_cuts) 화면(2026-09-28) — 20장 중 1장만 실패했는데 "이미지를 모두 만들지
   못했어요"로 보이던 문제. 오너 결정 B(#420: 부분 페이지를 내보내지 않고 전액 해제)는 그대로 두고,
   ① 완성 컷은 '완성됐어요'(보관본 미리보기)로 남기고 실패 컷에만 '실패' 표식
   ② 리본은 "19장 완성 · 1장 실패" + 사실대로 된 문구
   ③ [실패한 컷만 다시 만들기] = 오늘의 [다시 시도]와 같은 길(reset + POST) — 서버가 보관본을 이어 쓴다
   ④ 다시 시도 중 "보관된 N장은 그대로 써요"(콘티가 바뀌었으면 그 사실을) */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { applyDetailJobEvents } from '../../src/lib/detailPageJobEvents.js';
import {
  incompleteCutsOf,
  incompleteFailurePatch,
  incompleteMessage,
  incompleteSummary,
  retryOfIncomplete,
  retryReuseHint,
} from '../../src/lib/detailPageJobFailure.js';
import { clearFailedJobPreviewBlocks, decorateGenBlocks, failedJobPreviewUrls, fillGenBlocks } from '../../src/lib/editorWaitSkeleton.js';

const read = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');
const editor = read('src/features/editor/Editor.jsx');
const store = read('src/store/useAppStore.js');
const css = read('src/styles/features.css');

const PID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const JID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const HOUR = 3600_000;
const SERVER_MESSAGE = '20장 중 1장을 만들지 못했어요. 완성된 19장은 보관돼 있어 다시 시도하면 추가 비용 없이 '
  + '그대로 쓰고, 실패한 컷만 새로 만들어요. 이번 시도는 크레딧이 차감되지 않았어요.';

const blockIds = Array.from({ length: 20 }, (_, i) => `sb${i + 1}`);

/** GET /v1/jobs/{id} — 20장 중 sb7 만 실패한 잡(서버 detail_page_job 의 result 모양). */
function failedJob({ now = Date.now(), retainedUntil = new Date(now + 20 * HOUR).toISOString(), digest = 'sb-digest-1' } = {}) {
  return {
    id: JID,
    status: 'error',
    errorMessage: SERVER_MESSAGE,
    result: {
      errorCode: 'incomplete_cuts',
      requestedCuts: 20,
      generatedCuts: 19,
      failedBlockIds: ['sb7'],
      completedBlockIds: blockIds.filter((id) => id !== 'sb7'),
      retainedBlockIds: blockIds.filter((id) => id !== 'sb7'),
      storyboardDigest: digest,
      retainedUntil,
    },
  };
}

const emptyJob = () => ({
  status: 'running', jobId: JID, projectId: PID, progress: 0, phase: null, cutsDone: 0, cutsTotal: 0,
  cuts: {}, live: [], failedCuts: [], copy: {}, errorMessage: '', slow: false,
  incomplete: null, retryOf: null, storyboardDigest: null, reusedCuts: [],
});

const skeleton = () => blockIds.map((id, i) => ({
  id: `b${i + 1}`,
  elements: [
    { id: `img${i + 1}`, type: 'image', sourceBlockId: id, src: '/placeholder.jpg' },
    { id: `txt${i + 1}`, type: 'text', text: '셀러 문구' },
  ],
}));

const step = (id, payload) => ({ id, type: 'step', payload });

// ── 해석(순수) ────────────────────────────────────────────────────────────────

test('서버 결과 → 요약: 실패·완성·보관 블록과 콘티 지문', () => {
  const inc = incompleteCutsOf(failedJob());
  assert.equal(inc.requested, 20);
  assert.deepEqual(inc.failed, ['sb7']);
  assert.equal(inc.completed.length, 19);
  assert.equal(inc.retained.length, 19);
  assert.equal(inc.expired, false);
  assert.equal(inc.storyboardDigest, 'sb-digest-1');
  assert.equal(incompleteSummary(inc), '19장 완성 · 1장 미완성');
  assert.equal(incompleteMessage(inc, SERVER_MESSAGE), SERVER_MESSAGE, '서버 문구가 정본');
  // 다른 실패는 부분 실패가 아니다
  assert.equal(incompleteCutsOf({ ...failedJob(), result: { errorCode: 'all_cuts_failed' } }), null);
  assert.equal(incompleteCutsOf({ ...failedJob(), status: 'done' }), null);
  assert.equal(incompleteCutsOf({ status: 'error', result: null }), null);
});

test('보관 기한(24시간)이 지났으면 "보관돼 있어요"라고 말하지 않는다', () => {
  const now = Date.now();
  const inc = incompleteCutsOf(failedJob({ now, retainedUntil: new Date(now - 1000).toISOString() }), now);
  assert.equal(inc.expired, true);
  assert.deepEqual(inc.retained, []);
  const msg = incompleteMessage(inc, SERVER_MESSAGE);
  assert.doesNotMatch(msg, /보관돼 있어/);
  assert.match(msg, /보관 기간\(24시간\)이 지나 다시 만들면 모든 컷을 새로 만들어요/);
  assert.match(msg, /크레딧이 차감되지 않았어요/);
});

test('store 실패 종결에 덧씌우는 값 — 완성 컷은 표식만(주소 없음), 실패 컷은 실패 표식', () => {
  const patch = incompleteFailurePatch(failedJob());
  assert.deepEqual(patch.failedCuts, ['sb7']);
  assert.equal(Object.keys(patch.cuts).length, 19);
  for (const cut of Object.values(patch.cuts)) assert.deepEqual(cut, { url: null, done: true });
  assert.equal(patch.cuts.sb7, undefined);
  assert.doesNotMatch(JSON.stringify(patch), /https?:|r2\.|users\//, '지워진 출력의 서명 주소·키를 남기지 않는다');
  // 부분 실패가 아니면 오늘처럼 전부 비운다
  assert.deepEqual(incompleteFailurePatch({ status: 'error', result: { errorCode: 'generation_failed' } }), { incomplete: null });
});

// ── ① 타일: 완성 컷은 남고, 실패 컷만 '실패' ────────────────────────────────────

test('부분 실패로 끝나면 완성 컷은 "완성됐어요"로 남고 실패 컷만 실패로 표시된다', () => {
  let job = emptyJob();
  let blocks = decorateGenBlocks(skeleton(), job, {});
  // 대기 중: 비-REAL 컷은 임시 주소로 채워졌고, REAL 컷(sb2)은 주소 없이 완료, sb7 은 실패
  job = applyDetailJobEvents(job, [
    step(1, { blockId: 'sb1', status: 'cut_done', previewUrl: 'https://r2.test/out-1' }),
    step(2, { blockId: 'sb2', status: 'cut_done' }),
    step(3, { blockId: 'sb7', status: 'cut_failed' }),
  ]);
  blocks = fillGenBlocks(blocks, job);
  assert.equal(blocks[0].elements[0].src, 'https://r2.test/out-1');

  // 잡 종결 — store 가 쓰는 것과 같은 patch(오늘의 초기화 + 부분 실패 덧씌우기)
  const failed = failedJob();
  job = {
    ...job, status: 'error', revokedPreviewUrls: ['https://r2.test/out-1'], cuts: {}, live: [],
    errorMessage: failed.errorMessage, ...incompleteFailurePatch(failed),
  };
  const revoked = new Set([...job.revokedPreviewUrls, ...failedJobPreviewUrls(blocks)]);
  const cleaned = clearFailedJobPreviewBlocks(blocks, revoked,
    { keptSourceIds: new Set(job.incomplete.completed) });
  const bySource = Object.fromEntries(cleaned.map((b) => [b.elements[0].sourceBlockId, b.elements[0]]));

  // 완성 컷: 지워진 임시 주소는 철회하되 '완성됐어요' 타일로 남는다
  assert.equal(bySource.sb1.src, null);
  assert.equal(bySource.sb1.genPending, 'done');
  assert.equal(bySource.sb1.genFailed, undefined);
  assert.equal(bySource.sb2.genPending, 'done');
  for (const id of job.incomplete.completed) assert.equal(bySource[id].genPending, 'done', id);
  // 실패 컷만 실패
  assert.equal(bySource.sb7.genPending, 'failed');
  assert.equal(bySource.sb7.genFailed, true);
  assert.doesNotMatch(JSON.stringify(cleaned), /r2\.test/);
  // 셀러 문구는 그대로
  assert.equal(cleaned[0].elements[1].text, '셀러 문구');

  // 이후 이벤트 채움이 와도 상태가 흔들리지 않는다(done 은 done, failed 는 failed)
  const refilled = fillGenBlocks(cleaned, job);
  assert.equal(refilled[0].elements[0].genPending, 'done');
  assert.equal(refilled[6].elements[0].genPending, 'failed');

  // 에디터 배선: 정리 이펙트가 완성 컷을 남기도록 keptSourceIds 를 넘기고, 실패 타일에 '실패' 꼬리표
  assert.match(editor, /keptSourceIds: new Set\(dpJob\.incomplete\?\.completed \|\| \[\]\)/);
  assert.match(editor, /clearFailedJobPreviewBlocks\(latestBlocks\.current, revokedUrls, keep\)/);
  assert.match(editor, /el\.genPending === 'failed' && <span className="ed-genwait-tag fail">미완성<\/span>/);
  assert.match(css, /\.ed-genwait-tag\.fail \{/);
});

test('완성 타일은 부분 실패 뒤 보관본 미리보기를 받고(주인·라이선스는 서버가 확인), 보관 여부를 말한다', async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const inc = incompleteCutsOf({
    ...failedJob(),
    result: { ...failedJob().result, retainedBlockIds: blockIds.filter((id) => id !== 'sb7' && id !== 'sb3') },
  });
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'incomplete-tile-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === '@/lib/api/index.js' || id.endsWith('/src/lib/api/index.js')) return '\0ic-api';
        if (id === '@/store/useAppStore.js' || id.endsWith('/src/store/useAppStore.js')) return '\0ic-store';
        return null;
      },
      load(id) {
        if (id === '\0ic-api') return 'export const api = { detailCutPreview: async () => { throw new Error("SSR"); } };';
        if (id === '\0ic-store') {
          return `export const useAppStore = (select) => select({ detailPageJob: {
            projectId: '${PID}', jobId: '${JID}', status: 'error', incomplete: ${JSON.stringify(inc)} } });`;
        }
        return null;
      },
    }],
  });
  try {
    const view = await server.ssrLoadModule('/src/features/editor/genDoneTile.jsx');
    const { GenDonePreview } = await server.ssrLoadModule('/src/features/editor/GenDonePreview.jsx');
    const kept = renderToStaticMarkup(createElement(GenDonePreview, { sourceBlockId: 'sb1' }));
    assert.match(kept, /완성됐어요/);
    assert.match(kept, /보관 중 · 다시 만들 때 그대로 써요/);
    const lost = renderToStaticMarkup(createElement(GenDonePreview, { sourceBlockId: 'sb3' }));
    assert.match(lost, /보관되지 않아 다시 만들 때 새로 만들어요/);
    const withImage = renderToStaticMarkup(createElement(view.GenDoneTileBody, { previewUrl: 'blob:x', retained: 'kept' }));
    assert.match(withImage, /<img[^>]*src="blob:x"/);
    assert.match(withImage, /완성됐어요 · 보관 중 · 다시 만들 때 그대로 써요/);
    // 생성 중(retained 없음)은 예전 문구 그대로
    const running = renderToStaticMarkup(createElement(view.GenDoneTileBody, { previewUrl: null }));
    assert.match(running, /상세페이지가 마무리되면 여기에 보여요/);
  } finally {
    await server.close();
  }
  const container = read('src/features/editor/GenDonePreview.jsx');
  // 실패 잡에서도 **보관된 컷만** 미리보기를 묻는다(라우트가 주인·보관·라이선스를 다시 확인).
  assert.match(container, /status === 'running' \|\| status === 'done' \|\| retained === 'kept'/);
});

// ── ② 리본 문구 ────────────────────────────────────────────────────────────────

test('리본: 미완성 컷 안내 + 서버의 사실대로 된 문구 + 보관 안내', () => {
  assert.match(editor, /const genIncomplete = \(dpJob\.status === 'error' && !genFinalizeError && dpJob\.incomplete\) \|\| null;/);
  assert.match(editor, /아직 못 만든 컷이 있어요/);
  assert.match(editor, /className="ed-genbar-detail">\{genFailureMessage\}/);
  assert.match(editor, /incompleteMessage\(genIncomplete, dpJob\.errorMessage\)/);
  assert.match(editor, /콘티를 바꾸지 않았다면 보관된 \{genIncomplete\.retained\.length\}장을 그대로 써요 · 24시간 보관/);
  assert.doesNotMatch(SERVER_MESSAGE, /모두 만들지 못했어요/);
});

// ── ③ [실패한 컷만 다시 만들기] = 오늘의 [다시 시도]와 같은 길 ────────────────────────

test('[다시 만들기]는 보관 컷 기준을 유지하며 reset + startDetailPageGeneration(POST) 으로 새 잡을 연다', () => {
  const at = editor.indexOf('>다시 만들기</Button>');
  assert.ok(at > 0, '버튼이 있다');
  const branch = editor.slice(editor.indexOf('genIncomplete && genIncomplete.retained.length > 0 ? ('), at);
  assert.match(branch, /const retryOf = retryOfIncomplete\(dpJob\);/);
  assert.match(branch, /useAppStore\.getState\(\)\.resetDetailPageJob\(\);\s*useAppStore\.getState\(\)\.startDetailPageGeneration\(projectId, \{ retryOf \}\);/);
  assert.match(branch, /setGenActive\(true\)/);
  assert.doesNotMatch(branch, /generateDetailPage|clearEditorWaitDraft/, '다른 생성 경로·작업본 삭제 없음');
  // 보관본이 없으면(기한 경과·보관 실패) "실패한 컷만"이라 말하지 않고 기존 [다시 시도]를 쓴다
  assert.ok(editor.indexOf('>다시 만들기</Button>', at + 1) > at);
  // store: 새 잡(POST)은 retryOf 를 들고 시작하고, 재시도 대상 표식으로 쓴다
  assert.match(store, /async startDetailPageGeneration\(projectId, \{ retryOf = null \} = \{\}\)/);
  assert.match(store, /status: 'running', projectId, startedAt: Date\.now\(\), retryOf \}/);
  assert.match(store, /running\.jobId \? \{ jobId: running\.jobId \} : await api\.startDetailPage\(projectId\)/);
  // 실패 종결: 오늘의 초기화 위에 부분 실패만 덧씌운다
  assert.match(store, /cuts: \{\},\s*live: \[\],[\s\S]{0,600}\.\.\.incompleteFailurePatch\(job\),\s*\}\);/);
});

test('store 실제 동작: 부분 실패 → [실패한 컷만 다시 만들기] → 같은 프로젝트로 POST 1번, 보관 수를 들고 시작', async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const calls = { start: [], getJob: [] };
  globalThis.__icApi = {
    startDetailPage: async (pid) => { calls.start.push(pid); return { jobId: `job-${calls.start.length}` }; },
    getJob: async (id) => {
      calls.getJob.push(id);
      return id === 'job-1' ? failedJob() : { id, status: 'running', progress: 30 };
    },
    getJobEvents: async () => ({ events: [] }),
    getAccount: async () => ({ credits: 100 }),
  };
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null },
    plugins: [{
      name: 'incomplete-store-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === '@/lib/api/index.js' || id.endsWith('/src/lib/api/index.js')) return '\0ics-api';
        if (id === '@/lib/api/httpAdapter.js' || id.endsWith('/src/lib/api/httpAdapter.js')) return '\0ics-http';
        return null;
      },
      load(id) {
        if (id === '\0ics-api') return 'export const api = new Proxy({}, { get: (_t, k) => globalThis.__icApi[k] || (async () => ({})) });';
        if (id === '\0ics-http') return 'export const resetAnalysisCache = () => {};';
        return null;
      },
    }],
  });
  try {
    const { useAppStore } = await server.ssrLoadModule('/src/store/useAppStore.js');
    const s = useAppStore.getState();
    // 첫 생성 — 서버가 부분 실패로 끝낸다
    await s.startDetailPageGeneration(PID);
    let job = useAppStore.getState().detailPageJob;
    assert.equal(job.status, 'error');
    assert.equal(job.jobId, 'job-1', '보관본 미리보기가 쓸 실패 잡 id 를 지킨다');
    assert.equal(job.errorMessage, '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.');
    assert.equal(incompleteSummary(job.incomplete), '19장 완성 · 1장 미완성');
    assert.deepEqual(job.failedCuts, ['sb7']);
    assert.equal(job.cuts.sb1.done, true);
    assert.equal(job.cuts.sb1.url, null);
    assert.equal(job.live.length, 0);

    // [실패한 컷만 다시 만들기] — 에디터 버튼과 같은 순서
    const retryOf = retryOfIncomplete(job);
    assert.deepEqual(retryOf, { jobId: 'job-1', retained: 19, failed: 1, storyboardDigest: 'sb-digest-1' });
    useAppStore.getState().resetDetailPageJob();
    const pending = useAppStore.getState().startDetailPageGeneration(PID, { retryOf });
    await new Promise((r) => setTimeout(r, 20));
    job = useAppStore.getState().detailPageJob;
    assert.deepEqual(calls.start, [PID, PID], '같은 프로젝트로 새 POST 1번(=새 잡 → 서버가 보관본을 이어 쓴다)');
    assert.equal(job.status, 'running');
    assert.equal(job.jobId, 'job-2');
    assert.deepEqual(job.retryOf, retryOf);
    assert.equal(job.incomplete, null);
    assert.equal(retryReuseHint(job), '보관된 19장은 그대로 써요');
    useAppStore.getState().resetDetailPageJob();   // 폴링 루프 무효화
    await pending;

    for (const [response, message, code] of [
      [{ ...failedJob(), errorMessage: '아직 못 만든 컷이 있어요. 완성된 컷은 보관돼 있어요.' },
        '아직 못 만든 컷이 있어요. 완성된 컷은 보관돼 있어요.', 'incomplete_cuts'],
      [{ ...failedJob(), errorMessage: '' }, '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.', 'incomplete_cuts'],
      [{ status: 'error', errorMessage: '' }, '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.', null],
      [{ status: 'cancelled', errorMessage: '' }, '다시 만들 수 있어요.', 'job_cancelled'],
    ]) {
      useAppStore.getState().resetDetailPageJob();
      globalThis.__icApi.getJob = async () => response;
      await useAppStore.getState().startDetailPageGeneration(PID);
      const terminal = useAppStore.getState().detailPageJob;
      assert.equal(terminal.status, 'error');
      assert.equal(terminal.errorMessage, message);
      assert.equal(terminal.errorCode, code);
    }
    useAppStore.getState().resetDetailPageJob();
    globalThis.__icApi.startDetailPage = async () => { throw new Error(); };
    await useAppStore.getState().startDetailPageGeneration(PID);
    assert.equal(useAppStore.getState().detailPageJob.errorMessage, '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.');
    for (const [status, errorCode, message] of [
      ['error', null, '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.'],
      ['error', 'incomplete_cuts', '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.'],
      ['cancelled', null, '다시 만들 수 있어요.'],
    ]) {
      useAppStore.getState().resetDetailPageJob();
      useAppStore.getState().seedDetailPageJob({ id: PID, status: 'draft', latestJobs: { detailPage: { id: JID, status, errorCode } } });
      assert.equal(useAppStore.getState().detailPageJob.errorMessage, message);
    }
  } finally {
    delete globalThis.__icApi;
    await server.close();
  }
});

// ── ④ 다시 시도 중 문구 — 사실대로 ────────────────────────────────────────────────

test('다시 시도 중: 같은 콘티면 "보관된 N장은 그대로 써요", 바뀌었으면 그 사실, 끝나면 실제 이어 쓴 결과', () => {
  const retryOf = { jobId: JID, retained: 19, failed: 1, storyboardDigest: 'sb-digest-1' };
  let job = { ...emptyJob(), retryOf };
  assert.equal(retryReuseHint(job), '보관된 19장은 그대로 써요');
  job = applyDetailJobEvents(job, [
    { id: 1, type: 'progress', payload: { progress: 15, phase: 'inputs_loaded', aiCuts: 20, storyboardDigest: 'sb-digest-1' } },
  ]);
  assert.equal(job.storyboardDigest, 'sb-digest-1');
  assert.equal(retryReuseHint(job), '보관된 19장은 그대로 써요');
  // 서버가 실제로 이어 쓴 컷(cut_checkpoint_reused, final)을 센다
  job = applyDetailJobEvents(job, [
    step(2, { blockId: 'sb1', status: 'cut_start' }),
    step(3, { blockId: 'sb1', status: 'cut_checkpoint_reused', stage: 'final' }),
    step(4, { blockId: 'sb1', status: 'cut_checkpoint_reused', stage: 'final' }),
    step(5, { blockId: 'sb2', status: 'cut_checkpoint_reused', stage: 'base' }),
    { id: 6, type: 'progress', payload: { progress: 85, phase: 'assemble' } },
  ]);
  assert.deepEqual(job.reusedCuts, ['sb1']);
  assert.equal(retryReuseHint(job), '보관된 컷을 그대로 썼어요');
  // 컷 단계가 끝났는데 하나도 못 이어 썼으면 그렇다고 말한다
  assert.equal(retryReuseHint({ ...job, reusedCuts: [] }), '보관된 컷을 쓰지 못해 새로 만들었어요');
  // 콘티가 바뀌었으면(지문이 다름) 보관본을 못 쓸 수 있다고 말한다
  const edited = applyDetailJobEvents({ ...emptyJob(), retryOf }, [
    { id: 1, type: 'progress', payload: { progress: 15, phase: 'inputs_loaded', storyboardDigest: 'sb-digest-EDITED' } },
  ]);
  assert.equal(retryReuseHint(edited), '콘티가 바뀌어 보관된 컷을 그대로 쓰지 못할 수 있어요');
  // 보통 생성(재시도 아님)은 아무 말도 덧붙이지 않는다
  assert.equal(retryReuseHint(emptyJob()), '');
  assert.equal(retryReuseHint({ ...emptyJob(), retryOf: { ...retryOf, retained: 0 } }), '');
  // 리본 배선
  assert.match(editor, /const genReuseHint = dpJob\.status === 'running' \? retryReuseHint\(dpJob\) : '';/);
  assert.match(editor, /\{dpJob\.cutsTotal\}장 중 \{dpJob\.cutsDone\}장<\/b> 완료\{genReuseHint \? ` · \$\{genReuseHint\}` : ''\}/);
});
