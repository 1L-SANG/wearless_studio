export const DEFAULT_EDITOR_COLOR_PRESETS = [
  '#000000', '#333333', '#666666', '#999999', '#CCCCCC', '#E5E5E5', '#FFFFFF',
  '#B42318', '#D92D20', '#F04438', '#F97066', '#FDA29B',
  '#B54708', '#DC6803', '#F79009', '#FDB022', '#FEC84B',
  '#667085', '#475467', '#344054', '#1D2939', '#101828',
  '#067647', '#079455', '#12B76A', '#32D583', '#75E0A7',
  '#175CD3', '#1570EF', '#2E90FA', '#53B1FD', '#84CAFF',
  '#5925DC', '#6938EF', '#7F56D9', '#9E77ED', '#B692F6',
  '#C11574', '#DD2590', '#EE46BC', '#F670C7', '#FAA7E0',
];

const clampPercent = (value) => Math.min(100, Math.max(0, Number(value) || 0));

export function commitNumberDraft(draft, { min = -Infinity, max = Infinity, fallback = 0 } = {}) {
  const raw = String(draft ?? '').trim();
  const parsed = raw === '' ? NaN : Number(raw);
  const safeFallback = Number.isFinite(Number(fallback)) ? Number(fallback) : 0;
  const value = Number.isFinite(parsed) ? parsed : safeFallback;
  return Math.min(max, Math.max(min, value));
}

export function hexToHsv(value) {
  const raw = String(value || '').trim().replace(/^#/, '');
  const full = /^[0-9a-f]{3}$/i.test(raw)
    ? raw.split('').map((character) => character + character).join('')
    : raw;
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  const [r, g, b] = [0, 2, 4].map((index) => parseInt(full.slice(index, index + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let h = 0;
  if (delta) {
    if (max === r) h = 60 * (((g - b) / delta) % 6);
    else if (max === g) h = 60 * ((b - r) / delta + 2);
    else h = 60 * ((r - g) / delta + 4);
  }
  if (h < 0) h += 360;
  return {
    h: Math.round(h) % 360,
    s: Math.round(max ? (delta / max) * 100 : 0),
    v: Math.round(max * 100),
  };
}

export function hsvToHex({ h = 0, s = 0, v = 0 } = {}) {
  const hue = ((Number(h) || 0) % 360 + 360) % 360;
  const saturation = clampPercent(s) / 100;
  const value = clampPercent(v) / 100;
  const chroma = value * saturation;
  const x = chroma * (1 - Math.abs((hue / 60) % 2 - 1));
  const match = value - chroma;
  const [r1, g1, b1] = hue < 60 ? [chroma, x, 0]
    : hue < 120 ? [x, chroma, 0]
      : hue < 180 ? [0, chroma, x]
        : hue < 240 ? [0, x, chroma]
          : hue < 300 ? [x, 0, chroma]
            : [chroma, 0, x];
  return `#${[r1, g1, b1].map((channel) => Math.round((channel + match) * 255)
    .toString(16).padStart(2, '0')).join('').toUpperCase()}`;
}

const PHOTO_ROW_KINDS = new Set(['twocol', 'threecol', 'grid2x2', 'colorcmp']);

function isGeneratedPhotoBlock(block) {
  const elements = block?.elements || [];
  if (elements.some((element) => element.type === 'image' && element.sourceBlockId)) return true;
  if (PHOTO_ROW_KINDS.has(block?.kind) && elements.some((element) => element.type === 'image')) return true;
  return Boolean(
    block?.contentRole
    && block.contentRole !== 'custom'
    && elements.some((element) => element.type === 'image' && !element.frameSlot),
  );
}

/** Remove copy layers only from generated photo blocks. Information/FAQ blocks
 * may contain image slots, so source linkage and block semantics are required. */
export function stripPhotoBlockTextElements(blocks) {
  let changed = false;
  const next = (blocks || []).map((block) => {
    if (!isGeneratedPhotoBlock(block)) return block;
    const elements = (block.elements || []).filter((element) => element.type !== 'text');
    if (elements.length === (block.elements || []).length) return block;
    changed = true;
    return { ...block, elements };
  });
  return changed ? next : blocks;
}

const FREE_DIRECTIONS = ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'];
const RATIO_DIRECTIONS = ['nw', 'ne', 'sw', 'se'];

/** Text boxes must always expose side handles so their wrapping width can be
 * edited. Images and shapes keep the user's existing aspect-ratio preference. */
export function resizePolicyForElement(element, lockRatio) {
  const keepRatio = element?.type === 'text' ? false : Boolean(lockRatio);
  return {
    keepRatio,
    directions: keepRatio ? [...RATIO_DIRECTIONS] : [...FREE_DIRECTIONS],
  };
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Pixel-coordinate speech bubble path. Unlike the legacy normalized SVG path,
 * its corner radius remains an actual editor pixel value while the box resizes. */
export function speechBubblePath({ width, height, radius }) {
  const w = Math.max(1, Number(width) || 1);
  const h = Math.max(1, Number(height) || 1);
  const tailHeight = clamp(h * 0.16, 10, 22);
  const bodyBottom = Math.max(1, h - tailHeight);
  const r = clamp(Number(radius) || 0, 0, Math.min(w / 2, bodyBottom / 2));
  const tailStart = clamp(w * 0.26, r + 4, Math.max(r + 4, w - r - 26));
  const tailEnd = clamp(tailStart + 20, tailStart + 4, w - r);
  const tailTipX = clamp(tailStart - 10, r, tailStart);
  return [
    `M ${r} 0`, `H ${w - r}`, `Q ${w} 0 ${w} ${r}`,
    `V ${bodyBottom - r}`, `Q ${w} ${bodyBottom} ${w - r} ${bodyBottom}`,
    `H ${tailEnd}`, `L ${tailTipX} ${h}`, `L ${tailStart} ${bodyBottom}`,
    `H ${r}`, `Q 0 ${bodyBottom} 0 ${bodyBottom - r}`,
    `V ${r}`, `Q 0 0 ${r} 0`, 'Z',
  ].join(' ');
}
