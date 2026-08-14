const DEFAULT_BLOCK_WIDTH = 1000;
const SCREEN_THRESHOLD = 8;

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function rectBounds(rect) {
  const x = finiteNumber(rect?.x);
  const y = finiteNumber(rect?.y);
  const w = finiteNumber(rect?.w ?? rect?.width);
  const h = finiteNumber(rect?.h ?? rect?.height);

  return {
    left: x,
    center: x + w / 2,
    right: x + w,
    top: y,
    middle: y + h / 2,
    bottom: y + h,
  };
}

function groupBounds(rects) {
  const bounds = rects.map(rectBounds);
  if (!bounds.length) return null;

  const left = Math.min(...bounds.map((rect) => rect.left));
  const right = Math.max(...bounds.map((rect) => rect.right));
  const top = Math.min(...bounds.map((rect) => rect.top));
  const bottom = Math.max(...bounds.map((rect) => rect.bottom));

  return {
    left,
    center: (left + right) / 2,
    right,
    top,
    middle: (top + bottom) / 2,
    bottom,
  };
}

function guideCoordinates(guideRects, blockWidth, blockHeight) {
  const vertical = [40, blockWidth / 2, blockWidth - 40];
  const horizontal = [];

  for (const guideRect of guideRects) {
    const bounds = rectBounds(guideRect);
    vertical.push(bounds.left, bounds.center, bounds.right);
    horizontal.push(bounds.top, bounds.middle, bounds.bottom);
  }

  if (Number.isFinite(blockHeight)) {
    horizontal.push(blockHeight / 2);
  }

  return { vertical, horizontal };
}

function closestSnap(anchors, guides, rawDelta, threshold) {
  let best = null;

  for (const anchor of anchors) {
    const movedAnchor = anchor + rawDelta;
    for (const guide of guides) {
      const correction = guide - movedAnchor;
      const distance = Math.abs(correction);
      if (distance > threshold) continue;
      if (!best || distance < best.distance) {
        best = { correction, coordinate: guide, distance };
      }
    }
  }

  return best;
}

export function snapEditorDragDelta({
  startRects = [],
  guideRects = [],
  delta = [0, 0],
  scale = 1,
  blockWidth = DEFAULT_BLOCK_WIDTH,
  blockHeight,
} = {}) {
  const selectedBounds = groupBounds(startRects);
  const rawDx = finiteNumber(delta?.[0]);
  const rawDy = finiteNumber(delta?.[1]);

  if (!selectedBounds) {
    return { delta: [rawDx, rawDy], guides: { vertical: null, horizontal: null } };
  }

  const zoom = finiteNumber(scale, 1) > 0 ? finiteNumber(scale, 1) : 1;
  const threshold = SCREEN_THRESHOLD / zoom;
  const canvasWidth = finiteNumber(blockWidth, DEFAULT_BLOCK_WIDTH) || DEFAULT_BLOCK_WIDTH;
  const canvasHeight = finiteNumber(blockHeight, Number.NaN);
  const guides = guideCoordinates(guideRects, canvasWidth, canvasHeight);

  const xSnap = closestSnap(
    [selectedBounds.left, selectedBounds.center, selectedBounds.right],
    guides.vertical,
    rawDx,
    threshold,
  );
  const ySnap = closestSnap(
    [selectedBounds.top, selectedBounds.middle, selectedBounds.bottom],
    guides.horizontal,
    rawDy,
    threshold,
  );

  return {
    delta: [
      xSnap ? rawDx + xSnap.correction : rawDx,
      ySnap ? rawDy + ySnap.correction : rawDy,
    ],
    guides: {
      vertical: xSnap ? xSnap.coordinate : null,
      horizontal: ySnap ? ySnap.coordinate : null,
    },
  };
}
