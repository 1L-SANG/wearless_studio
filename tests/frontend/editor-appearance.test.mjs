import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as editorAppearance from '../../src/features/editor/editorAppearance.js';

const {
  DEFAULT_EDITOR_COLOR_PRESETS,
  imageResizeRect,
  resizePolicyForElement,
  speechBubblePath,
  stripPhotoBlockTextElements,
} = editorAppearance;

const editorPanelsSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
const editorStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/features.css', import.meta.url)), 'utf8');

test('editor colors expose a practical preset palette made only of HEX values', () => {
  assert.deepEqual(DEFAULT_EDITOR_COLOR_PRESETS, [
    '#000000', '#3C3C3C', '#5B5B5B', '#8E8E8E', '#C5C5C5', '#EBEBEB', '#F1F1F1', '#FFFFFF',
    '#F20011', '#FD0083', '#FF00E8', '#0F00E7', '#00EFFE', '#00F035', '#7FFA38', '#EDFF3B',
    '#F4C5C5', '#FDE7D3', '#FFF0C8', '#D3E7CE', '#C9DCDF', '#C8DEF0', '#D4CCE4', '#E8CAD7',
    '#E98D8F', '#FAC495', '#FFE194', '#ACD2A1', '#96BDC1', '#93BEE2', '#AB9CCE', '#D19BB4',
    '#DF595C', '#F7A866', '#FFD466', '#86BD76', '#699BA5', '#619ED4', '#8370B8', '#BC6E94',
    '#BB000D', '#E5853A', '#F1BA3D', '#5C9F4C', '#397682', '#317ABB', '#5D439A', '#9E426C',
    '#87000A', '#AD5318', '#B9851F', '#2E6B23', '#0F4651', '#024986', '#2F1967', '#6B173D',
    '#570606', '#6E3710', '#755514', '#214518', '#0C2E35', '#063056', '#1E1242', '#44112A',
  ]);
  assert.ok(DEFAULT_EDITOR_COLOR_PRESETS.every((color) => /^#[0-9A-F]{6}$/.test(color)));
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

test('preset colors use the compact eight-column reference grid', () => {
  assert.match(editorStylesSource, /\.sf-color-popover\s*\{[^}]*width:\s*190px/s);
  assert.match(editorStylesSource, /\.sf-preset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(8,\s*16px\)[^}]*gap:\s*6px/s);
  assert.match(editorStylesSource, /\.sf-preset\s*\{[^}]*width:\s*16px[^}]*height:\s*16px[^}]*border-radius:\s*3px/s);
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

test('unlocked images accept independent vertical resize handles', () => {
  assert.deepEqual(imageResizeRect({
    element: { type: 'image', crop: undefined },
    start: { x: 120, y: 200, w: 398, h: 517 },
    width: 398,
    height: 620,
    beforeTranslate: [0, 0],
    naturalWidth: 398,
    naturalHeight: 517,
    lockRatio: false,
  }), { x: 120, y: 200, w: 398, h: 620 });

  assert.deepEqual(imageResizeRect({
    element: { type: 'image', crop: undefined },
    start: { x: 120, y: 200, w: 398, h: 517 },
    width: 398,
    height: 620,
    beforeTranslate: [0, -103],
    naturalWidth: 398,
    naturalHeight: 517,
    lockRatio: false,
  }), { x: 120, y: 97, w: 398, h: 620 });
});

test('locked frame images keep the frame ratio instead of snapping to the source image ratio', () => {
  assert.deepEqual(imageResizeRect({
    element: { type: 'image', frameSlot: true, crop: undefined },
    start: { x: 60, y: 50, w: 880, h: 644 },
    width: 880,
    height: 644,
    beforeTranslate: [0, 0],
    naturalWidth: 880,
    naturalHeight: 1144,
    lockRatio: true,
  }), { x: 60, y: 50, w: 880, h: 644 });
});

test('speech bubble path applies the editable pixel corner radius', () => {
  const rounded = speechBubblePath({ width: 400, height: 200, radius: 45 });
  const square = speechBubblePath({ width: 400, height: 200, radius: 0 });
  assert.match(rounded, /^M 45 0/);
  assert.notEqual(rounded, square);
});
