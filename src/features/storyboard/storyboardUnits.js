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
