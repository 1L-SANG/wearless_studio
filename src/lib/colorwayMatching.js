/* 추가 색상 착용컷은 분석에서 고른 메인/서브 매칭 의류 중 한 벌만 쓴다.
   기준 색상은 마네킹컷과 같은 메인을 유지하고, 추가 색상은 명도 대비가 큰
   후보를 골라 상품과 매칭 의류가 서로 묻히지 않게 한다. */

const SWATCH_BRIGHTNESS = Object.freeze({
  white: 100,
  ivory: 93,
  yellow: 82,
  beige: 78,
  pink: 72,
  gray: 58,
  blue: 50,
  green: 42,
  red: 40,
  brown: 30,
  navy: 18,
  black: 4,
});

const SWATCH_BY_LABEL = Object.freeze({
  화이트: 'white',
  아이보리: 'ivory',
  옐로우: 'yellow',
  베이지: 'beige',
  핑크: 'pink',
  그레이: 'gray',
  블루: 'blue',
  그린: 'green',
  레드: 'red',
  브라운: 'brown',
  네이비: 'navy',
  블랙: 'black',
});

const SWATCH_LABELS = Object.freeze(Object.fromEntries(
  Object.entries(SWATCH_BY_LABEL).map(([label, swatchId]) => [swatchId, label]),
));

export function colorDisplayName(color, fallback = '색상') {
  const explicit = String(color?.name || color?.label || '').trim();
  if (explicit) return explicit;
  const swatchId = String(color?.swatchId || '').toLowerCase();
  return SWATCH_LABELS[swatchId] || fallback;
}

const selectedMatchingItems = (items) => (items || [])
  .filter((item) => item?.selected && item?.id && item?.isCompatible !== false)
  .slice()
  .sort((left, right) => (left.selOrder || 99) - (right.selOrder || 99));

const brightnessForColor = (color) => {
  if (!color) return null;
  const swatchId = String(color.swatchId || SWATCH_BY_LABEL[color.name] || '').toLowerCase();
  if (Object.prototype.hasOwnProperty.call(SWATCH_BRIGHTNESS, swatchId)) {
    return SWATCH_BRIGHTNESS[swatchId];
  }
  return null;
};

const brightnessForMatchingItem = (item) => {
  if (Number.isFinite(item?.colorBrightness)) return item.colorBrightness;
  const group = String(item?.colorGroup || '').toLowerCase();
  return Object.prototype.hasOwnProperty.call(SWATCH_BRIGHTNESS, group)
    ? SWATCH_BRIGHTNESS[group]
    : null;
};

export function matchingItemForColor(color, matchClothing, { preferMain = false } = {}) {
  const selected = selectedMatchingItems(matchClothing);
  if (!selected.length) return null;
  if (preferMain || selected.length === 1) return selected[0];

  const productBrightness = brightnessForColor(color);
  if (productBrightness == null) return selected[0];

  return selected.reduce((best, item) => {
    const brightness = brightnessForMatchingItem(item);
    const contrast = brightness == null ? -1 : Math.abs(brightness - productBrightness);
    return contrast > best.contrast ? { item, contrast } : best;
  }, { item: selected[0], contrast: -1 }).item;
}

export function matchingIdsForColor(color, matchClothing, options) {
  const item = matchingItemForColor(color, matchClothing, options);
  return item ? [item.id] : [];
}
