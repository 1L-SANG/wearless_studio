/* 기준 색상은 분석에서 사용자가 고른 메인 매칭 의류를 유지한다. 추가 색상은
   서버가 추천한 호환 후보 전체에서 명도 대비가 큰 한 벌을 자동으로 골라,
   선택 상한이 1벌이어도 확장형 컬러 룩마다 어울리는 코디를 배정한다. */

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

const SWATCH_ALIASES = Object.freeze([
  ['아이보리', 'ivory'], ['크림', 'ivory'], ['오프화이트', 'ivory'],
  ['화이트', 'white'], ['흰색', 'white'],
  ['베이지', 'beige'], ['브라운', 'brown'], ['카멜', 'brown'],
  ['그레이', 'gray'], ['회색', 'gray'], ['차콜', 'gray'],
  ['스카이블루', 'blue'], ['블루', 'blue'], ['파랑', 'blue'],
  ['네이비', 'navy'], ['남색', 'navy'],
  ['그린', 'green'], ['초록', 'green'], ['카키', 'green'],
  ['레드', 'red'], ['빨강', 'red'], ['핑크', 'pink'], ['옐로우', 'yellow'],
  ['블랙', 'black'], ['검정', 'black'],
]);

const SWATCH_LABELS = Object.freeze(Object.fromEntries(
  Object.entries(SWATCH_BY_LABEL).map(([label, swatchId]) => [swatchId, label]),
));

export function colorDisplayName(color, fallback = '색상') {
  const explicit = String(color?.name || color?.label || '').trim();
  if (explicit) return explicit;
  const swatchId = String(color?.swatchId || '').toLowerCase();
  return SWATCH_LABELS[swatchId] || fallback;
}

const compatibleMatchingItems = (items) => (items || [])
  .filter((item) => item?.id && item?.isCompatible !== false)
  .slice()
  .sort((left, right) => (left.selOrder || 99) - (right.selOrder || 99));

const selectedMainItem = (items) => compatibleMatchingItems(items)
  .find((item) => item.selected) || null;

const swatchForColor = (color) => {
  const explicit = String(color?.swatchId || '').toLowerCase();
  if (Object.prototype.hasOwnProperty.call(SWATCH_BRIGHTNESS, explicit)) return explicit;
  const name = String(color?.name || color?.label || '').replace(/\s+/g, '').toLowerCase();
  const exact = SWATCH_BY_LABEL[color?.name];
  if (exact) return exact;
  const alias = SWATCH_ALIASES.find(([label]) => name.includes(label.toLowerCase()));
  return alias?.[1] || null;
};

const brightnessForColor = (color) => {
  if (!color) return null;
  const swatchId = swatchForColor(color);
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
  const compatible = compatibleMatchingItems(matchClothing);
  if (!compatible.length) return null;
  const main = selectedMainItem(matchClothing) || compatible[0];
  if (preferMain) return main;

  // 사용자가 직접 올린 옷은 명시적으로 선택했을 때만 자동 컬러 룩에 사용한다.
  const candidates = compatible.filter((item) => !item.isCustom || item.selected);
  if (!candidates.length) return main;

  const productBrightness = brightnessForColor(color);
  if (productBrightness == null) return main;

  return candidates.reduce((best, item) => {
    const brightness = brightnessForMatchingItem(item);
    const contrast = brightness == null ? -1 : Math.abs(brightness - productBrightness);
    return contrast > best.contrast ? { item, contrast } : best;
  }, { item: main, contrast: -1 }).item;
}

export function matchingIdsForColor(color, matchClothing, options) {
  const item = matchingItemForColor(color, matchClothing, options);
  return item ? [item.id] : [];
}
