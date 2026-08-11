import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createMannequinGenerationRunner,
  resolveInitialGenerationCuts,
  runGenerationRelevantEditsRefresh,
} from '../../src/features/mannequin/generationRunnerCore.js';
import { createGenerationRelevantEditsSession } from '../../src/features/mannequin/generationRelevantEditsSession.js';
import {
  clearInitialGenerationRequested,
  cutsExistedBeforeInitialGeneration,
  hadInitialGenerationRequest,
  markInitialGenerationRequested,
} from '../../src/features/mannequin/initialGenerationSession.js';

function harness({ generate, onJobStarted }) {
  const jobs = [];
  const started = [];
  const runner = createMannequinGenerationRunner({
    generate,
    readProgress: () => 0,
    onJobChange: (pid, patch) => jobs.push({ pid, ...patch }),
    onJobStarted: (pid) => {
      started.push(pid);
      onJobStarted?.(pid);
    },
  });
  return { runner, jobs, started };
}

// 어댑터가 서버의 두 갈래를 러너에게 어떻게 전달하는지의 대역(httpAdapter.generateMannequins).
// 202 = job 생성 → onJobStarted 후 폴링 진행률. 200 = 컷이 이미 있음 → 아무 신호 없이 즉시 반환.
const serverStartedAJob = (payload, progresses = []) => async (_pid, { onJobStarted, onProgress }) => {
  onJobStarted('job-1');
  for (const p of progresses) onProgress(p);
  return payload;
};
const serverAnsweredWithExistingCuts = (payload) => async () => payload;

test('concurrent requests for one project share a single generate call', async () => {
  let calls = 0;
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const { runner, started } = harness({
    generate: (_pid, { onJobStarted }) => { calls += 1; onJobStarted('job-1'); return pending; },
  });

  const first = runner.request('p1');
  const second = runner.request('p1');
  assert.equal(first, second);
  assert.equal(calls, 1);
  assert.equal(started.length, 1);
  assert.equal(runner.isRunning('p1'), true);
  assert.equal(runner.isRunning('p2'), false);

  finish({ data: [], credits: 10 });
  assert.deepEqual(await first, { data: [], credits: 10 });
  assert.equal(runner.isRunning('p1'), false);
});

test('a settled run lets the next request fire again', async () => {
  let calls = 0;
  const { runner } = harness({
    generate: async () => { calls += 1; return { data: [], credits: 0 }; },
  });

  await runner.request('p1');
  await runner.request('p1');
  assert.equal(calls, 2);
});

test('progress callbacks and the initial patch reach the job sink', async () => {
  // emit() must fire while the run is still in flight (before the settle handler queues its
  // own job patch) — otherwise the assertions below race the terminal-status microtask below.
  let emit;
  const { runner, jobs } = harness({
    generate: (_pid, { onJobStarted, onProgress }) => {
      onJobStarted('job-1');
      emit = onProgress;
      return Promise.resolve({ data: [], credits: 0 });
    },
  });

  const pending = runner.request('p1');
  emit(42);
  await pending;
  assert.deepEqual(jobs[0], { pid: 'p1', status: 'running', progress: 0, errorMessage: '' });
  assert.deepEqual(jobs[1], { pid: 'p1', status: 'running', progress: 42, errorMessage: '' });
});

test('a progress tick alone is enough evidence that a job started', async () => {
  // onJobStarted 신호가 없어도 진행률이 오면 job 이 도는 것 — 종결 보고가 짝을 잃지 않아야 한다.
  const { runner, jobs, started } = harness({
    generate: async (_pid, { onProgress }) => { onProgress(7); return { data: [], credits: 0 }; },
  });

  await runner.request('p1');
  assert.deepEqual(started, ['p1']);
  assert.deepEqual(jobs.at(-1), { pid: 'p1', status: 'idle', progress: 100, errorMessage: '' });
});

// 백그라운드 발사(콘티)는 결과를 .then() 하지 않는다 — 러너가 종결 상태를 스스로 알리지
// 않으면 리본은 성공/실패를 영영 모른다. 두 테스트가 그 종결 보고를 고정한다.
test('a successful run reports a terminal idle status so a caller who never awaits still sees completion', async () => {
  const { runner, jobs } = harness({
    generate: serverStartedAJob({ data: [{ id: 'm1' }], credits: 3 }),
  });

  await runner.request('p1');
  assert.deepEqual(jobs.at(-1), { pid: 'p1', status: 'idle', progress: 100, errorMessage: '' });
});

test('a rejected run clears the in-flight slot', async () => {
  let calls = 0;
  const { runner } = harness({
    generate: async () => { calls += 1; throw new Error('generation failed'); },
  });

  await assert.rejects(runner.request('p1'), /generation failed/);
  assert.equal(runner.isRunning('p1'), false);
  await assert.rejects(runner.request('p1'), /generation failed/);
  assert.equal(calls, 2);
});

test('a rejected run reports a terminal error status carrying the failure message', async () => {
  // 발화 전에 죽은 실패(POST 자체 실패)도 알린다 — 백그라운드 발사는 rejection 을 삼키므로
  // 리본이 유일한 통로다. "없는 일을 진행 중이라 알리는 것" 과 달리 실패는 진짜 사건이다.
  const { runner, jobs } = harness({
    generate: async () => { throw new Error('generation failed'); },
  });

  await assert.rejects(runner.request('p1'), /generation failed/);
  assert.deepEqual(jobs.at(-1), {
    pid: 'p1', status: 'error', progress: 0, errorMessage: 'generation failed',
  });
});

test('a missing project id never calls generate', async () => {
  let calls = 0;
  const { runner } = harness({ generate: async () => { calls += 1; return null; } });
  assert.equal(await runner.request(null), null);
  assert.equal(calls, 0);
});

// ─── 시작하지 않은 생성은 알리지도, 소유권을 주장하지도 않는다 (C1 / I1) ────────────────
// 서버는 컷이 이미 있으면 job 없이 200 으로 답한다(무차감·무작업). 콘티는 진입할 때마다
// 발사하므로 이 갈래는 흔한 경로다.

test('a 200 "cuts already exist" answer never claims the initial generation', async () => {
  const { runner, started } = harness({
    generate: serverAnsweredWithExistingCuts({ data: [{ id: 'm1' }], credits: 0 }),
  });

  const result = await runner.request('p1');
  assert.deepEqual(started, []);
  assert.deepEqual(result, { data: [{ id: 'm1' }], credits: 0 });
});

test('a 200 "cuts already exist" answer writes no running and no completion to the ribbon', async () => {
  const { runner, jobs } = harness({
    generate: serverAnsweredWithExistingCuts({ data: [{ id: 'm1' }], credits: 0 }),
  });

  await runner.request('p1');
  // 진행 중도 완료도 없다 — 리본이 "만들고 있어요 0%" 를 번쩍이고 "준비 완료" 배지를 3초
  // 띄우면 일어나지 않은 작업을 보고하는 것이다(상세페이지 실패 후 콘티로 튕길 때가 최악).
  assert.deepEqual(jobs, []);
});

test('a 202 answer opens the ribbon and claims the initial generation', async () => {
  const { runner, jobs, started } = harness({
    generate: serverStartedAJob({ data: [{ id: 'm1' }], credits: 3 }, [40]),
  });

  await runner.request('p1');
  assert.deepEqual(started, ['p1']);
  assert.deepEqual(jobs[0], { pid: 'p1', status: 'running', progress: 0, errorMessage: '' });
  assert.deepEqual(jobs[1], { pid: 'p1', status: 'running', progress: 40, errorMessage: '' });
  assert.deepEqual(jobs.at(-1), { pid: 'p1', status: 'idle', progress: 100, errorMessage: '' });
});

// ─── 러너와 술어를 한 테스트에서 함께 돌린다 (C1 의 진짜 이음매) ─────────────────────────
// 두 반쪽(러너가 플래그를 세운다 / 마네킹 화면이 그 플래그로 유료 재생성을 게이트한다)은
// sessionStorage 키 하나로만 이어져 있어 따로 보면 둘 다 옳다. 여기서 붙여 본다.

function withSessionStorage() {
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => { store.set(key, String(value)); },
    removeItem: (key) => { store.delete(key); },
  };
  return store;
}

test('marking a relevant edit invalidates the prior initial-generation ownership marker', () => {
  const storage = withSessionStorage();
  const dirty = createGenerationRelevantEditsSession({
    storage: globalThis.sessionStorage,
    clearInitialRequested: clearInitialGenerationRequested,
  });
  markInitialGenerationRequested('p-edited');
  assert.equal(hadInitialGenerationRequest('p-edited'), true);

  assert.equal(dirty.mark('p-edited'), true);

  assert.equal(hadInitialGenerationRequest('p-edited'), false);
  assert.equal(cutsExistedBeforeInitialGeneration('p-edited', [{ id: 'old-cut' }]), true);
  assert.ok(storage.get('wl_generation_relevant_edits:p-edited'));
});

test('empty entry joins a pre-edit generation, preserves dirty, and starts paid regeneration', async () => {
  withSessionStorage();
  const projectId = 'p-pre-edit-inflight';
  const dirty = createGenerationRelevantEditsSession({
    storage: globalThis.sessionStorage,
    clearInitialRequested: clearInitialGenerationRequested,
  });
  let initialGenerateCalls = 0;
  let finishInitial;
  const initialPending = new Promise((resolve) => { finishInitial = resolve; });
  const { runner } = harness({
    generate: (_pid, { onJobStarted }) => {
      initialGenerateCalls += 1;
      onJobStarted('old-job');
      return initialPending;
    },
    onJobStarted: markInitialGenerationRequested,
  });

  const storyboardRequest = runner.request(projectId);
  assert.equal(hadInitialGenerationRequest(projectId), true);
  dirty.mark(projectId);
  assert.equal(hadInitialGenerationRequest(projectId), false);

  const entryLoad = resolveInitialGenerationCuts({
    projectId,
    initialCuts: [],
    requestGeneration: (pid) => {
      const joined = runner.request(pid);
      assert.equal(joined, storyboardRequest, 'the empty entry must join the storyboard request');
      return joined;
    },
    extractCuts: (data) => data,
    classifyCuts: cutsExistedBeforeInitialGeneration,
  });
  finishInitial({ data: [{ id: 'old-cut' }], credits: 9 });
  const loaded = await entryLoad;

  assert.equal(initialGenerateCalls, 1);
  assert.deepEqual(loaded.cuts, [{ id: 'old-cut' }]);
  assert.equal(loaded.cutsExisted, true, 'the joined pre-edit result must require regeneration');
  assert.equal(dirty.read(projectId), true);
  clearInitialGenerationRequested(projectId); // Mannequin load clears the marker after classifying.

  let paidCalls = 0;
  let acceptPaid;
  let finishPaid;
  const paidPending = new Promise((resolve) => { finishPaid = resolve; });
  const refresh = runGenerationRelevantEditsRefresh({
    handledRef: { current: false },
    readDirtyRevision: () => dirty.readRevision(projectId),
    cutsExisted: loaded.cutsExisted,
    regenerate: (onSucceeded) => {
      paidCalls += 1;
      acceptPaid = onSucceeded;
      return paidPending;
    },
    clearDirty: (revision) => dirty.clear(projectId, revision),
  });

  assert.equal(paidCalls, 1);
  assert.equal(dirty.read(projectId), true, 'dirty must survive until paid generation succeeds');
  acceptPaid();
  assert.equal(dirty.read(projectId), false);
  finishPaid(true);
  assert.equal(await refresh, true);
});

test('empty entry joins the same initial generation without paid regeneration when nothing changed', async () => {
  withSessionStorage();
  const projectId = 'p-unedited-inflight';
  const dirty = createGenerationRelevantEditsSession({ storage: globalThis.sessionStorage });
  let initialGenerateCalls = 0;
  let finishInitial;
  const initialPending = new Promise((resolve) => { finishInitial = resolve; });
  const { runner } = harness({
    generate: (_pid, { onJobStarted }) => {
      initialGenerateCalls += 1;
      onJobStarted('initial-job');
      return initialPending;
    },
    onJobStarted: markInitialGenerationRequested,
  });

  const storyboardRequest = runner.request(projectId);
  const entryLoad = resolveInitialGenerationCuts({
    projectId,
    initialCuts: [],
    requestGeneration: (pid) => {
      const joined = runner.request(pid);
      assert.equal(joined, storyboardRequest, 'the empty entry must join the storyboard request');
      return joined;
    },
    extractCuts: (data) => data,
    classifyCuts: cutsExistedBeforeInitialGeneration,
  });
  finishInitial({ data: [{ id: 'initial-cut' }], credits: 9 });
  const loaded = await entryLoad;

  assert.equal(initialGenerateCalls, 1);
  assert.equal(loaded.cutsExisted, false, 'the unedited initial result remains owned by its job');
  clearInitialGenerationRequested(projectId);
  let paidCalls = 0;
  const refreshed = await runGenerationRelevantEditsRefresh({
    handledRef: { current: false },
    readDirtyRevision: () => dirty.readRevision(projectId),
    cutsExisted: loaded.cutsExisted,
    regenerate: async () => { paidCalls += 1; return true; },
    clearDirty: (revision) => dirty.clear(projectId, revision),
  });

  assert.equal(refreshed, false);
  assert.equal(paidCalls, 0);
  assert.equal(dirty.read(projectId), false);
});

test('the dirty signal survives refresh only in the same tab and stays project-scoped', () => {
  withSessionStorage();
  const sameTabStorage = globalThis.sessionStorage;
  const currentPage = createGenerationRelevantEditsSession({ storage: sameTabStorage });
  currentPage.mark('p1');

  const refreshedPage = createGenerationRelevantEditsSession({ storage: sameTabStorage });
  assert.equal(refreshedPage.read('p1'), true);
  assert.equal(refreshedPage.read('p2'), false);
  refreshedPage.mark('p2');
  refreshedPage.clear('p1');
  assert.equal(refreshedPage.read('p2'), true, 'clearing p1 must not consume p2');

  // 게스트 편집(null)은 메모리 dirty로 남고, 로그인 뒤 같은 작업이 id를 얻을 때 그 id로 옮긴다.
  const anonymousDirty = currentPage.mark(null);
  assert.equal(currentPage.adopt('p3', { preserveDirty: anonymousDirty }), true);
  assert.equal(refreshedPage.read('p3'), true);

  const nextTab = createGenerationRelevantEditsSession({
    storage: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    },
  });
  assert.equal(nextTab.read('p1'), false);

  assert.equal(createGenerationRelevantEditsSession({ storage: sameTabStorage }).read('p1'), false);
});

test('a pending edit refresh is de-duplicated and clears dirty only after success', async () => {
  let calls = 0;
  let clears = 0;
  let accept;
  let finish;
  let dirtyRevision = 'revision-1';
  const handledRef = { current: false };
  const pending = new Promise((resolve) => { finish = resolve; });
  const options = {
    handledRef,
    readDirtyRevision: () => dirtyRevision,
    cutsExisted: true,
    regenerate: (onSucceeded) => { calls += 1; accept = onSucceeded; return pending; },
    clearDirty: (expectedRevision) => {
      if (dirtyRevision !== expectedRevision) return false;
      clears += 1;
      dirtyRevision = null;
      return true;
    },
  };

  const first = runGenerationRelevantEditsRefresh(options);
  const repeated = runGenerationRelevantEditsRefresh(options);
  assert.equal(calls, 1);
  assert.equal(dirtyRevision, 'revision-1');
  assert.equal(clears, 0);

  accept();
  assert.equal(dirtyRevision, null, 'server success must consume dirty before UI post-processing settles');
  assert.equal(clears, 1);
  finish(true);
  assert.equal(await first, true);
  assert.equal(await repeated, false);
  assert.equal(calls, 1);
  assert.equal(dirtyRevision, null);
  assert.equal(clears, 1);
});

test('a failed edit refresh stays dirty without re-firing until the next screen entry', async () => {
  let calls = 0;
  let clears = 0;
  let dirtyRevision = 'revision-1';
  const regenerate = async (onSucceeded) => {
    calls += 1;
    if (calls > 1) {
      onSucceeded();
      return true;
    }
    return false;
  };
  const run = (handledRef) => runGenerationRelevantEditsRefresh({
    handledRef,
    readDirtyRevision: () => dirtyRevision,
    cutsExisted: true,
    regenerate,
    clearDirty: (expectedRevision) => {
      if (dirtyRevision !== expectedRevision) return false;
      clears += 1;
      dirtyRevision = null;
      return true;
    },
  });

  const firstEntryRef = { current: false };
  assert.equal(await run(firstEntryRef), false);
  assert.equal(dirtyRevision, 'revision-1');
  assert.equal(clears, 0);
  assert.equal(await run(firstEntryRef), false);
  assert.equal(calls, 1, 'the same mount must not issue a second paid request');

  assert.equal(await run({ current: false }), true);
  assert.equal(calls, 2, 'a later screen entry may retry the preserved signal');
  assert.equal(dirtyRevision, null);
  assert.equal(clears, 1);
});

test('an edit made before initial cuts arrive clears without a paid regeneration', async () => {
  let calls = 0;
  let dirtyRevision = 'revision-1';
  const result = await runGenerationRelevantEditsRefresh({
    handledRef: { current: false },
    readDirtyRevision: () => dirtyRevision,
    cutsExisted: false,
    regenerate: async () => { calls += 1; return true; },
    clearDirty: (expectedRevision) => {
      if (dirtyRevision === expectedRevision) dirtyRevision = null;
    },
  });

  assert.equal(result, true);
  assert.equal(calls, 0);
  assert.equal(dirtyRevision, null);
});

test('a successful older request cannot consume a newer edit in the same project', async () => {
  let accept;
  let finish;
  let dirtyRevision = 'revision-1';
  const pending = new Promise((resolve) => { finish = resolve; });
  const request = runGenerationRelevantEditsRefresh({
    handledRef: { current: false },
    readDirtyRevision: () => dirtyRevision,
    cutsExisted: true,
    regenerate: (onSucceeded) => { accept = onSucceeded; return pending; },
    clearDirty: (expectedRevision) => {
      if (dirtyRevision !== expectedRevision) return false;
      dirtyRevision = null;
      return true;
    },
  });

  dirtyRevision = 'revision-2';
  accept();
  assert.equal(dirtyRevision, 'revision-2');
  finish(true);
  assert.equal(await request, true);
  assert.equal(dirtyRevision, 'revision-2', 'the success fallback must use the same captured revision');
});

test('a refresh recognizes a landed attempt from its persisted cut baseline', () => {
  const storage = withSessionStorage();
  const beforeRefresh = createGenerationRelevantEditsSession({
    storage: globalThis.sessionStorage,
    nextAttemptId: () => 'attempt-1',
  });
  beforeRefresh.mark('p1');
  const revision = beforeRefresh.readRevision('p1');
  const idempotencyKey = beforeRefresh.markAttempt('p1', revision, {
    ids: new Set(['cut-v1']),
    maxVersion: 1,
  });

  const afterRefresh = createGenerationRelevantEditsSession({ storage: globalThis.sessionStorage });
  assert.equal(
    afterRefresh.markAttempt('p1', revision, { ids: new Set(['cut-v1']), maxVersion: 1 }),
    idempotencyKey,
    'the POST after refresh must reuse the original server idempotency key',
  );
  assert.equal(afterRefresh.landedAttemptRevision('p1', [{ id: 'cut-v2', version: 2 }]), revision);
  assert.equal(afterRefresh.clear('p1', revision), true);
  assert.equal(afterRefresh.read('p1'), false);
  assert.equal(storage.has('wl_generation_relevant_edits_attempt:p1'), false);
});

test('a confirmed failed job can rotate its idempotency key without clearing dirty', () => {
  let attempt = 0;
  const storage = withSessionStorage();
  const session = createGenerationRelevantEditsSession({
    storage: globalThis.sessionStorage,
    nextAttemptId: () => `attempt-${++attempt}`,
  });
  session.mark('p1');
  const revision = session.readRevision('p1');
  const baseline = { ids: new Set(['cut-v1']), maxVersion: 1 };
  const firstKey = session.markAttempt('p1', revision, baseline);

  assert.equal(session.clearAttempt('p1', revision), true);
  const retryKey = session.markAttempt('p1', revision, baseline);
  assert.notEqual(retryKey, firstKey);
  assert.equal(session.readRevision('p1'), revision, 'rotating a failed attempt must preserve dirty');
  assert.ok(storage.has('wl_generation_relevant_edits:p1'));
});

test('blocked sessionStorage falls back to an in-memory revision for the current mount', () => {
  const blockedStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  const session = createGenerationRelevantEditsSession({
    storage: blockedStorage,
    nextAttemptId: () => 'memory-attempt',
  });

  session.mark('p1');
  const revision = session.readRevision('p1');
  assert.ok(revision);
  assert.equal(session.read('p1'), true);
  assert.match(
    session.markAttempt('p1', revision, { ids: new Set(['cut-v1']), maxVersion: 1 }),
    /memory-attempt/,
  );
  assert.equal(session.clear('p1', revision), true);
  assert.equal(session.read('p1'), false);
});

test('a newer edit invalidates the prior attempt and rejects its conditional clear', () => {
  const storage = withSessionStorage();
  const session = createGenerationRelevantEditsSession({ storage: globalThis.sessionStorage });
  session.mark('p1');
  const firstRevision = session.readRevision('p1');
  session.markAttempt('p1', firstRevision, { ids: new Set(['cut-v1']), maxVersion: 1 });

  session.mark('p1');
  const secondRevision = session.readRevision('p1');
  assert.notEqual(secondRevision, firstRevision);
  assert.equal(session.clear('p1', firstRevision), false);
  assert.equal(session.readRevision('p1'), secondRevision);
  assert.equal(session.landedAttemptRevision('p1', [{ id: 'cut-v2', version: 2 }]), null);
  assert.equal(storage.has('wl_generation_relevant_edits_attempt:p1'), false);
});

test('a storyboard fire that started nothing leaves the paid regeneration gate armed', async () => {
  withSessionStorage();
  clearInitialGenerationRequested('p1');
  const existingCuts = [{ id: 'm1' }];

  // 사용자가 입력으로 되돌아가 분석을 고친 뒤 콘티를 다시 지나온 상황: 컷은 이미 있으므로
  // 서버는 200 으로 답한다(job 없음, 차감 없음). 이 호출은 아무것도 시작하지 않았다.
  const { runner } = harness({
    generate: serverAnsweredWithExistingCuts({ data: existingCuts, credits: 0 }),
    onJobStarted: markInitialGenerationRequested,
  });
  await runner.request('p1');

  // 마네킹 화면이 initialCutsExistedRef 를 계산하는 지점. true 여야 dirty 신호가
  // 유료 regenerate() 로 이어진다 — false 면 사용자의 분석 수정이 조용히 버려진다.
  assert.equal(cutsExistedBeforeInitialGeneration('p1', existingCuts), true);
});

test('a fire that really started the initial generation disowns its own fresh cuts', async () => {
  withSessionStorage();
  clearInitialGenerationRequested('p2');
  const freshCuts = [{ id: 'm1' }];

  // 컷이 없던 프로젝트: 서버가 202 로 job 을 만든다. 잠시 뒤 마네킹 화면이 보는 컷은
  // 원래 있던 게 아니라 이 발사가 만든 것이므로 재생성 게이트는 닫혀 있어야 한다.
  const { runner } = harness({
    generate: serverStartedAJob({ data: freshCuts, credits: 3 }),
    onJobStarted: markInitialGenerationRequested,
  });
  await runner.request('p2');

  assert.equal(cutsExistedBeforeInitialGeneration('p2', freshCuts), false);
});

// ─── 어댑터 쪽 절반 (import.meta.env + '@/' alias 때문에 node 로 임포트 불가) ────────────
// 러너 테스트는 "onJobStarted 를 받으면 이렇게 행동한다" 까지만 고정한다. 그 신호가 202
// 갈래에서만 나온다는 사실은 어댑터 소스에서만 확인할 수 있어 여기만 텍스트로 고정한다.
test('generateMannequins signals the job start only after the 200 cache branch has returned', () => {
  const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const body = source.slice(
    source.indexOf('async generateMannequins'),
    source.indexOf('async adjustMannequin'),
  );
  assert.match(body, /\{ onProgress, onJobStarted \} = \{\}/);
  const cacheReturn = body.indexOf('return { data: res.data, credits: res.credits };');
  const signal = body.indexOf('onJobStarted?.(res.jobId)');
  assert.ok(cacheReturn > 0 && signal > 0);
  // 200 캐시 early-return 이 신호보다 먼저 와야 한다 — 순서가 뒤집히면 job 없는 응답도
  // "시작했다" 로 보고된다.
  assert.ok(cacheReturn < signal, '200 early-return must precede the onJobStarted signal');
});

test('regenerateMannequin forwards the stable edit-attempt idempotency key', () => {
  const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const body = source.slice(
    source.indexOf('async regenerateMannequin'),
    source.indexOf('// 에디터 Wardrobe'),
  );
  assert.match(body, /\{ fitProfile, onProgress, idempotencyKey \}/);
  assert.match(body, /'Idempotency-Key': idempotencyKey/);

  const mannequinSource = readFileSync(new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url), 'utf8');
  assert.match(mannequinSource, /idempotencyKey: generationAttempt\.idempotencyKey/);
  assert.match(mannequinSource, /error\?\.code === 'job_failed'/);
});
