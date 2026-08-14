import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as editorAppearance from '../../src/features/editor/editorAppearance.js';

const {
  DEFAULT_EDITOR_COLOR_PRESETS,
  imageResizeRect,
  lineHitStrokeWidth,
  resizePolicyForElement,
  shouldShowRotationHandle,
  speechBubblePath,
  stripPhotoBlockTextElements,
} = editorAppearance;

const editorPanelsSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
const editorStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/features.css', import.meta.url)), 'utf8');
const moveableStylesSource = readFileSync(fileURLToPath(new URL('../../src/styles/moveable.css', import.meta.url)), 'utf8');
const editorSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');

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

test('element selection does not paint the parent block as selected', () => {
  assert.match(editorSource, /className=\{`canvas-block\$\{blockSelected \? ' on' : ''\}/);
  assert.doesNotMatch(editorSource, /className=\{`canvas-block\$\{blockActive \? ' on' : ''\}/);
});

test('multi-selection keeps every member and its Moveable bounds visibly in sync while dragging', () => {
  assert.match(editorSource, /selectionCount > 1 \? ' multi-selected' : ''/);
  assert.match(editorSource, /syncPointerGroupSelectionBounds\(\);/);
  assert.match(editorStylesSource, /\.el\.on\.multi-selected \{[^}]*outline:/);
});

test('template catalog shows readable completed references instead of checkerboards', () => {
  assert.match(editorStylesSource, /\.frame-layout-prev\.template \{[^}]*aspect-ratio:\s*3\s*\/\s*4/s);
  assert.match(editorStylesSource, /\.frame-layout-prev\.template > img \{[^}]*object-fit:\s*contain[^}]*background:\s*#fff/s);
  assert.doesNotMatch(editorStylesSource, /\.frame-layout-prev\.template \{[^}]*linear-gradient/s);
});

test('preset colors keep eight columns with practical pointer targets', () => {
  assert.match(editorStylesSource, /\.sf-color-popover\s*\{[^}]*width:\s*244px/s);
  assert.match(editorStylesSource, /\.sf-preset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(8,\s*24px\)[^}]*gap:\s*4px/s);
  assert.match(editorStylesSource, /\.sf-preset\s*\{[^}]*width:\s*24px[^}]*height:\s*24px[^}]*border-radius:\s*4px/s);
  assert.match(editorStylesSource, /\.sf-preset::after\s*\{[^}]*inset:\s*-2px/s);
});

test('compact Moveable controls expose a larger invisible hit surface', () => {
  assert.match(moveableStylesSource, /\.moveable-control::after\s*\{[^}]*inset:\s*-7px/s);
});

test('auto-height text keeps side controls attached to the selection border', () => {
  assert.match(editorSource, /className=\{autoHeightTextTarget \? 'moveable-auto-text' : undefined\}/);
  assert.doesNotMatch(moveableStylesSource, /\.moveable-auto-text \.moveable-[we]\s*\{[^}]*margin-left:/s);
  assert.match(moveableStylesSource, /\.moveable-control\s*\{[^}]*margin-left:\s*-6px/s);
  assert.match(moveableStylesSource, /\.moveable-auto-text \.moveable-control::after\s*\{[^}]*inset:\s*-2px/s);
});

test('crop image clipping does not clip the outside half of resize hit targets', () => {
  assert.match(editorSource, /className="crop-frame-image"/);
  assert.match(editorStylesSource, /\.crop-layer\s*\{[^}]*z-index:\s*7[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-frame\s*\{[^}]*overflow:\s*visible/s);
  assert.match(editorStylesSource, /\.crop-frame-image\s*\{[^}]*overflow:\s*hidden[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-h::after\s*\{[^}]*inset:\s*-6px/s);
  assert.match(editorStylesSource, /\.crop-bar\s*\{[^}]*pointer-events:\s*none/s);
  assert.match(editorStylesSource, /\.crop-bar button\s*\{[^}]*pointer-events:\s*auto/s);
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

test('ordinary text restores the complete editable box around its saved bounds', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'text' }, true), {
    keepRatio: false,
    directions: ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'],
  });
});

test('speech bubbles retain free resize handles while thin rules keep only their endpoints', () => {
  assert.deepEqual(resizePolicyForElement({ type: 'text', shape: 'bubble' }, true), {
    keepRatio: false,
    directions: ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'],
  });
  assert.deepEqual(resizePolicyForElement({ type: 'line' }, true), {
    keepRatio: false,
    directions: ['w', 'e'],
  });
});

test('thin rules keep a minimum twelve-pixel pointer target at every editor zoom', () => {
  assert.equal(lineHitStrokeWidth(2, 1), 12);
  assert.equal(lineHitStrokeWidth(2, 0.4), 30);
  assert.equal(lineHitStrokeWidth(16, 1), 16);
  assert.match(editorSource, /stroke="transparent" strokeWidth=\{hitWidth\}/);
});

test('text restores direct rotation while thin rules keep the numeric control', () => {
  assert.equal(shouldShowRotationHandle({ type: 'text' }), true);
  assert.equal(shouldShowRotationHandle({ type: 'line' }), false);
  assert.equal(shouldShowRotationHandle({ type: 'text', shape: 'bubble' }), true);
  assert.equal(shouldShowRotationHandle({ type: 'image' }), true);
  assert.match(editorPanelsSource, /labelText="회전" value=\{el\.rotate \|\| 0\}/);
  assert.match(editorSource, /rotatable=\{showRotationHandle\}/);
});

test('auto-height text expands its pointer target without covering adjacent table rules', () => {
  assert.match(editorStylesSource, /\.el-text:not\(\.editing\)::before/);
  assert.match(editorStylesSource, /top: calc\(-3px \* var\(--canvas-inv, 1\)\)/);
  assert.match(editorStylesSource, /bottom: calc\(-3px \* var\(--canvas-inv, 1\)\)/);
});

test('standalone text drags cannot fall through to native browser text selection', () => {
  const textDragPick = editorSource.slice(
    editorSource.indexOf('if (shouldStartTextOnlyDrag(el, e.shiftKey))'),
    editorSource.indexOf('// 처음 누른 완성형 오브젝트'),
  );

  assert.match(textDragPick, /e\.preventDefault\(\)/);
  assert.match(textDragPick, /window\.getSelection\?\.\(\)\?\.removeAllRanges\(\)/);
  assert.match(editorStylesSource, /\.el-text:not\(\.editing\)\s*\{[^}]*user-select:\s*none[^}]*touch-action:\s*none/s);
  assert.match(editorStylesSource, /\.el-text\.editing\s*\{[^}]*user-select:\s*text[^}]*touch-action:\s*auto/s);
  assert.match(editorSource, /onDoubleClick=\{\(e\) => \{ e\.stopPropagation\(\); pendingBubbleFit\.current = null; onEdit\(el\.id\)/);
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

test('speech bubble path keeps a clearly visible tail at compact sizes', () => {
  const path = speechBubblePath({ width: 196, height: 87, radius: 28 });
  const tailTip = path.match(/L ([\d.]+) 87 L/);

  assert.ok(tailTip, 'the tail reaches the bottom edge of the bubble box');
  assert.ok(Number(tailTip[1]) < 32, 'the tail tip extends clearly beyond its left base');
});
