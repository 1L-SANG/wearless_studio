import test from 'node:test';
import assert from 'node:assert/strict';

import { generationWorkWarningKind } from '../../src/lib/generationWorkWarning.js';

test('no cuts and no running job for this project is not warned', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: false, jobStatus: 'idle', jobProjectId: null, projectId: 'p1',
  }), 'none');
});

test('existing cuts use the already-made wording regardless of job status', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: true, jobStatus: 'idle', jobProjectId: null, projectId: 'p1',
  }), 'cuts');
});

test('a job running for this project uses the in-flight wording when no cuts exist yet', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: false, jobStatus: 'running', jobProjectId: 'p1', projectId: 'p1',
  }), 'running');
});

test('a job running for a different project is not warned', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: false, jobStatus: 'running', jobProjectId: 'p2', projectId: 'p1',
  }), 'none');
});

test('an idle/error job for this project with no cuts is not warned', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: false, jobStatus: 'error', jobProjectId: 'p1', projectId: 'p1',
  }), 'none');
});

test('cuts existing wins over a simultaneously running job (regeneration replacing existing cuts)', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: true, jobStatus: 'running', jobProjectId: 'p1', projectId: 'p1',
  }), 'cuts');
});

test('a missing current project id never matches a running job', () => {
  assert.equal(generationWorkWarningKind({
    cutsExist: false, jobStatus: 'running', jobProjectId: null, projectId: null,
  }), 'none');
});
