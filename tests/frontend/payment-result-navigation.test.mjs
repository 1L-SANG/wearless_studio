import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(
  new URL('../../src/features/payments/PaymentResult.jsx', import.meta.url),
  'utf8',
);

test('payment approval does not navigate after leaving the success route', () => {
  assert.match(source, /mounted\.current = false/);
  assert.match(
    source,
    /if \(!mounted\.current \|\| currentPath\.current !== '\/payments\/success'\) return;[^]*navigate\(resume\.path/,
  );
});
