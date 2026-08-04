import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  MANNEQUIN_EDIT_OPTIONS,
  buildMannequinEditRequest,
  mannequinEditFailureMessage,
  runMannequinEdit,
} from '../../src/features/mannequin/mannequinEdit.js';

const httpAdapterSource = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);
const mannequinSource = readFileSync(
  new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
  'utf8',
);
const mockApiSource = readFileSync(
  new URL('../../src/mock/api.js', import.meta.url),
  'utf8',
);

test('seller garment-length choice becomes one bounded baseline edit request', () => {
  assert.deepEqual(buildMannequinEditRequest('garment_length', -2), {
    editType: 'GARMENT_LENGTH_ONLY',
    adjustments: { garmentLengthStep: -2 },
  });
});

test('HTTP mode exposes baseline approval and polls a limited edit into the full cut history', () => {
  assert.match(httpAdapterSource, /async approveMannequin\(projectId, cutId\)/);
  assert.match(httpAdapterSource, /mannequins:approve[^]*body: \{ cutId \}/);
  assert.match(httpAdapterSource, /async editMannequin\([^]*projectId[^]*editType[^]*adjustments[^]*baselineId[^]*onProgress/);
  assert.match(httpAdapterSource, /mannequins:edit[^]*idempotencyKey/);
  assert.match(httpAdapterSource, /body: \{ editType, adjustments, baselineId \}/);
  assert.match(httpAdapterSource, /pollJob\(session\.jobId/);
  assert.match(httpAdapterSource, /const cuts = await http\(`\/v1\/projects\/\$\{projectId\}\/mannequins`\)/);
});

test('AI adjustment explicitly approves the selected cut before editing that baseline', async () => {
  const calls = [];
  const boundary = {
    async approveMannequin(projectId, cutId) {
      calls.push(['approve', projectId, cutId]);
      return { id: 'baseline-1', cutId };
    },
    async editMannequin(projectId, request) {
      calls.push(['edit', projectId, request]);
      return { data: { cuts: [{ id: 'cut-v2', version: 2 }] }, credits: 11 };
    },
  };

  const result = await runMannequinEdit({
    api: boundary,
    projectId: 'project-1',
    cutId: 'cut-v1',
    kind: 'garment_length',
    step: 1,
    idempotencyKey: 'request-1',
  });

  assert.deepEqual(result.data.cuts, [{ id: 'cut-v2', version: 2 }]);
  assert.deepEqual(calls, [
    ['approve', 'project-1', 'cut-v1'],
    ['edit', 'project-1', {
      editType: 'GARMENT_LENGTH_ONLY',
      adjustments: { garmentLengthStep: 1 },
      baselineId: 'baseline-1',
      idempotencyKey: 'request-1',
      onProgress: undefined,
    }],
  ]);
});

test('the UI exposes every limited edit the server can safely execute', () => {
  assert.deepEqual(MANNEQUIN_EDIT_OPTIONS.map((option) => option.id), [
    'garment_length',
    'sleeve_length',
    'body_width',
    'shoulder_width',
    'tuck_state',
    'mannequin_volume',
  ]);
});

test('mock mode implements the same approve/edit baseline contract for local QA', () => {
  assert.match(mockApiSource, /let mannequinBaseline = null/);
  assert.match(mockApiSource, /async approveMannequin\(_projectId, cutId\)/);
  assert.match(mockApiSource, /async editMannequin\([^]*baselineId[^]*no_approved_baseline[^]*baseline_changed/);
  assert.match(mockApiSource, /mannequinEditReplay/);
});

test('mannequin screen has a separate AI partial-edit panel wired to the limited edit API', () => {
  assert.match(mannequinSource, /MannequinEditPanel/);
  assert.match(mannequinSource, /현재 사진을 AI로 부분 수정/);
  assert.match(mannequinSource, /MANNEQUIN_EDIT_OPTIONS/);
  assert.match(mannequinSource, /runMannequinEdit\(\{/);
  assert.match(mannequinSource, /newIdempotencyKey\(\)/);
  assert.match(mannequinSource, /mannequinEditFailureMessage/);
  assert.match(mannequinSource, /api\.regenerateMannequin/);
  assert.match(mannequinSource, /selectedReviewState\.hardBlocked/);
});

test('limited edit failures are translated to seller-actionable messages', () => {
  assert.equal(
    mannequinEditFailureMessage({ code: 'no_approved_baseline' }),
    '먼저 현재 마네킹컷을 승인한 뒤 다시 시도해 주세요.',
  );
  assert.equal(
    mannequinEditFailureMessage({ response: { detail: { code: 'baseline_changed' } } }),
    '승인 기준이 바뀌었어요. 현재 컷을 다시 선택한 뒤 부분 수정을 실행해 주세요.',
  );
  assert.equal(
    mannequinEditFailureMessage({ code: 'edit_not_enabled' }),
    'AI 부분 수정 기능이 아직 켜져 있지 않아요.',
  );
});
