/* 콘티 캔버스 렌더 단위 — "몇 개를 한 덩어리로 그리는가"의 계약.
   2026-08-16 오너: 후킹 네 컷 구성은 사진 넷이 붙은 2×2 한 덩어리로 보여야 한다. */
import test from 'node:test';
import assert from 'node:assert/strict';

import { frameUnits } from '../../src/features/storyboard/storyboardUnits.js';

const item = (id, block = {}) => ({ block: { id, ...block }, index: 0 });
const shape = (units) => units.map((unit) => `${unit.kind}:${unit.items.map((entry) => entry.block.id).join('+')}`);

test('낱장 컷은 낱장 그대로', () => {
  assert.deepEqual(shape(frameUnits([item('a'), item('b')])), ['card:a', 'card:b']);
});

test('2칸 행은 좌우 한 프레임', () => {
  const units = frameUnits([item('a', { layoutRowId: 'r1' }), item('b', { layoutRowId: 'r1' }), item('c')]);
  assert.deepEqual(shape(units), ['frame:a+b', 'card:c']);
});

test('4칸 행(grid2x2)은 2×2 한 덩어리', () => {
  const row = ['a', 'b', 'c', 'd'].map((id) => item(id, { layoutRowId: 'r1', sectionLayout: 'grid2x2' }));
  assert.deepEqual(shape(frameUnits([...row, item('e')])), ['grid4:a+b+c+d', 'card:e']);
});

test('예전 보드의 네 컷(2칸 2행)도 한 덩어리로 이어 붙인다 — 저장본은 안 고친다', () => {
  const hook = (id, row) => item(id, {
    layoutRowId: row, sectionLayout: 'twoColumn', hookFrameId: 'hf', hookStyle: 'moodGrid',
  });
  const units = frameUnits([hook('a', 'r1'), hook('b', 'r1'), hook('c', 'r2'), hook('d', 'r2')]);
  assert.deepEqual(shape(units), ['grid4:a+b+c+d']);
});

test('다른 프레임끼리는 안 붙는다 — 두 컷 구성 두 벌은 각각 한 프레임', () => {
  const pair = (id, row) => item(id, { layoutRowId: row, hookFrameId: row, hookStyle: 'pair' });
  const units = frameUnits([pair('a', 'r1'), pair('b', 'r1'), pair('c', 'r2'), pair('d', 'r2')]);
  assert.deepEqual(shape(units), ['frame:a+b', 'frame:c+d']);
});

test('3칸 행은 낱장 셋 — 뒤 두 장이 둘이서 프레임으로 묶이면 2단처럼 보인다', () => {
  const row = ['a', 'b', 'c'].map((id) => item(id, { layoutRowId: 'r1' }));
  assert.deepEqual(shape(frameUnits(row)), ['card:a', 'card:b', 'card:c']);
});

test('시그니처 컷(행 없음·프레임 표식만)은 낱장', () => {
  const units = frameUnits([item('a', { hookFrameId: 'hf', hookStyle: 'signature' }), item('b')]);
  assert.deepEqual(shape(units), ['card:a', 'card:b']);
});
