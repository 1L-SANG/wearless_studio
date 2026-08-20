import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const editorSource = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);

test('FaceMarket receipt labels the result as an on-chain record, not completed settlement', () => {
  assert.match(editorSource, /fm-receipt-badge[^>]*>.*온체인 기록 완료<\/span>/);
  assert.doesNotMatch(editorSource, /fm-receipt-badge[^>]*>.*정산 완료<\/span>/);
});
