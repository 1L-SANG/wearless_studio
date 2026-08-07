import test from 'node:test';
import assert from 'node:assert/strict';

import { WIZARD_STEPS, STEP_INDEX } from '../../src/lib/wizardSteps.js';

test('the wizard walks input, storyboard, mannequin, editor', () => {
  assert.deepEqual(WIZARD_STEPS.map((s) => s.key), ['input', 'storyboard', 'mannequin', 'editor']);
});

test('every route step maps onto its dot', () => {
  assert.equal(STEP_INDEX.input, 0);
  assert.equal(STEP_INDEX.analysis, 0);
  assert.equal(STEP_INDEX.storyboard, 1);
  assert.equal(STEP_INDEX.mannequin, 2);
  assert.equal(STEP_INDEX.generating, 3);
  assert.equal(STEP_INDEX.editor, 3);
});

test('no step index points past the last dot', () => {
  for (const index of Object.values(STEP_INDEX)) {
    assert.ok(index < WIZARD_STEPS.length, `step index ${index} has no dot`);
  }
});
