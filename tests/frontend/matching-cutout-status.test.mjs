import assert from 'node:assert/strict';
import test from 'node:test';
import { toMatchItem } from '../../src/lib/api/matchingItems.js';

test('toMatchItem 이 cutoutStatus 를 통과시킨다', () => {
  const item = toMatchItem({ id: 'custom_x', name: '내 바지', isCustom: true,
    cutoutStatus: 'processing' }, null);
  assert.equal(item.cutoutStatus, 'processing');
});

test('시드 아이템은 cutoutStatus 가 없다', () => {
  const item = toMatchItem({ id: 'match_women_top_01', name: '시드' }, null);
  assert.equal(item.cutoutStatus ?? null, null);
});
