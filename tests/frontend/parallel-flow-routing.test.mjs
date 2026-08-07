import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const mannequinSource = read('../../src/features/mannequin/Mannequin.jsx');
const productInputSource = read('../../src/features/product-input/ProductInput.jsx');

test('the regeneration signal travels in the store, not in router state', () => {
  // 입력 → 콘티 → 마네킹 사이에 화면이 하나 끼면 route state 는 증발한다.
  assert.doesNotMatch(productInputSource, /refreshForEdits/);
  assert.doesNotMatch(mannequinSource, /location\.state\?\.refreshForEdits/);
  assert.match(mannequinSource, /generationRelevantEditsDirty/);
  assert.match(mannequinSource, /clearGenerationRelevantEdits\(\)/);
});
