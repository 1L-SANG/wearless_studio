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
