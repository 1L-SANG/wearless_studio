export const DEFAULT_EDITOR_COLOR_PRESETS = [
  '#000000', '#3C3C3C', '#5B5B5B', '#8E8E8E', '#C5C5C5', '#EBEBEB', '#F1F1F1', '#FFFFFF',
  '#F20011', '#FD0083', '#FF00E8', '#0F00E7', '#00EFFE', '#00F035', '#7FFA38', '#EDFF3B',
  '#F4C5C5', '#FDE7D3', '#FFF0C8', '#D3E7CE', '#C9DCDF', '#C8DEF0', '#D4CCE4', '#E8CAD7',
  '#E98D8F', '#FAC495', '#FFE194', '#ACD2A1', '#96BDC1', '#93BEE2', '#AB9CCE', '#D19BB4',
  '#DF595C', '#F7A866', '#FFD466', '#86BD76', '#699BA5', '#619ED4', '#8370B8', '#BC6E94',
  '#BB000D', '#E5853A', '#F1BA3D', '#5C9F4C', '#397682', '#317ABB', '#5D439A', '#9E426C',
  '#87000A', '#AD5318', '#B9851F', '#2E6B23', '#0F4651', '#024986', '#2F1967', '#6B173D',
  '#570606', '#6E3710', '#755514', '#214518', '#0C2E35', '#063056', '#1E1242', '#44112A',
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

/** Resolve the live rectangle for an image resize gesture. Moveable owns the
 * ratio constraint and reports dimensions based on the element's current box.
 * Reapplying the source-file ratio here would make filled frames jump back to
 * the original photo proportions on their first resize. */
export function imageResizeRect({
  start,
  width,
  height,
  beforeTranslate = [0, 0],
}) {
  const [dx = 0, dy = 0] = beforeTranslate || [];
  return {
    x: start.x + dx,
    y: start.y + dy,
    w: width,
    h: height,
  };
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Pixel-coordinate speech bubble path. Unlike the legacy normalized SVG path,
 * its corner radius remains an actual editor pixel value while the box resizes. */
export function speechBubblePath({ width, height, radius }) {
  const w = Math.max(1, Number(width) || 1);
  const h = Math.max(1, Number(height) || 1);
  // 축소된 에디터에서도 꼬리가 둥근 사각형에 묻히지 않도록 본체 높이의 약 1/4을 쓴다.
  const tailHeight = Math.min(Math.max(0, h - 1), clamp(h * 0.28, 18, 30));
  const bodyBottom = Math.max(1, h - tailHeight);
  const r = clamp(Number(radius) || 0, 0, Math.min(w / 2, bodyBottom / 2));
  const tailWidth = Math.min(clamp(w * 0.2, 24, 44), Math.max(4, w - r * 2));
  const tailStart = clamp(w * 0.22, r, Math.max(r, w - r - tailWidth));
  const tailEnd = Math.min(w - r, tailStart + tailWidth);
  const tailTipX = clamp(tailStart - 14, 0, w);
  return [
    `M ${r} 0`, `H ${w - r}`, `Q ${w} 0 ${w} ${r}`,
    `V ${bodyBottom - r}`, `Q ${w} ${bodyBottom} ${w - r} ${bodyBottom}`,
    `H ${tailEnd}`, `L ${tailTipX} ${h}`, `L ${tailStart} ${bodyBottom}`,
    `H ${r}`, `Q 0 ${bodyBottom} 0 ${bodyBottom - r}`,
    `V ${r}`, `Q 0 0 ${r} 0`, 'Z',
  ].join(' ');
}
