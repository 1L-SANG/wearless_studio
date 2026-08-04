import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getPendingTileCount,
  PENDING_TILE_DELAY_MS,
} from '../../src/features/product-input/pendingTiles.js';

test('대기 타일은 선택한 파일 수만큼 만들되 남은 room을 넘지 않는다', () => {
  assert.equal(getPendingTileCount(2, 6), 2);
  assert.equal(getPendingTileCount(8, 6), 6);
  assert.equal(getPendingTileCount(3, 1), 1);
});

test('room이 없거나 잘못된 입력이면 대기 타일을 만들지 않는다', () => {
  assert.equal(getPendingTileCount(2, 0), 0);
  assert.equal(getPendingTileCount(2, -1), 0);
  assert.equal(getPendingTileCount(undefined, 3), 0);
});

test('빠른 변환의 깜빡임을 막는 유예 시간은 120ms다', () => {
  assert.equal(PENDING_TILE_DELAY_MS, 120);
});
