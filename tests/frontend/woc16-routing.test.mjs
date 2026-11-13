import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import { hooks } from './helpers/reactHarness.mjs';
import { hasEditorEntered, markEditorEntered } from '../../src/lib/editorEntered.js';
import * as markers from '../../src/lib/storyboardEntered.js';
import * as flowSession from '../../src/lib/flowSession.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const app = read('src/apps/seller/App.jsx'), shell = read('src/features/shell/shell.jsx');
const message = '콘티를 만든 뒤에는 분석으로 돌아갈 수 없어요';
const storage = () => {
  const map = new Map();
  return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key) };
};
function installStorage(t) {
  const before = { localStorage: globalThis.localStorage, sessionStorage: globalThis.sessionStorage };
  globalThis.localStorage = storage(); globalThis.sessionStorage = storage();
  t.after(() => { Object.assign(globalThis, before); });
}
const sliceIfPresent = (source, start, end) => source.includes(start) ? source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))) : '';

async function routeHarness(path, { projectId = 'p', entered = true, editor = false, status = 'draft', intent = false, missing = false, fail = false } = {}) {
  if (entered) localStorage.setItem('sb-entered-p', '1');
  if (editor) markEditorEntered(projectId);
  const runtime = hooks(), calls = [];
  const state = { projectId, projectPersisted: Boolean(projectId), projectGeneration: 0, userId: 'seller', resumePath: '/create/storyboard',
    loadProject: async () => { if (missing) state.projectId = null; return state.projectId; },
    setResumePath: path => { state.resumePath = path; } };
  const useAppStore = selector => selector(state);
  useAppStore.getState = () => state;
  const entry = app.match(new RegExp(`<Route path="${path}" element=\\{(.+)\\} />`));
  const hookSource = sliceIfPresent(shell, 'export function useStoryboardInputGuard(', 'export function DoneGuardModal(').replace(/^export /gm, '');
  const wrappers = sliceIfPresent(app, 'function BeforeStoryboardRoute(', "/* '새 제작'");
  const { code } = await transformWithEsbuild(`${hookSource}\n${wrappers}\nfunction routeElement() { return ${entry?.[1] || 'null'}; }`, 'Woc16Routes.jsx', { jsx: 'transform' });
  const bindings = { React, ...runtime, ...markers, ...flowSession, hasEditorEntered, useLayoutEffect: runtime.useEffect, useAppStore,
    isMockMode: false, useAuth: () => ({ session: { user: { id: 'seller' } }, loading: false }),
    useLocation: () => ({ pathname: `/create/${path}`, key: 'route', state: intent ? { startNewFromResume: true } : null }),
    useNavigate: () => (path, options) => calls.push(['navigate', path, options]),
    useToast: () => ({ push: msg => calls.push(['toast', msg]) }), isMobileDevice: () => false,
    api: { getProject: async id => { if (fail) throw new Error('offline'); return { id, status }; } },
    ProductInputRoute: 'ProductInputRoute', ProductInputPlaceholder: 'ProductInputPlaceholder', Storyboard: 'Storyboard', DoneGuardModal: 'DoneGuardModal', Navigate: 'Navigate' };
  const makeRoute = new Function(...Object.keys(bindings), `${code}; return routeElement;`)(...Object.values(bindings));
  const render = () => runtime.render(() => {
    let node = makeRoute();
    while (typeof node?.type === 'function') node = node.type(node.props);
    return node;
  });
  const settle = async () => {
    let node;
    for (let i = 0; i < 4; i += 1) {
      node = render(); runtime.flushEffects(); await new Promise(setImmediate);
    }
    return node;
  };
  return { calls, state, render, settle };
}

for (const path of ['input', 'analysis']) test(`WOC16 /create/${path} rejects browser back and direct entry after storyboard`, async t => {
  installStorage(t);
  const h = await routeHarness(path);
  assert.notEqual(h.render()?.type, 'ProductInputRoute', 'the locked input must not mount while checking');
  await h.settle();
  assert.deepEqual(h.calls.filter(call => call[0] === 'navigate'), [['navigate', '/create/storyboard', { replace: true }]]);
  assert.deepEqual(h.calls.filter(call => call[0] === 'toast'), [['toast', message]]);
  assert.equal(h.state.resumePath, '/create/storyboard');
  assert.equal(flowSession.readFlowSession().path, '/create/storyboard');
});

for (const path of ['input', 'analysis']) test(`WOC16 a new or pre-storyboard project can still open ${path}`, async t => {
  installStorage(t);
  const h = await routeHarness(path, { entered: false });
  assert.equal((await h.settle())?.type, 'ProductInputRoute');
  assert.deepEqual(h.calls, []);
});

test('WOC16 entering the actual storyboard route marks only that project and survives a new tab', async t => {
  installStorage(t);
  const h = await routeHarness('storyboard', { entered: false });
  assert.equal((await h.settle())?.type, 'Storyboard');
  assert.equal(localStorage.getItem('sb-entered-p'), '1');
  assert.equal(localStorage.getItem('ed-entered-p'), null, 'storyboard must not trigger the stronger editor guard');
  const reopened = await routeHarness('analysis');
  await reopened.settle();
  assert.equal(reopened.calls[0]?.[1], message);
  const newProject = await routeHarness('input', { projectId: 'new-project' });
  assert.equal((await newProject.settle())?.type, 'ProductInputRoute');
  assert.equal(localStorage.getItem('sb-entered-p'), '1');
});

test('WOC16 explicit new-start intent reaches the existing draft-choice flow', async t => {
  installStorage(t);
  const h = await routeHarness('input', { intent: true });
  assert.equal((await h.settle())?.type, 'ProductInputRoute');
  assert.deepEqual(h.calls, []);
  assert.equal(localStorage.getItem('sb-entered-p'), '1');
});

for (const protection of ['editor', 'done']) test(`WOC16 ${protection} protection remains stronger than storyboard redirection`, async t => {
  installStorage(t);
  const h = await routeHarness('input', { editor: protection === 'editor', status: protection === 'done' ? 'done' : 'draft' });
  assert.equal((await h.settle())?.type, 'DoneGuardModal');
  assert.deepEqual(h.calls, []);
});

test('WOC16 a deleted project does not trap the input route', async t => {
  installStorage(t);
  const h = await routeHarness('input', { missing: true });
  assert.equal((await h.settle())?.type, 'ProductInputRoute');
  assert.deepEqual(h.calls, []);
});

test('WOC16 Library new-product action carries explicit new-start intent', () => {
  const source = read('src/features/library/Library.jsx');
  const body = source.slice(source.indexOf('  const onNew ='), source.indexOf('  const onOpen ='));
  const calls = [], entryToken = { current: 0 };
  new Function('entryToken', 'navigate', `${body}; onNew();`)(entryToken, (...args) => calls.push(args));
  assert.deepEqual(calls, [['/create/input', { state: { startNewFromResume: true } }]]);
});

for (const milestone of ['none', 'storyboard', 'editor']) test(`WOC16 PC handoff photo-upload action preserves ${milestone} input intent`, async t => {
  installStorage(t);
  if (milestone === 'storyboard') markers.markStoryboardEntered('p');
  if (milestone === 'editor') markEditorEntered('p');
  const source = read('src/features/shell/PcOnlyPage.jsx');
  const button = source.match(/<button type="button" className="btn btn-primary btn-block" onClick=\{(.+)\}>사진 업로드 화면으로<\/button>/);
  const calls = [];
  const bindings = { ...markers, hasEditorEntered, useAppStore: { getState: () => ({ projectId: 'p' }) }, navigate: (...args) => calls.push(args) };
  const callback = source.includes('  const openInput =')
    ? `${source.slice(source.indexOf('  const openInput ='), source.indexOf('  useEffect('))}; return openInput;`
    : `return (${button[1]});`;
  new Function(...Object.keys(bindings), callback)(...Object.values(bindings))();
  assert.deepEqual(calls, [milestone === 'none' ? ['/create/input'] : ['/create/input', { state: { startNewFromResume: true } }]]);
});
