import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const mannequinSource = readFileSync(
  new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
  'utf8',
);

// Regression: ISSUE-001 — the edit panel received obsolete prop names, so its
// strength choices never rendered and the paid action stayed disabled.
// Found by /qa on 2026-08-04
// Report: .gstack/qa-reports/qa-report-localhost-2026-08-04.md
test('the mannequin edit panel receives the option, selection, and handlers it renders', () => {
  const panelCall = mannequinSource.match(/<MannequinEditPanel[\s\S]*?\/>/)?.[0] || '';

  assert.match(panelCall, /option=\{aiEditOption\}/);
  assert.match(panelCall, /selectedKind=\{aiEditKind\}/);
  assert.match(panelCall, /selectedStep=\{aiEditStep\}/);
  assert.match(panelCall, /disabled=\{!selected \|\| selectedReviewState\.hardBlocked\}/);
  assert.match(panelCall, /onSelectKind=/);
  assert.match(panelCall, /onSelectStep=/);
});
