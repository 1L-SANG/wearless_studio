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
  fitImageToFrameBlock,
  fitImageToFrameSlot,
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

test('the two-column frame keeps its width and derives the exact portrait height from the source', () => {
  let sequence = 0;
  const block = buildFrameBlock('split2', (prefix) => `${prefix}${++sequence}`);
  const [slot] = block.elements.filter((element) => element.type === 'image');

  assert.equal(slot.w, 450);
  assert.deepEqual(fitImageToFrameSlot(slot, { width: 900, height: 1460 }), { h: 730 });
  assert.deepEqual(fitImageToFrameSlot({ ...slot, imageSizing: undefined }, { width: 900, height: 1460 }), {});
});

test('the image-description frame grows each photo box to its source ratio and moves its own copy below it', () => {
  let sequence = 0;
  const block = buildFrameBlock('image-description-3', (prefix) => `${prefix}${++sequence}`);
  const slots = block.elements.filter((element) => element.type === 'image');
  const firstSlot = slots[0];
  const firstCopy = block.elements.filter((element) => element.imageFlowGroup === firstSlot.imageFlowGroup && element.type === 'text');
  const otherCopy = block.elements.filter((element) => element.imageFlowGroup === slots[1].imageFlowGroup && element.type === 'text');

  const fitted = fitImageToFrameBlock(block, firstSlot.id, { width: 900, height: 1500 });
  const fittedSlot = fitted.elements.find((element) => element.id === firstSlot.id);
  const fittedFirstCopy = fitted.elements.filter((element) => element.imageFlowGroup === firstSlot.imageFlowGroup && element.type === 'text');
  const fittedOtherCopy = fitted.elements.filter((element) => element.imageFlowGroup === slots[1].imageFlowGroup && element.type === 'text');

  assert.equal(fittedSlot.h, 450);
  assert.deepEqual(fittedFirstCopy.map((element) => element.y), [645, 688]);
  assert.deepEqual(fittedOtherCopy.map((element) => element.y), otherCopy.map((element) => element.y));
  assert.deepEqual(firstCopy.map((element) => element.y), [415, 458]);
});

test('a multi-row blank frame keeps the next row below the tallest resized photo', () => {
  let sequence = 0;
  const block = buildFrameBlock('grid4', (prefix) => `${prefix}${++sequence}`);
  const slots = block.elements.filter((element) => element.type === 'image');

  const fitted = fitImageToFrameBlock(block, slots[0].id, { width: 450, height: 900 });
  const fittedSlots = fitted.elements.filter((element) => element.type === 'image');

  assert.equal(fittedSlots[0].h, 900);
  assert.deepEqual(fittedSlots.slice(0, 2).map((element) => element.y), [40, 40]);
  assert.deepEqual(fittedSlots.slice(2).map((element) => element.y), [960, 960]);
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
  assert.match(editorSource, /imageDropOver && <ImageDropGuide scale=\{scale\} width=\{el\.w\} height=\{el\.h\} rotate=\{el\.rotate\}/);
  assert.doesNotMatch(editorSource, /이 프레임에 \{filled \? '교체' : '넣기'\}/);
  assert.doesNotMatch(editorSource, /여기에 사진이 들어가요/);
  assert.match(stylesSource, /\.image-drop-guide\s*\{[^}]*pointer-events:\s*none/s);
  assert.match(stylesSource, /\.image-drop-guide\s*\{[^}]*background-image:\s*linear-gradient/s);
  assert.match(stylesSource, /\.image-drop-guide-content\s*\{[^}]*rotate\(var\(--drop-counter-rotate\)\) scale\(var\(--drop-inv/s);
  assert.match(stylesSource, /animation:\s*image-drop-target-pulse/);
});

test('frame images show the full source and a wardrobe click fills the pending slot immediately', () => {
  assert.match(editorSource, /objectFit: el\.fit \|\| 'cover'/);
  assert.match(editorSource, /return fitImageToFrameBlock\(nextBlock, elId, image\)/);
  assert.match(editorSource, /const requestSlotImage = \(blockId, el\) => \{[\s\S]*selectEl\(blockId, el, false, true\);[\s\S]*setPendingSlot\(\{ blockId, elId: el\.id \}\);[\s\S]*setTab\('wardrobe'\);[\s\S]*\}/);
  assert.match(editorSource, /if \(pendingSlot\) \{[\s\S]*setSlotImage\(pendingSlot\.blockId, pendingSlot\.elId,[\s\S]*setPendingSlot\(null\);[\s\S]*setTab\('image'\);[\s\S]*return;/);
  assert.match(panelSource, /onClick=\{\(e\) => \{ const image = e\.currentTarget\.querySelector\('img'\); onInsert\(\{ \.\.\.im, width: image\?\.naturalWidth \|\| im\.width, height: image\?\.naturalHeight \|\| im\.height \}\); \}\}/);
});

test('pending frame placement clearly invites one-click selection in the wardrobe', () => {
  assert.match(panelSource, /프레임에 넣을 사진을 선택하세요/);
  assert.match(panelSource, /아래 사진을 한 번 누르면 바로 들어가요\./);
  assert.match(panelSource, /pendingSlot \? ' select-target' : ''/);
  assert.match(panelSource, /pendingSlot && <span className="ward-pick-check" aria-hidden="true"><Icon name="check" size=\{15\} \/><\/span>/);
  assert.match(stylesSource, /\.ward-cell\.select-target:hover\s*\{[^}]*box-shadow:\s*0 0 0 2px var\(--link\)/s);
  assert.match(stylesSource, /\.ward-pick-check\s*\{[^}]*opacity:\s*0[^}]*pointer-events:\s*none/s);
  assert.match(stylesSource, /\.ward-cell\.select-target:hover \.ward-pick-check,[\s\S]*opacity:\s*1/);
});

test('pending frame placement is cancelled when the user selects something else', () => {
  assert.match(editorSource, /if \(pendingSlot && tab !== 'wardrobe'\) setPendingSlot\(null\)/);
  assert.match(editorSource, /const selectEl = \(blockId, el, additive, keepTab\) => \{[\s\S]*setPendingSlot\(null\);/);
  assert.match(editorSource, /const clearSel = \(\) => \{[^}]*setPendingSlot\(null\);[^}]*\}/);
});

test('empty template frames always label the exact place where a photo goes', () => {
  assert.match(editorSource, /aria-label="이 프레임에 사진 넣기"/);
  assert.match(editorSource, /<Icon name=\{el\.genFailed \? 'alertTri' : 'imagePlus'\} size=\{compactSlot \? 22 : 28\}/);
  assert.match(editorSource, /<span>여기에 사진 넣기<\/span>/);
  // 못 만든 컷은 같은 자리를 쓰되 빈 슬롯과 구분돼야 한다 — 이유·미차감·다음 행동까지.
  assert.match(editorSource, /el\.genFailed\s*\n?\s*\? <span>이 컷은 만들지 못했어요/);
  assert.match(editorSource, /크레딧 미차감 · 눌러서 사진을 넣거나 AI 탭에서 다시 만들 수 있어요/);
  assert.match(editorSource, /onPointerDown=\{\(e\) => e\.stopPropagation\(\)\}/);
  assert.match(editorSource, /const requestSlotImage = \(blockId, el\) => \{[\s\S]*selectEl\(blockId, el, false, true\);[\s\S]*setPendingSlot\(\{ blockId, elId: el\.id \}\);[\s\S]*setTab\('wardrobe'\);[\s\S]*\}/);
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

test('an image import placeholder preserves a square blank-frame corner', () => {
  const elements = [
    { id: 'empty', type: 'image', src: null, frameSlot: true, x: 40, y: 40, w: 300, h: 220, radius: 0 },
  ];

  assert.deepEqual(pendingImageImportTarget({
    elements,
    blockHeight: 300,
    point: { x: 100, y: 100 },
  }), { slotId: 'empty', x: 40, y: 40, w: 300, h: 220, radius: 0 });
  assert.match(editorSource, /borderRadius:\s*item\.radius \?\? 12/);
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
