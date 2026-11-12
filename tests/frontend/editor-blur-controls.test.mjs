import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, ImagePanel;
before(async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  vite = await createServer({ configFile: false, root, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ ImagePanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx'));
});
after(() => vite?.close());
const photo = { id: 'p', type: 'image', src: '/photo', w: 1000, h: 500 };

test('backdrop selection exposes a labeled live range at zero and throughout the allowed bounds', () => {
  for (const value of [0, 48, 100]) {
    const html = renderToStaticMarkup(React.createElement(ImagePanel, { el: { ...photo, blur: value }, onChange() {} }));
    assert.match(html, /type="range"[^>]*min="0"[^>]*max="100"[^>]*aria-label="흐림"[^>]*value="(?:0|48|100)"/);
    assert.match(html, /흐림 숫자 입력/);
    assert.match(html, />효과</);
    assert.doesNotMatch(html, /class="psec-title">흐림</);
  }
});
test('ordinary photos and shapes do not get a backdrop blur control', () => {
  for (const el of [photo, { ...photo, type: 'shape', blur: 48 }]) {
    assert.doesNotMatch(renderToStaticMarkup(React.createElement(ImagePanel, { el, onChange() {} })), /aria-label="흐림"|>효과</);
  }
});

test('moving the actual range updates immediately through onChange including zero', () => {
  const patches = [];
  const tree = ImagePanel({ el: { ...photo, blur: 48 }, onChange: patch => patches.push(patch) });
  const find = (node, predicate) => !node || typeof node !== 'object' ? null : predicate(node) ? node
    : React.Children.toArray(node.props?.children).map(child => find(child, predicate)).find(Boolean);
  const control = find(tree, node => node.props?.label === '흐림');
  assert.ok(control);
  const range = find(control.type(control.props), node => node.type === 'input' && node.props.type === 'range');
  range.props.onChange({ target: { value: '74' } });
  range.props.onChange({ target: { value: '0' } });
  assert.deepEqual(patches, [{ blur: 74 }, { blur: 0 }]);
});

test('mock save/load round-trip preserves the same optional field without a second backdrop marker', async () => {
  const { api } = await vite.ssrLoadModule('/src/mock/api.js');
  const blocks = [{ id: 'b', elements: [{ ...photo, blur: 0 }, { ...photo, id: 'ordinary' }] }];
  await api.saveEditorBlocks('blur-test', blocks);
  const loaded = await api.getEditorBlocks('blur-test');
  assert.equal(loaded[0].elements[0].blur, 0);
  assert.equal(Object.hasOwn(loaded[0].elements[1], 'blur'), false);
  for (const invalid of [-1, 101, null, Infinity, '48', true]) {
    await assert.rejects(api.saveEditorBlocks('blur-test', [{ id: 'b', elements: [{ ...photo, blur: invalid }] }]),
      error => error.status === 400 && error.code === 'editor_blur_invalid');
  }
  assert.equal((await api.getEditorBlocks('blur-test'))[0].elements[0].blur, 0);
});

test('screen backdrop preserves crop with clipped edge extension and zero still has export metadata', async () => {
  const module = await vite.ssrLoadModule('/src/features/editor/EditorBackdropImage.jsx').catch(() => ({}));
  assert.equal(typeof module.EditorBackdropImage, 'function');
  const html = renderToStaticMarkup(React.createElement(module.EditorBackdropImage, {
    element: { ...photo, blur: 48, crop: { ox: 30, oy: 40, iw: 1200, ih: 900 } },
  }));
  assert.match(html, /stdDeviation="48"/);
  assert.match(html, /x="-30" y="-40" width="1200" height="900"/);
  assert.match(html, /overflow="hidden"/);
  assert.match(html, /viewBox="0 0 1 500"/);
  assert.match(html, /data-editor-blur="48"/);
  const zero = renderToStaticMarkup(React.createElement(module.EditorBackdropImage, { element: { ...photo, blur: 0 } }));
  assert.match(zero, /data-editor-blur="0"/);
  assert.doesNotMatch(zero, /feGaussianBlur/);
  const gif = renderToStaticMarkup(React.createElement(module.EditorBackdropImage, {
    element: { ...photo, blur: 48, kind: 'gif', posterSrc: '/first-frame.png', gifMeta: { frames: 4 } },
  }));
  assert.match(gif, /data-gif-poster="\/first-frame.png"/);
});

test('blur edits use the existing immutable document patch and undo history including zero', () => {
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const original = [{ id: 'b', elements: [{ ...photo, blur: 48 }] }];
  let current = original;
  const past = [], future = [];
  const begin = source.indexOf('  const patchEl ='), end = source.indexOf('  const patchBubbleAppearance =', begin);
  const patch = new Function('editedTextElement', 'selEl', 'setBlocks', `${source.slice(begin, end)}; return patchEl;`)(editedTextElement, 'p', update => {
    const next = update(current); if (next !== current) past.push(current); current = next;
  });
  patch({ blur: 0 });
  assert.equal(JSON.parse(JSON.stringify(current))[0].elements[0].blur, 0);
  assert.equal(original[0].elements[0].blur, 48);
  const undoStart = source.indexOf('  const undo ='), undoEnd = source.indexOf('  const redo =', undoStart);
  const scope = { croppingRef: { current: null }, hist: { current: { past, future } }, prevBlocks: { current }, fromHistory: { current: false },
    clearSel() {}, setBlocks(next) { current = next; }, toast: { push() {} } };
  new Function(...Object.keys(scope), `${source.slice(undoStart, undoEnd)}; return undo;`)(...Object.values(scope))();
  assert.equal(current[0].elements[0].blur, 48);
  assert.equal(future[0][0].elements[0].blur, 0);
});
