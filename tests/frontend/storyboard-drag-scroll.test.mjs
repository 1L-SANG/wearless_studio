import test from 'node:test';
import assert from 'node:assert/strict';
import { edgeScrollVelocity, attachStoryboardDragScroll } from '../../src/lib/storyboardDragScroll.js';

test('edge scrolling is directional, gradual, bounded, and inactive in the middle', () => {
  assert.equal(edgeScrollVelocity(400, 800), 0);
  assert.ok(edgeScrollVelocity(30, 800) < edgeScrollVelocity(70, 800));
  assert.ok(edgeScrollVelocity(770, 800) > edgeScrollVelocity(730, 800));
  assert.equal(edgeScrollVelocity(0, 800), -300);
  assert.equal(edgeScrollVelocity(800, 800), 300);
  assert.equal(edgeScrollVelocity(-1, 800), 0);
});

function fixture() {
  const win = new EventTarget();
  win.document = new EventTarget();
  Object.assign(win, { innerHeight: 800, innerWidth: 1200, calls: [], frames: new Map(), serial: 0 });
  win.scrollBy = (value) => win.calls.push(value.top);
  win.requestAnimationFrame = (fn) => { win.frames.set(++win.serial, fn); return win.serial; };
  win.cancelAnimationFrame = (id) => win.frames.delete(id);
  win.tick = (time) => { const jobs = [...win.frames.values()]; win.frames.clear(); jobs.forEach(fn => fn(time)); };
  win.fire = (type, data = {}) => { const event = new Event(type, { cancelable: true }); Object.assign(event, data); win.document.dispatchEvent(event); return event; };
  return win;
}

test('edge holds scroll without repeated dragover and stops immediately on drop', () => {
  const win = fixture();
  const cleanup = attachStoryboardDragScroll(win);
  win.fire('dragover', { clientX: 300, clientY: 795 });
  win.tick(0); win.tick(16); win.tick(32);
  assert.ok(win.calls.length >= 2 && win.calls.every(n => n > 0 && n < 6));
  win.fire('drop'); const count = win.calls.length; win.tick(48);
  assert.equal(win.calls.length, count);
  assert.equal(win.frames.size, 0);
  cleanup();
});

test('wheel supports line units, preserves browser zoom, and cleanup stops listeners', () => {
  const win = fixture();
  const cleanup = attachStoryboardDragScroll(win);
  const wheel = win.fire('wheel', { deltaY: 3, deltaMode: 1, ctrlKey: false });
  assert.equal(wheel.defaultPrevented, true);
  assert.deepEqual(win.calls, [48]);
  const zoom = win.fire('wheel', { deltaY: 10, deltaMode: 0, ctrlKey: true });
  assert.equal(zoom.defaultPrevented, false);
  cleanup();
  win.fire('wheel', { deltaY: 50, deltaMode: 0 });
  assert.deepEqual(win.calls, [48]);
});

test('moving to the centre, leaving the window, blur and Escape stop edge motion', () => {
  for (const end of ['centre', 'leave', 'blur', 'escape', 'dragend']) {
    const win = fixture(); const cleanup = attachStoryboardDragScroll(win);
    win.fire('dragover', { clientX: 300, clientY: 5 });
    win.tick(0); win.tick(16);
    if (end === 'centre') win.fire('dragover', { clientX: 300, clientY: 400 });
    if (end === 'leave') win.fire('dragleave', { relatedTarget: null, clientX: -1, clientY: 4 });
    if (end === 'blur') win.dispatchEvent(new Event('blur'));
    if (end === 'escape') win.fire('keydown', { key: 'Escape' });
    if (end === 'dragend') win.fire('dragend');
    const count = win.calls.length; win.tick(32);
    assert.equal(win.calls.length, count, end);
    cleanup();
  }
});
