import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as editorAppearance from '../../src/features/editor/editorAppearance.js';

const {
  DEFAULT_EDITOR_COLOR_PRESETS,
  resizePolicyForElement,
  speechBubblePath,
  stripPhotoBlockTextElements,
} = editorAppearance;

const editorPanelsSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
const editorStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/features.css', import.meta.url)), 'utf8');

test('editor colors expose a practical preset palette made only of HEX values', () => {
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.length >= 24);
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.every((color) => /^#[0-9A-F]{6}$/.test(color)));
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.includes('#000000'));
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.includes('#FFFFFF'));
});

test('editor color controls use app-owned presets and a HEX-only text entry', () => {
  assert.doesNotMatch(editorPanelsSource, /type=["']color["']/);
  assert.match(editorPanelsSource, /aria-label="HEX 색상"/);
  assert.match(editorPanelsSource, /className="sf-preset-grid"/);
});

test('editor color popover shows both default presets and a functional custom palette', () => {
  assert.match(editorPanelsSource, /className="sf-color-palette"/);
  assert.match(editorPanelsSource, /aria-label="색조"/);
  assert.equal(typeof editorAppearance.hexToHsv, 'function');
  assert.equal(typeof editorAppearance.hsvToHex, 'function');
  assert.deepEqual(editorAppearance.hexToHsv('#FF0000'), { h: 0, s: 100, v: 100 });
  assert.equal(editorAppearance.hsvToHex({ h: 210, s: 100, v: 100 }), '#0080FF');
});

test('speech bubbles hide the neutral editor outline outside the actual bubble border', () => {
  assert.match(editorStylesSource, /\.el-speech-bubble:not\(\.on\)\s*\{[^}]*outline:\s*none/);
});

test('photo content blocks discard every text layer while non-photo blocks keep theirs', () => {
  const photoText = { id: 'copy', type: 'text', text: '사진 위 카피', sourceBlockId: 'shot-1' };
  const photo = { id: 'photo', type: 'image', src: '/cut.png', sourceBlockId: 'shot-1' };
  const faqText = { id: 'faq', type: 'text', text: 'FAQ' };
  const input = [
    { id: 'shot', contentRole: 'hero', elements: [photo, photoText, { id: 'badge', type: 'text', text: 'SALE' }] },
    { id: 'faq-block', kind: 'faq', elements: [faqText] },
  ];

  const output = stripPhotoBlockTextElements(input);

  assert.deepEqual(output[0].elements, [photo]);
  assert.deepEqual(output[1].elements, [faqText]);
  assert.equal(output[1], input[1], 'unrelated blocks keep their object identity');
});

test('row photo blocks are recognized by source-linked images even without contentRole', () => {
  const output = stripPhotoBlockTextElements([{
    id: 'row', kind: 'twocol', elements: [
      { id: 'image', type: 'image', src: '/row.png', sourceBlockId: 'shot-2' },
      { id: 'copy', type: 'text', text: '행 카피' },
    ],
  }]);

  assert.deepEqual(output[0].elements.map((element) => element.id), ['image']);
});

test('text boxes always expose horizontal resize handles without aspect-ratio lock', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'text' }, true), {
    keepRatio: false,
    directions: ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'],
  });
});

test('text numeric controls allow an empty editing draft and commit the finished number', () => {
  assert.equal(typeof editorAppearance.commitNumberDraft, 'function');
  assert.equal(editorAppearance.commitNumberDraft('', { min: 1, max: 10000, fallback: 24 }), 24);
  assert.equal(editorAppearance.commitNumberDraft('7', { min: 1, max: 10000, fallback: 24 }), 7);
  assert.equal(editorAppearance.commitNumberDraft('125.5', { min: 1, max: 10000, fallback: 24 }), 125.5);
  assert.match(editorPanelsSource, /function DraftNumberInput/);
  assert.match(editorPanelsSource, /onChange=\{\(event\) => setDraft\(event\.target\.value\)\}/);
  assert.match(editorPanelsSource, /iconText="가로"[^>]*min=\{1\}[^>]*max=\{10000\}/);
});

test('image resize keeps the existing lock preference', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'image' }, true), {
    keepRatio: true,
    directions: ['nw', 'ne', 'sw', 'se'],
  });
  assert.equal(resizePolicyForElement({ type: 'image' }, false).keepRatio, false);
});

test('speech bubble path applies the editable pixel corner radius', () => {
  const rounded = speechBubblePath({ width: 400, height: 200, radius: 45 });
  const square = speechBubblePath({ width: 400, height: 200, radius: 0 });
  assert.match(rounded, /^M 45 0/);
  assert.notEqual(rounded, square);
});
