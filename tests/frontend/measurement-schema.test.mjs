import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createMeasurementFields,
  MEASUREMENT_LABELS,
  MEASUREMENT_SCHEMA,
  normalizeMeasurementValue,
  sanitizeMeasurementInput,
} from '../../src/lib/measurementSchema.js';

test('measurement fields follow the selected clothing type without carrying values', () => {
  assert.deepEqual(
    createMeasurementFields('bottom').map(({ key }) => key),
    MEASUREMENT_SCHEMA.bottom,
  );
  assert.ok(createMeasurementFields('bottom').every(({ value, unit }) => value === null && unit === 'cm'));
  assert.equal(MEASUREMENT_LABELS.waistWidth, '허리단면');
  assert.equal(MEASUREMENT_LABELS.hipWidth, '힙단면');
});

test('measurement values allow one decimal and clamp pasted input to 0..150', () => {
  assert.equal(sanitizeMeasurementInput('42.'), '42.');
  assert.equal(sanitizeMeasurementInput('42.37'), '42.3');
  assert.equal(normalizeMeasurementValue('42.37'), 42.3);
  assert.equal(normalizeMeasurementValue('e+151.9'), 150);
  assert.equal(normalizeMeasurementValue('-12'), 12);
  assert.equal(normalizeMeasurementValue('abc'), null);
  assert.equal(normalizeMeasurementValue('0.4'), 0.4);
});
