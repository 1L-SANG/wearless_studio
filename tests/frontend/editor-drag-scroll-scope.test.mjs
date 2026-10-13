import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { clampDragDelta, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
import { snapEditorDragDelta, snapEditorSpacing } from '../../src/features/editor/editorSnap.js';
import { photoMoveDestination } from '../../src/features/editor/editorPhotoEditing.js';

// Execute the production pointer handler. The DOM/event loop is the external
// boundary; snapping and the handler's decisions stay real, without a browser.
function dragHarness({ type = 'shape', threshold = 0 } = {}) {
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const begin = source.indexOf('  const startPointerSelectionDrag =');
  const end = source.indexOf('  const startElementDrag =', begin);
  const listeners = new Map(), frames = new Map();
  let frameId = 0;
  const element = { id: 'element', type, x: 100, y: 100, w: 100, h: 100, ...(type === 'image' ? { src: '/photo.jpg' } : {}) };
  const block = { id: 'source', h: 1000, elements: [element] };
  let scrollTop = 0;
  const wrap = { get scrollTop() { return scrollTop; }, set scrollTop(value) { scrollTop = Math.max(0, Math.min(700, value)); },
    getBoundingClientRect: () => ({ left: 0, right: 1000, top: 0, bottom: 300 }),
    querySelectorAll: selector => selector === '.canvas-block' ? [blockNode] : [] };
  const node = { dataset: { elid: 'element' }, style: {},
    getBoundingClientRect: () => ({ left: 100, top: 100 - wrap.scrollTop, width: 100, height: 100 }),
    setPointerCapture() {}, hasPointerCapture: () => false };
  const clip = { getBoundingClientRect: () => ({ left: 0, top: -wrap.scrollTop, right: 1000, bottom: 1000 - wrap.scrollTop, width: 1000, height: 1000 }) };
  const blockNode = { dataset: { blockid: 'source' }, querySelector: () => clip, querySelectorAll: () => [node] };
  const scope = {
    wrapRef: { current: wrap }, scale: 1, genActive: false, getBlockRenderHeight, clampDragDelta,
    snapEditorDragDelta, snapEditorSpacing, photoMoveDestination, hidePointerSnapGuides() {}, showPointerSnapGuides() {}, syncPointerGroupSelectionBounds() {},
    activePointerDragCleanup: { current: null }, gesturing: { current: false }, setCanvasGesture() {}, pointerGroupRectFrame: { current: null },
    document: { body: { style: { userSelect: 'auto' } } },
    window: { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key),
      requestAnimationFrame: fn => { frames.set(++frameId, fn); return frameId; }, cancelAnimationFrame: id => frames.delete(id) },
    lastPush: { current: 0 }, setBlocks() {},
  };
  const start = new Function(...Object.keys(scope), `${source.slice(begin, end)}; return startPointerSelectionDrag;`)(...Object.values(scope));
  start(block, ['element'], { clientX: 150, clientY: 150, pointerId: 1, currentTarget: node }, { threshold });
  const move = (x, y) => listeners.get('pointermove')({ clientX: x, clientY: y, pointerId: 1, preventDefault() {} });
  return { wrap, node, move, cancel: () => listeners.get('pointercancel')({ type: 'pointercancel', pointerId: 1 }),
    release: () => listeners.get('pointerup')({ type: 'pointerup', pointerId: 1, clientX: 150, clientY: 290, preventDefault() {} }),
    pending: () => frames.size, tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); } };
}

test('dragging a shape to the viewport edge does not scroll the canvas', () => {
  const drag = dragHarness();
  drag.move(150, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 0);
});

test('holding a photo body at the bottom edge keeps scrolling without another pointer move', () => {
  const drag = dragHarness({ type: 'image' });
  drag.move(150, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 12);
  assert.equal(drag.node.style.top, '252px');
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 24);
  assert.equal(drag.node.style.top, '264px');
});

test('holding a photo body at the top edge scrolls upward', () => {
  const drag = dragHarness({ type: 'image' });
  drag.wrap.scrollTop = 100;
  drag.move(150, 10);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 88);
});

test('moving a held photo away from the edge stops automatic scrolling', () => {
  const drag = dragHarness({ type: 'image' });
  drag.move(150, 290);
  drag.tick();
  drag.move(150, 150);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 12);
  assert.equal(drag.pending(), 0);
});

test('cancelling a photo drag stops queued scrolling and restores its original position', () => {
  const drag = dragHarness({ type: 'image' });
  drag.move(150, 290);
  drag.tick();
  drag.cancel();
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 12);
  assert.equal(drag.node.style.top, '100px');
  assert.equal(drag.pending(), 0);
});

test('releasing a photo stops scrolling immediately after the drop', () => {
  const drag = dragHarness({ type: 'image' });
  drag.move(150, 290);
  drag.tick();
  drag.release();
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 12);
  assert.equal(drag.pending(), 0);
});

test('photo scrolling stops at the document boundary instead of keeping a frame loop alive', () => {
  const drag = dragHarness({ type: 'image' });
  drag.wrap.scrollTop = 700;
  drag.move(150, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 700);
  assert.equal(drag.pending(), 0);
});

test('a click that never passes the photo body drag threshold does not start scrolling', () => {
  const drag = dragHarness({ type: 'image', threshold: 200 });
  drag.move(150, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 0);
  assert.equal(drag.pending(), 0);
});

test('a held photo outside the canvas horizontally does not auto-scroll', () => {
  const drag = dragHarness({ type: 'image' });
  drag.move(1100, 290);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 0);
});

test('a held photo above the canvas viewport does not auto-scroll', () => {
  const drag = dragHarness({ type: 'image' });
  drag.wrap.scrollTop = 100;
  drag.move(150, -10);
  drag.tick();
  assert.equal(drag.wrap.scrollTop, 100);
});

test('manual scrolling during a drag still keeps document coordinates under the pointer', () => {
  const drag = dragHarness();
  drag.wrap.scrollTop = 100;
  drag.move(150, 160);
  assert.equal(drag.node.style.top, '210px');
});
