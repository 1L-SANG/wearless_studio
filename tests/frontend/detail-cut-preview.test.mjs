import { fileURLToPath } from 'node:url';
/* 생성 중 REAL 얼굴 컷 미리보기(2026-09-26, 오너 결정 b).
   REAL 컷의 cut_done 에는 주소가 없다(492cbc64). 대기 타일은 '완성됐어요'만 말하다가, 이제
   서버 미리보기 라우트(요청마다 소유·라이선스 재확인)가 준 그림으로 바뀐다. 거절(403)이면
   조용히 타일로 남는다 — 오류 배너·콘솔 로그 없음. 미리보기는 블록 데이터에 들어가지 않는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  DETAIL_CUT_PREVIEW_RETRY_MS,
  createDetailCutPreviewCache,
  detailCutPreviewPath,
  fetchDetailCutPreview,
  openDetailCutPreview,
} from '../../src/lib/detailCutPreview.js';

const read = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');
const editor = read('src/features/editor/Editor.jsx');
const container = read('src/features/editor/GenDonePreview.jsx');
const httpAdapter = read('src/lib/api/httpAdapter.js');
const css = read('src/styles/features.css');

const PID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const JID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

const httpError = (status) => Object.assign(new Error('이미지를 불러오지 못했어요.'), { status });
const pngBlob = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: 'image/png' });
const noSleep = async () => {};

/* 콘솔 스팸 감시 — 거절·없음은 조용해야 한다. */
function watchConsole() {
  const calls = [];
  const orig = { error: console.error, warn: console.warn };
  console.error = (...a) => calls.push(['error', ...a]);
  console.warn = (...a) => calls.push(['warn', ...a]);
  return { calls, restore: () => Object.assign(console, orig) };
}

/* GenDonePreview 훅이 하는 일을 그대로 — 받기 → objectURL → 화면 값. */
function fakeObjectUrls() {
  const created = [];
  const revoked = [];
  return {
    created,
    revoked,
    createObjectURL: (blob) => { const url = `blob:test/${created.length + 1}`; created.push({ url, blob }); return url; },
    revokeObjectURL: (url) => revoked.push(url),
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function tileHarness() {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root,
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'detail-cut-preview-test-harness',
      enforce: 'pre',
      resolveId(id) {
        // '@' 별칭이 먼저 풀리므로 절대 경로로도 가로챈다(실제 api·store 는 supabase 를 당긴다).
        if (id === '@/lib/api/index.js' || id.endsWith('/src/lib/api/index.js')) return '\0dcp-api';
        if (id === '@/store/useAppStore.js' || id.endsWith('/src/store/useAppStore.js')) return '\0dcp-store';
        return null;
      },
      load(id) {
        if (id === '\0dcp-api') {
          return 'export const api = { detailCutPreview: async () => { throw new Error("SSR 에서는 부르지 않는다"); } };';
        }
        if (id === '\0dcp-store') {
          return `export const useAppStore = (select) => select({
            detailPageJob: { projectId: '${PID}', jobId: '${JID}', status: 'running' } });`;
        }
        return null;
      },
    }],
  });
  const view = await server.ssrLoadModule('/src/features/editor/genDoneTile.jsx');
  const containerModule = await server.ssrLoadModule('/src/features/editor/GenDonePreview.jsx');
  return { server, view, containerModule };
}

test('미리보기 경로 — 프로젝트·잡·블록을 모두 담고 인코딩한다', () => {
  assert.equal(detailCutPreviewPath(PID, JID, 'b1'), `/v1/projects/${PID}/jobs/${JID}/cuts/b1/preview`);
  assert.equal(detailCutPreviewPath('p', 'j', 'a/b c'), '/v1/projects/p/jobs/j/cuts/a%2Fb%20c/preview');
});

test('받으면 그림 — 인증 fetch(blob) 한 번', async () => {
  const seen = [];
  const res = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', sleep: noSleep,
    fetchBlob: async (path, opts) => { seen.push({ path, opts }); return pngBlob(); },
  });
  assert.equal(res.kind, 'image');
  assert.equal(res.blob.type, 'image/png');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].path, detailCutPreviewPath(PID, JID, 'b1'));
});

test('403(라이선스가 지금 유효하지 않음)은 다시 묻지 않고 조용히 타일로 남는다', async () => {
  const con = watchConsole();
  let calls = 0;
  try {
    const res = await fetchDetailCutPreview({
      projectId: PID, jobId: JID, blockId: 'b1', sleep: noSleep,
      fetchBlob: async () => { calls += 1; throw httpError(403); },
    });
    assert.deepEqual(res, { kind: 'tile', status: 403 });
  } finally { con.restore(); }
  assert.equal(calls, 1, '거절은 재시도하지 않는다(스팸 없음)');
  assert.deepEqual(con.calls, [], '콘솔 오류·경고도 없다');
});

test('404(끝났거나 아직 없음)도 다시 묻지 않는다', async () => {
  let calls = 0;
  const res = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', sleep: noSleep,
    fetchBlob: async () => { calls += 1; throw httpError(404); },
  });
  assert.deepEqual(res, { kind: 'tile', status: 404 });
  assert.equal(calls, 1);
});

test('일시 장애(503·연결 끊김)는 두 번까지만 다시 묻는다', async () => {
  const sleeps = [];
  let calls = 0;
  const ok = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', sleep: async (ms) => { sleeps.push(ms); },
    fetchBlob: async () => {
      calls += 1;
      if (calls === 1) throw httpError(503);
      if (calls === 2) throw new TypeError('Failed to fetch');
      return pngBlob();
    },
  });
  assert.equal(ok.kind, 'image');
  assert.deepEqual(sleeps, DETAIL_CUT_PREVIEW_RETRY_MS);

  let downCalls = 0;
  const down = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', sleep: noSleep,
    fetchBlob: async () => { downCalls += 1; throw httpError(503); },
  });
  assert.deepEqual(down, { kind: 'tile', status: 503 });
  assert.equal(downCalls, 1 + DETAIL_CUT_PREVIEW_RETRY_MS.length);
});

test('이미지가 아닌 응답·빈 입력·끊긴 요청은 타일', async () => {
  const html = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', sleep: noSleep,
    fetchBlob: async () => new Blob(['<html>'], { type: 'text/html' }),
  });
  assert.equal(html.kind, 'tile');
  assert.equal((await fetchDetailCutPreview({ projectId: PID, jobId: null, blockId: 'b1', fetchBlob: async () => pngBlob() })).kind, 'tile');
  const ctrl = new AbortController();
  ctrl.abort();
  const aborted = await fetchDetailCutPreview({
    projectId: PID, jobId: JID, blockId: 'b1', signal: ctrl.signal, sleep: noSleep,
    fetchBlob: async () => { throw new Error('부르면 안 된다'); },
  });
  assert.equal(aborted.kind, 'tile');
  assert.equal(aborted.aborted, true);
});

test('타일 → 그림: 받은 그림은 objectURL 로 보이고, 타일이 사라지면 거둔다', async () => {
  const { server, view } = await tileHarness();
  try {
    const urls = fakeObjectUrls();
    let previewUrl = null;
    // 처음엔 '완성됐어요' 타일
    const before = renderToStaticMarkup(createElement(view.GenDoneTileBody, { previewUrl }));
    assert.match(before, /ed-genwait-done/);
    assert.match(before, /완성됐어요/);
    assert.doesNotMatch(before, /<img/);

    const close = openDetailCutPreview({
      load: (signal) => fetchDetailCutPreview({
        projectId: PID, jobId: JID, blockId: 'b1', signal, sleep: noSleep,
        fetchBlob: async () => pngBlob(),
      }),
      ...urls,
      onUrl: (url) => { previewUrl = url; },
    });
    await flush();
    assert.equal(previewUrl, 'blob:test/1');
    const after = renderToStaticMarkup(createElement(view.GenDoneTileBody, { previewUrl }));
    assert.match(after, /<img class="ed-genwait-preview" src="blob:test\/1"/);
    assert.doesNotMatch(after, /ed-genwait-done/, '그림이 오면 완성 타일 문구 자리를 그림이 차지한다');
    assert.match(after, /마무리되면 편집할 수 있어요/, '자리는 여전히 잠겨 있다고 알린다');

    close();                        // 완료 병합으로 src 가 채워져 타일이 사라진 순간
    assert.deepEqual(urls.revoked, ['blob:test/1']);
  } finally {
    await server.close();
  }
});

test('403 이면 타일 그대로 — objectURL 을 만들지도, 콘솔에 남기지도 않는다', async () => {
  const { server, view } = await tileHarness();
  const con = watchConsole();
  try {
    const urls = fakeObjectUrls();
    let previewUrl = null;
    const close = openDetailCutPreview({
      load: (signal) => fetchDetailCutPreview({
        projectId: PID, jobId: JID, blockId: 'b1', signal, sleep: noSleep,
        fetchBlob: async () => { throw httpError(403); },
      }),
      ...urls,
      onUrl: (url) => { previewUrl = url; },
    });
    await flush();
    assert.equal(previewUrl, null);
    assert.deepEqual(urls.created, []);
    const html = renderToStaticMarkup(createElement(view.GenDoneTileBody, { previewUrl }));
    assert.match(html, /완성됐어요/);
    assert.doesNotMatch(html, /<img/);
    close();
    assert.deepEqual(urls.revoked, []);
  } finally {
    con.restore();
    await server.close();
  }
  assert.deepEqual(con.calls, []);
});

test('타일이 먼저 사라지면(늦게 온 응답) objectURL 을 만들지 않는다', async () => {
  const urls = fakeObjectUrls();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let seenSignal;
  let onUrlCalls = 0;
  const close = openDetailCutPreview({
    load: async (signal) => { seenSignal = signal; await gate; return { kind: 'image', blob: pngBlob() }; },
    ...urls,
    onUrl: () => { onUrlCalls += 1; },
  });
  await flush();
  close();
  assert.equal(seenSignal.aborted, true, '요청을 끊는다');
  release();
  await flush();
  assert.equal(onUrlCalls, 0);
  assert.deepEqual(urls.created, []);
});

test('같은 컷을 여러 곳이 그려도(캔버스·썸네일) 요청은 한 번, 마지막 사용처가 사라질 때 거둔다', async () => {
  const urls = fakeObjectUrls();
  const cache = createDetailCutPreviewCache(urls);
  let loads = 0;
  const load = async () => { loads += 1; return { kind: 'image', blob: pngBlob() }; };
  const seenA = [];
  const seenB = [];
  const releaseA = cache.acquire('p:j:b1', load, (u) => seenA.push(u));
  const releaseB = cache.acquire('p:j:b1', load, (u) => seenB.push(u));
  await flush();
  assert.equal(loads, 1);
  assert.deepEqual(seenA, ['blob:test/1']);
  assert.deepEqual(seenB, ['blob:test/1']);
  // 늦게 붙은 사용처는 이미 받은 그림을 바로 받는다
  const seenC = [];
  const releaseC = cache.acquire('p:j:b1', load, (u) => seenC.push(u));
  assert.deepEqual(seenC, ['blob:test/1']);
  releaseA();
  releaseA();                                   // 두 번 불러도 한 번만 센다
  releaseB();
  assert.deepEqual(urls.revoked, [], '아직 보는 곳이 있으면 거두지 않는다');
  releaseC();
  assert.deepEqual(urls.revoked, ['blob:test/1']);
  assert.equal(cache.size(), 0);
  // 다른 컷 자리는 따로 받는다
  const releaseD = cache.acquire('p:j:b2', load, () => {});
  await flush();
  assert.equal(loads, 2);
  releaseD();
});

test('컨테이너는 서버 렌더에서 타일로 시작하고, 블록 데이터를 건드리지 않는다', async () => {
  const { server, containerModule } = await tileHarness();
  try {
    const html = renderToStaticMarkup(createElement(containerModule.GenDonePreview, { sourceBlockId: 'b1' }));
    assert.match(html, /완성됐어요/);
  } finally {
    await server.close();
  }
  // 미리보기는 컴포넌트 상태로만 산다 — 블록·잡 스토어에 blob: 주소를 넣지 않는다.
  assert.doesNotMatch(container, /setBlocks|saveEditorBlocks|saveEditorWaitDraft|patch\(/);
  assert.match(container, /api\.detailCutPreview\(projectId, jobId, blockId, opts\)/);
  assert.match(container, /revokeObjectURL/);
  assert.match(container, /previewCache\.acquire\(/, '같은 컷은 한 번만 받는다');
  // 잡이 실패·차단으로 끝나면 거둔다(마감 재확인에서 철회됐을 수 있다).
  assert.match(container, /status === 'running' \|\| status === 'done'/);
});

test('배선 — 에디터 완성 타일·http 어댑터·스타일', () => {
  assert.match(editor, /import \{ GenDonePreview \} from '@\/features\/editor\/GenDonePreview\.jsx';/);
  assert.match(editor, /el\.genPending === 'done'[\s\S]{0,400}<GenDonePreview sourceBlockId=\{el\.sourceBlockId\} \/>/);
  // Bearer 를 싣는 blob 요청(httpBlob)으로 받는다 — <img src> 로는 인증을 못 싣는다.
  assert.match(httpAdapter, /detailCutPreview\(projectId, jobId, blockId, opts\) \{\s*return httpBlob\(detailCutPreviewPath\(projectId, jobId, blockId\), opts\);/);
  assert.match(css, /\.ed-genwait-preview \{[^}]*object-fit: cover/);
});
