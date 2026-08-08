import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createMannequinGenerationRunner } from '../../src/features/mannequin/generationRunnerCore.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('pollJob terminates a cancelled server job with the job_cancelled code', () => {
  const source = read('../../src/lib/api/httpAdapter.js');
  const poll = source.slice(
    source.indexOf('async function pollJob'),
    source.indexOf('// 사진 1장 업로드'),
  );
  const cancelled = poll.indexOf("if (job.status === 'cancelled')");
  const progress = poll.indexOf("if (typeof job.progress === 'number'");

  assert.ok(cancelled > 0 && cancelled < progress, 'cancelled must terminate before another progress tick');
  assert.match(poll, /error\.code = 'job_cancelled';\s*\n\s*throw error;/);
});

test('the http adapter posts mannequin cancellation to the project-scoped route', () => {
  const source = read('../../src/lib/api/httpAdapter.js');
  const method = source.slice(
    source.indexOf('async cancelMannequinGeneration'),
    source.indexOf('// @deprecated', source.indexOf('async cancelMannequinGeneration')),
  );

  assert.match(method, /`\/v1\/projects\/\$\{projectId\}\/mannequins:cancel`/);
  assert.match(method, /method: 'POST'/);
});

test('job_cancelled quietly resets the runner without an error ribbon or completion badge', async () => {
  const original = new Error('cancelled by owner');
  original.code = 'job_cancelled';
  const jobs = [];
  const runner = createMannequinGenerationRunner({
    generate: async (_projectId, { onJobStarted }) => {
      onJobStarted('job-1');
      throw original;
    },
    readProgress: () => 61,
    onJobChange: (projectId, patch) => jobs.push({ sinkProjectId: projectId, ...patch }),
  });

  await assert.rejects(runner.request('p1'), (error) => error === original);
  assert.deepEqual(jobs, [
    { sinkProjectId: 'p1', status: 'running', progress: 61, errorMessage: '' },
    {
      sinkProjectId: 'p1', projectId: null, status: 'idle', progress: 0, errorMessage: '',
    },
  ]);
  assert.equal(jobs.some((job) => job.status === 'error' || job.progress === 100), false);
});

test('acknowledged cancellation detaches the old poller so a new payload starts immediately', async () => {
  const requests = [];
  const jobs = [];
  const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
  const first = deferred();
  const second = deferred();
  const runner = createMannequinGenerationRunner({
    generate: async (_projectId, { onJobStarted }) => {
      const request = requests.length === 0 ? first : second;
      requests.push(request);
      onJobStarted(`job-${requests.length}`);
      return request.promise;
    },
    readProgress: () => 0,
    onJobChange: (projectId, patch) => jobs.push({ sinkProjectId: projectId, ...patch }),
  });

  const oldRequest = runner.request('p1');
  assert.equal(runner.acknowledgeCancellation('p1'), true);
  const newRequest = runner.request('p1');
  assert.equal(requests.length, 2, 'the new payload must not join the cancelled poller');

  const cancelled = new Error('cancelled');
  cancelled.code = 'job_cancelled';
  first.reject(cancelled);
  await assert.rejects(oldRequest, (error) => error === cancelled);
  assert.equal(jobs.at(-1).status, 'running', 'the stale poller must not reset the new run');

  second.resolve({ data: { cuts: [] }, credits: 6 });
  await newRequest;
  assert.equal(jobs.at(-1).status, 'idle');
  assert.equal(jobs.at(-1).progress, 100);
});

test('the running-work warning uses the owner wording and confirms cancel → credits → patch', () => {
  const source = read('../../src/features/product-input/ProductInput.jsx');
  const exactWarning = '지금 만들던 마네킹컷 생성이 취소돼요. 취소된 생성의 크레딧(2)도 차감되고, 새로 만들 때 2크레딧이 더 들어요.';
  assert.ok(source.includes(`<p>${exactWarning}</p>`));

  const handler = source.slice(
    source.indexOf('const confirmRunningRelevantPatch = async () =>'),
    source.indexOf('useEffect(() => {', source.indexOf('const confirmRunningRelevantPatch = async () =>')),
  );
  const cancel = handler.indexOf('await api.cancelMannequinGeneration(analysisProjectId)');
  const sync = handler.indexOf('useAppStore.getState().syncCredits(credits)');
  const detach = handler.indexOf('acknowledgeMannequinGenerationCancellation(analysisProjectId)');
  const apply = handler.indexOf('applyAnalysisPatch(patch)');
  const failure = handler.indexOf('} catch (error) {');

  assert.ok(cancel > 0 && cancel < sync && sync < detach && detach < apply && apply < failure);
  assert.match(handler, /if \(cancellingRelevantPatchRef\.current \|\| !pendingRelevantPatch\) return;/);
  assert.match(handler, /catch \(error\)[\s\S]*?toast\.push\(/);
  assert.equal(handler.match(/applyAnalysisPatch\(patch\)/g)?.length, 1);
});

test('the completed-cuts warning and immediate apply branch stay unchanged', () => {
  const source = read('../../src/features/product-input/ProductInput.jsx');
  assert.match(source, /<h3>바꾸면 마네킹 컷을 다시 만들어야 해요<\/h3>/);
  assert.match(
    source,
    /<p>마네킹 컷이 다시 만들어져요 · \{CREDIT_COSTS\.mannequinGenerate\} 크레딧\. 콘티에서 고른 촬영 세트도 다시 골라야 해요\.<\/p>/,
  );
  assert.match(
    source,
    /const patch = pendingRelevantPatch;\s*\n\s*setPendingRelevantPatch\(null\);\s*\n\s*applyAnalysisPatch\(patch\);/,
  );
});

test('mock cancellation settles once and rejects the active generation as job_cancelled', () => {
  const source = read('../../src/mock/api.js');
  const cancel = source.slice(
    source.indexOf('async cancelMannequinGeneration'),
    source.indexOf('async regenerateMannequin', source.indexOf('async cancelMannequinGeneration')),
  );

  assert.match(source, /error\.code = 'job_cancelled'/);
  assert.match(source, /if \(!job\.creditsSettled\)/);
  assert.match(cancel, /if \(!job \|\| job\.cancelled\)[\s\S]*?cancelled: false/);
  assert.match(cancel, /settleMockMannequinCharge\(job\)/);
  assert.match(cancel, /job\.cancel\?\.\(\)/);
  assert.match(cancel, /cancelled: true, credits/);
});
