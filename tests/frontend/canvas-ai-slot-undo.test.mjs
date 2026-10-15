import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import * as apply from '../../src/features/editor/canvasAiApply.js';
import { buildCanvasAiMenu, canvasAiActionCreditCost } from '../../src/features/editor/canvasAiActions.js';
import { editorImageCreditCost } from '../../src/lib/limits.js';
import { hasImageSource } from '../../src/features/editor/editorImageSource.js';
import { applySlotFillToInfo } from '../../src/features/editor/presets/infoPresets.js';
import { fitImageToFrameBlock } from '../../src/features/editor/editorImageDrop.js';
import { canvasAiHookHarness, editorCanvasAiHandlers } from './helpers/editorCanvasAiHarness.mjs';

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const layer = readFileSync(new URL('../../src/features/editor/CanvasAiLayer.jsx', import.meta.url), 'utf8');
const elementSource = editor.slice(editor.indexOf('function CanvasElement('), editor.indexOf('const IMAGE_IMPORT_COPY'));
const { code } = await transformWithEsbuild(`${layer.replace(/^import .*;\r?\n/gm, '').replace(/export function /g, 'function ')}\n${elementSource}`,
  'canvas-ai-test.jsx', { jsx: 'transform', jsxFactory: 'React.createElement', jsxFragment: 'React.Fragment' });

// Execute the real components and event handlers. Only DOM effects and React's
// state storage are replaced, so no browser or paid image request is needed.
function components() {
  const values = []; let cursor = 0;
  const scope = { React, ...apply, hasImageSource, editorImageCreditCost, canvasAiActionCreditCost, CREDIT_COSTS: { editorImage: 19 },
    useRef: (current) => ({ current }), useCallback: (callback) => callback,
    useEffect() {}, useLayoutEffect() {},
    useState(initial) {
      const index = cursor++;
      if (!(index in values)) values[index] = initial;
      return [values[index], (value) => { values[index] = value; }];
    }, Icon: () => null,
  };
  const result = new Function(...Object.keys(scope), `${code}; return { CanvasElement, CanvasAiProgress, CanvasAiLayer };`)(...Object.values(scope));
  return { ...result, render(name, props) { cursor = 0; return result[name](props); } };
}
function find(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of React.Children.toArray(node.props?.children)) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}
const empty = { id: 'empty', type: 'image', src: null, x: 0, y: 0, w: 300, h: 450, radius: 12 };
const initialBlocks = [{ id: 'block', kind: 'twocol', contentRole: 'custom', elements: [empty] }];

for (const status of ['cancel', 'busy', 'failed', 'arrived']) {
  test(`empty-slot progress renders only actionable ${status} state`, () => {
    const ui = components();
    const job = { isSlot: true, status, result: { src: '/generated.jpg' } };
    const tree = ui.render('CanvasElement', { el: empty, blockId: 'block', scale: 1, canvasAiJob: job });
    const progress = find(tree, (node) => node.type === ui.CanvasAiProgress);
    const upload = find(tree, (node) => node.props?.['aria-label'] === '이 프레임에 사진 넣기');
    assert.equal(Boolean(progress), status !== 'arrived');
    assert.equal(Boolean(upload), status === 'arrived');
    const overlay = ui.CanvasAiProgress({ job, slot: true });
    assert.equal(Boolean(overlay), status !== 'arrived');
  });
}

test('fill completion then toolbar Undo restores a clickable empty slot and its orb menu', async () => {
  let blocks = initialBlocks;
  const latestBlocks = { current: blocks }, prevBlocks = { current: blocks };
  const hist = { current: { past: [], future: [] } }, fromHistory = { current: false };
  const setBlocks = (update) => {
    const next = typeof update === 'function' ? update(blocks) : update;
    if (!fromHistory.current) hist.current.past.push(blocks);
    fromHistory.current = false;
    blocks = next; latestBlocks.current = next; prevBlocks.current = next;
  };
  const slotSource = editor.slice(editor.indexOf('  const setSlotImage ='), editor.indexOf('  const insertImage ='));
  const setSlotImage = new Function('setBlocks', 'applySlotFillToInfo', 'fitImageToFrameBlock', `${slotSource}; return setSlotImage;`)
    (setBlocks, applySlotFillToInfo, fitImageToFrameBlock);
  const wardrobe = [];
  const handlers = editorCanvasAiHandlers({ blocks, latestBlocks, setSlotImage,
    api: { saveEditorBlocks: async () => ({}) }, placeGeneratedImage: (group, id, image) => wardrobe.push(image) });
  const harness = canvasAiHookHarness({ blocks, selectedId: 'empty', handlers,
    api: { fillEmptySlot: async () => ({ data: { src: '/generated.jpg', cutType: 'styling' }, credits: 81 }) } });
  harness.start('fill');
  const job = await harness.run();
  assert.equal(blocks[0].elements[0].src, '/generated.jpg');
  assert.equal(job.dismissed, true, 'the applied slot result is dismissed immediately');
  assert.equal(hist.current.past.length, 1);

  const undoSource = editor.slice(editor.indexOf('  const undo ='), editor.indexOf('  const redo ='));
  const undo = new Function('hist', 'prevBlocks', 'fromHistory', 'clearSel', 'setBlocks', 'toast',
    'croppingRef', 'flushSync', 'kb', `${undoSource}; return undo;`)
    (hist, prevBlocks, fromHistory, () => {}, setBlocks, { push() {} },
      { current: null }, (callback) => callback(), { current: { cropCommit: () => assert.fail('no active crop to commit') } });
  undo();
  assert.equal(blocks[0].elements[0].src, null);
  assert.equal(wardrobe[0].src, '/generated.jpg', 'Undo keeps the generated image in the wardrobe');
  const ui = components(); let uploads = 0;
  const tree = ui.render('CanvasElement', { el: blocks[0].elements[0], blockId: 'block', scale: 1,
    canvasAiJob: job, onAddImage: () => { uploads += 1; } });
  assert.equal(find(tree, (node) => node.type === ui.CanvasAiProgress), null);
  const upload = find(tree, (node) => node.props?.['aria-label'] === '이 프레임에 사진 넣기');
  assert.ok(upload);
  upload.props.onClick({ stopPropagation() {} });
  assert.equal(uploads, 1);

  const menu = buildCanvasAiMenu({ block: blocks[0], element: blocks[0].elements[0] });
  assert.deepEqual(menu.branches.map((branch) => branch.id), ['upload', 'fill']);
  const orbUi = components(); const actions = [];
  const props = { block: blocks[0], element: blocks[0].elements[0], scale: 1, wrapRef: { current: null },
    menu, job, visible: true, onAction: (action) => actions.push(action) };
  const orb = find(orbUi.render('CanvasAiLayer', props), (node) => node.type === 'button' && node.props?.['aria-haspopup'] === 'menu'
    && node.props['aria-label'] === 'AI 생성');
  assert.ok(orb);
  orb.props.onClick();
  const fill = find(orbUi.render('CanvasAiLayer', props), (node) => node.props?.role === 'menuitem'
    && find(node, (child) => child.type === 'span' && child.props.children === 'AI로 채우기'));
  assert.ok(fill);
  assert.equal(fill.props['aria-disabled'], 'false');
  fill.props.onClick();
  assert.deepEqual(actions, ['fill']);
});
