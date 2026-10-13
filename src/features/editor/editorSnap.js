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
  altKey = false,
} = {}) {
  const selectedBounds = groupBounds(startRects);
  const rawDx = finiteNumber(delta?.[0]);
  const rawDy = finiteNumber(delta?.[1]);

  if (!selectedBounds || altKey) {
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

export function snapEditorSpacing({ startRects = [], guideRects = [], delta = [0, 0], scale = 1, altKey = false, snapping = true } = {}) {
  const bounds = groupBounds(startRects);
  if (!bounds || altKey) return { delta, gaps: [] };
  const threshold = SCREEN_THRESHOLD / (scale > 0 ? scale : 1);
  const next = [...delta], gaps = [];
  for (const [axis, index, start, end, crossStart, crossEnd] of [
    ['x', 0, 'left', 'right', 'top', 'bottom'], ['y', 1, 'top', 'bottom', 'left', 'right'],
  ]) {
    const moved = { ...bounds, [start]: bounds[start] + delta[index], [end]: bounds[end] + delta[index],
      [crossStart]: bounds[crossStart] + delta[1 - index], [crossEnd]: bounds[crossEnd] + delta[1 - index] };
    const neighbors = guideRects.map(rectBounds).filter(r => r[crossStart] < moved[crossEnd] && r[crossEnd] > moved[crossStart]);
    const before = neighbors.filter(r => r[end] <= moved[start]).sort((a, b) => b[end] - a[end]);
    const after = neighbors.filter(r => r[start] >= moved[end]).sort((a, b) => a[start] - b[start]);
    let correction = null;
    const size = bounds[end] - bounds[start];
    if (before[0] && after[0]) correction = (before[0][end] + after[0][start] - size) / 2 - moved[start];
    else if (before.length >= 2) correction = before[0][end] + (before[0][start] - before[1][end]) - moved[start];
    else if (after.length >= 2) correction = after[0][start] - (after[1][start] - after[0][end]) - moved[end];
    if (snapping && correction != null && Math.abs(correction) <= threshold) next[index] += correction;
    const from = bounds[start] + next[index], to = bounds[end] + next[index];
    const cross = (moved[crossStart] + moved[crossEnd]) / 2;
    if (before[0] && from > before[0][end]) gaps.push({ axis, from: before[0][end], to: from, cross, distance: Math.round(from - before[0][end]) });
    if (after[0] && after[0][start] > to) gaps.push({ axis, from: to, to: after[0][start], cross, distance: Math.round(after[0][start] - to) });
  }
  return { delta: next, gaps };
}
