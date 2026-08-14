import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  EDITOR_INFO_PRESET_DRAG_TYPE,
  EDITOR_IMAGE_DRAG_TYPE,
  acceptsEditorBlockInsert,
  decodeEditorImageDrag,
  encodeEditorImageDrag,
  findImageDropSlot,
  pendingImageImportTarget,
  placeImageInBlock,
  viewportPointToBlock,
} from '../../src/features/editor/editorImageDrop.js';
import { buildFrameBlock } from '../../src/features/editor/editorLibrary.js';

const editorSource = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const panelSource = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const contentPanelSource = readFileSync(new URL('../../src/features/editor/ContentPanel.jsx', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
const httpAdapterSource = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');

test('wardrobe image drag payload keeps only the data needed by the canvas', () => {
  assert.equal(EDITOR_IMAGE_DRAG_TYPE, 'application/x-wearless-image');

  const encoded = encodeEditorImageDrag(
    { id: 'wardrobe-1', src: 'blob:shirt', cutType: 'front', ignored: 'large metadata' },
    { width: 1200, height: 1600 },
  );

  assert.deepEqual(decodeEditorImageDrag(encoded), {
    id: 'wardrobe-1',
    src: 'blob:shirt',
    cutType: 'front',
    width: 1200,
    height: 1600,
  });
  assert.equal(decodeEditorImageDrag('not json'), null);
  assert.equal(decodeEditorImageDrag(JSON.stringify({ id: 'missing-src' })), null);
});

test('content presets participate in the same between-block drag contract', () => {
  assert.equal(EDITOR_INFO_PRESET_DRAG_TYPE, 'application/x-wearless-info-preset');
  assert.equal(acceptsEditorBlockInsert(['text/plain']), false);
  assert.equal(acceptsEditorBlockInsert([EDITOR_INFO_PRESET_DRAG_TYPE]), true);
  assert.match(contentPanelSource, /draggable/);
  assert.match(contentPanelSource, /setData\(EDITOR_INFO_PRESET_DRAG_TYPE,\s*p\.type\)/);
  assert.match(editorSource, /EDITOR_INFO_PRESET_DRAG_TYPE/);
  assert.match(editorSource, /addInfoPresetBlock/);
});

test('the frame panel separates blank, example, and guide frames', () => {
  assert.match(panelSource, /value: 'blank', label: '빈 프레임'/);
  assert.match(panelSource, /value: 'example', label: '예시 프레임'/);
  assert.match(panelSource, /value: 'guide', label: '안내 프레임'/);
  assert.match(panelSource, /category === 'blank' \? !frame\.template : frame\.template/);
  assert.match(panelSource, /category === 'guide' \? \(/);
  assert.match(panelSource, /<ContentPanel[^>]*showIntro=\{false\}/s);
});

test('viewport drop coordinates are converted through the current canvas zoom', () => {
  assert.deepEqual(viewportPointToBlock({
    clientX: 340,
    clientY: 190,
    blockLeft: 140,
    blockTop: 130,
    scale: 0.4,
  }), { x: 500, y: 150 });
});

test('a dropped portrait image is contained inside the target block and centered at the drop point', () => {
  assert.deepEqual(placeImageInBlock({
    blockHeight: 300,
    imageWidth: 1200,
    imageHeight: 1600,
    dropX: 500,
    dropY: 150,
  }), { x: 418, y: 40, w: 165, h: 220 });
});

test('image placement clamps edge drops without escaping the block frame', () => {
  assert.deepEqual(placeImageInBlock({
    blockHeight: 300,
    imageWidth: 1600,
    imageHeight: 900,
    dropX: 990,
    dropY: 295,
  }), { x: 569, y: 40, w: 391, h: 220 });
});

test('a drop inside an empty image frame fills that frame instead of creating a new layer', () => {
  const elements = [
    { id: 'filled', type: 'image', src: '/old.webp', frameSlot: true, x: 40, y: 40, w: 300, h: 220 },
    { id: 'empty', type: 'image', src: null, frameSlot: true, x: 360, y: 40, w: 300, h: 220 },
  ];

  assert.equal(findImageDropSlot(elements, { x: 500, y: 150 })?.id, 'empty');
  assert.equal(findImageDropSlot(elements, { x: 200, y: 150 })?.id, 'filled', 'dropping on a filled frame replaces it');
  assert.equal(findImageDropSlot(elements, { x: 900, y: 150 }), null);
  assert.equal(findImageDropSlot(elements)?.id, 'empty', 'click insert uses the first empty frame');
});

test('overlapping template slots prefer the smallest foreground target', () => {
  const elements = [
    { id: 'background', type: 'image', src: null, frameSlot: true, x: 0, y: 0, w: 1000, h: 1500 },
    { id: 'card', type: 'image', src: null, frameSlot: true, x: 170, y: 440, w: 680, h: 700 },
  ];

  assert.equal(findImageDropSlot(elements, { x: 500, y: 700 })?.id, 'card');
  assert.equal(findImageDropSlot(elements, { x: 50, y: 50 })?.id, 'background');
});

test('detail callout drops target the circular photos above the background', () => {
  let sequence = 0;
  const block = buildFrameBlock('kiwi-15', (prefix) => `${prefix}${++sequence}`);

  assert.equal(findImageDropSlot(block.elements, { x: 270, y: 385 })?.w, 245);
  assert.equal(findImageDropSlot(block.elements, { x: 678, y: 908 })?.w, 315);
});

test('image frames show an exact placement guide for wardrobe and external file drags', () => {
  assert.match(editorSource, /types\.includes\('Files'\)/);
  assert.match(editorSource, /onDropImageFiles\?\.\(files\)/);
  assert.match(editorSource, /onDropImageFiles=\{\(files\) => onDropImageFiles\(block\.id, files, null, el\.id\)\}/);
  assert.match(editorSource, /imageDropOver && <ImageDropGuide scale=\{scale\} filled=\{false\} width=\{el\.w\} height=\{el\.h\} rotate=\{el\.rotate\}/);
  assert.match(editorSource, /이 프레임에 \{filled \? '교체' : '넣기'\}/);
  assert.match(stylesSource, /\.image-drop-guide\s*\{[^}]*pointer-events:\s*none/s);
  assert.match(stylesSource, /\.image-drop-guide\s*\{[^}]*background-image:\s*linear-gradient/s);
  assert.match(stylesSource, /\.image-drop-guide-content\s*\{[^}]*rotate\(var\(--drop-counter-rotate\)\) scale\(var\(--drop-inv/s);
  assert.match(stylesSource, /animation:\s*image-drop-target-pulse/);
});

test('empty template frames always label the exact place where a photo goes', () => {
  assert.match(editorSource, /aria-label="이 프레임에 사진 넣기"/);
  assert.match(editorSource, /<Icon name="imagePlus" size=\{compactSlot \? 22 : 28\}/);
  assert.match(editorSource, /!compactSlot && <span>여기에 사진 넣기<\/span>/);
  assert.match(stylesSource, /\.el-slot\.checkerboard\s*\{[^}]*background-image:\s*linear-gradient/s);
});

test('an image import placeholder immediately occupies the exact target frame', () => {
  const elements = [
    { id: 'empty', type: 'image', src: null, frameSlot: true, x: 360, y: 40, w: 300, h: 220, radius: 18 },
  ];

  assert.deepEqual(pendingImageImportTarget({
    elements,
    blockHeight: 300,
    point: { x: 500, y: 150 },
  }), { slotId: 'empty', x: 360, y: 40, w: 300, h: 220, radius: 18 });
});

test('an image import placeholder uses a stable portrait tile outside a frame', () => {
  assert.deepEqual(pendingImageImportTarget({
    elements: [],
    blockHeight: 300,
    point: { x: 500, y: 150 },
  }), { slotId: null, x: 412, y: 40, w: 176, h: 220, radius: 12 });
});

test('an explicit rotated frame remains the exact external upload target', () => {
  const elements = [
    { id: 'background', type: 'image', src: null, frameSlot: true, x: 0, y: 0, w: 1000, h: 1508 },
    { id: 'polaroid', type: 'image', src: null, frameSlot: true, x: 575, y: 550, w: 270, h: 365, radius: 2, rotate: -14 },
  ];

  assert.deepEqual(pendingImageImportTarget({
    elements,
    blockHeight: 1508,
    point: null,
    slotId: 'polaroid',
  }), { slotId: 'polaroid', x: 575, y: 550, w: 270, h: 365, radius: 2, rotate: -14 });
  assert.match(editorSource, /transform:\s*item\.rotate \? `rotate\(\$\{item\.rotate\}deg\)`/);
});

test('uploaded editor images wait for a renderable stable asset URL before showing success', () => {
  assert.match(httpAdapterSource, /url:\s*absolutizeAssetUrls\(`\/v1\/assets\/\$\{assetId\}\/file`\)/);
  assert.match(editorSource, /await waitForImageSource\(uploaded\.url\)/);
  assert.match(editorSource, /const slot = slotId[\s\S]{0,240}element\.id === slotId/);
});

test('quick toolbars stay non-interactive by default and remain open for the selected block', () => {
  const styles = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  const quickRule = styles.match(/\.canvas-block \.quick \{[^}]+\}/s)?.[0] || '';

  assert.match(quickRule, /pointer-events:\s*none/);
  assert.match(quickRule, /visibility:\s*hidden/);
  assert.match(styles, /\.canvas-block:hover \.quick, \.canvas-block\.on \.quick \{[^}]*visibility:\s*visible[^}]*pointer-events:\s*auto/s);
});

test('quick toolbar has a pointer bridge across its visual gap', () => {
  const styles = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  const quickRule = styles.match(/\.canvas-block \.quick \{[^}]+\}/s)?.[0] || '';
  const bridgeRule = styles.match(/\.canvas-block \.quick::before \{[^}]+\}/s)?.[0] || '';

  assert.match(quickRule, /--quick-gap:\s*14px/);
  assert.match(quickRule, /margin-left:\s*var\(--quick-gap\)/);
  assert.match(bridgeRule, /content:\s*['"]["']/);
  assert.match(bridgeRule, /right:\s*100%/);
  assert.match(bridgeRule, /width:\s*var\(--quick-gap\)/);
  assert.match(bridgeRule, /height:\s*100%/);
});

test('wardrobe image drags activate the same between-block insertion rows as frames', () => {
  assert.match(panelSource, /onImageDragStart/);
  assert.match(panelSource, /onImageDragEnd/);
  assert.equal(acceptsEditorBlockInsert([EDITOR_IMAGE_DRAG_TYPE]), true);
  assert.match(editorSource, /acceptsEditorBlockInsert\(e\.dataTransfer\.types\)[\s\S]{0,180}setFrameOver/);
  assert.match(editorSource, /buildImageBlock/);
});

test('the active between-block drop target is a full-width placement band', () => {
  assert.doesNotMatch(editorSource, /canvas-dropplus/);
  assert.doesNotMatch(stylesSource, /\.canvas-dropplus\s*\{/);
  assert.match(editorSource, /canvas-droprow[\s\S]{0,700}currentTarget\.contains\(e\.relatedTarget\)/);
});

test('the insertion guide matches the reference with a zoom-invariant blue drop band', () => {
  const activeLineRule = stylesSource.match(/\.canvas-dropline\.on\s*\{[^}]*\}/s)?.[0] || '';
  const lineRule = stylesSource.match(/\.canvas-dropline\s*\{[^}]*\}/s)?.[0] || '';
  assert.match(editorSource, /'--canvas-inv':\s*1\s*\/\s*\(scale\s*\|\|\s*1\)/);
  assert.match(lineRule, /height:\s*calc\(12px\s*\*\s*var\(--canvas-inv,\s*1\)\)/);
  assert.match(activeLineRule, /background:\s*rgba\(35,\s*131,\s*226,\s*\.1\)/);
  assert.match(activeLineRule, /box-shadow:\s*inset/);
  assert.match(activeLineRule, /animation:\s*none/);
  assert.match(stylesSource, /\.ed-canvas\.frame-dragging \.canvas-droprow\s*\{[^}]*height:\s*128px[^}]*margin:\s*-56px 0[^}]*padding:\s*56px 0/s);
  assert.doesNotMatch(stylesSource, /@keyframes canvas-drop-/);
});
