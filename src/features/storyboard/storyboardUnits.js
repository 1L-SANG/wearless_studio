/* 콘티 캔버스의 렌더 단위 묶기 — 저장 계약(블록 목록)은 그대로 두고 "화면에서 몇 개를
   한 덩어리로 그릴지"만 정하는 순수 함수. Storyboard.jsx 와 node 테스트가 공유한다.

   - 2칸 행(layoutRowId 공유 2개)  → frame  : 좌우 두 컷 한 프레임
   - 4칸 행(grid2x2)               → grid4  : 빈틈 없이 붙은 2×2 한 덩어리
   - 그 밖                          → card   : 낱장

   네 컷 구성(후킹 moodGrid)은 지금은 4칸 1행으로 저장되지만, 예전 보드는 2칸 2행으로
   저장돼 있다. 화면에서는 둘 다 한 덩어리여야 하므로 프레임 표식(hookFrameId)으로도
   이어 붙인다 — 저장본을 고쳐 쓰지 않고 구보드가 그대로 새 모습으로 보인다. */

const sameGridRun = (head, next) => (
  (!!head.layoutRowId && head.layoutRowId === next.layoutRowId)
  || (head.hookStyle === 'moodGrid' && next.hookStyle === 'moodGrid'
    && !!head.hookFrameId && head.hookFrameId === next.hookFrameId)
);

export function frameUnits(items) {
  const units = [];
  for (let index = 0; index < items.length;) {
    const head = items[index].block;
    if (head.layoutRowId || head.hookFrameId) {
      let end = index + 1;
      while (end < items.length && sameGridRun(head, items[end].block)) end += 1;
      const size = end - index;
      if (size === 2 || size === 4) {
        units.push({ kind: size === 2 ? 'frame' : 'grid4', items: items.slice(index, end) });
        index = end;
        continue;
      }
      // 프레임 단위가 없는 행(3열 등)은 행 전체를 낱장으로 넘긴다. 한 장씩 앞으로 밀며
      // 다시 보면 3열 행의 2·3번째가 둘이서 프레임으로 묶여 2단처럼 보였다(옛 동작).
      for (let cursor = index; cursor < end; cursor += 1) units.push({ kind: 'card', items: [items[cursor]] });
      index = end;
      continue;
    }
    units.push({ kind: 'card', items: [items[index]] });
    index += 1;
  }
  return units;
}

/* 덩어리(장소세트·첫 화면 프레임·행)의 연속 run 계약 — 남의 덩어리 **안쪽**을 목적지로
   찍으면 run 끝으로 밀어낸다. run 이 쪼개지면 세트가 둘로 갈리거나 프레임이 흩어진다.

   Storyboard.jsx 안에 있던 것을 옮겼다: 테스트가 이 규칙을 자기 파일에 베껴 쓰고 있어서,
   진짜 가드를 지워도 테스트가 그대로 통과했다(2026-08-17 리뷰). 규칙은 한 곳에만 둔다. */
export const bundleKeyOf = (block) => (
  block?.spaceGroupId || block?.hookFrameId || block?.layoutRowId || null
);

export function snapOutOfForeignBundle(list, movingBlock, idx) {
  const blocks = Array.isArray(list) ? list : [];
  const movingKey = bundleKeyOf(movingBlock);
  // idx 는 '원본 배열 기준 삽입 위치' — 그 앞뒤가 같은 덩어리면 run 내부를 가리킨 것이다.
  const beforeKey = bundleKeyOf(blocks[idx - 1]);
  if (!beforeKey || beforeKey !== bundleKeyOf(blocks[idx])) return idx;
  if (movingKey && movingKey === beforeKey) return idx;   // 제 덩어리 안 재배치는 그대로
  // run 의 끝으로 밀어낸다(앞쪽 경계보다 뒤쪽이 사용자의 의도에 더 가깝다).
  let end = idx;
  while (end < blocks.length && bundleKeyOf(blocks[end]) === beforeKey) end += 1;
  return end;
}
