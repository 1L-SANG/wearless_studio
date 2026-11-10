import test from 'node:test';
import assert from 'node:assert/strict';
import { hookHarness, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';
import { baseColorOf, isNeutralPageSwatch, resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';

async function harness() {
  const h = hookHarness(), tasks = new Map(), saves = [], motion = { matches: false, addEventListener(_, cb) { this.listener = cb; }, removeEventListener() {} };
  let serial = 0;
  const timing = { setTimeout: (fn, ms) => { tasks.set(++serial, { fn, ms }); return serial; }, clearTimeout: id => tasks.delete(id), matchMedia: () => motion };
  const useTone = await loadStoryboardComponent('useStoryboardTone.js', 'useStoryboardTone', { ...h.hooks, baseColorOf, isNeutralPageSwatch, resolvePageTokens });
  const props = { projectId: 'one', pageStyle: defaultPageStyle(), product: { colors: [{ isBase: true, swatchId: 'blue' }] }, sectionCount: 8,
    timing, onChange(mode) { saves.push(mode); props.pageStyle = { ...props.pageStyle, colorMode: mode }; } };
  return { h, props, saves, tasks, motion, render: () => h.render(useTone, props),
    advance(ms) { [...tasks].filter(([, task]) => task.ms <= ms).sort((a, b) => a[1].ms - b[1].ms).forEach(([id, task]) => { tasks.delete(id); task.fn(); }); } };
}
test('WOC7 hover/focus previews point, restores selected mode and never saves', async t => {
  const h = await harness(); t.after(() => h.h.close());
  let tone = h.render(); assert.equal(tone.mode, 'mono');
  tone.beginPreview('pointer'); tone = h.render(); assert.equal(tone.mode, 'point');
  tone.beginPreview('focus'); tone = h.render(); tone.endPreview('pointer'); tone = h.render(); assert.equal(tone.mode, 'point');
  tone.endPreview('focus'); tone = h.render(); assert.equal(tone.mode, 'mono'); assert.deepEqual(h.saves, []);
});
test('WOC7 selection starts at stage, reveals all outline sections in under one second and cancels old waves', async t => {
  const h = await harness(); t.after(() => h.h.close());
  let tone = h.render(); tone.beginPreview('pointer'); tone = h.render(); tone.select('point'); tone = h.render();
  assert.deepEqual(h.saves, ['point']); assert.equal(tone.mode, 'point'); assert.equal(tone.wave.fromMode, 'mono'); assert.equal(tone.wave.revealed, 0);
  h.advance(100); tone = h.render(); assert.equal(tone.wave.revealed, 1);
  h.advance(500); tone = h.render(); assert.equal(tone.wave.revealed, 8);
  h.advance(800); tone = h.render(); assert.equal(tone.wave, null);
  tone.select('mono'); tone = h.render(); tone.select('point'); tone = h.render(); h.advance(800);
  assert.equal(h.render().mode, 'point'); assert.equal(h.tasks.size, 0);
});
test('WOC7 reduced motion applies immediately and switching to reduced motion cancels a running wave', async t => {
  const h = await harness(); t.after(() => h.h.close());
  h.motion.matches = true; let tone = h.render(); tone.select('point'); tone = h.render();
  assert.equal(tone.mode, 'point'); assert.equal(tone.wave, null); assert.equal(h.tasks.size, 0);
  h.motion.matches = false; tone.select('mono'); tone = h.render(); assert.ok(tone.wave);
  h.motion.matches = true; h.motion.listener({ matches: true }); tone = h.render(); assert.equal(tone.wave, null); assert.equal(h.tasks.size, 0);
});
test('WOC7 neutral base, project change and unmount cancel transient preview without writing', async () => {
  const h = await harness(); let tone = h.render(); tone.beginPreview('focus'); tone = h.render();
  h.props.product = { colors: [{ isBase: true, swatchId: 'black' }] }; tone = h.render(); assert.equal(tone.mode, 'mono'); assert.equal(tone.available, false);
  tone.select('point'); assert.deepEqual(h.saves, []);
  h.props.product = { colors: [{ swatchId: 'blue' }] }; tone = h.render(); assert.equal(tone.mode, 'mono');
  tone.select('point'); tone = h.render(); assert.ok(h.tasks.size);
  h.props.projectId = 'two'; h.props.pageStyle = defaultPageStyle(); tone = h.render(); assert.equal(tone.mode, 'mono'); assert.equal(tone.wave, null); assert.equal(h.tasks.size, 0);
  tone.select('point'); h.render(); h.h.close(); assert.equal(h.tasks.size, 0);
});
