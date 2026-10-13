import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { clampDragDelta, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
import { snapEditorDragDelta, snapEditorSpacing } from '../../src/features/editor/editorSnap.js';

// Execute the production pointer handler. The DOM/event loop is the external
// boundary; snapping and the handler's decisions stay real, without a browser.
function dragHarness() {
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const begin = source.indexOf('  const startPointerSelectionDrag =');
  const end = source.indexOf('  const startElementDrag =', begin);
  const listeners = new Map(), frames = new Map();
  let frameId = 0;
  const element = { id: 'shape', type: 'shape', x: 100, y: 100, w: 100, h: 100 };
  const block = { id: 'source', h: 1000, elements: [element] };
  const wrap = { scrollTop: 0, getBoundingClientRect: () => ({ left: 0, right: 1000, top: 0, bottom: 300 }),
    querySelectorAll: selector => selector === '.canvas-block' ? [blockNode] : [] };
  const node = { dataset: { elid: 'shape' }, style: {},
    getBoundingClientRect: () => ({ left: 100, top: 100 - wrap.scrollTop, width: 100, height: 100 }),
    setPointerCapture() {}, hasPointerCapture: () => false };
  const clip = { getBoundingClientRect: () => ({ left: 0, top: -wrap.scrollTop, right: 1000, bottom: 1000 - wrap.scrollTop, width: 1000, height: 1000 }) };
  const blockNode = { dataset: { blockid: 'source' }, querySelector: () => clip, querySelectorAll: () => [node] };
  const scope = {
    wrapRef: { current: wrap }, scale: 1, genActive: false, getBlockRenderHeight, clampDragDelta,
    snapEditorDragDelta, snapEditorSpacing, hidePointerSnapGuides() {}, showPointerSnapGuides() {}, syncPointerGroupSelectionBounds() {},
    activePointerDragCleanup: { current: null }, gesturing: { current: false }, setCanvasGesture() {}, pointerGroupRectFrame: { current: null },
    document: { body: { style: { userSelect: 'auto' } } },
    window: { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key),
      requestAnimationFrame: fn => { frames.set(++frameId, fn); return frameId; }, cancelAnimationFrame: id => frames.delete(id) },
    lastPush: { current: 0 }, setBlocks() {},
  };
  const start = new Function(...Object.keys(scope), `${source.slice(begin, end)}; return startPointerSelectionDrag;`)(...Object.values(scope));
  start(block, ['shape'], { clientX: 150, clientY: 150, pointerId: 1, currentTarget: node });
  const move = (x, y) => listeners.get('pointermove')({ clientX: x, clientY: y, pointerId: 1, preventDefault() {} });
  return { wrap, node, move, tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); } };
}

test('dragging to the viewport edge does not turn on an unapproved automatic scroll', () => {
  const drag = dragHarness();
  drag.move(150, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 0);
});

test('manual scrolling during a drag still keeps document coordinates under the pointer', () => {
  const drag = dragHarness();
  drag.wrap.scrollTop = 100;
  drag.move(150, 160);
  assert.equal(drag.node.style.top, '210px');
});
