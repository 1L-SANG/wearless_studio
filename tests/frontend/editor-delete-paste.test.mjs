import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { removeSelectedElements } from '../../src/features/editor/editorSelection.js';

const block = (elements) => ({ id: 'b1', kind: 'twocol', elements });
const contiEmpty = { id: 'e1', type: 'image', sourceBlockId: 'blk_cut', src: null, frameSlot: true };
const contiFilled = { id: 'e2', type: 'image', sourceBlockId: 'blk_cut', src: 'https://x/a.png' };

test('콘티 사진: 첫 Delete 는 비우고, 생성 중이 아니면 빈 자리는 요소째 지운다', () => {
  const [emptied] = removeSelectedElements([block([contiFilled])], ['e2'], { allowRemoveContiSlots: true });
  assert.equal(emptied.elements.length, 1);
  assert.equal(emptied.elements[0].src, null);
  const [removed] = removeSelectedElements([block([contiEmpty])], ['e1'], { allowRemoveContiSlots: true });
  assert.deepEqual(removed.elements, []);
});

test('생성 중(기본값)에는 빈 콘티 자리를 남긴다 — 완료 병합 안전', () => {
  const [kept] = removeSelectedElements([block([contiEmpty])], ['e1']);
  assert.equal(kept.elements.length, 1);
});

test('붙여넣기는 지금 고른 프레임을 먼저 찾는다(복사 원본 프레임은 마지막)', () => {
  const src = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const body = src.slice(src.indexOf('const pasteCopiedElements'), src.indexOf('const patchEl ='));
  const selectedAt = body.indexOf('block.id === selectedBlockId');
  const copiedAt = body.indexOf('block.id === copied.blockId');
  assert.ok(selectedAt > -1 && copiedAt > -1 && selectedAt < copiedAt, '복사 원본 프레임을 먼저 찾는다');
  assert.match(src, /removeSelectedElements\(bs, selEls, \{ allowRemoveContiSlots \}\)/);
  assert.match(src, /const allowRemoveContiSlots = !genActiveRef\.current/);
});
