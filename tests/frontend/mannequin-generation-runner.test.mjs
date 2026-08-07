import test from 'node:test';
import assert from 'node:assert/strict';

import { createMannequinGenerationRunner } from '../../src/features/mannequin/generationRunnerCore.js';

function harness({ generate }) {
  const jobs = [];
  const requested = [];
  const runner = createMannequinGenerationRunner({
    generate,
    readProgress: () => 0,
    onJobChange: (pid, patch) => jobs.push({ pid, ...patch }),
    onRequested: (pid) => requested.push(pid),
  });
  return { runner, jobs, requested };
}

test('concurrent requests for one project share a single generate call', async () => {
  let calls = 0;
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const { runner, requested } = harness({
    generate: () => { calls += 1; return pending; },
  });

  const first = runner.request('p1');
  const second = runner.request('p1');
  assert.equal(first, second);
  assert.equal(calls, 1);
  assert.equal(requested.length, 1);
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
    generate: (pid, { onProgress }) => {
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

// 백그라운드 발사(콘티)는 결과를 .then() 하지 않는다 — 러너가 종결 상태를 스스로 알리지
// 않으면 리본은 성공/실패를 영영 모른다. 두 테스트가 그 종결 보고를 고정한다.
test('a successful run reports a terminal idle status so a caller who never awaits still sees completion', async () => {
  const { runner, jobs } = harness({
    generate: async () => ({ data: [{ id: 'm1' }], credits: 3 }),
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
