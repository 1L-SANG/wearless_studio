import test from 'node:test';
import assert from 'node:assert/strict';

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
