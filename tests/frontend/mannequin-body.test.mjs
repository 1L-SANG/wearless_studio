import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BODY_LEVELS,
  DEFAULT_BODY_LEVEL,
  normalizeMannequinBody,
} from '../../src/lib/mannequinBody.js';

test('levels mirror the server catalog exactly', () => {
  assert.deepEqual(BODY_LEVELS.map((o) => o.value), ['slim', 'regular', 'volume']);
  assert.equal(DEFAULT_BODY_LEVEL, 'regular');
});

test('keeps valid levels for women', () => {
  assert.deepEqual(
    normalizeMannequinBody({ bust: 'volume', hip: 'slim' }, 'women'),
    { bust: 'volume', hip: 'slim' },
  );
});

test('returns null for men — the matrix is women-only', () => {
  assert.equal(normalizeMannequinBody({ bust: 'volume', hip: 'volume' }, 'men'), null);
});

test('falls back to regular for unknown or missing values', () => {
  assert.deepEqual(
    normalizeMannequinBody({ bust: 'huge' }, 'women'),
    { bust: 'regular', hip: 'regular' },
  );
  assert.deepEqual(
    normalizeMannequinBody(null, 'women'),
    { bust: 'regular', hip: 'regular' },
  );
});

// Minor-3 backstop: Chips (src/components/ui.jsx:136) toggles a re-clicked chip's value to
// `null`, and AnalysisForm's setBody guard (`if (!value) return`) is meant to swallow that
// before it ever reaches onChange — so this exact shape should never reach the wire in
// practice. setBody lives inside the AnalysisForm component and isn't importable from a plain
// node:test module (this repo's frontend tests are pure-module tests, no DOM harness), so the
// guard itself is verified by reading the component, not by an automated test here. What *is*
// testable, and documents the normalization backstop for a null axis if it ever did arrive
// (e.g. a legacy/hand-written analysis payload), is normalizeMannequinBody itself:
test('normalizes a null axis to regular (backstop if a null ever reaches storage)', () => {
  assert.deepEqual(
    normalizeMannequinBody({ bust: null, hip: 'volume' }, 'women'),
    { bust: 'regular', hip: 'volume' },
  );
});
