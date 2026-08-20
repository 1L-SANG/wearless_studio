import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_CONSECUTIVE_FAILURES,
  POLL_LIMIT,
  POLL_MS,
  startToneEditorPolling,
} from '../../src/features/mannequin/toneEditorPolling.js';


const flush = () => new Promise((resolve) => setImmediate(resolve));

function manualSchedule() {
  const queued = [];
  return {
    queued,
    schedule(callback) {
      queued.push(callback);
      return callback;
    },
    cancel(handle) {
      const index = queued.indexOf(handle);
      if (index >= 0) queued.splice(index, 1);
    },
    async runNext() {
      assert.ok(queued.length, '다음 폴이 예약되어야 한다');
      queued.shift()();
      await flush();
    },
  };
}

test('tone polling survives three consecutive fetch failures and resets after success', async () => {
  const scheduler = manualSchedule();
  const responses = [
    () => Promise.reject(new Error('network-1')),
    () => Promise.reject(new Error('network-2')),
    () => Promise.reject(new Error('network-3')),
    () => Promise.resolve({ status: 'processing' }),
    () => Promise.reject(new Error('network-4')),
    () => Promise.reject(new Error('network-5')),
    () => Promise.reject(new Error('network-6')),
    () => Promise.resolve({ status: 'ready' }),
  ];
  const states = [];
  let failed = 0;

  const stop = startToneEditorPolling({
    fetchState: () => responses.shift()(),
    onState: (state) => states.push(state.status),
    onUnavailable: () => { failed += 1; },
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancel,
  });
  await flush();
  while (scheduler.queued.length) await scheduler.runNext();
  stop();

  assert.deepEqual(states, ['processing', 'ready']);
  assert.equal(failed, 0, '성공 응답 뒤에는 연속 실패 횟수가 초기화되어야 한다');
});

test('tone polling fails only after exceeding three consecutive fetch failures', async () => {
  const scheduler = manualSchedule();
  let calls = 0;
  let failed = 0;

  startToneEditorPolling({
    fetchState: async () => { calls += 1; throw new Error('offline'); },
    onState: () => {},
    onUnavailable: () => { failed += 1; },
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancel,
  });
  await flush();
  for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i += 1) {
    assert.equal(failed, 0);
    await scheduler.runNext();
  }

  assert.equal(calls, MAX_CONSECUTIVE_FAILURES + 1);
  assert.equal(failed, 1);
  assert.equal(scheduler.queued.length, 0);
});

test('tone polling runway covers four 90-second jobs plus escalating backoff', () => {
  const jobRuntimeMs = 4 * 90_000;
  const serverBackoffMs = (15 + 60 + 120) * 1_000;
  const pollAlignmentMs = 6 * POLL_MS;
  assert.ok(POLL_LIMIT * POLL_MS >= jobRuntimeMs + serverBackoffMs + pollAlignmentMs);
});

test('tone polling reports timeout instead of leaving processing on screen forever', async () => {
  const scheduler = manualSchedule();
  const reasons = [];

  startToneEditorPolling({
    fetchState: async () => ({ status: 'processing' }),
    onState: () => {},
    onUnavailable: (reason) => reasons.push(reason),
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancel,
    pollLimit: 2,
  });
  await flush();
  await scheduler.runNext();

  assert.deepEqual(reasons, ['timeout']);
  assert.equal(scheduler.queued.length, 0);
});
