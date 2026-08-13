export function getBlockRenderHeight(block) {
  const contentBottom = block.elements.reduce(
    (bottom, element) => Math.max(bottom, (element.y || 0) + (element.h || 40)),
    0,
  );
  const blockHeight = block.h || 220;
  // 이미 부모 안에 들어온 요소에는 여백을 다시 더하지 않는다. 요소가 실제로 넘친
  // 경우에만 한 번 50px 안전 여백과 함께 확장해야, 하단에 딱 맞춘 뒤 높이가 반복 증가하지 않는다.
  return contentBottom > blockHeight ? contentBottom + 50 : blockHeight;
}

export function expandBlockHeights(blocks) {
  return blocks.map((block) => ({ ...block, h: getBlockRenderHeight(block) }));
}

export function blockHeightFromBottom(startHeight, screenDeltaY, scale = 1) {
  const zoom = Number(scale) > 0 ? Number(scale) : 1;
  return Math.max(120, Math.round(Number(startHeight) + Number(screenDeltaY) / zoom));
}

export function clampDragDelta(snapshot, [dx, dy], blockHeight) {
  const elements = Object.values(snapshot || {});
  if (!elements.length) return [dx, dy];

  const left = Math.min(...elements.map((element) => element.x));
  const right = Math.max(...elements.map((element) => element.x + (element.w || 0)));
  const minDx = -left;
  const maxDx = Math.max(minDx, 1000 - right);
  const top = Math.min(...elements.map((element) => element.y));
  const bottom = Math.max(...elements.map((element) => element.y + (element.h || 0)));
  const minDy = -top;
  const maxDy = Number.isFinite(blockHeight)
    ? Math.max(minDy, blockHeight - bottom)
    : Number.POSITIVE_INFINITY;

  return [
    Math.max(minDx, Math.min(maxDx, dx)),
    Math.max(minDy, Math.min(maxDy, dy)),
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
   줄 상자(Range.getClientRects)에서 화면상 3px 이상 벗어나면 그 클릭은 블록 몫이다.
   캔버스가 축소되면 글자 높이도 6~8px까지 작아지므로 2px 오차만 허용하면 같은 문장을
   눌러도 포인터 위치에 따라 선택이 빠진다. 3px는 표 안의 인접 구분선 중심을 덮지 않는
   최대 안전 여유이며, viewport 좌표라 줌과 무관하게 유지된다.

   rects 가 비면(빈 텍스트) false — 잡을 단서가 상자밖에 없으니 종전대로 요소가 받는다. */
export function pointMissesTextLines(rects, x, y, pad = 3) {
  if (!rects || !rects.length) return false;
  return !rects.some((r) => x >= r.left - pad && x <= r.right + pad
    && y >= r.top - pad && y <= r.bottom + pad);
}
