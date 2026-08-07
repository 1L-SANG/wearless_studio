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
  let emit;
  const { runner, jobs } = harness({
    generate: (pid, { onProgress }) => {
      emit = onProgress;
      return Promise.resolve({ data: [], credits: 0 });
    },
  });

  await runner.request('p1');
  emit(42);
  assert.deepEqual(jobs[0], { pid: 'p1', status: 'running', progress: 0, errorMessage: '' });
  assert.deepEqual(jobs[1], { pid: 'p1', status: 'running', progress: 42, errorMessage: '' });
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

test('a missing project id never calls generate', async () => {
  let calls = 0;
  const { runner } = harness({ generate: async () => { calls += 1; return null; } });
  assert.equal(await runner.request(null), null);
  assert.equal(calls, 0);
});
