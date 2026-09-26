/* 상세페이지 생성 대기 — 화면이 먼저 포기하지 않고, 만든 컷은 바로·끝까지 보인다(2026-09-26).
   실측: 잡 2240c252(20컷)가 15분 7초에 끝났는데 화면은 15분에 오류 배너를 띄우고 폴링을
   멈춰, 서버가 19컷을 저장했는데도 셀러는 빈 캔버스를 봤다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DETAIL_JOB_POLL_MS,
  DETAIL_JOB_SLOW_AFTER_MS,
  DETAIL_JOB_SLOW_POLL_MS,
  pollDetailPageJob,
} from '../../src/lib/detailPageJobPoll.js';
import { applyDetailJobEvents } from '../../src/lib/detailPageJobEvents.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';

const store = readFileSync(new URL('../../src/store/useAppStore.js', import.meta.url), 'utf8');
const httpAdapter = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');

const MIN = 60_000;

/* 가짜 시계 — sleep 이 시간을 흘려 보낸다. 실제로 기다리지 않는다. */
function fakeClock(start = 1_000_000) {
  let t = start;
  const sleeps = [];
  return {
    now: () => t,
    sleep: async (ms) => { sleeps.push(ms); t += ms; },
    sleeps,
    start,
  };
}

const emptyJob = () => ({
  status: 'running', progress: 0, phase: null, cutsDone: 0, cutsTotal: 0,
  cuts: {}, live: [], failedCuts: [], copy: {}, errorMessage: '', slow: false,
});

test('15분을 넘겨도 서버 잡이 running 이면 오류 없이 계속 기다린다(느린 주기로)', async () => {
  const clock = fakeClock();
  const doneAt = clock.start + 15 * MIN + 7_000;   // 실측과 같은 15분 7초
  let polls = 0;
  const slowFlags = [];
  const job = await pollDetailPageJob({
    jobId: 'j1',
    startedAt: clock.start,
    now: clock.now,
    sleep: clock.sleep,
    getJob: async () => {
      polls += 1;
      return clock.now() >= doneAt
        ? { status: 'done', progress: 100, result: { credits: 7 } }
        : { status: 'running', progress: 80 };
    },
    getJobEvents: async () => ({ events: [] }),
    onJob: (_j, { slow }) => slowFlags.push(slow),
  });
  assert.equal(job.status, 'done', '15분에 포기하지 않고 완료를 받아야 한다');
  assert.ok(polls > 700, `폴링이 15분 내내 이어졌다(${polls}회)`);
  // 15분 전에는 1.2초, 넘긴 뒤에는 5초 주기
  assert.equal(clock.sleeps[0], DETAIL_JOB_POLL_MS);
  assert.equal(clock.sleeps.at(-1), DETAIL_JOB_SLOW_POLL_MS);
  assert.equal(slowFlags[0], false);
  assert.equal(slowFlags.at(-1), true, '15분 뒤에는 slow(오래 걸리는 중) 표식');
  assert.ok(DETAIL_JOB_SLOW_AFTER_MS === 15 * MIN);
});

test('한 시간이 지나도 running 이면 계속 본다 — 끝은 서버가 정한다', async () => {
  const clock = fakeClock();
  let n = 0;
  const job = await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async () => {
      n += 1;
      return clock.now() - clock.start > 60 * MIN ? { status: 'done' } : { status: 'pending' };
    },
  });
  assert.equal(job.status, 'done');
  assert.ok(n > 700);
});

test('오류는 서버가 error 라고 답할 때만 — 서버 errorMessage 를 그대로 돌려준다', async () => {
  const clock = fakeClock();
  let n = 0;
  const job = await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async () => {
      n += 1;
      return n < 3 ? { status: 'running' } : { status: 'error', errorMessage: '작업 서버가 종료되어 생성이 중단됐어요. 다시 시도해 주세요.' };
    },
  });
  assert.equal(job.status, 'error');
  assert.equal(job.errorMessage, '작업 서버가 종료되어 생성이 중단됐어요. 다시 시도해 주세요.');
});

test('cancelled 도 종결로 끝낸다 — 상한이 없으니 여기서 멈추지 않으면 영원히 돈다', async () => {
  const clock = fakeClock();
  const job = await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async () => ({ status: 'cancelled' }),
  });
  assert.equal(job.status, 'cancelled');
});

test('DB 503·게이트웨이·연결 끊김은 실패가 아니다 — 같은 jobId 재조회만 늦춘다', async () => {
  const clock = fakeClock();
  const failures = [
    Object.assign(new Error('db'), { status: 503 }),
    Object.assign(new Error('gw'), { status: 502 }),
    Object.assign(new Error('net'), { code: 'api_network' }),
    Object.assign(new Error('offline'), { code: 'browser_offline' }),
  ];
  const asked = [];
  const job = await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async (id) => {
      asked.push(id);
      if (failures.length) throw failures.shift();
      return { status: 'done' };
    },
  });
  assert.equal(job.status, 'done');
  assert.deepEqual([...new Set(asked)], ['j1'], '새 잡(POST) 없이 같은 jobId 만 다시 묻는다');
  assert.ok(clock.sleeps.every((ms) => ms <= DETAIL_JOB_SLOW_POLL_MS), '재조회 간격 상한 5초');
});

test('잡이 사라졌으면(404) 기다리지 않고 던진다 — 호출부가 실패로 보인다', async () => {
  const clock = fakeClock();
  await assert.rejects(pollDetailPageJob({
    jobId: 'gone', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async () => { throw Object.assign(new Error('작업을 찾을 수 없습니다.'), { status: 404 }); },
  }), /작업을 찾을 수 없습니다/);
});

test('재시작으로 무효화된 루프는 결과 없이 조용히 빠진다', async () => {
  const clock = fakeClock();
  let alive = true;
  const job = await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    isAlive: () => alive,
    getJob: async () => { alive = false; return { status: 'running' }; },
  });
  assert.equal(job, null);
});

test('이벤트는 after 커서로 이어 받고, 이벤트 조회 실패는 잡 폴링을 멈추지 않는다', async () => {
  const clock = fakeClock();
  const cursors = [];
  let n = 0;
  const got = [];
  await pollDetailPageJob({
    jobId: 'j1', startedAt: clock.start, now: clock.now, sleep: clock.sleep,
    getJob: async () => { n += 1; return { status: n >= 4 ? 'done' : 'running' }; },
    getJobEvents: async (_id, after) => {
      cursors.push(after);
      if (n === 2) throw new Error('events 503');
      return { events: [{ id: after + 1, type: 'progress', payload: { progress: 30 } }] };
    },
    onEvents: (events) => got.push(...events.map((e) => e.id)),
  });
  assert.deepEqual(cursors, [0, 1, 1, 2]);
  assert.deepEqual(got, [1, 2, 3]);
});

/* ---- 만든 컷은 바로 보이고, 잡이 멈춰도 남는다 ---- */

const skeleton = () => [{ id: 'b1', elements: [
  { id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'mock-placeholder.jpg' },
  { id: 'i2', type: 'image', sourceBlockId: 'sb2', src: 'mock-placeholder.jpg' },
  { id: 'i3', type: 'image', sourceBlockId: 'sb3', src: 'mock-placeholder.jpg' },
] }];
const step = (id, payload) => ({ id, type: 'step', payload });

test('cut_done 의 previewUrl 은 도착 즉시 그 자리에 그려진다', () => {
  let job = emptyJob();
  let blocks = decorateGenBlocks(skeleton(), job, {});
  assert.deepEqual(blocks[0].elements.map((el) => el.src), [null, null, null], '대기 타일로 시작');

  job = applyDetailJobEvents(job, [
    step(1, { blockId: 'sb1', status: 'cut_start' }),
    step(2, { blockId: 'sb2', status: 'cut_start' }),
    step(3, { blockId: 'sb1', status: 'cut_done', previewUrl: 'https://r2.test/p1', width: 880, height: 1320 }),
  ]);
  blocks = fillGenBlocks(blocks, job);
  const [e1, e2, e3] = blocks[0].elements;
  assert.equal(e1.src, 'https://r2.test/p1', '끝난 컷은 곧바로 보인다');
  assert.equal(e1.genPending, undefined);
  assert.equal(e2.genPending, 'live');
  assert.equal(e3.genPending, 'wait');
});

test('주소 없는 cut_done(REAL 얼굴 컷)은 "완성" 타일 — 대기로 되돌아가 보이지 않는다', () => {
  let job = emptyJob();
  let blocks = decorateGenBlocks(skeleton(), job, {});
  job = applyDetailJobEvents(job, [
    step(1, { blockId: 'sb1', status: 'cut_start' }),
    step(2, { blockId: 'sb1', status: 'cut_done', width: 880, height: 1320 }),
  ]);
  blocks = fillGenBlocks(blocks, job);
  assert.equal(blocks[0].elements[0].src, null);
  assert.equal(blocks[0].elements[0].genPending, 'done');
  assert.match(editor, /el\.genPending === 'done'/, '에디터가 완성 타일을 그린다');
  // 2026-09-26: 타일 내용은 GenDonePreview(미리보기, 거절되면 '완성됐어요')가 그린다 —
  // tests/frontend/detail-cut-preview.test.mjs 가 그 교체를 고정한다.
  assert.match(editor, /el\.genPending === 'done'[\s\S]{0,400}<GenDonePreview sourceBlockId=\{el\.sourceBlockId\} \/>/);
});

test('재생(after=0)으로 같은 이벤트를 다시 받아도 이미 받은 주소를 잃지 않는다', () => {
  const events = [
    step(1, { blockId: 'sb1', status: 'cut_start' }),
    step(2, { blockId: 'sb1', status: 'cut_done', previewUrl: 'https://r2.test/p1' }),
  ];
  const once = applyDetailJobEvents(emptyJob(), events);
  const twice = applyDetailJobEvents(once, events);
  assert.deepEqual(twice.cuts, once.cuts);
  // 주소 없는 재수신이 있던 주소를 지우지 않는다
  const noUrl = applyDetailJobEvents(once, [step(3, { blockId: 'sb1', status: 'cut_done' })]);
  assert.equal(noUrl.cuts.sb1.url, 'https://r2.test/p1');
});

test('잡이 error 로 멈춰도 이미 그린 컷은 캔버스에 그대로 남는다', () => {
  let job = emptyJob();
  let blocks = decorateGenBlocks(skeleton(), job, {});
  job = applyDetailJobEvents(job, [
    step(1, { blockId: 'sb1', status: 'cut_done', previewUrl: 'https://r2.test/p1' }),
    step(2, { blockId: 'sb2', status: 'cut_start' }),
  ]);
  blocks = fillGenBlocks(blocks, job);
  // store 의 실패 종결 — cuts 는 그대로, live 만 비운다
  job = { ...job, status: 'error', live: [], errorMessage: '작업 서버가 종료되어 생성이 중단됐어요. 다시 시도해 주세요.' };
  blocks = fillGenBlocks(blocks, job);
  assert.equal(blocks[0].elements[0].src, 'https://r2.test/p1', '만든 컷은 남는다');
  assert.equal(blocks[0].elements[1].genPending, 'wait', '더는 "생성 중"으로 돌지 않는다');

  // [다시 시도] — resetDetailPageJob 이 잡 상태를 비워도 캔버스의 컷은 지워지지 않는다
  const reset = emptyJob();
  blocks = fillGenBlocks(blocks, reset);
  assert.equal(blocks[0].elements[0].src, 'https://r2.test/p1');
});

test('store 는 실패 종결에서 컷을 지우지 않는다 — live 만 비운다', () => {
  const start = store.indexOf('const job = await pollDetailPageJob({');
  assert.ok(start > 0, 'store 가 공용 폴링 루프를 쓴다');
  const body = store.slice(start, store.indexOf('resetDetailPageJob() {', start));
  const errorPatches = [...body.matchAll(/patch\(\{\s*status: 'error'[^}]*\}/g)].map((m) => m[0]);
  assert.ok(errorPatches.length >= 2, `실패 종결 경로 ${errorPatches.length}개`);
  for (const p of errorPatches) {
    assert.doesNotMatch(p, /cuts:/, '실패 종결이 cuts 를 덮으면 만든 컷이 사라진다');
    assert.match(p, /live: \[\]/);
  }
});

test('완료 병합은 서버 완성본이 이긴다 — 확인 안 된 임시 미리보기는 저장본에 싣지 않는다', () => {
  const local = [{ id: 'b1', elements: [
    // 대기 중 자동으로 채운 미리보기(서버 완성본에 있음 → 안정 주소로 교체)
    { id: 'i1', type: 'image', sourceBlockId: 'sb1', src: 'https://r2.test/p1', genAutoSrc: 'https://r2.test/p1' },
    // 앞선 실패 잡의 미리보기인데 이번 완성본엔 없음(이번에도 실패) → 1h 뒤 깨질 주소를 남기지 않는다
    { id: 'i2', type: 'image', sourceBlockId: 'sb2', src: 'https://r2.test/old', genAutoSrc: 'https://r2.test/old', genPending: 'wait' },
  ] }];
  const server = [{ id: 's1', elements: [
    { id: 'x1', type: 'image', sourceBlockId: 'sb1', src: '/v1/assets/a1/file' },
    { id: 'x2', type: 'image', sourceBlockId: 'sb2', src: null },
  ] }];
  const merged = mergeServerBlocks(local, server, new Set(['sb2']))[0].elements;
  assert.equal(merged[0].src, '/v1/assets/a1/file');
  assert.equal(merged[1].src, null);
  assert.equal(merged[1].genFailed, true, '못 만든 컷 표식');
  // 재진입 병합(실패 목록 없음)은 기존처럼 로컬 주소를 지킨다
  const reentry = mergeServerBlocks(local, [], undefined)[0].elements;
  assert.equal(reentry[1].src, 'https://r2.test/old');
});

test('store·어댑터에 15분 포기 규칙이 남아 있지 않다', () => {
  assert.doesNotMatch(store, /\+ 900000/, 'store 가 시작+15분 데드라인으로 포기하면 안 된다');
  assert.doesNotMatch(store, /생성이 예상보다 오래 걸리고 있어요\. 잠시 후 다시 확인/);
  const call = httpAdapter.slice(httpAdapter.indexOf('async generateDetailPage'));
  const body = call.slice(0, call.indexOf('async startDetailPage'));
  assert.doesNotMatch(body, /timeoutMs/);
  assert.match(body, /pollDetailPageJob\(/);
});

test('에디터 리본: 오래 걸리면 "계속 만들고 있어요" + 진행 + 나중에 하기, 오류 배너는 실패 때만', () => {
  const bar = editor.slice(editor.indexOf('<span className="ed-genbar-msg">'), editor.indexOf('{/* FaceMarket 차단(409)'));
  assert.match(bar, /dpJob\.slow \? '예상보다 오래 걸리지만 계속 만들고 있어요'/);
  assert.match(bar, /\{dpJob\.cutsTotal\}장 중 \{dpJob\.cutsDone\}장<\/b> 완료/);
  assert.match(bar, /\{dpJob\.slow && \(\s*<Button[^>]*onClick=\{leaveToLibrary\}>나중에 하기<\/Button>/);
  assert.match(editor, /const genFailed = dpJob\.status === 'error' \|\| Boolean\(genFinalizeError\);/);
});

test('[다시 시도]는 서버 멱등 POST 로만 간다 — 활성 잡 합류·완성본 반환(중복 과금 없음)', () => {
  const retry = editor.slice(
    editor.indexOf('useAppStore.getState().resetDetailPageJob();', editor.indexOf('genFinalizeError ?')),
    editor.indexOf('>다시 시도</Button>'),
  );
  assert.match(retry, /useAppStore\.getState\(\)\.startDetailPageGeneration\(projectId\)/);
  assert.doesNotMatch(retry, /generateDetailPage|clearEditorWaitDraft/);
  // POST 는 startDetailPage 한 곳뿐이고, 완료 재호출(res.data)은 새 잡 없이 끝낸다
  assert.match(store, /running\.jobId \? \{ jobId: running\.jobId \} : await api\.startDetailPage\(projectId\)/);
  assert.match(store, /if \(res\.data\) \{/);
});
