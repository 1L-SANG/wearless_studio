import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);

function extractExportedFunction(name) {
  const marker = `export function ${name}`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} export not found`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') depth -= 1;
    if (depth === 0) {
      return `${source.slice(start, i + 1).replace('export ', '')}; return ${name};`;
    }
  }
  throw new Error(`${name} body was not closed`);
}

const jobErrorFromStatus = new Function(extractExportedFunction('jobErrorFromStatus'))();

test('jobErrorFromStatus preserves message and attaches typed failure contract', () => {
  const error = jobErrorFromStatus({
    status: 'error',
    errorMessage: '마네킹컷 생성에 실패했어요.',
    errorCode: 'hybrid_composite_failed_closed',
    errorDetails: {
      error: 'hybrid_composite_failed_closed',
      failureReason: 'geometry_carrier_mismatch',
      hybridComposite: {
        applied: false,
        needsReview: true,
        failureReason: 'geometry_carrier_mismatch',
      },
    },
  });

  assert.equal(error.message, '마네킹컷 생성에 실패했어요.');
  assert.equal(error.code, 'hybrid_composite_failed_closed');
  assert.equal(error.details.error, 'hybrid_composite_failed_closed');
  assert.equal(
    error.details.hybridComposite.failureReason,
    'geometry_carrier_mismatch',
  );
});

test('jobErrorFromStatus keeps legacy jobs compatible', () => {
  const error = jobErrorFromStatus({ status: 'error' });

  assert.equal(error.message, '작업에 실패했어요.');
  assert.equal('code' in error, false);
  assert.equal('details' in error, false);
});
