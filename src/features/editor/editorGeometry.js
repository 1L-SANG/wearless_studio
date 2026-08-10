export function getBlockRenderHeight(block) {
  const contentBottom = block.elements.reduce(
    (bottom, element) => Math.max(bottom, (element.y || 0) + (element.h || 40)),
    0,
  );
  return Math.max(block.h || 220, contentBottom + 50);
}

export function expandBlockHeights(blocks) {
  return blocks.map((block) => ({ ...block, h: getBlockRenderHeight(block) }));
}

export function clampDragDelta(snapshot, [dx, dy]) {
  const elements = Object.values(snapshot || {});
  if (!elements.length) return [dx, dy];

  const left = Math.min(...elements.map((element) => element.x));
  const right = Math.max(...elements.map((element) => element.x + (element.w || 0)));
  const minDx = -left;
  const maxDx = Math.max(minDx, 1000 - right);

  return [
    Math.max(minDx, Math.min(maxDx, dx)),
    Math.max(-Math.min(...elements.map((element) => element.y)), dy),
  ];
}

export function clampElementRect(x, y, width, height) {
  const right = Math.min(1000, Math.max(24, x + Math.max(24, width)));
  const bottom = Math.max(24, y + Math.max(24, height));
  const left = Math.max(0, Math.min(x, right - 24));
  const top = Math.max(0, Math.min(y, bottom - 24));

  return { x: left, y: top, w: right - left, h: bottom - top };
}

/* 텍스트 요소의 클릭 판정 — 상자가 아니라 실제로 글자가 그려진 줄 상자로 본다.

   텍스트 요소는 배치 폭(보통 880)을 통째로 차지한다. 그래서 한 줄짜리 설명 오른쪽의 넓은
   흰 공간도 요소 안이고, 셀러가 "빈 곳"이라 여기고 눌러도 블록이 아니라 그 텍스트가 잡혔다.
   줄 상자(Range.getClientRects)에 닿지 않으면 그 클릭은 블록 몫이다.

   rects 가 비면(빈 텍스트) false — 잡을 단서가 상자밖에 없으니 종전대로 요소가 받는다. */
export function pointMissesTextLines(rects, x, y, pad = 2) {
  if (!rects || !rects.length) return false;
  return !rects.some((r) => x >= r.left - pad && x <= r.right + pad
    && y >= r.top - pad && y <= r.bottom + pad);
}
