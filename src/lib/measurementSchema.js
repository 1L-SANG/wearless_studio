import measurementLabels from '../../server/app/data/measurement_labels.json' with { type: 'json' };

export const MEASUREMENT_SCHEMA = Object.freeze({
  top: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'],
  bottom: ['totalLength', 'waistWidth', 'hipWidth', 'rise', 'thighWidth', 'hemWidth'],
  outer: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'],
  dress: ['totalLength', 'shoulderWidth', 'chestWidth', 'waistWidth', 'hipWidth', 'armhole', 'sleeveLength', 'hemWidth'],
});

export const MEASUREMENT_LABELS = Object.freeze(measurementLabels);

export function measurementKeysFor(clothingType) {
  return MEASUREMENT_SCHEMA[clothingType] || MEASUREMENT_SCHEMA.top;
}

export function createMeasurementFields(clothingType, values = {}) {
  return measurementKeysFor(clothingType).map((key) => ({
    key,
    value: values[key] ?? null,
    unit: 'cm',
  }));
}

export function sanitizeMeasurementInput(rawValue) {
  const digits = String(rawValue ?? '').replace(/[^\d.]/g, '');
  const [whole = '', ...fractions] = digits.split('.');
  const normalized = `${whole}${digits.includes('.') ? `.${fractions.join('').slice(0, 1)}` : ''}`;
  if (!normalized || normalized === '.') return '';
  const value = Number(normalized);
  if (Number.isFinite(value) && value > 150) return '150';
  return normalized;
}

export function normalizeMeasurementValue(rawValue) {
  const normalized = sanitizeMeasurementInput(rawValue);
  if (!normalized) return null;
  const value = Number(normalized);
  if (!Number.isFinite(value)) return null;
  return Math.min(150, Math.max(0, Math.round(value * 10) / 10));
}
