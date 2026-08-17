/* 덩어리(첫 화면 구성 · 행 · 장소세트) 연속 run 계약을 **동작으로** 고정한다.
   2026-08-16~17 자체 리뷰에서 반복해 깨진 지점이다 — 소스 문자열 가드는 계약이 깨져도
   green 이었으므로, 여기서는 실제 함수를 태워 run 이 유지되는지만 본다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { moveBlockWithSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
import { adoptSection, normalizeBoard } from '../../src/lib/sections.js';
import { bundleKeyOf, frameUnits, snapOutOfForeignBundle } from '../../src/features/storyboard/storyboardUnits.js';
import { deriveHookFrame } from '../../src/lib/storyboardHookFrame.js';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);

const ROW = 'row__hookframe__hf1__1';
const slot = (id, extra = {}) => ({
  id, source: 'ai', sectionId: 'sec-hook', sectionRole: 'hooking',
  cutType: 'styling', shot: 'medium', colorId: 'c1',
  hookFrameId: 'hookframe__hf1', hookStyle: 'pair', hookFrameVersion: 1,
  layoutRowId: ROW, layoutRowVersion: 1, sectionLayout: 'twoColumn', ...extra,
});
const loose = (id) => ({
  id, source: 'ai', sectionId: 'sec-hook', sectionRole: 'hooking',
  cutType: 'styling', shot: 'full', colorId: 'c1',
});
const hookingItems = (bs) => bs
  .filter((b) => b.sectionRole === 'hooking')
  .map((b, i) => ({ index: i + 1, block: b }));

/* 규칙을 여기 베껴 쓰면 진짜 가드를 지워도 테스트가 통과한다 — 실제 함수를 그대로
   가져와 태운다(2026-08-17 리뷰에서 이 테스트가 헛돈다고 지적된 지점). */
const moveWithGuard = (board, id, rawIdx) => {
  const moving = board.find((b) => b.id === id);
  const idx = snapOutOfForeignBundle(board, moving, rawIdx);
  return normalizeBoard(adoptSection(
    moveBlockWithSpaceMembership(board, id, idx, {}), id, 'sec-hook', 'hooking',
  ));
};

test('덩어리 밖 낱장이 구성 run 한가운데로 들어가지 않는다 — 두 컷 구성 유지', () => {
  const board = [slot('f1', { hookSlotRole: 'left' }), slot('f2', { hookSlotRole: 'right' }), loose('x')];
  assert.deepEqual(frameUnits(hookingItems(board)).map((u) => u.kind), ['frame', 'card']);

  // '앞으로 한 칸' = 배열 한 칸 앞(=f1·f2 사이)을 가리킨다 → 가드가 run 끝으로 밀어내야 한다.
  const moved = moveWithGuard(board, 'x', 1);
  assert.deepEqual(frameUnits(hookingItems(moved)).map((u) => u.kind), ['frame', 'card'],
    '구성이 낱장으로 쪼개지면 안 된다');
  assert.ok(deriveHookFrame(moved), '프레임 파생이 살아 있어야 한다');
  const rows = moved.filter((b) => b.hookFrameId).map((b) => b.layoutRowId);
  assert.equal(new Set(rows).size, 1, '행 표식이 유지된다');
});

test('가드 없이 같은 이동을 하면 실제로 깨진다 — 가드가 하는 일이 무엇인지 고정', () => {
  const board = [slot('f1'), slot('f2'), loose('x')];
  const broken = normalizeBoard(adoptSection(
    moveBlockWithSpaceMembership(board, 'x', 1, {}), 'x', 'sec-hook', 'hooking',
  ));
  assert.equal(deriveHookFrame(broken), null, '가드가 없으면 프레임이 소멸한다(회귀 감시용)');
});

test('제 덩어리 안에서의 재배치는 가드가 막지 않는다', () => {
  const board = [slot('f1'), slot('f2'), slot('f3')];
  const idx = snapOutOfForeignBundle(board, board[2], 1);
  assert.equal(idx, 1, '같은 덩어리 형제는 run 안에서 그대로 움직인다');
});

test('덩어리 안 컷은 끌 수 없다 — 카드 드래그가 봉인돼 있다', () => {
  // 끌 수 있으면 '사이 자리'에 떨궈 run 밖으로 나갈 수 있다(자체 리뷰 high).
  assert.match(storyboardSource, /dragFor=\{\(id\) => \(\{ draggable: false/);
  assert.match(storyboardSource, /cardDrag=\{\{ draggable: !bundleKeyOf\(block\)/);
});

test('구성이 깨진 보드에서도 탈출구가 남는다 — 칩과 삭제 중 하나는 살아 있다', () => {
  // 칩: 후킹에 AI 컷이 있으면 프레임 파생 실패와 무관하게 뜬다(재선택이 복구 경로).
  assert.match(storyboardSource, /const hookStyleChipProps = hookingHasAiCut \? \{/);
  // 삭제: 프레임이 온전한 슬롯일 때만 막는다.
  assert.match(
    storyboardSource,
    /return !hookFrame \|\| !hookFrame\.slotIds\.includes\(block\.id\);/,
  );
  // 스타일 재적용이 프레임 없는 보드에서도 진입한다.
  assert.doesNotMatch(storyboardSource, /if \(locked \|\| hookStyleSaving \|\| !hookFrame\) return;/);
});

test('예비가 소진된 장소세트는 복제로도 컷이 늘지 않는다', () => {
  assert.match(storyboardSource, /if \(!nextSpaceSetMemberReservation\(set, members\)\) \{/);
});
