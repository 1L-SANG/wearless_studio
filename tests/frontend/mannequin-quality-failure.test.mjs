import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { jobFailure } from '../../src/lib/api/jobFailure.js';
import { isNonRetryableRegenerateError, runGenerationRelevantEditsRefresh } from '../../src/features/mannequin/generationRunnerCore.js';
import { createGenerationRelevantEditsSession } from '../../src/features/mannequin/generationRelevantEditsSession.js';
import { createInitialGenerationAttempts, runInitialGenerationAttempt } from '../../src/features/mannequin/initialGenerationAttempts.js';

test('final quality failure from job result stops automatic regeneration', () => {
  const error = jobFailure({
    status: 'error', errorMessage: '상품 재현 기준을 충족하지 못했어요.',
    result: { errorCode: 'mannequin_quality_failed' },
  });
  assert.equal(error.code, 'mannequin_quality_failed');
  assert.equal(error.message, '상품 재현 기준을 충족하지 못했어요.');
  assert.equal(isNonRetryableRegenerateError(error), true);
});

test('an ordinary job failure keeps the existing retry behavior', () => {
  const error = jobFailure({ status: 'error', errorMessage: '일시적인 서버 오류' });
  assert.equal(error.code, 'job_failed');
  assert.equal(isNonRetryableRegenerateError(error), false);
});

test('unrecognized result codes cannot change the regeneration policy', () => {
  const error = jobFailure({ status: 'error', result: { errorCode: 'untrusted_new_code' } });
  assert.equal(error.code, 'job_failed');
  assert.equal(isNonRetryableRegenerateError(error), false);
});

test('terminal quality failure survives remount and only explicit retry rotates its key', async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  let nextId = 0;
  const session = () => createGenerationRelevantEditsSession({
    storage, clearInitialRequested: () => {}, nextAttemptId: () => ++nextId,
  });
  const first = session();
  first.mark('p');
  const revision = first.readRevision('p');
  const baseline = { ids: ['old'], maxVersion: 1 };
  const failedKey = first.markAttempt('p', revision, baseline);
  assert.equal(first.markTerminalFailure('p', revision), true);

  const remounted = session();
  let generated = 0;
  assert.equal(await runGenerationRelevantEditsRefresh({
    handledRef: { current: false }, readDirtyRevision: () => remounted.readRevision('p'),
    cutsExisted: true, isTerminalFailure: (rev) => remounted.isTerminalFailure('p', rev),
    regenerate: async () => { generated++; return true; },
    clearDirty: (rev) => remounted.clear('p', rev),
  }), false);
  assert.equal(generated, 0);
  assert.equal(remounted.readRevision('p'), revision);
  assert.equal(remounted.markAttempt('p', revision, baseline), failedKey);
  const manualKey = remounted.markAttempt('p', revision, baseline, { manual: true });
  assert.notEqual(manualKey, failedKey);
  assert.equal(remounted.isTerminalFailure('p', revision), false);
});

test('a terminal result for an older edit cannot block a newly changed product', () => {
  const session = createGenerationRelevantEditsSession({ storage: null, clearInitialRequested: () => {} });
  session.mark('p');
  const oldRevision = session.readRevision('p');
  session.markAttempt('p', oldRevision, { ids: [], maxVersion: 0 });
  session.mark('p');
  assert.equal(session.markTerminalFailure('p', oldRevision), false);
  assert.equal(session.isTerminalFailure('p', session.readRevision('p')), false);
});

test('initial generation stays terminal across reload and explicit retry creates one new job', async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  let nextId = 0;
  const sessions = () => createInitialGenerationAttempts({ storage, nextId: () => ++nextId });
  const calls = [];
  const generate = async (pid, options) => {
    calls.push(options.idempotencyKey);
    throw jobFailure({ result: { errorCode: 'mannequin_quality_failed' }, errorMessage: '품질 검수 실패' });
  };
  const first = sessions();
  await assert.rejects(runInitialGenerationAttempt(first, generate, 'p'), { code: 'mannequin_quality_failed' });
  const reloaded = sessions();
  await assert.rejects(runInitialGenerationAttempt(reloaded, generate, 'p'), { code: 'mannequin_quality_failed' });
  assert.equal(calls.length, 1);
  reloaded.retry('p');
  await assert.rejects(runInitialGenerationAttempt(reloaded, generate, 'p'), { code: 'mannequin_quality_failed' });
  assert.equal(calls.length, 2);
  assert.notEqual(calls[0], calls[1]);
});

test('initial request lost before terminal response rejoins the same job after reload', async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  let nextId = 0;
  const sessions = () => createInitialGenerationAttempts({ storage, nextId: () => ++nextId });
  const jobs = new Set();
  let requests = 0;
  const generate = async (pid, { idempotencyKey }) => {
    jobs.add(idempotencyKey);
    if (++requests === 1) throw new Error('network disconnected');
    throw jobFailure({ result: { errorCode: 'mannequin_quality_failed' } });
  };
  await assert.rejects(runInitialGenerationAttempt(sessions(), generate, 'p'));
  const reloaded = sessions();
  reloaded.retry('p'); // 결과 불명은 수동 클릭도 새 유료 작업으로 바꾸지 않는다.
  await assert.rejects(runInitialGenerationAttempt(reloaded, generate, 'p'), { code: 'mannequin_quality_failed' });
  assert.equal(jobs.size, 1);
  await assert.rejects(runInitialGenerationAttempt(sessions(), generate, 'p'), { code: 'mannequin_quality_failed' });
  assert.equal(requests, 2);
});

test('a second tab resumes the job it joined rather than reposting its unbound key', async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  let nextId = 0;
  const sessions = () => createInitialGenerationAttempts({ storage, nextId: () => ++nextId });
  let active = true;
  let posts = 0;
  let newJobs = 0;
  const generate = async (pid, { resumeJobId, onJobStarted }) => {
    let jobId = resumeJobId;
    if (!jobId) {
      posts++;
      jobId = active ? 'other-tab-job' : `new-job-${++newJobs}`;
    }
    onJobStarted?.(jobId);
    if (active) {
      active = false;
      throw new Error('poll disconnected');
    }
    throw jobFailure({ result: { errorCode: 'mannequin_quality_failed' } });
  };
  await assert.rejects(runInitialGenerationAttempt(sessions(), generate, 'p'));
  await assert.rejects(runInitialGenerationAttempt(sessions(), generate, 'p'), { code: 'mannequin_quality_failed' });
  assert.equal(posts, 1);
  assert.equal(newJobs, 0);
});

test('changing initial inputs clears the old attempt without a late failure blocking the new one', async () => {
  let nextId = 0;
  const attempts = createInitialGenerationAttempts({ storage: null, nextId: () => ++nextId });
  let rejectOld;
  const old = runInitialGenerationAttempt(attempts,
    () => new Promise((_, reject) => { rejectOld = reject; }), 'p');
  const oldKey = attempts.begin('p').idempotencyKey;
  attempts.clear('p');
  const newKey = attempts.begin('p').idempotencyKey;
  rejectOld(jobFailure({ result: { errorCode: 'mannequin_quality_failed' } }));
  await assert.rejects(old);
  assert.notEqual(newKey, oldKey);
  assert.equal(attempts.begin('p').idempotencyKey, newKey);
  assert.equal(attempts.begin('p').terminalFailure, undefined);
});

for (const mode of ['cached', 'new', 'resume']) {
  test(`initial HTTP adapter uses the actual job without reposting: ${mode}`, async () => {
    const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
    // 브라우저 전용 import는 제외하고 실제 어댑터 메서드를 가짜 HTTP 경계에 연결한다.
    const method = source.slice(source.indexOf('async generateMannequins'), source.indexOf('async adjustMannequin'));
    const posts = [], polls = [], started = [];
    const adapter = new Function('http', 'pollJob', 'LONG_IMAGE_JOB_TIMEOUT_MS', 'MANNEQUIN_JOB_TIMEOUT_MESSAGE',
      `return ({ ${method} });`)(
      async (url, options) => {
        posts.push(options);
        return mode === 'cached' ? { data: ['existing'], credits: 7 } : { jobId: 'joined-job' };
      },
      async (id) => { polls.push(id); return { data: ['generated'], credits: 5 }; },
      900000, 'wait',
    );
    const result = await adapter.generateMannequins('p', {
      idempotencyKey: 'request-key', resumeJobId: mode === 'resume' ? 'joined-job' : undefined,
      onJobStarted: (id) => started.push(id),
    });
    assert.equal(posts.length, mode === 'resume' ? 0 : 1);
    if (posts.length) assert.equal(posts[0].headers['Idempotency-Key'], 'request-key');
    assert.deepEqual(polls, mode === 'cached' ? [] : ['joined-job']);
    assert.deepEqual(started, mode === 'cached' ? [] : ['joined-job']);
    assert.deepEqual(result.data, mode === 'cached' ? ['existing'] : ['generated']);
  });
}
