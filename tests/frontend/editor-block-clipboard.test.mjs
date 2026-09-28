import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { copyEditorBlock, pasteEditorBlock } from '../../src/features/editor/editorClipboard.js';

let n = 0;
const createId = (prefix) => `${prefix}_new${++n}`;
const blocks = [{
  id: 'b1', kind: 'styling', auto: true, info: { a: 1 }, infoType: 'x', contentRole: 'point', templateId: 't1',
  bg: '#fff', h: 1200, name: 'POINT 02',
  elements: [
    { id: 'e1', type: 'image', src: 'https://x/a.png', sourceBlockId: 'blk', frameSlot: true, x: 90, y: 100, w: 800, h: 1000, groupId: 'g1' },
    { id: 'e2', type: 'text', text: 'POINT 02', x: 90, y: 1120, w: 400, h: 40, groupId: 'g1' },
  ],
}];

test('프레임 복사·붙여넣기: 보이는 건 그대로, 아이디는 새로, 자동 관리 표식은 뗀다', () => {
  const copied = copyEditorBlock(blocks, 'b1');
  assert.equal(copied.kind, 'block');
  const pasted = pasteEditorBlock(copied.block, createId);
  assert.notEqual(pasted.id, 'b1');
  for (const key of ['auto', 'info', 'infoType', 'contentRole', 'templateId']) assert.ok(!(key in pasted), key);
  assert.equal(pasted.bg, '#fff'); assert.equal(pasted.h, 1200); assert.equal(pasted.name, 'POINT 02');
  assert.equal(pasted.elements.length, 2);
  assert.ok(pasted.elements.every((el) => !['e1', 'e2'].includes(el.id)));
  assert.equal(pasted.elements[0].groupId, pasted.elements[1].groupId);
  assert.notEqual(pasted.elements[0].groupId, 'g1');
  assert.equal(pasted.elements[0].src, 'https://x/a.png');
  assert.equal(pasted.elements[0].x, 90); assert.equal(pasted.elements[0].y, 100);
  assert.ok(!('sourceBlockId' in pasted.elements[0]));
  // 원본은 건드리지 않는다.
  assert.equal(blocks[0].elements[0].sourceBlockId, 'blk');
});

test('에디터: 요소 없이 프레임만 골랐을 때 Cmd+C 는 프레임을, 붙여넣기는 고른 프레임 아래에 넣는다', () => {
  const src = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.match(src, /if \(!selEls\.length && blockFocused && selBlock\) \{\s*const copiedBlock = copyEditorBlock/);
  assert.match(src, /if \(copied\.kind === 'block'\)/);
  assert.match(src, /contentRole: CONTENT_ROLES\.CUSTOM/);
});
