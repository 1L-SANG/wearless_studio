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
