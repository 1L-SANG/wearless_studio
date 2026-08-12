import test from 'node:test';
import assert from 'node:assert/strict';

import {
  advanceMannequinCompletion,
  createMannequinCompletionState,
} from '../../src/features/mannequin/completionToastCore.js';

const job = (status, overrides = {}) => ({
  projectId: 'project-1',
  status,
  progress: status === 'idle' ? 100 : 48,
  errorMessage: '',
  ...overrides,
});

test('running to successful idle fires exactly once', () => {
  let state = createMannequinCompletionState(job('running'));
  const completed = advanceMannequinCompletion(state, job('idle'), '/create/storyboard');
  assert.equal(completed.completedProjectId, 'project-1');

  state = completed.state;
  const repeated = advanceMannequinCompletion(state, job('idle'), '/create/storyboard');
  assert.equal(repeated.completedProjectId, null);
});

test('an error terminal state does not fire', () => {
  const state = createMannequinCompletionState(job('running'));
  const result = advanceMannequinCompletion(
    state,
    job('error', { progress: 48, errorMessage: '생성 실패' }),
    '/create/storyboard',
  );
  assert.equal(result.completedProjectId, null);
});

test('completion on the mannequin screen is skipped', () => {
  const state = createMannequinCompletionState(job('running'));
  const result = advanceMannequinCompletion(state, job('idle'), '/create/mannequin');
  assert.equal(result.completedProjectId, null);
});

test('a skipped transition is not notified after leaving the mannequin screen', () => {
  let state = createMannequinCompletionState(job('running'));
  const skipped = advanceMannequinCompletion(state, job('idle'), '/create/mannequin');
  state = skipped.state;

  const afterNavigation = advanceMannequinCompletion(state, job('idle'), '/create/storyboard');
  assert.equal(afterNavigation.completedProjectId, null);
});
