import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { createDraftSyncSingleFlight } from '../../src/lib/draftSyncSingleFlight.js';

test('draft sync shares one in-flight create and reuses its project after caller timeout', async () => {
  let creates = 0;
  let finish;
  const coordinator = createDraftSyncSingleFlight(async (_draft, { projectId }) => {
    if (!projectId) creates += 1;
    await new Promise((resolve) => { finish = resolve; });
    return { projectId: projectId || 'project-1' };
  });

  const first = coordinator.sync({ product: { name: 'knit' } });
  await Promise.resolve();
  const retry = coordinator.sync({ product: { name: 'knit' } });
  assert.equal(first, retry);
  assert.equal(creates, 1);

  finish();
  assert.deepEqual(await first, { projectId: 'project-1' });
  assert.deepEqual(await coordinator.sync({ product: { name: 'knit' } }), { projectId: 'project-1' });
  assert.equal(creates, 1);
});

test('a draft edited after timeout is saved again into the completed project', async () => {
  const seen = [];
  const coordinator = createDraftSyncSingleFlight(async (draft, { projectId }) => {
    seen.push({ name: draft.product.name, projectId });
    return { projectId: projectId || 'project-1' };
  });

  await coordinator.sync({
    updatedAt: '2026-08-11T07:00:00.000Z',
    product: { name: 'before-timeout' },
  });
  await coordinator.sync({
    updatedAt: '2026-08-11T07:01:00.000Z',
    product: { name: 'edited-after-timeout' },
  });

  assert.deepEqual(seen, [
    { name: 'before-timeout', projectId: undefined },
    { name: 'edited-after-timeout', projectId: 'project-1' },
  ]);
});

test('a newer draft waits for an older in-flight save and then persists', async () => {
  const seen = [];
  let finishFirst;
  const coordinator = createDraftSyncSingleFlight(async (draft, { projectId }) => {
    seen.push({ name: draft.product.name, projectId });
    if (draft.product.name === 'slow-old-draft') {
      await new Promise((resolve) => { finishFirst = resolve; });
    }
    return { projectId: projectId || 'project-1' };
  });

  const first = coordinator.sync({
    updatedAt: '2026-08-11T07:00:00.000Z',
    product: { name: 'slow-old-draft' },
  });
  await Promise.resolve();
  const latest = coordinator.sync({
    updatedAt: '2026-08-11T07:01:00.000Z',
    product: { name: 'latest-draft' },
  });

  finishFirst();
  await Promise.all([first, latest]);
  assert.deepEqual(seen, [
    { name: 'slow-old-draft', projectId: undefined },
    { name: 'latest-draft', projectId: 'project-1' },
  ]);
});

test('draft sync retry preserves a project id created before a partial failure', async () => {
  const seenProjectIds = [];
  let attempt = 0;
  const coordinator = createDraftSyncSingleFlight(async (_draft, { projectId }) => {
    attempt += 1;
    seenProjectIds.push(projectId);
    if (attempt === 1) {
      const error = new Error('upload failed');
      error.projectId = 'project-existing';
      throw error;
    }
    return { projectId };
  });

  await assert.rejects(coordinator.sync({}), /upload failed/);
  assert.deepEqual(await coordinator.sync({}), { projectId: 'project-existing' });
  assert.deepEqual(seenProjectIds, [undefined, 'project-existing']);
});

test('post-promotion cleanup failure reruns the latest draft on the same project', async () => {
  const seen = [];
  const coordinator = createDraftSyncSingleFlight(async (draft, { projectId }) => {
    seen.push({ name: draft.product.name, projectId });
    return { projectId: projectId || 'project-1' };
  });

  assert.deepEqual(
    await coordinator.sync({ product: { name: 'before-delete-failure' } }),
    { projectId: 'project-1' },
  );
  assert.equal(coordinator.retryFrom('project-1'), true);
  assert.deepEqual(
    await coordinator.sync({ product: { name: 'edited-before-retry' } }),
    { projectId: 'project-1' },
  );
  assert.deepEqual(seen, [
    { name: 'before-delete-failure', projectId: undefined },
    { name: 'edited-before-retry', projectId: 'project-1' },
  ]);
});

test('F6 같은 revision 합류자도 project ready와 사진 진행률 콜백을 함께 받는다', async () => {
  let runningOptions;
  let finish;
  const coordinator = createDraftSyncSingleFlight(async (_draft, options) => {
    runningOptions = options;
    await new Promise((resolve) => { finish = resolve; });
    return { projectId: 'project-f6' };
  });
  const draft = { updatedAt: 'revision-f6', product: { name: 'knit' } };
  const firstReady = [];
  const joinedReady = [];
  const joinedProgress = [];
  const first = coordinator.sync(draft, { onProjectReady: (id) => firstReady.push(id) });
  await Promise.resolve();
  const joined = coordinator.sync(draft, {
    onProjectReady: (id) => joinedReady.push(id),
    onPhotoProgress: (progress) => joinedProgress.push(progress),
  });

  runningOptions.onProjectReady('project-f6');
  runningOptions.onPhotoProgress({ done: 1, total: 2 });
  finish();
  await Promise.all([first, joined]);

  assert.equal(joined, first);
  assert.deepEqual(firstReady, ['project-f6']);
  assert.deepEqual(joinedReady, ['project-f6']);
  assert.deepEqual(joinedProgress, [{ done: 1, total: 2 }]);
});

test('새 제작을 시작하면 다음 승격이 이전 프로젝트를 물려받지 않는다', async () => {
  const seen = [];
  const coordinator = createDraftSyncSingleFlight(async (draft, { projectId }) => {
    seen.push({ name: draft.product.name, projectId });
    return { projectId: projectId || `project-${seen.length}` };
  });

  await coordinator.sync({ updatedAt: 'A', product: { name: '상품A' } });
  coordinator.forgetProject();               // '새 만들기' = 이 플로우와 신원을 끊는다
  await coordinator.sync({ updatedAt: 'B', product: { name: '상품B' } });

  assert.deepEqual(seen, [
    { name: '상품A', projectId: undefined },
    { name: '상품B', projectId: undefined },   // 상품B 는 자기 프로젝트를 새로 만든다
  ]);
});

test('업로드가 도는 중에 새 제작을 시작해도 다음 승격이 이전 프로젝트에 덮어쓰지 않는다', async () => {
  const seen = [];
  let finishFirst;
  const coordinator = createDraftSyncSingleFlight(async (draft, { projectId, onProjectReady }) => {
    seen.push({ name: draft.product.name, projectId });
    const id = projectId || `project-${seen.length}`;
    onProjectReady?.(id);
    if (seen.length === 1) await new Promise((resolve) => { finishFirst = resolve; });
    return { projectId: id };
  });

  const readyA = [];
  const first = coordinator.sync(
    { updatedAt: 'A', product: { name: '상품A' } },
    { onProjectReady: (id) => readyA.push(id) },
  );
  await Promise.resolve();

  coordinator.forgetProject();               // 업로드 도는 중 '새 만들기'
  const second = coordinator.sync({ updatedAt: 'B', product: { name: '상품B' } });

  // A의 업로드 promise는 살아 있지만, 새 흐름 B는 그 완료를 기다리지 않고 바로 시작한다.
  await Promise.resolve();
  assert.deepEqual(seen, [
    { name: '상품A', projectId: undefined },
    { name: '상품B', projectId: undefined },
  ]);
  await second;

  finishFirst();
  await first;

  // A가 늦게 끝나도 B의 완료 캐시는 그대로다. 같은 B revision 재호출은 다시 실행하지 않는다.
  await coordinator.sync({ updatedAt: 'B', product: { name: '상품B' } });

  assert.deepEqual(readyA, ['project-1']);   // A 를 기다리던 화면은 그대로 A 를 본다
  assert.deepEqual(seen, [
    { name: '상품A', projectId: undefined },
    { name: '상품B', projectId: undefined },   // B 가 A 를 덮어쓰지 않는다
  ]);
});

test("'새 제작'은 도는 승격을 끊지 않으면서 신원만 끊는다 — draftSync 배선", async () => {
  const src = await readFile(new URL('../../src/store/useAppStore.js', import.meta.url), 'utf8');
  // beginProject 는 localStorage 세션(clearDraftPromotionSession)만 지우고 메모리 안의
  // single-flight project 기억은 남겨, 다음 상품이 앞 프로젝트에 덮어쓰던 사고가 났다.
  const begin = src.slice(src.indexOf('async beginProject()'), src.indexOf('async ensureProject()'));
  assert.match(begin, /forgetDraftSyncProject\(\)/);
  assert.match(src, /import[\s\S]*forgetDraftSyncProject[\s\S]*from '@\/lib\/draftSync\.js'/);
});
