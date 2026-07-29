import assert from 'node:assert/strict';
import test from 'node:test';

import { clampEditorNumber, resolveEditorNumberDraft } from '../../src/features/editor/editorInputs.js';

test('editor number fields accept values above 400 when no maximum is configured', () => {
  assert.equal(clampEditorNumber(401, 0), 401);
  assert.equal(clampEditorNumber(10_000, 0), 10_000);
  assert.equal(clampEditorNumber(-1, 0), 0);
});

test('editor number fields still honor an explicitly configured maximum', () => {
  assert.equal(clampEditorNumber(401, 0, 400), 400);
});

test('editor number fields keep an empty draft so the existing value can be replaced', () => {
  assert.deepEqual(resolveEditorNumberDraft('', 0), { draft: '', value: null });
  assert.deepEqual(resolveEditorNumberDraft('25', 0), { draft: '25', value: 25 });
});
