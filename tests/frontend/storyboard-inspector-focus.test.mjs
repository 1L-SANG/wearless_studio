import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { hookHarness, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';

test('drawer opening and cleanup transfer focus to the panel and back to its actual trigger', async () => {
  const h = hookHarness(), focus = [], events = new Map();
  const trigger = { isConnected: true, focus: () => focus.push('trigger') };
  const document = { activeElement: trigger, body: { classList: { add() {}, remove() {} } },
    addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) };
  const Drawer = await loadStoryboardComponent('StoryboardDrawer.jsx', 'StoryboardDrawer', { ...h.hooks, Icon: 'svg', document, createPortal: value => value });
  h.render(Drawer, { mode: 'photo', onClose() {} }, tree => { tree.ref.current = { focus: () => focus.push('panel') }; });
  assert.deepEqual(focus, ['panel']); h.close(); assert.deepEqual(focus, ['panel', 'trigger']); assert.equal(events.size, 0);
});
test('drawer cleanup does not try to focus a removed trigger', async () => {
  const h = hookHarness(); let focused = 0;
  const document = { activeElement: { isConnected: false, focus: () => focused++ }, body: { classList: { add() {}, remove() {} } }, addEventListener() {}, removeEventListener() {} };
  const Drawer = await loadStoryboardComponent('StoryboardDrawer.jsx', 'StoryboardDrawer', { ...h.hooks, Icon: 'svg', document, createPortal: value => value });
  h.render(Drawer, { mode: 'photo', onClose() {} }, tree => { tree.ref.current = { focus() {} }; }); h.close(); assert.equal(focused, 0);
});
test('switching the open photo panel records the latest external trigger for focus restoration', async () => {
  const h = hookHarness(), focused = [];
  const trigger = id => ({ isConnected: true, focus: () => focused.push(id) });
  const document = { activeElement: trigger('first'), body: { classList: { add() {}, remove() {} } }, addEventListener() {}, removeEventListener() {} };
  const panel = { contains: element => element === panel, focus: () => { document.activeElement = panel; } };
  const Drawer = await loadStoryboardComponent('StoryboardDrawer.jsx', 'StoryboardDrawer', { ...h.hooks, Icon: 'svg', document, createPortal: value => value });
  h.render(Drawer, { mode: 'photo', focusKey: 'first', onClose() {} }, tree => { tree.ref.current = panel; });
  document.activeElement = trigger('second');
  h.render(Drawer, { mode: 'photo', focusKey: 'second', onClose() {} }); h.close();
  assert.deepEqual(focused, ['second']);
});
test('the live close action is blocked while an atomic save is pending', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('  const closeInspector ='), source.indexOf('  const selectCard ='));
  for (const busy of [true, false]) {
    const calls = [], clear = () => calls.push('clear');
    const close = new Function('atomicSavingRef', 'setDrawer', 'setSetPicker', 'setSetPickerError', 'setSelectedId', 'setSelectedViewId', `${body};return closeInspector;`)({ current: busy }, clear, clear, clear, clear, clear);
    close(); assert.equal(calls.length, busy ? 0 : 5);
  }
});
