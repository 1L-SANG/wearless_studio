import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');

test('a rejected photo edit returns the current document without normalizing it into a fake history entry', () => {
  const begin = source.indexOf('  const setBlocks = useCallback(');
  const end = source.indexOf('  const [wardrobe,', begin);
  let document = [{ id: 'a', elements: [] }], normalized = 0;
  const original = document, latestBlocks = { current: original };
  const scope = { useCallback: callback => callback, setBlocksState: callback => { document = callback(document); }, latestBlocks,
    normalizeEditorSelectionGroups: value => value, upgradeLegacyKiwiTemplateBlocks: value => value,
    expandBlockHeights: value => { normalized++; return value.map(b => ({ ...b })); }, uid: () => 'unused' };
  const update = new Function(...Object.keys(scope), `${source.slice(begin, end)}; return setBlocks;`)(...Object.values(scope));
  update(current => current);
  assert.equal(document, original);
  assert.equal(latestBlocks.current, original);
  assert.equal(normalized, 0);
});

test('Undo during a crop commits its history boundary before undoing, preserving the earlier edit and crop redo', () => {
  const beforeResize = [{ id: 'a', elements: [{ id: 'p', w: 100 }] }];
  const beforeCrop = [{ id: 'a', elements: [{ id: 'p', w: 200 }] }];
  const cropped = [{ id: 'a', elements: [{ id: 'p', w: 180, crop: { ox: 20, oy: 0, iw: 200, ih: 300 } }] }];
  let current = beforeCrop, pending = null;
  const croppingRef = { current: { elId: 'p' } }, prevBlocks = { current }, hist = { current: { past: [beforeResize], future: [] } };
  // The renderer's deferred effects are an external boundary here: only a flush
  // publishes the crop. Execute the real Undo handler against that boundary.
  const scope = { croppingRef, hist, prevBlocks, fromHistory: { current: false },
    kb: { current: { cropCommit() { pending = cropped; croppingRef.current = null; } } },
    flushSync(callback) { callback(); if (pending) { hist.current.past.push(prevBlocks.current); current = prevBlocks.current = pending; pending = null; } },
    clearSel() {}, setBlocks(next) { current = next; }, toast: { push() {} } };
  const begin = source.indexOf('  const undo ='), end = source.indexOf('  const redo =', begin);
  const undo = new Function(...Object.keys(scope), `${source.slice(begin, end)}; return undo;`)(...Object.values(scope));
  undo();
  assert.equal(current, beforeCrop);
  assert.equal(hist.current.future[0], cropped);
  assert.equal(hist.current.past[0], beforeResize);
});

test('blurring an unchanged photo size field does not append a duplicate undo step', () => {
  let current = [{ id: 'a', elements: [{ id: 'p', type: 'image', w: 900, h: 573 }] }];
  const original = current;
  const begin = source.indexOf('  const patchEl ='), end = source.indexOf('  const patchBubbleAppearance =', begin);
  const patch = new Function('selEl', 'setBlocks', `${source.slice(begin,end)}; return patchEl;`)('p', update => { current = update(current); });
  patch({ w: 900, h: 573 });
  assert.equal(current, original);
  patch({ w: 880 });
  assert.equal(current[0].elements[0].w, 880);
});
