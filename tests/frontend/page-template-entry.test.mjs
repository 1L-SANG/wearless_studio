import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { defaultPageStyle, selectTemplateStyle, validPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { outfitHarness, nodes, textOf, drain } from './helpers/outfitHarness.mjs';

const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const helpers = source.slice(source.indexOf('  const persistSelectedPageStyle ='), source.indexOf('  const [ccDraft'));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function preparation(overrides = {}) {
  const events = [], style = defaultPageStyle();
  const scope = { validPageStyle, pageStyleReady: true, pageStyleValueRef: { current: style },
    pageStyleSaveRef: { current: Promise.resolve() }, a: {},
    onChange: async patch => events.push(['save', patch]),
    commitMdComment: async () => events.push(['md']), setPageStyleError() {}, ...overrides };
  const actions = new Function('scope', `with (scope) { ${helpers}; return { prepareOutfit, persistSelectedPageStyle }; }`)(scope);
  return { scope, events, ...actions };
}

test('outfit preparation awaits the selection queue and persists the default before proceeding', async () => {
  const queued = deferred(), saved = deferred(), events = [];
  const h = preparation({ pageStyleSaveRef: { current: queued.promise }, onChange: patch => { events.push(patch); return saved.promise; } });
  let completed = false;
  const task = h.prepareOutfit().then(() => { completed = true; });
  await drain(); assert.equal(completed, false); assert.equal(events.length, 0);
  queued.resolve(); await drain(); assert.equal(completed, false);
  assert.deepEqual(events, [{ pageStyle: defaultPageStyle() }]);
  saved.resolve(); await task; assert.equal(completed, true);
});

test('a newer queued selection is also awaited and its latest pageStyle is saved', async () => {
  const first = deferred(), second = deferred();
  const h = preparation({ pageStyleSaveRef: { current: first.promise } });
  let completed = false;
  const task = h.persistSelectedPageStyle().then(() => { completed = true; });
  h.scope.pageStyleValueRef.current = selectTemplateStyle('show', '2026-11-05T00:00:00Z', { ...defaultPageStyle(), colorMode: 'point' });
  h.scope.pageStyleSaveRef.current = second.promise;
  first.resolve(); await drain(); assert.equal(completed, false);
  second.resolve(); await task;
  assert.equal(h.scope.pageStyleValueRef.current.templateId, 'show');
  assert.equal(h.scope.pageStyleValueRef.current.colorMode, 'point');
  assert.deepEqual(h.events, [['save', { pageStyle: h.scope.pageStyleValueRef.current }]]);
});

test('missing or loading selection and save failures prevent preparation', async () => {
  for (const overrides of [{ pageStyleReady: false }, { pageStyleValueRef: { current: null } }]) {
    const h = preparation(overrides);
    await assert.rejects(h.prepareOutfit(), /템플릿을 골라 주세요/);
    assert.equal(h.events.some(([name]) => name === 'save'), false);
  }
  const h = preparation({ onChange: async () => { throw new Error('저장 실패'); } });
  await assert.rejects(h.prepareOutfit(), /저장 실패/);
  assert.match(source, /disabled=\{!pageStyleReady \|\| preparingOutfit \|\| confirming\}/);
  assert.match(source, /enterDisabled=\{!pageStyleReady \|\| !validPageStyle\(pageStyle\) \|\| confirming\}/);
});

for (const success of [true, false]) test(`outfit stage remains closed until template save ${success ? 'succeeds' : 'fails'}`, async t => {
  const oldWindow = globalThis.window;
  globalThis.window = { scrollY: 0, matchMedia: () => ({ matches: true }), scrollTo() {}, localStorage: { getItem: () => '1', setItem() {} } };
  t.after(() => { globalThis.window = oldWindow; });
  const h = await outfitHarness(); t.after(() => h.close());
  const { AnalysisStepFlow } = await h.load('/src/features/analysis/AnalysisStepFlow.jsx');
  const save = deferred(), states = [];
  const props = { inline: true, analysis: {}, afterAnalysis: 'template-picker', outfit: 'outfit-board', cta: 'confirm',
    beforeEnter: () => save.promise, onEnteringChange: value => states.push(value) };
  let tree = h.render(AnalysisStepFlow, props, root => {
    root.props.ref.current = { closest() { return this; } };
  });
  h.runtime.slots[8].current = { getBoundingClientRect: () => ({ top: 76 }), focus() {} };
  const pending = nodes(tree).find(node => node.type === 'button' && textOf(node) === '코디 고르기').props.onClick();
  tree = h.render(AnalysisStepFlow, props);
  assert.equal(tree.props['data-stage'], 'analysis');
  assert.equal(nodes(tree).find(node => node.type === 'button' && textOf(node) === '코디 고르기').props.disabled, true);
  assert.equal(textOf(tree).includes('outfit-board'), false);
  if (success) save.resolve(); else save.reject(new Error('템플릿을 저장하지 못했어요.'));
  await pending; tree = h.render(AnalysisStepFlow, props);
  assert.equal(tree.props['data-stage'], success ? 'outfit' : 'analysis');
  assert.deepEqual(states, [true, false]);
  if (!success) assert.ok(nodes(tree).some(node => node.props.role === 'alert' && textOf(node).includes('템플릿을 저장하지 못했어요')));
});

test('a disabled entry cannot advance or start a save even when called directly', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { AnalysisStepFlow } = await h.load('/src/features/analysis/AnalysisStepFlow.jsx');
  let called = false;
  const props = { inline: true, analysis: {}, enterDisabled: true, beforeEnter: () => { called = true; } };
  const tree = h.render(AnalysisStepFlow, props);
  const button = nodes(tree).find(node => node.type === 'button' && textOf(node) === '코디 고르기');
  assert.equal(button.props.disabled, true); await button.props.onClick();
  assert.equal(called, false);
  assert.equal(h.render(AnalysisStepFlow, props).props['data-stage'], 'analysis');
});
