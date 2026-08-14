import assert from 'node:assert/strict';
import test from 'node:test';

import { copyEditorElements, pasteEditorElements } from '../../src/features/editor/editorClipboard.js';

const ids = () => {
  let value = 0;
  return (prefix) => `${prefix}-${++value}`;
};

test('copies selected elements from their owning block without sharing nested state', () => {
  const blocks = [{ id: 'block-1', elements: [
    { id: 'image', type: 'image', x: 10, y: 20, crop: { ox: 4 } },
    { id: 'text', type: 'text', x: 80, y: 90 },
  ] }];
  const copied = copyEditorElements(blocks, ['image']);

  assert.equal(copied.blockId, 'block-1');
  assert.deepEqual(copied.elements.map((element) => element.id), ['image']);
  copied.elements[0].crop.ox = 99;
  assert.equal(blocks[0].elements[0].crop.ox, 4);
});

test('pastes a grouped object with fresh element and relationship ids', () => {
  const copied = [
    { id: 'bubble', type: 'shape', groupId: 'group-old', bubblePairId: 'pair-old', libraryItemId: 'qa', x: 100, y: 120, w: 180, h: 80 },
    { id: 'copy', type: 'text', groupId: 'group-old', bubblePairId: 'pair-old', libraryItemId: 'qa', x: 130, y: 145, w: 100, h: 30 },
  ];
  const pasted = pasteEditorElements({ id: 'block-1', h: 800 }, copied, ids());

  assert.deepEqual(pasted.offset, [24, 24]);
  assert.deepEqual(pasted.elements.map(({ x, y }) => [x, y]), [[124, 144], [154, 169]]);
  assert.equal(pasted.elements[0].groupId, pasted.elements[1].groupId);
  assert.notEqual(pasted.elements[0].groupId, 'group-old');
  assert.equal(pasted.elements[0].bubblePairId, pasted.elements[1].bubblePairId);
  assert.notEqual(pasted.elements[0].bubblePairId, 'pair-old');
  assert.equal(pasted.elements[0].libraryItemId, 'qa');
  assert.deepEqual(pasted.selectedIds, pasted.elements.map((element) => element.id));
  assert.ok(pasted.elements.every((element) => !['bubble', 'copy'].includes(element.id)));
});

test('moves a pasted selection inward when the usual down-right offset would overflow', () => {
  const copied = [{ id: 'edge', type: 'image', x: 880, y: 680, w: 120, h: 120 }];
  const pasted = pasteEditorElements({ id: 'block-1', h: 800 }, copied, ids());

  assert.deepEqual(pasted.offset, [-24, -24]);
  assert.deepEqual([pasted.elements[0].x, pasted.elements[0].y], [856, 656]);
});

test('can cascade repeated pastes from the previously pasted copy', () => {
  const createId = ids();
  const first = pasteEditorElements({ id: 'block-1', h: 800 }, [{ id: 'a', x: 10, y: 20, w: 50, h: 50 }], createId);
  const second = pasteEditorElements({ id: 'block-1', h: 800 }, first.elements, createId);

  assert.deepEqual([first.elements[0].x, first.elements[0].y], [34, 44]);
  assert.deepEqual([second.elements[0].x, second.elements[0].y], [58, 68]);
  assert.notEqual(second.elements[0].id, first.elements[0].id);
});

test('returns null for empty clipboard operations', () => {
  assert.equal(copyEditorElements([], ['missing']), null);
  assert.equal(pasteEditorElements({ id: 'block-1', h: 800 }, [], ids()), null);
});
