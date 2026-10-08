import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const clean = (source) => source.replace(/^import [\s\S]*?;\n/gm, '').replace(/^export default .*;$/gm, '').replace(/^export /gm, '');
const captureSource = await transformWithEsbuild(clean(read('src/components/ClientErrorCapture.jsx')), 'Capture.jsx', { jsx: 'transform' });
const uuid = '2df3af7f-7b3f-4a6d-bf29-147a3c59a567';

function harness() {
  const slots = [];
  let cursor = 0;
  let effects = [];
  const reports = [];
  const bindings = { React, reportUserVisibleError: (details) => reports.push(details),
    useRef: (value) => slots[cursor++] ||= { current: value },
    useEffect: (effect, deps) => {
      const index = cursor++;
      if (!slots[index] || deps.some((value, i) => !Object.is(slots[index][i], value))) { slots[index] = deps; effects.push(effect); }
    },
    useState: (value) => [typeof value === 'function' ? value() : value, () => {}],
    useCallback: (fn) => fn, createContext: () => ({ Provider: 'provider' }), useContext: () => null,
    createPortal: (tree) => tree, document: { body: {} }, setTimeout: () => 0,
  };
  const exports = new Function(...Object.keys(bindings), `${captureSource.code}; return { ClientErrorCapture, useClientErrorCapture, ERROR_CAPTURE_POINTS };`)(...Object.values(bindings));
  const render = (fn, props) => {
    cursor = 0; effects = [];
    const tree = fn(props);
    for (const effect of effects) effect();
    return tree;
  };
  return { bindings: { ...bindings, ...exports }, ...exports, render, reports };
}

const points = [
  ['storyboardSave', 'src/features/storyboard/Storyboard.jsx', 'save_error', 'save', true],
  ['editorSave', 'src/features/editor/Editor.jsx', 'save_error', 'save', true],
  ['editorGeneration', 'src/features/editor/Editor.jsx', 'generation_error', 'detailPageGenerate', true],
  ['photoRestore', 'src/features/product-input/ProductInput.jsx', 'upload_error', 'restore', true],
  ['customMatchUpload', 'src/features/analysis/AnalysisForm.jsx', 'upload_error', 'upload', true],
];

for (const [point, path, kind, action, blocking] of points) {
  test(`${point} actual visible capture node reports once with kind, action, blocking and original Error`, async () => {
    const h = harness();
    const source = read(path);
    const node = source.match(new RegExp(`<ClientErrorCapture point="${point}"[\\s\\S]*?/>`))[0];
    const { code } = await transformWithEsbuild(`function View() { return (${node}); }`, 'View.jsx', { jsx: 'transform' });
    const error = Object.assign(new Error('user typed private@example.com'), { requestId: uuid });
    const values = { ...h.bindings, projectId: uuid, autosaveFailed: error, saveError: { error, backedUp: true },
      genFailed: true, genFinalizeError: '', genFinalizeOriginalError: error, dpJob: { error, jobId: uuid, status: 'error' },
      photoFailure: { error, canOmit: false }, originalError: error };
    const View = new Function(...Object.keys(values), `${code}; return View;`)(...Object.values(values));
    const tree = View();
    h.render(tree.type, tree.props); h.render(tree.type, tree.props);
    assert.equal(h.reports.length, 1);
    assert.deepEqual([h.reports[0].kind, h.reports[0].action, h.reports[0].blocking], [kind, action, blocking]);
    assert.equal(h.reports[0].error, error);
    assert.equal(h.reports[0].context.projectId, uuid);
    h.render(tree.type, { ...tree.props, active: false });
    h.render(tree.type, tree.props);
    assert.equal(h.reports.length, 2, 'a new visible failure may be reported after dismissal');
  });
}

test('photo restoration can continue by omitting optional images and reports nonblocking', () => {
  const h = harness();
  h.render(h.ClientErrorCapture, { point: 'photoRestore', blocking: false });
  assert.equal(h.reports[0].blocking, false);
  assert.match(read('src/features/product-input/ProductInput.jsx'), /blocking=\{!photoFailure.canOmit\}/);
});

test('ErrorState reports once without collecting the rendered description', async () => {
  const h = harness();
  const { code } = await transformWithEsbuild(clean(read('src/components/ui.jsx')), 'UI.jsx', { jsx: 'transform' });
  const { ErrorState } = new Function(...Object.keys(h.bindings), `${code}; return { ErrorState };`)(...Object.values(h.bindings));
  const error = Object.assign(new Error('technical'), { requestId: uuid });
  h.render(ErrorState, { error, desc: 'private@example.com typed text.jpg' });
  h.render(ErrorState, { error, desc: 'changed typed text' });
  assert.equal(h.reports.length, 1);
  assert.deepEqual([h.reports[0].kind, h.reports[0].action, h.reports[0].blocking], ['ui_error', 'load', true]);
  assert.equal(h.reports[0].error, error);
  assert.equal(JSON.stringify(h.reports).includes('private'), false);
});

test('ToastProvider captures only the error variant and preserves original Error and explicit blocking', async () => {
  const h = harness();
  const { code } = await transformWithEsbuild(clean(read('src/components/ui.jsx')), 'UI.jsx', { jsx: 'transform' });
  const ToastProvider = new Function(...Object.keys(h.bindings), `${code}; return ToastProvider;`)(...Object.values(h.bindings));
  const tree = h.render(ToastProvider, { children: null });
  const error = Object.assign(new Error('technical'), { requestId: uuid });
  tree.props.value.push('input 안내 typed.jpg', { icon: 'alertTri' });
  assert.equal(h.reports.length, 0);
  tree.props.value.push('사용자 문구 private@example.com', { variant: 'error', error, actionName: 'save', messageCode: 'save_failed', blocking: true });
  assert.equal(h.reports.length, 1);
  assert.deepEqual([h.reports[0].kind, h.reports[0].action, h.reports[0].blocking], ['toast_error', 'save', true]);
  assert.equal(h.reports[0].error, error);
  assert.equal(JSON.stringify(h.reports).includes('private'), false);
});

test('mobile save modal reports only failed, once, with original Error, above the early return', async () => {
  const h = harness();
  const { code } = await transformWithEsbuild(clean(read('src/features/product-input/MobileInputHandoff.jsx')), 'Mobile.jsx', { jsx: 'transform' });
  const bindings = { ...h.bindings, useAuth: () => ({ session: null }), Modal: 'div', Button: 'button', UploadPendingTile: 'span' };
  const MobileSaveModal = new Function(...Object.keys(bindings), `${code}; return MobileSaveModal;`)(...Object.values(bindings));
  h.render(MobileSaveModal, { result: null });
  h.render(MobileSaveModal, { result: { status: 'saving' } });
  assert.equal(h.reports.length, 0);
  const error = Object.assign(new Error('offline'), { requestId: uuid });
  h.render(MobileSaveModal, { result: { status: 'failed', error } });
  h.render(MobileSaveModal, { result: { status: 'failed', error } });
  assert.equal(h.reports.length, 1);
  assert.deepEqual([h.reports[0].kind, h.reports[0].action, h.reports[0].blocking], ['save_error', 'save', true]);
  assert.equal(h.reports[0].error, error);
  h.render(MobileSaveModal, { result: { status: 'locked' } });
  h.render(MobileSaveModal, { result: { status: 'gone' } });
  assert.equal(h.reports.length, 1);
});

test('HandoffStatusPage reports only error, once, with correct restore action and blocking', async () => {
  const h = harness();
  const { code } = await transformWithEsbuild(clean(read('src/features/shell/HandoffStatusPage.jsx')), 'Handoff.jsx', { jsx: 'transform' });
  const bindings = { ...h.bindings, useAuth: () => ({ session: null }), s: {}, p: {}, HangTag: 'svg', Button: 'button', DraftProductRow: 'div' };
  const HandoffStatusPage = new Function(...Object.keys(bindings), `${code}; return HandoffStatusPage;`)(...Object.values(bindings));
  h.render(HandoffStatusPage, { variant: 'pc' }); h.render(HandoffStatusPage, { variant: 'gone' });
  assert.equal(h.reports.length, 0);
  const error = Object.assign(new Error('offline'), { requestId: uuid });
  h.render(HandoffStatusPage, { variant: 'error', error }); h.render(HandoffStatusPage, { variant: 'error', error });
  assert.equal(h.reports.length, 1);
  assert.deepEqual([h.reports[0].kind, h.reports[0].action, h.reports[0].blocking], ['ui_error', 'restore', true]);
  assert.equal(h.reports[0].error, error);
});

test('root error boundary renders calm fallback, reload button and reports the caught Error', async () => {
  const source = await transformWithEsbuild(clean(read('src/components/AppErrorBoundary.jsx')), 'Boundary.jsx', { jsx: 'transform' });
  const reports = [];
  const bindings = { React, Component: React.Component, Button: 'button', HangTag: 'svg', s: {}, reportUserVisibleError: (details) => reports.push(details) };
  const Boundary = new Function(...Object.keys(bindings), `${source.code}; return AppErrorBoundary;`)(...Object.values(bindings));
  const boundary = new Boundary({ children: 'child' });
  assert.equal(boundary.render(), 'child');
  const error = Object.assign(new Error('typed private@example.com'), { requestId: uuid });
  boundary.state = Boundary.getDerivedStateFromError(error); boundary.componentDidCatch(error);
  const tree = boundary.render();
  const html = renderToStaticMarkup(tree);
  assert.match(html, /화면을 불러오지 못했어요/); assert.match(html, /새로고침/); assert.match(html, /role="alert"/);
  assert.equal(html.includes('private'), false);
  assert.deepEqual([reports[0].kind, reports[0].action, reports[0].blocking], ['error_boundary', 'load', true]);
  assert.equal(reports[0].error, error);
  assert.match(read('src/apps/AppProviders.jsx'), /<AppErrorBoundary>[\s\S]*<AuthProvider>[\s\S]*<App \/>/);
  assert.match(read('src/apps/AppProviders.jsx'), /clientEvents.install\(\)/);
});

test('main save, analysis, upload, generation and restore failures pass original Error objects', () => {
  assert.match(read('src/features/editor/Editor.jsx'), /setSaveError\(\{ \.\.\.classifyEditorLoadError\(error\), backedUp, error \}\)/);
  assert.match(read('src/features/product-input/ProductInput.jsx'), /variant: 'error', error: e, kind: 'analysis_error', actionName: 'analyze'/);
  assert.match(read('src/features/analysis/AnalysisForm.jsx'), /setOriginalError\(uploadError\)/);
  assert.match(read('src/store/useAppStore.js'), /error: jobFailure\(job\)/);
  assert.match(read('src/features/product-input/MobileInputHandoff.jsx'), /error: next.error/);
  assert.match(read('src/lib/draftPhotoConfirmation.js'), /failure.error/);
});
