import test from 'node:test';
import assert from 'node:assert/strict';

import { axesFor, normalizeAnalysisFit } from '../../src/lib/fitAxes.js';
import { registeredFitExampleKeys } from '../../src/lib/fitExampleImages.js';

test('women top fit uses the same four-value vocabulary as men', () => {
  const values = (gender) => axesFor('top', gender).fit.map(({ value }) => value);
  assert.deepEqual(values('women'), ['slim', 'regular', 'semi_over', 'over']);
  assert.deepEqual(values('men'), ['slim', 'regular', 'semi_over', 'over']);
  assert.equal(registeredFitExampleKeys().includes('top-women-fit-tight'), false);
});

test('legacy tight analysis values load as slim without mutating stored data', () => {
  const legacy = {
    fit: 'tight',
    fitProfile: { category: 'top', gender: 'women', axes: { fit: 'tight', length: 'basic' } },
  };
  const normalized = normalizeAnalysisFit(legacy);
  assert.equal(normalized.fit, 'slim');
  assert.deepEqual(normalized.fitProfile.axes, { fit: 'slim', length: 'basic' });
  assert.equal(legacy.fitProfile.axes.fit, 'tight');
});

test('current analysis values keep their object identity', () => {
  const current = { fit: 'regular', fitProfile: null };
  assert.equal(normalizeAnalysisFit(current), current);
});
