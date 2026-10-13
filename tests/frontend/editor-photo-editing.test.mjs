import test from 'node:test';
import assert from 'node:assert/strict';

const photo = await import('../../src/features/editor/editorPhotoEditing.js').catch(() => ({}));
const image = { id: 'photo', type: 'image', src: '/old.png', x: 80, y: 60, w: 400, h: 500,
  radius: 18, rotate: 5, opacity: 0.8, crop: { ox: 10, oy: 20, iw: 500, ih: 600 },
  userUploaded: true, wardrobeGroup: 'old', cutType: 'front' };

test('library replacement preserves the frame, resets crop and old photo metadata', () => {
  assert.equal(typeof photo.replaceEditorPhoto, 'function');
  const blocks = [{ id: 'a', h: 900, elements: [image] }];
  const next = photo.replaceEditorPhoto(blocks, 'photo', { src: '/new.png', cutType: 'back', wardrobeGroup: 'navy' });
  assert.deepEqual(next[0].elements[0], { id: 'photo', type: 'image', src: '/new.png',
    x: 80, y: 60, w: 400, h: 500, radius: 18, rotate: 5, opacity: 0.8, cutType: 'back', wardrobeGroup: 'navy' });
  assert.equal(blocks[0].elements[0], image);
});

test('replacement rejects locked, pending, missing and same-source photos without an undo mutation', () => {
  assert.equal(typeof photo.replaceEditorPhoto, 'function');
  for (const element of [image, { ...image, locked: true }, { ...image, genPending: 'live' }]) {
    const blocks = [{ id: 'a', elements: [element] }];
    const replacement = element === image ? { src: '/old.png' } : { src: '/new.png' };
    assert.equal(photo.replaceEditorPhoto(blocks, 'photo', replacement), blocks);
    assert.equal(photo.replaceEditorPhoto(blocks, 'missing', { src: '/new.png' }), blocks);
  }
});

test('replacement keeps a natural-height slot size and synchronizes the info form', () => {
  assert.equal(typeof photo.replaceEditorPhoto, 'function');
  const blocks = [{ id: 'a', kind: 'info', infoType: 'feature_icons', info: { items: [{ src: '/old.png', title: 'Pocket' }] },
    elements: [{ ...image, frameSlot: true, imageSizing: 'natural-height' }] }];
  const result = photo.replaceEditorPhoto(blocks, 'photo', { src: '/new.png', width: 1000, height: 2000 });
  assert.equal(result[0].elements[0].h, 500);
  assert.equal(result[0].info.items[0].src, '/new.png');
});

test('only a body move with a deliberate interior drop chooses another block', () => {
  assert.equal(typeof photo.photoMoveDestination, 'function');
  const options = { sourceBlockId: 'a', gesture: 'move', pointer: { x: 250, y: 480 },
    photoRect: { left: 100, top: 400, width: 400, height: 300 },
    blocks: [{ id: 'a', left: 100, top: 0, width: 600, height: 350 },
      { id: 'b', left: 100, top: 400, width: 600, height: 350 }] };
  assert.equal(photo.photoMoveDestination(options)?.id, 'b');
  assert.equal(photo.photoMoveDestination({ ...options, gesture: 'resize' }), null);
  assert.equal(photo.photoMoveDestination({ ...options, pointer: { x: 250, y: 410 } }), null);
  assert.equal(photo.photoMoveDestination({ ...options, photoRect: { left: 100, top: 100, width: 400, height: 300 } }), null);
});

test('photo transfer preserves appearance and target photos, and leaves the source block', () => {
  assert.equal(typeof photo.transferEditorPhoto, 'function');
  const target = { id: 'b', h: 300, elements: [{ id: 'other', type: 'image', src: '/keep.png' }] };
  const blocks = [{ id: 'a', h: 900, elements: [image] }, target];
  const result = photo.transferEditorPhoto(blocks, { elementId: 'photo', sourceBlockId: 'a', targetBlockId: 'b', x: 220, y: 80, emptyId: 'empty' });
  assert.equal(result.length, 2);
  assert.deepEqual(result[0].elements, []);
  assert.equal(result[1].elements[0], target.elements[0]);
  assert.deepEqual(result[1].elements[1], { ...image, x: 220, y: 80 });
  assert.equal(result[1].h, 630);
  assert.equal(blocks[0].elements[0], image);
});

test('moving a photo slot keeps an empty source slot and clears its info binding', () => {
  assert.equal(typeof photo.transferEditorPhoto, 'function');
  const blocks = [{ id: 'a', kind: 'info', infoType: 'feature_icons', info: { items: [{ src: '/old.png' }] },
    elements: [{ ...image, frameSlot: true, imageSizing: 'natural-height', imageFlowGroup: 'source-flow' }] }, { id: 'b', h: 700, elements: [] }];
  const next = photo.transferEditorPhoto(blocks, { elementId: 'photo', sourceBlockId: 'a', targetBlockId: 'b', x: 100, y: 30, emptyId: 'empty' });
  assert.equal(next[0].elements[0].id, 'empty');
  assert.equal(next[0].elements[0].src, null);
  assert.equal(next[0].info.items[0].src, null);
  assert.equal(next[1].elements[0].frameSlot, undefined);
  assert.equal(next[1].elements[0].imageFlowGroup, undefined);
  assert.deepEqual(next[1].elements[0].crop, image.crop);
});

test('transfer refuses cancelled generation, missing targets, locked photos and partial groups', () => {
  assert.equal(typeof photo.transferEditorPhoto, 'function');
  for (const element of [image, { ...image, locked: true }, { ...image, groupId: 'group' }, { ...image, genPending: 'live' }]) {
    const blocks = [{ id: 'a', elements: [element] }, { id: 'b', h: 300, elements: [] }];
    const opts = { elementId: 'photo', sourceBlockId: 'a', targetBlockId: 'b', x: 10, y: 20, emptyId: 'empty', generationActive: element === image };
    assert.equal(photo.transferEditorPhoto(blocks, opts), blocks);
    assert.equal(photo.transferEditorPhoto(blocks, { ...opts, targetBlockId: 'gone' }), blocks);
  }
});

test('replacing a loose photo added to a size block does not replace its diagram form value', () => {
  const blocks = [{ id: 'a', h: 900, elements: [image] }, {
    id: 'b', kind: 'size', info: { diagramSrc: '/diagram.png' }, h: 900,
    elements: [{ id: 'diagram', type: 'image', frameSlot: true, src: '/diagram.png', x: 0, y: 0, w: 200, h: 300 }],
  }];
  const moved = photo.transferEditorPhoto(blocks, { elementId: 'photo', sourceBlockId: 'a', targetBlockId: 'b', x: 220, y: 30 });
  const replaced = photo.replaceEditorPhoto(moved, 'photo', { src: '/replacement.png' });
  assert.equal(replaced[1].info.diagramSrc, '/diagram.png');
  assert.equal(replaced[1].elements.find(e => e.id === 'photo').src, '/replacement.png');
});
