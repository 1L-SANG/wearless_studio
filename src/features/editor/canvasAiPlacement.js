const ORB_HIT_SIZE = 40;
const RESULT_WIDTH = 150;
const clamp = (value, min, max) => Math.max(min, Math.min(Math.max(min, max), value));

function readRect(value) {
  if (!value || !Number.isFinite(value.left) || !Number.isFinite(value.top)) return null;
  const right = Number.isFinite(value.right) ? value.right : value.left + value.width;
  const bottom = Number.isFinite(value.bottom) ? value.bottom : value.top + value.height;
  return Number.isFinite(right) && Number.isFinite(bottom)
    ? { left: value.left, top: value.top, right, bottom, width: right - value.left, height: bottom - value.top }
    : null;
}

/**
 * Rects and menu/result group heights are screen pixels. Positions returned here
 * are unscaled block coordinates; right/resultRight are offsets from block.right.
 * Widths and menuMaxHeight stay in screen pixels for the inverse-scaled UI layer.
 */
export function calculateCanvasAiPlacement({ blockRect, elementRect, viewportRect, scale = 1, menuHeight = 0, resultHeight = 0 } = {}) {
  const block = readRect(blockRect), element = readRect(elementRect), viewport = readRect(viewportRect);
  if (!block || !element || !viewport || !Number.isFinite(scale) || scale <= 0 || viewport.width <= 0 || viewport.height <= 0) return null;

  const fixed = block.left - viewport.left < 156;
  const centerX = fixed ? viewport.left + 16 + ORB_HIT_SIZE / 2 : block.left - 32;
  const centerY = fixed ? viewport.top + 16 + ORB_HIT_SIZE / 2 : clamp(element.top, viewport.top + 28, viewport.bottom - 28);
  const orbTop = centerY - ORB_HIT_SIZE / 2;
  const edge = centerX + ORB_HIT_SIZE / 2;
  const height = Math.max(0, Number.isFinite(menuHeight) ? menuHeight : 0);
  const menuBelow = orbTop + ORB_HIT_SIZE + 10;
  const roomBelow = Math.max(0, viewport.bottom - 12 - menuBelow);
  const roomAbove = Math.max(0, orbTop - 10 - (viewport.top + 12));
  const menuUp = height > roomBelow && (height <= roomAbove || roomAbove > roomBelow);
  const menuMaxHeight = menuUp ? roomAbove : roomBelow;
  const menuTop = menuUp ? Math.max(viewport.top + 12, orbTop - 10 - height) : menuBelow;

  const groupHeight = Math.max(0, Number.isFinite(resultHeight) ? resultHeight : 0);
  const resultEdge = fixed ? viewport.left + (viewport.width + RESULT_WIDTH) / 2 : edge;
  let resultTop = fixed ? viewport.top + (viewport.height - groupHeight) / 2 : element.top + 28;
  if (!fixed && resultTop + groupHeight > viewport.bottom - 12) resultTop = centerY - 28 - groupHeight;
  resultTop = clamp(resultTop, viewport.top + 12, viewport.bottom - 12 - groupHeight);

  return {
    right: (block.right - edge) / scale,
    top: (orbTop - block.top) / scale,
    resultRight: (block.right - resultEdge) / scale,
    resultTop: (resultTop - block.top) / scale,
    menuTop: (menuTop - block.top) / scale,
    menuUp,
    fixed,
    resultWidth: RESULT_WIDTH,
    menuMaxHeight,
    readWidth: Math.max(0, fixed ? viewport.right - 8 - (centerX + 25) : centerX - 25 - (viewport.left + 8)),
    tooltipSide: fixed ? 'right' : 'left',
  };
}
