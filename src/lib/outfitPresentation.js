// Pure presentation rules for the v18 outfit picker and the 2026-10-18 catalog.
export const OUTFIT_COLOR_GROUPS = Object.freeze({
  garment: [
    { value: 'white', label: '화이트', hex: '#f4f4f4', colors: ['white', 'ivory', 'cream'] },
    { value: 'beige', label: '베이지', hex: '#d8c4a3', colors: ['beige', 'camel', 'brown', 'khaki', 'tan'] },
    { value: 'gray', label: '그레이', hex: '#8e8e95', colors: ['gray', 'charcoal'] },
    { value: 'blue', label: '블루', hex: '#5b8ad1', colors: ['lightblue', 'blue', 'navy'] },
    { value: 'black', label: '블랙', hex: '#15141a', colors: ['black'] },
    { value: 'color', label: '컬러', hex: 'var(--outfit-swatch-color)', colors: ['green', 'olive', 'red', 'burgundy', 'pink', 'purple', 'yellow', 'orange'] },
  ],
  shoes: [
    { value: 'white', label: '화이트', hex: '#f4f4f4', colors: ['white', 'ivory', 'cream'] },
    { value: 'brown', label: '브라운', hex: '#6b4a35', colors: ['brown', 'beige', 'tan', 'camel'] },
    { value: 'black', label: '블랙', hex: '#15141a', colors: ['black'] },
  ],
  bag: [
    { value: 'white', label: '화이트', hex: '#f3efe6', colors: ['white', 'ivory', 'cream'] },
    { value: 'gray', label: '그레이', hex: 'var(--fg-2)', colors: ['gray', 'silver'] },
    { value: 'brown', label: '브라운', hex: '#6b4a35', colors: ['beige', 'camel', 'brown'] },
    { value: 'black', label: '블랙', hex: '#1f1e24', colors: ['black', 'navy'] },
  ],
  accessory: [
    { value: 'white', label: '화이트', hex: '#f3efe6', colors: ['white', 'ivory', 'pearl'] },
    { value: 'silver', label: '실버', hex: 'var(--fg-3)', colors: ['silver', 'gray'] },
    { value: 'gold', label: '골드', hex: '#d2b069', colors: ['gold'] },
    { value: 'brown', label: '브라운', hex: '#6b4a35', colors: ['beige', 'camel', 'brown'] },
    { value: 'black', label: '블랙', hex: '#1f1e24', colors: ['black', 'navy'] },
  ],
});

export function colorGroupsForRole(role) {
  if (['footwear', 'shoes'].includes(role)) return OUTFIT_COLOR_GROUPS.shoes;
  if (role === 'bag') return OUTFIT_COLOR_GROUPS.bag;
  if (['accessory', 'acc'].includes(role)) return OUTFIT_COLOR_GROUPS.accessory;
  if (['top', 'bottom', 'inner', 'outer', 'dress', 'garment'].includes(role)) return OUTFIT_COLOR_GROUPS.garment;
  return [];
}

const COLOR_NAMES = [
  ['ivory', /ivory|아이보리|오프화이트/], ['cream', /cream|크림/],
  ['pearl', /pearl|진주/], ['white', /white|화이트|흰색/],
  ['gold', /gold|골드|금색/], ['beige', /beige|베이지/],
  ['camel', /camel|카멜/], ['khaki', /khaki|카키/],
  ['brown', /brown|브라운|갈색/], ['tan', /^tan$/],
  ['charcoal', /charcoal|차콜/], ['gray', /gray|grey|그레이|회색/],
  ['lightblue', /lightblue|skyblue|라이트블루|스카이블루|아이스블루/],
  ['navy', /navy|네이비|남색/], ['blue', /blue|블루|파랑|청색/],
  ['black', /black|블랙|검정|검은/],
  ['pink', /pink|핑크/], ['green', /green|olive|그린|초록|올리브/],
  ['burgundy', /burgundy|버건디|와인/], ['orange', /orange|오렌지|주황/],
  ['red', /^red$|레드|빨강/], ['purple', /purple|퍼플|보라/],
  ['yellow', /yellow|옐로우|노랑/], ['silver', /silver|실버|은색/],
];
const normalizedText = (value) => typeof value === 'string' ? value.trim().toLowerCase().replace(/[\s_-]+/g, '') : '';
const namedColor = (value) => COLOR_NAMES.find(([, pattern]) => pattern.test(normalizedText(value)))?.[0];

function hexColor(value, role) {
  const hex = normalizedText(value);
  if (!/^#(?:[\da-f]{3}|[\da-f]{6})$/.test(hex)) return null;
  const digits = hex.length === 4 ? [...hex.slice(1)].map(x => x + x).join('') : hex.slice(1);
  const [r, g, b] = [0, 2, 4].map(index => parseInt(digits.slice(index, index + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
  if (max < 0.2) return 'black';
  if (min > 0.82 && delta < 0.15) return 'white';
  if (delta / max < 0.16) return 'gray';
  const hue = ((max === r ? (g - b) / delta : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4) * 60 + 360) % 360;
  if (hue >= 180 && hue <= 260) return max < 0.5 ? 'navy' : 'blue';
  if (hue >= 15 && hue <= 75) {
    if (['bag', 'accessory', 'acc'].includes(role) && hue >= 36 && delta / max > 0.35) return 'gold';
    return max > 0.65 ? 'beige' : 'brown';
  }
  return null;
}

/** Catalog groups are authoritative; names and hex only fill absent color metadata. */
export function colorGroupForItem(item, role) {
  const color = item?.color;
  const values = [item?.colorGroup, item?.color_group, item?.colorKey, item?.colorName, typeof color === 'string' ? color : null,
    color?.key, color?.name, item?.colorFamily, color?.family, item?.family, item?.swatchId];
  const named = values.map(namedColor).find(Boolean);
  const resolved = named || [item?.colorHex, item?.hex, color?.hex, color]
    .map(value => hexColor(value, role)).find(Boolean);
  return colorGroupsForRole(role).find(group => group.colors.includes(resolved))?.value || null;
}

/** Input order is the server's AI order; selected-first applies only to the main grid. */
export function sortOutfitItems(items, { role, selectedId, drawer = false } = {}) {
  const groups = colorGroupsForRole(role);
  const colorRank = (item) => {
    const index = groups.findIndex(group => group.value === colorGroupForItem(item, role));
    return index < 0 ? groups.length : index;
  };
  return (items || []).map((item, index) => ({ item, index })).sort((a, b) => {
    if (drawer) {
      const difference = colorRank(a.item) - colorRank(b.item);
      if (difference) return difference;
    } else {
      const difference = Number(b.item.id === selectedId) - Number(a.item.id === selectedId);
      if (difference) return difference;
    }
    return Number(b.item.inSets === true) - Number(a.item.inSets === true) || a.index - b.index;
  }).map(({ item }) => item);
}

export function filterOutfitItems(items, { role, color = '', search = '', category = '' } = {}) {
  const query = normalizedText(search);
  return (items || []).filter(item => {
    const group = colorGroupForItem(item, role);
    if (category && item.category !== category) return false;
    if (color && group !== color) return false;
    const label = colorGroupsForRole(role).find(value => value.value === group)?.label;
    return !query || [item.name, item.displayName, item.colorName, group, label].some(value => normalizedText(value).includes(query));
  });
}

const positiveNumber = (value, fallback) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : fallback;

export const outfitModelCredit = fee => fee > 0 ? `+${fee} 크레딧` : '무료';

export function compressedMatchHeight(productHeight, productCm, matchCm) {
  const height = positiveNumber(productHeight, 0);
  if (!positiveNumber(productCm, 0) || !positiveNumber(matchCm, 0)) return height;
  return height * (Number(matchCm) / Number(productCm)) ** 0.7;
}

export function selectProductBoardImage(visuals, originalSrc) {
  const front = visuals?.front;
  return (front?.tidyStatus === 'ready' && front.tidyUrl) || front?.cutoutUrl || originalSrc || null;
}

// 26cm would make Chelsea boots consume more than the old 16cm shoe budget.
// 18.5cm also leaves room for pixel rounding when measuring at 256px.
export const SHOE_LENGTH_CM = 18.5;
export const SHOE_PAIR_WIDTH = 1.36;
export const SHOE_PAIR_HEIGHT = 1.08;
export const SHOE_PAIR_MAX_HEIGHT_CM = 30;
export const MIRROR_REAR_SHOE = false;
const DEFAULT_SHOE_ASPECT = 2.1;

export function footwearPairSize(aspectRatio = DEFAULT_SHOE_ASPECT) {
  const ar = positiveNumber(aspectRatio, DEFAULT_SHOE_ASPECT);
  const height = SHOE_LENGTH_CM / ar * SHOE_PAIR_HEIGHT;
  const scale = Math.min(1, SHOE_PAIR_MAX_HEIGHT_CM / height);
  return { widthCm: SHOE_LENGTH_CM * SHOE_PAIR_WIDTH * scale, heightCm: height * scale };
}

export function shoePairImageLayers() {
  const size = { width: `${100 / SHOE_PAIR_WIDTH}%`, height: `${100 / SHOE_PAIR_HEIGHT}%` };
  return [
    { ...size, left: `${100 * (SHOE_PAIR_WIDTH - 1) / SHOE_PAIR_WIDTH}%`, top: '0%', zIndex: 0,
      ...(MIRROR_REAR_SHOE ? { transform: 'scaleX(-1)' } : {}) },
    { ...size, left: '0%', top: `${100 * (SHOE_PAIR_HEIGHT - 1) / SHOE_PAIR_HEIGHT}%`, zIndex: 1 },
  ];
}

/** Preserve the v18 proportions, then fit the final rows and circles with one scale. */
export function buildOutfitBoardLayout({ clothingType, product, items = {}, maxHeight = 440, width = 244 } = {}) {
  const canvasWidth = positiveNumber(width, 244), canvasHeight = positiveNumber(maxHeight, 440);
  const pad = 10, gap = 10, outerDx = 22, outerDy = 0.3;
  const itemAt = slot => Array.isArray(items) ? items.find(item => (item.slot || item.role) === slot) : items?.[slot];
  const piece = (slot, item, cm, ar) => ({ ...item, slot, cm: positiveNumber(item?.cm, cm), ar: positiveNumber(item?.ar, ar) });
  const productCm = { top: 64, outer: 80, bottom: 100, dress: 110 }[clothingType] || 64;
  const prod = piece('product', product, productCm, clothingType === 'bottom' ? 0.52 : 0.8);
  const top = piece(clothingType === 'outer' ? 'inner' : 'top', itemAt(clothingType === 'outer' ? 'inner' : 'top'), 64, 0.95);
  const bottom = piece('bottom', itemAt('bottom'), 100, 0.52);
  const rows = clothingType === 'outer' ? [[prod, top], [bottom]]
    : clothingType === 'bottom' ? [[top], [prod]] : clothingType === 'dress' ? [[prod]] : [[prod], [bottom]];
  const garments = rows.flat();
  const footwear = itemAt('footwear') || itemAt('shoes');
  const shoeSize = footwearPairSize(footwear?.ar);
  const shoes = { ...footwear, slot: 'footwear', cm: shoeSize.heightCm, ar: shoeSize.widthCm / shoeSize.heightCm };
  const rowCm = row => row.length === 2 ? Math.max(row[0].cm, row[0].cm * outerDy + row[1].cm) : row[0].cm;
  const sumCm = rows.reduce((sum, row) => sum + rowCm(row), 0) + shoes.cm;
  let scale = Math.min(3.4, Math.max(0, canvasHeight - pad * 2 - gap * rows.length) / sumCm);
  for (const item of garments) scale = Math.min(scale, Math.round(canvasWidth * 0.74) / (item.cm * item.ar));
  scale *= 0.9;
  const match = clothingType === 'bottom' ? top : clothingType === 'dress' ? null : bottom;
  const bundleCm = rowCm(rows.find(row => row.includes(prod)));
  for (const item of garments) {
    item.height = item === match ? compressedMatchHeight(bundleCm * scale, bundleCm, item.cm) : item.cm * scale;
    item.width = item.height * item.ar;
  }
  shoes.height = shoes.cm * scale;
  shoes.width = shoeSize.widthCm * scale;
  const accessoryItems = [itemAt('accessory')].flat().filter(item => item?.src || item?.pending).slice(0, 3);
  const accessories = [
    ...(itemAt('bag')?.src || itemAt('bag')?.pending ? [{ ...itemAt('bag'), slot: 'bag' }] : []),
    ...accessoryItems.map(item => ({ ...item, slot: 'accessory' })),
  ].map(item => ({ ...item, width: 58, height: 58, zIndex: 20 }));
  const leftColumn = accessories.some(item => item.slot === 'bag');
  const rightCount = accessoryItems.length;
  const columnCount = Number(leftColumn) + Number(rightCount > 0);
  const circleStackHeight = rightCount * 58;
  const stackGaps = Math.max(0, rightCount - 1) * gap;
  const rowHeight = row => row.length === 2
    ? Math.max(row[0].height, row[0].height * outerDy + row[1].height) : row[0].height;
  const rowWidth = row => row.length === 2
    ? Math.max(row[0].width, row[1].width, (row[0].width + row[1].width) / 2 + outerDx * 2) : row[0].width;
  // The 0.7 correction can make a short match taller, or a wide match wider,
  // than the raw cm estimate. Fit AFTER that correction; gaps remain 10px.
  const contentHeight = rows.reduce((sum, row) => sum + rowHeight(row), 0) + shoes.height;
  const fit = Math.min(1,
    Math.max(0, canvasHeight - pad * 2 - gap * rows.length) / contentHeight,
    Math.max(0, canvasWidth - pad * 2 - columnCount * gap) / (Math.max(...rows.map(rowWidth), shoes.width) + columnCount * 58),
    ...(accessories.length ? [Math.max(0, canvasHeight - pad * 2 - stackGaps) / Math.max(58, circleStackHeight)] : []));
  for (const item of [...garments, shoes, ...accessories]) { item.width *= fit; item.height *= fit; }
  const leftInset = leftColumn ? 58 * fit + gap : 0;
  const rightInset = rightCount ? 58 * fit + gap : 0;
  const garmentCenter = (pad + leftInset + canvasWidth - pad - rightInset) / 2;
  let y = pad, zIndex = 10;
  for (const row of rows) {
    if (row.length === 2) {
      const [outer, inner] = row;
      const left = Math.min(-outer.width / 2 - outerDx * fit, -inner.width / 2 + outerDx * fit);
      const right = Math.max(outer.width / 2 - outerDx * fit, inner.width / 2 + outerDx * fit);
      const center = Math.max(pad + leftInset - left, Math.min(canvasWidth - pad - rightInset - right, garmentCenter));
      Object.assign(outer, { x: center - outer.width / 2 - outerDx * fit, y, zIndex: zIndex - 1 });
      Object.assign(inner, { x: center - inner.width / 2 + outerDx * fit,
        y: y + outer.height * outerDy, zIndex });
      y = Math.max(outer.y + outer.height, inner.y + inner.height) + gap;
      zIndex -= 2;
    } else {
      const item = row[0];
      Object.assign(item, { x: garmentCenter - item.width / 2, y, zIndex });
      y += item.height + gap;
      zIndex -= 1;
    }
  }
  Object.assign(shoes, { x: garmentCenter - shoes.width / 2, y, zIndex: 1 });
  const stackHeight = circleStackHeight * fit + stackGaps;
  const height = Math.max(y + shoes.height + pad, stackHeight + pad * 2, ...accessories.map(item => item.height + pad * 2));
  let accessoryIndex = 0;
  for (const item of accessories) Object.assign(item, {
    x: item.slot === 'bag' ? pad : canvasWidth - pad - item.width,
    y: item.slot === 'bag' ? pad + (height - pad * 2 - item.height) * .45
      : pad + Math.max(0, height - pad * 2 - stackHeight) * .15 + accessoryIndex++ * (item.height + gap),
  });
  return { pieces: [...garments, shoes], accessories, height, width: canvasWidth };
}

export function claimOutfitHint(storage) {
  try {
    const key = 'wearless.outfit.v18.hintSeen';
    if (!storage || storage.getItem(key)) return false;
    storage.setItem(key, '1');
    return true;
  } catch {
    return false;
  }
}

const fallbackRecommendation = recommendation => recommendation?.status === 'pending'
  ? { ...recommendation, status: 'fallback' } : recommendation;

export function initialRecommendationState(recommendation, now = 0) {
  return { recommendation, lateRecommendation: null, skeleton: recommendation?.status === 'pending',
    enteredAt: Number.isFinite(now) ? now : 0 };
}

export function advanceRecommendationState(state, recommendation, now) {
  if (!state) return initialRecommendationState(recommendation, now);
  const next = recommendation || state.recommendation;
  const oldKey = state.recommendation?.contextKey, nextKey = next?.contextKey;
  if (oldKey != null && nextKey != null && oldKey !== nextKey) return initialRecommendationState(next, now);
  const expired = now - state.enteredAt >= 1500;
  if (next?.status === 'pending') {
    if (state.lateRecommendation || state.recommendation?.status === 'ready') return state;
    return { ...state, recommendation: expired || !state.skeleton ? fallbackRecommendation(next) : next,
      skeleton: state.skeleton && !expired };
  }
  if (next?.status === 'ready' && (state.recommendation?.status === 'fallback'
    || (state.recommendation?.status === 'pending' && expired))) {
    return { ...state, recommendation: fallbackRecommendation(state.recommendation),
      lateRecommendation: next, skeleton: false };
  }
  return { ...state, recommendation: next, lateRecommendation: null, skeleton: false };
}

/** Applying selected items is deliberately left to applyOutfitSet in the selection layer. */
export function acceptLateRecommendation(state) {
  return state?.lateRecommendation ? { ...state, recommendation: state.lateRecommendation,
    lateRecommendation: null, skeleton: false } : state;
}

export const closeOutfitDrawer = state => ({ ...state, drawer: null });
