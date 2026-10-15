import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isEditorDeleteKey, isPhotoSlotElement, removeSelectedElements, removeSelectedBlock } from '../../src/features/editor/editorSelection.js';
import { applySlotFillToInfo, presetTypeOf } from '../../src/features/editor/presets/infoPresets.js';
import { beginEditorCrop, ratioEditorCrop, dragEditorCrop } from '../../src/features/editor/editorCrop.js';
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');

function harness(frameSlot) {
  const photo = { id: 'photo', type: 'image', src: '/photo.jpg', x: 0, y: 0, w: 400, h: 600, frameSlot };
  let current = [{ id: 'source', kind: 'styling', h: 800, elements: [photo] }];
  const original = current;
  const croppingRef = { current: ratioEditorCrop(beginEditorCrop('source', photo), 1) };
  const setCropping = update => { croppingRef.current = typeof update === 'function' ? update(croppingRef.current) : update; };
  const cancelBegin = editor.indexOf('  const cancelCrop =');
  const cancelEnd = editor.indexOf('  const resetCrop =', cancelBegin);
  const cancel = new Function('setCropping', `${editor.slice(cancelBegin, cancelEnd)}; return cancelCrop;`)(setCropping);
  const listeners = new Map(), document = { body: { style: { userSelect: 'auto' } } };
  const window = { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) };
  const latestBlocks = { current }, prevBlocks = { current }, hist = { current: { past: [], future: [] } }, fromHistory = { current: false };
  const setBlocks = update => {
    const next = typeof update === 'function' ? update(current) : update;
    // React publishes history separately; the keyboard actions themselves stay real.
    if (!fromHistory.current) { hist.current.past.push(current); hist.current.future = []; }
    fromHistory.current = false;
    current = latestBlocks.current = prevBlocks.current = next;
  };
  const kb = { current: { croppingOn: true, cropCancel: cancel, cropCommit() { throw new Error('Delete must not commit an unconfirmed crop'); } } };
  const scope = { selEls: ['photo'], selBlock: 'source', blockFocused: true, isEditorDeleteKey, isPhotoSlotElement,
    latestBlocks, blocks: current, genActiveRef: { current: false }, removeSelectedElements, removeSelectedBlock,
    presetTypeOf, applySlotFillToInfo, setBlocks, setSelEl() {}, setSelEls() {}, setSelBlock() {}, setBlockFocused() {},
    toast: { push() {} }, kb, croppingRef, setCropping, cancelCrop: cancel };
  const begin = editor.indexOf('    const h =', editor.indexOf('// Delete/Backspace removes'));
  const end = editor.indexOf("    window.addEventListener('keydown', h)", begin);
  const keyboard = new Function(...Object.keys(scope), `${editor.slice(begin, end)}; return h;`)(...Object.values(scope));
  const dragBegin = editor.indexOf('  const cropDrag ='), dragEnd = editor.indexOf('  const downloadPhoto =', dragBegin);
  const drag = new Function('croppingRef', 'setCropping', 'dragEditorCrop', 'scale', 'document', 'window',
    `${editor.slice(dragBegin, dragEnd)}; return cropDrag;`)(croppingRef, setCropping, dragEditorCrop, 1, document, window);
  const undoBegin = editor.indexOf('  const undo ='), redoBegin = editor.indexOf('  const redo =', undoBegin), saveBegin = editor.indexOf('  const save =', redoBegin);
  const historyScope = { croppingRef, kb, flushSync: fn => fn(), hist, prevBlocks, fromHistory, clearSel() {}, setBlocks, toast: { push() {} } };
  const actions = new Function(...Object.keys(historyScope), `${editor.slice(undoBegin, saveBegin)}; return {undo,redo};`)(...Object.values(historyScope));
  return { original, current: () => current, crop: () => croppingRef.current, document, actions,
    key: (key, tagName = 'DIV') => keyboard({ key, code: key, target: { tagName }, preventDefault() {} }),
    startDrag: () => drag({ button: 0, clientX: 0, clientY: 0, preventDefault() {}, stopPropagation() {} }, 'frame', 'e'),
    move: () => listeners.get('pointermove')?.({ clientX: 10, clientY: 0 }),
    release: () => listeners.get('pointerup')?.(),
  };
}

for (const frameSlot of [false, true]) for (const key of ['Delete', 'Backspace']) {
  test(`${key} cancels the crop and keeps ${frameSlot ? 'slot clearing' : 'loose photo deletion'} undoable`, () => {
    const h = harness(frameSlot);
    h.key(key);
    assert.equal(h.crop(), null);
    const elements = h.current()[0].elements;
    if (frameSlot) {
      assert.equal(elements[0].src, null);
      assert.equal(elements[0].h, 600, 'an unconfirmed square crop must not resize the cleared slot');
    } else assert.equal(elements.length, 0);
    h.actions.undo();
    assert.deepEqual(h.current(), h.original);
    h.actions.redo();
    assert.equal(h.current()[0].elements[0]?.src ?? null, null);
    assert.equal(h.crop(), null);
  });
}
test('Delete typed inside a field does not close the crop or delete its photo', () => {
  const h = harness(true);
  h.key('Delete', 'INPUT');
  assert.notEqual(h.crop(), null);
  assert.equal(h.current(), h.original);
});
test('a pointer move after crop deletion cannot bring the cancelled crop back', () => {
  const h = harness(true);
  h.startDrag();
  h.key('Delete');
  h.move();
  h.release();
  assert.equal(h.crop(), null);
  assert.equal(h.current()[0].elements[0].src, null);
  assert.equal(h.document.body.style.userSelect, 'auto');
});
