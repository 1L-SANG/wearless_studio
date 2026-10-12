/* design.md §6. Existing explicit typography is preserved until auto is enabled. */
const ANCHORS = [[8, 13], [17, 26], [26, 36], [40, 52], [64, 77], [120, 138], [200, 224]];
const safeSize = (value) => Number.isFinite(Number(value)) && Number(value) > 0 ? Math.max(8, Math.min(200, Number(value))) : 18;

export function recommendedTextSpacing(size, font = 'Pretendard') {
  const value = safeSize(size);
  const upper = ANCHORS.findIndex(([anchor]) => anchor >= value);
  const [x1, y1] = ANCHORS[Math.max(0, upper - 1)];
  const [x2, y2] = ANCHORS[upper];
  const lineHeight = Math.round(x1 === x2 ? y1 : y1 + (value - x1) * (y2 - y1) / (x2 - x1));
  const trackingFont = ['Pretendard', 'Pretendard Variable', 'Inter', 'Cal Sans'].includes(font || 'Pretendard');
  const amount = trackingFont ? Math.min(2, Math.round(Math.max(0, value - 20) * .025 * 10) / 10) : 0;
  return { lineHeight, tracking: amount ? -amount : 0 };
}

export function resolveTextSpacing(style = {}) {
  if (style.spacingMode === 'auto') return recommendedTextSpacing(style.size, style.font);
  const lineHeight = Number(style.lineHeight);
  const size = Number(style.size);
  const leakedDefault = lineHeight === size * 1.4 && !Number.isInteger(lineHeight);
  return {
    lineHeight: lineHeight > 0 ? (leakedDefault ? Math.round(lineHeight) : lineHeight) : Math.round((size || 18) * 1.4),
    tracking: Number.isFinite(Number(style.tracking)) ? Number(style.tracking) : 0,
  };
}

export function patchTextStyle(style = {}, patch = {}) {
  const next = { ...style, ...patch };
  const currentSpacing = resolveTextSpacing(style);
  const changedSpacing = ['lineHeight', 'tracking'].some((key) => Object.hasOwn(patch, key)
    && Number(patch[key]) !== currentSpacing[key]);
  if (!Object.hasOwn(patch, 'spacingMode') && changedSpacing) {
    next.spacingMode = 'manual';
  }
  if (next.spacingMode === 'auto') Object.assign(next, recommendedTextSpacing(next.size, next.font));
  return next;
}
