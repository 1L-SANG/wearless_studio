import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const start = source.indexOf('  const returnFromSetPicker =');
const handler = source.slice(start, source.indexOf('  const selectCard =', start));
function returnToCut(target, busy = false) {
  const focused = [];
  const fallback = { focus: () => focused.push('fallback') };
  const action = new Function('atomicSavingRef', 'selectedId', 'closeInspector', 'setSetPicker', 'setSetPickerError', 'requestAnimationFrame', 'setPickerReturnFocus', 'document', `${handler}; return returnFromSetPicker;`)(
    { current: busy }, 'cut', () => {}, () => {}, () => {}, callback => callback(),
    { current: { ...target, focus: () => focused.push('original') } }, { querySelector: () => fallback },
  );
  action();
  return focused;
}
test('returning from set settings skips a trigger hidden by a collapsed section', () => {
  assert.deepEqual(returnToCut({ isConnected: true, getClientRects: () => [] }), ['fallback']);
});
test('returning from set settings keeps a visible trigger and skips a removed one', () => {
  assert.deepEqual(returnToCut({ isConnected: true, getClientRects: () => [{}] }), ['original']);
  assert.deepEqual(returnToCut({ isConnected: false, getClientRects: () => [] }), ['fallback']);
});
test('returning is blocked while an atomic save is pending', () => {
  assert.deepEqual(returnToCut({ isConnected: true, getClientRects: () => [{}] }, true), []);
});
