import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';

import {
  FRAME_LIBRARY_ITEMS,
  OBJECT_LIBRARY_ITEMS,
  buildFrameBlock,
  buildImageBlock,
  buildObjectPreset,
  colorWithOpacity,
  decodeWardrobeImage,
  encodeWardrobeImage,
  normalizeHexColor,
  objectPresetInitialSelectionIds,
  upgradeLegacyKiwiTemplateBlocks,
} from '../../src/features/editor/editorLibrary.js';
import {
  isEditorDeleteKey,
  isEditorGrayWorkspaceTarget,
  normalizeEditorSelectionGroups,
  removeSelectedBlock,
  removeSelectedElements,
  selectableElementBelowBlankText,
  selectionIdsForElement,
  selectionIdsInsideMarquee,
  shouldClearEditorSelection,
  shouldPassGroupDragArea,
  shouldPreserveMultiSelectionOnPointerDown,
  shouldStartTextOnlyDrag,
} from '../../src/features/editor/editorSelection.js';

const seqId = () => { let n = 0; return (prefix) => `${prefix}${++n}`; };

test('the recommended frame catalog contains the six common layouts', () => {
  const ids = FRAME_LIBRARY_ITEMS.filter((item) => item.recommended).map((item) => item.id);
  assert.deepEqual(ids, ['single', 'split2', 'grid3', 'grid4', 'hero2', 'colorcmp']);
});

test('every frame builds replaceable image slots inside the 1000px canvas', () => {
  for (const frame of FRAME_LIBRARY_ITEMS) {
    const block = buildFrameBlock(frame.id, seqId());
    const imageSlots = block.elements.filter((element) => element.type === 'image');
    assert.equal(imageSlots.length, frame.slots.length, frame.id);
    assert.equal(block.bgOpacity, 1);
    for (const element of imageSlots) {
      assert.equal(element.type, 'image');
      if (element.exampleImage) assert.match(element.src, /^\//);
      else assert.equal(element.src, null);
      assert.equal(element.frameSlot, true);
      assert.ok(element.x >= 0 && element.y >= 0);
      assert.ok(element.x + element.w <= 1000, `${frame.id}: slot fits horizontally`);
      assert.ok(element.y + element.h <= block.h, `${frame.id}: slot fits vertically`);
    }
  }
});

test('kiwi templates rebuild the references with native editable elements', () => {
  const kiwiFrames = FRAME_LIBRARY_ITEMS.filter((item) => item.template);
  assert.deepEqual(kiwiFrames.map((item) => item.id), [
    'kiwi-1', 'kiwi-2', 'kiwi-3', 'kiwi-4', 'kiwi-5', 'kiwi-6', 'kiwi-7', 'kiwi-8', 'kiwi-9',
    'kiwi-10', 'kiwi-11', 'kiwi-12', 'kiwi-13', 'kiwi-14', 'kiwi-15',
    'kiwi-16', 'kiwi-17', 'kiwi-18', 'kiwi-19', 'kiwi-20',
  ]);
  assert.equal(kiwiFrames.reduce((count, frame) => count + frame.slots.length, 0), 36);
  const expectedExampleCounts = {
    'kiwi-1': 4, 'kiwi-2': 4, 'kiwi-3': 2, 'kiwi-4': 3, 'kiwi-5': 4,
    'kiwi-6': 3, 'kiwi-7': 1, 'kiwi-8': 1, 'kiwi-9': 0,
    'kiwi-10': 2, 'kiwi-11': 0, 'kiwi-12': 2, 'kiwi-13': 1, 'kiwi-14': 4, 'kiwi-15': 2,
    'kiwi-16': 0, 'kiwi-17': 0, 'kiwi-18': 0, 'kiwi-19': 0, 'kiwi-20': 0,
  };

  for (const frame of kiwiFrames) {
    const block = buildFrameBlock(frame, seqId());
    const imageSlots = block.elements.filter((element) => element.type === 'image');
    const overlays = block.elements.filter((element) => element.type === 'template-overlay');
    const editableCopy = block.elements.filter((element) => element.type === 'text');
    assert.equal(overlays.length, 0, `${frame.id}: reference art must not be inserted`);
    assert.ok(block.elements.every((element) => ['image', 'text', 'shape', 'line'].includes(element.type)), frame.id);
    assert.ok(editableCopy.length >= 2, `${frame.id}: copy is editable text`);
    assert.ok(block.elements.every((element) => !element.locked && !element.system), frame.id);
    assert.ok(imageSlots.every((element) => element.checkerboard), frame.id);
    assert.ok(editableCopy.every((element) => element.fullTextHitArea), `${frame.id}: template copy owns its click box`);
    assert.equal(frame.preview, `/assets/editor/kiwi-templates/${frame.id}-preview.jpg`, `${frame.id}: catalog uses its completed JPEG reference`);
    assert.ok(existsSync(new URL(`../../public${frame.preview}`, import.meta.url)), `${frame.id}: catalog preview exists`);
    const examples = imageSlots.filter((element) => element.exampleImage);
    assert.equal(examples.length, expectedExampleCounts[frame.id], `${frame.id}: every available JPEG photo is prefilled`);
    const expectedExampleId = frame.id.replace('kiwi-', '');
    for (const example of examples) {
      assert.match(example.src, new RegExp(`/kiwi-${expectedExampleId}(?:-|\\.)`));
      assert.ok(existsSync(new URL(`../../public${example.src}`, import.meta.url)), `${frame.id}: ${example.src} exists`);
    }
  }
});

test('detail callout circles are native replaceable image slots', () => {
  const block = buildFrameBlock('kiwi-15', seqId());
  const circles = block.elements.filter((element) => (
    element.type === 'image' && element.frameSlot && element.radius === Math.round(element.w / 2)
  ));

  assert.equal(circles.length, 2);
  assert.equal(circles.filter((element) => element.exampleImage).length, 2);
  assert.match(circles[0].src, /kiwi-15\.jpg$/);
  assert.match(circles[1].src, /kiwi-15-2\.jpg$/);
  assert.ok(circles.every((element) => element.stroke === '#ffffff'));
  assert.ok(circles.every((element) => element.strokeWidth === 5 && element.dash === 'dashed'));
});

test('legacy locked Kiwi artwork upgrades to editable elements and keeps filled photos', () => {
  const legacy = [{
    id: 'legacy-block',
    name: '리뷰 카드',
    h: 1460,
    elements: [
      { id: 'photo-a', type: 'image', frameSlot: true, src: '/uploads/a.webp', cutType: 'full', w: 100, h: 100, crop: { ox: 10, oy: 20, iw: 120, ih: 140 } },
      { id: 'photo-b', type: 'image', frameSlot: true, src: null },
      { id: 'overlay', type: 'template-overlay', src: '/assets/editor/kiwi-templates/kiwi-1-overlay.png', locked: true, system: true },
    ],
  }];

  const [upgraded] = upgradeLegacyKiwiTemplateBlocks(legacy, seqId());
  const photoSlots = upgraded.elements.filter((element) => element.type === 'image');
  assert.equal(upgraded.id, 'legacy-block');
  assert.equal(upgraded.templateId, 'kiwi-1');
  assert.equal(upgraded.elements.some((element) => element.type === 'template-overlay'), false);
  assert.ok(upgraded.elements.filter((element) => element.type === 'text').length >= 2);
  assert.equal(photoSlots[0].src, '/uploads/a.webp');
  assert.equal(photoSlots[0].cutType, 'full');
  assert.deepEqual(photoSlots[0].crop, { ox: 13, oy: 25, iw: 151, ih: 176 });
  assert.equal(photoSlots[1].src, null);
});

test('a wardrobe image dropped between blocks becomes its own padded image block', () => {
  const block = buildImageBlock({
    id: 'wardrobe-1',
    src: '/portrait.webp',
    cutType: 'full',
    width: 1200,
    height: 1800,
    userUploaded: true,
    wardrobeGroup: 'misc',
  }, seqId());

  assert.equal(block.name, '이미지');
  assert.equal(block.bg, '#ffffff');
  assert.equal(block.bgOpacity, 1);
  assert.equal(block.h, 1420);
  assert.equal(block.elements.length, 1);
  assert.deepEqual(block.elements[0], {
    id: 'el2',
    type: 'image',
    x: 60,
    y: 50,
    w: 880,
    h: 1320,
    src: '/portrait.webp',
    radius: 0,
    cutType: 'full',
    userUploaded: true,
    wardrobeGroup: 'misc',
  });
});

test('object presets materialize as one selectable group', () => {
  for (const item of OBJECT_LIBRARY_ITEMS.filter((candidate) => candidate.id !== 'qa-bubbles')) {
    const elements = buildObjectPreset(item.id, { x: 100, y: 80, idFn: seqId() });
    assert.ok(elements.length > 0, item.id);
    assert.equal(new Set(elements.map((element) => element.groupId)).size, 1, `${item.id}: one group`);
    assert.ok(elements.every((element) => ['text', 'shape', 'line'].includes(element.type)));
    assert.ok(elements.every((element) => element.libraryItemId === item.id));
    for (const element of elements) {
      assert.deepEqual(selectionIdsForElement(elements, element), elements.map((candidate) => candidate.id), `${item.id}: ${element.type}`);
    }
  }
});

test('text inside an object preset selects its background or line with it', () => {
  for (const itemId of ['text-box', 'arrow-callout', 'label-badge']) {
    const elements = buildObjectPreset(itemId, { x: 100, y: 80, idFn: seqId() });
    const copy = elements.find((element) => element.type === 'text');
    assert.ok(copy, `${itemId}: text layer`);
    assert.deepEqual(selectionIdsForElement(elements, copy), elements.map((element) => element.id));
  }
});

test('only standalone text starts a text-only drag', () => {
  assert.equal(shouldStartTextOnlyDrag({ type: 'text' }, false), true);
  assert.equal(shouldStartTextOnlyDrag({ type: 'text', groupId: 'object' }, false), true);
  assert.equal(shouldStartTextOnlyDrag({ type: 'text', groupId: 'object', libraryItemId: 'text-box' }, false), false);
  assert.equal(shouldStartTextOnlyDrag({ type: 'text', shape: 'bubble' }, false), false);
  assert.equal(shouldStartTextOnlyDrag({ type: 'shape' }, false), false);
  assert.equal(shouldStartTextOnlyDrag({ type: 'text' }, true), false, 'shift remains additive selection');
});

test('object presets stay inside the 1000px canvas when dropped near an edge', () => {
  const elements = buildObjectPreset('qa-bubbles', { x: 920, y: -30, idFn: seqId() });
  assert.ok(elements.every((element) => element.x >= 0 && element.x + element.w <= 1000));
  assert.ok(elements.every((element) => element.y >= 0));
});

test('Q&A bubbles are two independent unified text+bubble objects', () => {
  const elements = buildObjectPreset('qa-bubbles', { x: 100, y: 80, idFn: seqId() });
  const bubbles = elements.filter((element) => element.type === 'text' && element.shape === 'bubble');
  assert.equal(bubbles.length, 2);
  assert.ok(bubbles.every((element) => element.radius === 45), 'new bubbles use the near-pill 45px radius');
  assert.equal(elements.length, 2);
  assert.ok(bubbles.every((element) => element.stroke === '#b9b9be' && element.strokeWidth === 1));
  assert.equal(bubbles[0].flipX, undefined);
  assert.equal(bubbles[1].flipX, true);
  assert.ok(bubbles.every((element) => element.text && element.style && element.bubbleFit));
  assert.ok(bubbles.every((element) => !element.bubblePairId));
  assert.equal(new Set(bubbles.map((element) => element.groupId)).size, 2);
  assert.deepEqual(selectionIdsForElement(elements, bubbles[0]), [bubbles[0].id]);
  assert.deepEqual(selectionIdsForElement(elements, bubbles[1]), [bubbles[1].id]);
  assert.deepEqual(objectPresetInitialSelectionIds('qa-bubbles', elements), [bubbles[0].id]);
});

test('ordinary composite presets still select every member when they are added', () => {
  const elements = buildObjectPreset('text-box', { x: 100, y: 80, idFn: seqId() });
  assert.deepEqual(objectPresetInitialSelectionIds('text-box', elements), elements.map((element) => element.id));
});

test('the object library offers one standalone responsive speech bubble', () => {
  const item = OBJECT_LIBRARY_ITEMS.find((candidate) => candidate.id === 'single-bubble');
  assert.deepEqual(item, { id: 'single-bubble', label: '말풍선', preview: '말풍선' });

  const elements = buildObjectPreset('single-bubble', { x: 100, y: 80, idFn: seqId() });
  assert.equal(elements.length, 1);
  assert.equal(elements[0].type, 'text');
  assert.equal(elements[0].shape, 'bubble');
  assert.equal(elements[0].text, '내용을 입력하세요');
  assert.equal(elements[0].fill, '#FFFFFF');
  assert.equal(elements[0].style.color, '#000000');
  assert.equal(elements[0].stroke, '#000000');
  assert.equal(elements[0].strokeWidth, 2);
  assert.equal(elements[0].radius, 28);
  assert.ok(elements[0].bubbleFit);
  assert.deepEqual(selectionIdsForElement(elements, elements[0]), [elements[0].id]);
});

test('a selected unified speech bubble keeps Moveable drag capture enabled', () => {
  const bubbles = buildObjectPreset('qa-bubbles', { x: 100, y: 80, idFn: seqId() });
  const textBox = buildObjectPreset('text-box', { x: 100, y: 80, idFn: seqId() });

  assert.equal(shouldPassGroupDragArea([bubbles[0]]), false, 'the drag area must capture one Q&A bubble drag');
  assert.equal(shouldPassGroupDragArea(textBox), true, 'other composite children remain directly selectable');
});

test('marquee selection includes intersecting elements, expands groups, and skips locked layers', () => {
  const elements = [
    { id: 'free', type: 'shape' },
    { id: 'group-a', type: 'text', shape: 'bubble', groupId: 'group' },
    { id: 'group-b', type: 'text', shape: 'bubble', groupId: 'group' },
    { id: 'partial', type: 'shape' },
    { id: 'locked', type: 'shape', locked: true },
    { id: 'hidden', type: 'shape', hidden: true },
  ];
  const rects = new Map([
    ['free', { left: 20, top: 20, right: 40, bottom: 40 }],
    ['group-a', { left: 50, top: 50, right: 70, bottom: 70 }],
    ['group-b', { left: 140, top: 140, right: 160, bottom: 160 }],
    ['partial', { left: 90, top: 90, right: 120, bottom: 120 }],
    ['locked', { left: 30, top: 30, right: 50, bottom: 50 }],
    ['hidden', { left: 35, top: 35, right: 55, bottom: 55 }],
  ]);

  assert.deepEqual(selectionIdsInsideMarquee(elements, rects, {
    left: 10, top: 10, right: 100, bottom: 100,
  }), ['free', 'group-a', 'group-b', 'partial']);
});

test('marquee selection reacts symmetrically when entering an element from either side', () => {
  const elements = [{ id: 'target', type: 'shape' }];
  const rects = new Map([
    ['target', { left: 100, top: 100, right: 200, bottom: 200 }],
  ]);

  assert.deepEqual(selectionIdsInsideMarquee(elements, rects, {
    left: 90, top: 120, right: 101, bottom: 180,
  }), ['target']);
  assert.deepEqual(selectionIdsInsideMarquee(elements, rects, {
    left: 199, top: 120, right: 210, bottom: 180,
  }), ['target']);
});

test('Delete and macOS Backspace remove every selected element while preserving other blocks', () => {
  assert.equal(isEditorDeleteKey({ key: 'Delete', target: { tagName: 'DIV', isContentEditable: false } }), true);
  assert.equal(isEditorDeleteKey({ key: 'Backspace', target: { tagName: 'DIV', isContentEditable: false } }), true);
  assert.equal(isEditorDeleteKey({ key: 'Backspace', target: { tagName: 'INPUT', isContentEditable: false } }), false);

  const blocks = [
    { id: 'b1', elements: [{ id: 'shape' }, { id: 'text' }, { id: 'keep' }] },
    { id: 'b2', elements: [{ id: 'other' }] },
  ];
  assert.deepEqual(removeSelectedElements(blocks, ['shape', 'text']), [
    { id: 'b1', elements: [{ id: 'keep' }] },
    { id: 'b2', elements: [{ id: 'other' }] },
  ]);
});

test('Delete and macOS Backspace can remove a selected top-level block with all of its slots', () => {
  const blocks = [
    { id: 'gallery', elements: [{ id: 'slot-1' }, { id: 'slot-2' }, { id: 'slot-3' }] },
    { id: 'keep', elements: [{ id: 'copy' }] },
  ];

  assert.deepEqual(removeSelectedBlock(blocks, 'gallery'), [
    { id: 'keep', elements: [{ id: 'copy' }] },
  ]);
  assert.equal(removeSelectedBlock(blocks, null), blocks);
});

test('legacy object composites recover whole-object selection while FAQ text stays independently draggable', () => {
  const legacyObject = { id: 'object', elements: [
    { id: 'box', type: 'shape', libraryItemId: 'text-box', x: 10, y: 10, w: 200, h: 100 },
    { id: 'copy', type: 'text', libraryItemId: 'text-box', x: 20, y: 30, w: 180, h: 40 },
  ] };
  const legacyFaq = { id: 'faq', infoType: 'faq', elements: [
    { id: 'bubble', type: 'shape', shape: 'rect', x: 10, y: 10, w: 300, h: 80 },
    { id: 'answer', type: 'text', x: 30, y: 30, w: 260, h: 30 },
  ] };
  const normalized = normalizeEditorSelectionGroups([legacyObject, legacyFaq]);
  assert.deepEqual(selectionIdsForElement(normalized[0].elements, normalized[0].elements[1]), ['box', 'copy']);
  assert.deepEqual(selectionIdsForElement(normalized[0].elements, normalized[0].elements[0]), ['box', 'copy']);
  assert.deepEqual(selectionIdsForElement(normalized[1].elements, normalized[1].elements[1]), ['answer']);
  assert.deepEqual(selectionIdsForElement(normalized[1].elements, normalized[1].elements[0]), ['bubble']);
  assert.equal(normalized[0].elements[0].groupId, normalized[0].elements[1].groupId);
  assert.equal(normalized[1].elements[0].groupId, normalized[1].elements[1].groupId);
});

test('saved Q&A bubbles that shared one group migrate into independent objects', () => {
  const elements = buildObjectPreset('qa-bubbles', { x: 100, y: 80, idFn: seqId() })
    .map((element) => ({ ...element, groupId: 'legacy-shared-qa' }));
  const [normalized] = normalizeEditorSelectionGroups([{ id: 'qa-block', elements }]);

  assert.equal(new Set(normalized.elements.map((element) => element.groupId)).size, 2);
  assert.deepEqual(selectionIdsForElement(normalized.elements, normalized.elements[0]), [normalized.elements[0].id]);
  assert.deepEqual(selectionIdsForElement(normalized.elements, normalized.elements[1]), [normalized.elements[1].id]);
});

test('legacy speech-bubble layers normalize into one selectable and deletable element', () => {
  const legacy = { id: 'faq', infoType: 'faq', elements: [
    { id: 'bubble', type: 'shape', shape: 'bubble', bubblePairId: 'pair', x: 10, y: 10, w: 300, h: 90, fill: '#fff' },
    { id: 'copy', type: 'text', bubblePairId: 'pair', x: 30, y: 30, w: 260, h: 30, text: '질문', style: { size: 20 } },
  ] };
  const [normalized] = normalizeEditorSelectionGroups([legacy]);
  assert.equal(normalized.elements.length, 1);
  assert.equal(normalized.elements[0].id, 'bubble');
  assert.equal(normalized.elements[0].type, 'text');
  assert.equal(normalized.elements[0].shape, 'bubble');
  assert.equal(normalized.elements[0].text, '질문');
  assert.deepEqual(removeSelectedElements([normalized], ['bubble'])[0].elements, []);
});

test('canvas click-away never clears a selection for an element, block, or Moveable control', () => {
  const target = (match) => ({ closest: (selector) => selector.split(', ').includes(match) ? {} : null });
  assert.equal(shouldClearEditorSelection(target('[data-elid]')), false);
  assert.equal(shouldClearEditorSelection(target('.canvas-block')), false);
  assert.equal(shouldClearEditorSelection(target('.moveable-control-box')), false);
  assert.equal(shouldClearEditorSelection({ closest: () => null }), true);
});

test('Moveable controls are canvas content, not gray workspace that reveals zoom controls', () => {
  const target = (match) => ({ closest: (selector) => selector.includes(match) ? {} : null });

  assert.equal(isEditorGrayWorkspaceTarget(target('.canvas-block')), false);
  assert.equal(isEditorGrayWorkspaceTarget(target('.moveable-control-box')), false);
  assert.equal(isEditorGrayWorkspaceTarget({ closest: () => null }), true);
  assert.equal(isEditorGrayWorkspaceTarget(null), false);
});

test('pressing an already selected child preserves a multi-selection for group dragging', () => {
  assert.equal(shouldPreserveMultiSelectionOnPointerDown({ selected: true, selectionCount: 4, additive: false }), true);
  assert.equal(shouldPreserveMultiSelectionOnPointerDown({ selected: true, selectionCount: 1, additive: false }), false);
  assert.equal(shouldPreserveMultiSelectionOnPointerDown({ selected: false, selectionCount: 4, additive: false }), false);
  assert.equal(shouldPreserveMultiSelectionOnPointerDown({ selected: true, selectionCount: 4, additive: true }), false);
});

test('blank space in a wide text box yields to the first visible element underneath', () => {
  const elements = [
    { id: 'image', type: 'image', src: '/product.png' },
    { id: 'rule', type: 'line', shape: 'line' },
    { id: 'blank-copy', type: 'text', text: '짧은 문구' },
  ];
  assert.equal(selectableElementBelowBlankText(elements, 'blank-copy', ['blank-copy', 'rule', 'image'], []).id, 'rule');
  assert.equal(selectableElementBelowBlankText(elements, 'blank-copy', ['blank-copy', 'image'], []).id, 'image');
});

test('click-through skips another normal text box unless its rendered glyph line is hit', () => {
  const elements = [
    { id: 'image', type: 'image', src: '/product.png' },
    { id: 'object-copy', type: 'text', text: '완성형 오브젝트', groupId: 'object', libraryItemId: 'text-box' },
    { id: 'faq-copy', type: 'text', text: '독립 FAQ 문구', groupId: 'faq-card' },
    { id: 'other-copy', type: 'text', text: '다른 문구' },
    { id: 'current-copy', type: 'text', text: '현재 문구' },
  ];
  assert.equal(selectableElementBelowBlankText(elements, 'current-copy', ['current-copy', 'other-copy', 'image'], []).id, 'image');
  assert.equal(selectableElementBelowBlankText(elements, 'current-copy', ['current-copy', 'other-copy', 'image'], ['other-copy']).id, 'other-copy');
  assert.equal(selectableElementBelowBlankText(elements, 'current-copy', ['current-copy', 'faq-copy', 'image'], []).id, 'image');
  assert.equal(selectableElementBelowBlankText(elements, 'current-copy', ['current-copy', 'object-copy', 'image'], []).id, 'object-copy');
  assert.equal(selectableElementBelowBlankText([
    { id: 'image', type: 'image', src: '/product.png' },
    { id: 'template-copy', type: 'text', text: '템플릿 문구', fullTextHitArea: true },
    { id: 'current-copy', type: 'text', text: '현재 문구' },
  ], 'current-copy', ['current-copy', 'template-copy', 'image'], []).id, 'template-copy');
  assert.equal(selectableElementBelowBlankText([{ id: 'locked', type: 'line', locked: true }], 'copy', ['locked'], []), null);
});

test('background opacity changes only the rendered color alpha', () => {
  assert.equal(colorWithOpacity('#0e0d14', 0.92), 'rgba(14, 13, 20, 0.92)');
  assert.equal(colorWithOpacity('#fff', 0.4), 'rgba(255, 255, 255, 0.4)');
  assert.equal(colorWithOpacity('#ffffff', 1), '#ffffff');
});

test('hex values accept Photoshop-style shorthand and reject invalid input', () => {
  assert.equal(normalizeHexColor('fff'), '#FFFFFF');
  assert.equal(normalizeHexColor('#0e0d14'), '#0E0D14');
  assert.equal(normalizeHexColor(' 4f8 '), '#44FF88');
  assert.equal(normalizeHexColor('#12zz90'), null);
});

test('wardrobe drag payload accepts valid images and rejects malformed values', () => {
  const encoded = encodeWardrobeImage({ src: '/garment.webp', cutType: 'product' });
  assert.deepEqual(decodeWardrobeImage(encoded), { src: '/garment.webp', cutType: 'product' });
  const uploaded = encodeWardrobeImage({ src: '/mine.webp', userUploaded: true, wardrobeGroup: 'misc' });
  assert.deepEqual(decodeWardrobeImage(uploaded), {
    src: '/mine.webp',
    cutType: null,
    userUploaded: true,
    wardrobeGroup: 'misc',
  });
  assert.equal(decodeWardrobeImage('{oops'), null);
  assert.equal(decodeWardrobeImage(JSON.stringify({ src: '' })), null);
});
