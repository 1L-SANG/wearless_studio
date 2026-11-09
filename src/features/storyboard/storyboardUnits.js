// Template section designs own rows. Each storyboard photo remains a single card.
export const cardUnits = items => items.map(item => ({ kind: 'card', items: [item] }));

/* 덩어리(장소세트·첫 화면 프레임·행)의 연속 run 계약 — 남의 덩어리 **안쪽**을 목적지로
   찍으면 run 끝으로 밀어낸다. run 이 쪼개지면 세트가 둘로 갈리거나 프레임이 흩어진다.

   Storyboard.jsx 안에 있던 것을 옮겼다: 테스트가 이 규칙을 자기 파일에 베껴 쓰고 있어서,
   진짜 가드를 지워도 테스트가 그대로 통과했다(2026-08-17 리뷰). 규칙은 한 곳에만 둔다. */
export const bundleKeyOf = (block) => (
  block?.spaceGroupId || null
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
