/* =============================================================
   mock/matchingRecommendation.js — recommendation helpers for
   matching clothing seeds. Keeps seed data, ranking, and legacy UI
   shape mapping separate from mock/db.js.
   ============================================================= */
import { seedMatchingItems } from './seedMatchingItems.js';
import { LIMITS } from '../lib/limits.js';

const DEFAULT_STYLE_TAGS = ['basic', 'daily', 'clean'];
const TOP_SIDE_TYPES = ['top', 'outer'];
const FULL_LENGTH_PANTS_CATEGORIES = new Set([
  '데님팬츠', '트라우저', '팬츠', '스웨트팬츠', '치노팬츠',
  'denim_pants', 'trousers', 'pants', 'sweatpants', 'chino_pants',
]);
const SKIRT_CATEGORIES = new Set(['스커트', 'skirt']);

const pairMap = (pairs) => new Map(
  pairs.map(([left, right, score]) => [`${left}|${right}`, score]),
);

// server/app/agents/style_affinity.py 사본 — mock도 실서버와 같은 스타일 합을 계산한다.
const STYLE_AFFINITY = pairMap([
  ['basic', 'daily', 0.9],
  ['basic', 'minimal', 0.85],
  ['basic', 'casual', 0.75],
  ['formal', 'minimal', 0.8],
  ['formal', 'classic', 0.85],
  ['sporty', 'casual', 0.8],
  ['sporty', 'daily', 0.6],
  ['minimal', 'casual', 0.7],
  ['daily', 'casual', 0.85],
  ['trendy', 'casual', 0.65],
  ['trendy', 'daily', 0.55],
  ['formal', 'daily', 0.3],
  ['sporty', 'formal', 0.15],
  ['minimal', 'trendy', 0.5],
  ['basic', 'trendy', 0.45],
  ['minimal', 'modern', 0.92],
  ['minimal', 'sophisticated', 0.82],
  ['minimal', 'chic', 0.78],
  ['modern', 'sophisticated', 0.9],
  ['modern', 'chic', 0.85],
  ['modern', 'luxury', 0.72],
  ['basic', 'modern', 0.72],
  ['daily', 'cozy', 0.88],
  ['casual', 'cozy', 0.9],
  ['minimal', 'cozy', 0.65],
  ['street', 'trendy', 0.9],
  ['street', 'y2k', 0.88],
  ['street', 'unique', 0.82],
  ['street', 'casual', 0.82],
  ['y2k', 'trendy', 0.92],
  ['y2k', 'unique', 0.85],
  ['y2k', 'retro', 0.8],
  ['unique', 'trendy', 0.83],
  ['vintage', 'retro', 0.9],
  ['retro', 'trendy', 0.72],
  ['sporty', 'athleisure', 0.95],
  ['athleisure', 'casual', 0.88],
  ['athleisure', 'daily', 0.78],
  ['athleisure', 'street', 0.75],
  ['sporty', 'street', 0.7],
  ['feminine', 'lovely', 0.9],
  ['feminine', 'romantic', 0.92],
  ['feminine', 'chic', 0.78],
  ['feminine', 'sophisticated', 0.82],
  ['feminine', 'luxury', 0.72],
  ['lovely', 'romantic', 0.88],
  ['romantic', 'vintage', 0.78],
  ['chic', 'luxury', 0.86],
  ['chic', 'sophisticated', 0.9],
  ['classic', 'sophisticated', 0.92],
  ['classic', 'luxury', 0.86],
  ['formal', 'sophisticated', 0.92],
  ['formal', 'luxury', 0.84],
  ['formal', 'chic', 0.8],
  ['formal', 'workwear', 0.86],
  ['workwear', 'classic', 0.82],
  ['workwear', 'modern', 0.8],
  ['preppy', 'classic', 0.88],
  ['preppy', 'casual', 0.74],
  ['preppy', 'vintage', 0.72],
]);

// server/app/agents/color_harmony.py 사본. 한 방향만 저장하고 조회 때 대칭 처리한다.
export const COLOR_HARMONY = pairMap([
  // 톤온톤
  ['white', 'white', 0.55],
  ['ivory', 'ivory', 0.55],
  ['gray', 'gray', 0.55],
  ['black', 'black', 0.55],
  ['beige', 'beige', 0.55],
  ['brown', 'brown', 0.55],
  ['red', 'red', 0.55],
  ['yellow', 'yellow', 0.55],
  ['green', 'green', 0.55],
  ['blue', 'blue', 0.55],
  ['navy', 'navy', 0.55],
  ['pink', 'pink', 0.55],
  ['khaki', 'khaki', 0.55],
  // 무채색끼리
  ['white', 'ivory', 0.75],
  ['white', 'gray', 0.82],
  ['white', 'black', 0.90],
  ['ivory', 'gray', 0.78],
  ['ivory', 'black', 0.82],
  ['gray', 'black', 0.72],
  // 화이트·아이보리 × 유채색/어스톤
  ['white', 'beige', 0.86],
  ['white', 'brown', 0.80],
  ['white', 'red', 0.88],
  ['white', 'yellow', 0.78],
  ['white', 'green', 0.82],
  ['white', 'blue', 0.92],
  ['white', 'navy', 0.90],
  ['white', 'pink', 0.86],
  ['white', 'khaki', 0.80],
  ['ivory', 'beige', 0.80],
  ['ivory', 'brown', 0.82],
  ['ivory', 'red', 0.80],
  ['ivory', 'yellow', 0.74],
  ['ivory', 'green', 0.78],
  ['ivory', 'blue', 0.84],
  ['ivory', 'navy', 0.88],
  ['ivory', 'pink', 0.84],
  ['ivory', 'khaki', 0.82],
  // 그레이 × 유채색/어스톤
  ['gray', 'beige', 0.78],
  ['gray', 'brown', 0.70],
  ['gray', 'red', 0.82],
  ['gray', 'yellow', 0.72],
  ['gray', 'green', 0.76],
  ['gray', 'blue', 0.82],
  ['gray', 'navy', 0.76],
  ['gray', 'pink', 0.82],
  ['gray', 'khaki', 0.72],
  // 블랙 × 유채색/어스톤
  ['black', 'beige', 0.90],
  ['black', 'brown', 0.35],
  ['black', 'red', 0.85],
  ['black', 'yellow', 0.78],
  ['black', 'green', 0.68],
  ['black', 'blue', 0.62],
  ['black', 'navy', 0.38],
  ['black', 'pink', 0.78],
  ['black', 'khaki', 0.58],
  // 베이지 중심 클래식/어스톤
  ['beige', 'brown', 0.78],
  ['beige', 'red', 0.72],
  ['beige', 'yellow', 0.70],
  ['beige', 'green', 0.75],
  ['beige', 'blue', 0.82],
  ['beige', 'navy', 0.92],
  ['beige', 'pink', 0.78],
  ['beige', 'khaki', 0.80],
  // 브라운·카키 중심 어스톤
  ['brown', 'red', 0.65],
  ['brown', 'yellow', 0.68],
  ['brown', 'green', 0.72],
  ['brown', 'blue', 0.58],
  ['brown', 'navy', 0.62],
  ['brown', 'pink', 0.70],
  ['brown', 'khaki', 0.78],
  ['red', 'khaki', 0.55],
  ['yellow', 'khaki', 0.60],
  ['green', 'khaki', 0.72],
  ['blue', 'khaki', 0.55],
  ['navy', 'khaki', 0.72],
  ['pink', 'khaki', 0.58],
  // 강한 유채색끼리
  ['red', 'yellow', 0.30],
  ['red', 'green', 0.22],
  ['red', 'blue', 0.35],
  ['red', 'navy', 0.65],
  ['red', 'pink', 0.30],
  ['yellow', 'green', 0.30],
  ['yellow', 'blue', 0.35],
  ['yellow', 'navy', 0.58],
  ['yellow', 'pink', 0.28],
  ['green', 'blue', 0.35],
  ['green', 'navy', 0.62],
  ['green', 'pink', 0.25],
  ['blue', 'navy', 0.60],
  ['blue', 'pink', 0.35],
  ['navy', 'pink', 0.68],
]);

const unique = (items) => [...new Set((items || []).filter(Boolean))];

const symmetricScore = (scores, left, right, fallback) => {
  if (!left || !right) return fallback;
  return scores.get(`${left}|${right}`) ?? scores.get(`${right}|${left}`) ?? fallback;
};

export const colorHarmonyScore = (productColor, itemColor) => (
  symmetricScore(COLOR_HARMONY, productColor, itemColor, 0.5)
);

export function productColorFrom(product, analysis) {
  const colors = Array.isArray(product?.colors) ? product.colors : [];
  const base = colors.find((color) => color?.isBase) || colors[0];
  if (base?.swatchId) return base.swatchId;
  // 서버 routes._matching_product_color 와 동일: 첫 제안이 아니라 기준 색 그룹과
  // 연결된 제안을 우선한다 — 다색 상품에서 엉뚱한 색이 랭킹에 들어가지 않게.
  const suggestions = Array.isArray(analysis?.swatchSuggestions) ? analysis.swatchSuggestions : [];
  const preferred = (base?.id && suggestions.find((s) => s?.colorGroupId === base.id)) || suggestions[0];
  return preferred?.swatchId || null;
}

const styleAffinityScore = (item, productTags) => (productTags || []).reduce(
  (total, productTag) => total + (item.styleTags || []).reduce(
    (subtotal, itemTag) => subtotal
      + symmetricScore(STYLE_AFFINITY, productTag, itemTag, 0),
    0,
  ),
  0,
);

const compareIds = (left, right) => {
  const a = String(left.id);
  const b = String(right.id);
  return a < b ? -1 : a > b ? 1 : 0;
};

const rankByStyleAndColor = (items, productTags, productColor, colorWeight) => {
  // 서버 retrieval._quantize 와 동일 — 합산 순서의 부동소수점 노이즈를 지워
  // 같은 태그 집합이 같은 점수가 되게 한다(id tie-break·서버 순서 일치).
  const quantize = (score) => Math.round(score * 1e9) / 1e9;

  const styleScored = items.map((item) => ({
    item,
    styleScore: quantize(styleAffinityScore(item, productTags)),
  }));
  if (!productColor || colorWeight <= 0) {
    return styleScored
      .sort((left, right) => (right.styleScore - left.styleScore)
        || compareIds(left.item, right.item))
      .map(({ item }) => item);
  }
  const maxStyle = Math.max(0, ...styleScored.map(({ styleScore }) => styleScore));
  const weight = Math.min(colorWeight, 1);
  return styleScored
    .map(({ item, styleScore }) => ({
      item,
      combinedScore: quantize((1 - weight) * (maxStyle > 0 ? styleScore / maxStyle : 0)
        + weight * colorHarmonyScore(productColor, item.colorGroup)),
    }))
    .sort((left, right) => (right.combinedScore - left.combinedScore)
      || compareIds(left.item, right.item))
    .map(({ item }) => item);
};

const colorFamily = (item) => {
  if (item.colorGroup) return `group:${item.colorGroup}`;
  if (item.colorBrightness == null) return null;
  if (item.colorBrightness <= 33) return 'brightness:dark';
  if (item.colorBrightness <= 66) return 'brightness:mid';
  return 'brightness:light';
};

export function diversifyTopTwo(items) {
  const ranked = [...(items || [])];
  if (ranked.length < 3) return ranked;
  const firstFamily = colorFamily(ranked[0]);
  if (!firstFamily || colorFamily(ranked[1]) !== firstFamily) return ranked;
  const replacement = ranked.findIndex((item, index) => (
    index >= 2 && colorFamily(item) && colorFamily(item) !== firstFamily
  ));
  if (replacement >= 2) [ranked[1], ranked[replacement]] = [ranked[replacement], ranked[1]];
  return ranked;
}

export function getComplementaryMatchingType(clothingType) {
  if (clothingType === 'dress') return null;
  return TOP_SIDE_TYPES.includes(clothingType) ? 'bottom' : 'top';
}

// Mock-only mirror of the server-derived field. It uses closed structured
// category/length metadata, never the seller-facing garment name.
export function fitCategoryFromMatchingMetadata(item) {
  // 내 옷(custom)은 조정 축 없음 — 올린 실물 그대로 입힌다(D11, 서버 matching.fit_category 미러).
  if (item?.isCustom) return null;
  if (item?.clothingType === 'top') return 'top';
  if (item?.clothingType !== 'bottom') return null;
  if (SKIRT_CATEGORIES.has(item.category)) return 'skirt';
  if (item.length === 'full' && FULL_LENGTH_PANTS_CATEGORIES.has(item.category)) return 'pants';
  return null;
}

function toLegacyMatchItem(item, selected, selOrder) {
  return {
    id: item.id,
    name: item.name,
    thumb: item.thumbnailUrl,
    imageUrl: item.imageUrl,
    thumbnailUrl: item.thumbnailUrl,
    gender: item.gender,
    clothingType: item.clothingType ?? null,
    category: item.category ?? null,
    fit: item.fit ?? null,
    length: item.length ?? null,
    fitCategory: item.fitCategory ?? fitCategoryFromMatchingMetadata(item),
    isCustom: item.isCustom === true,
    isCompatible: item.isCompatible !== false,
    selected,
    ...(selected ? { selOrder } : {}),
  };
}

export function recommendMatchingItems({
  clothingType = 'top',
  targetGenders = ['women'],
  styleTags = DEFAULT_STYLE_TAGS,
  productColor = null,
  colorWeight = 0.3,
  limit,
  items = seedMatchingItems,
} = {}) {
  const preferredType = getComplementaryMatchingType(clothingType);
  const genders = unique(targetGenders);
  if (!preferredType) return [];

  const custom = items
    .filter((item) => item.isCustom)
    .map((item) => ({ ...item, isCompatible: item.clothingType === preferredType }));
  const pool = items
    .filter((item) => !item.isCustom)
    .filter((item) => item.isActive)
    .filter((item) => item.clothingType === preferredType)
    .filter((item) => !genders.length || item.gender === 'unisex' || genders.includes(item.gender));
  // 서버 라우트 패리티: styleTags가 있을 때만 recommend_v1(스타일+색), 없으면
  // 레거시 밝기 정렬로 폴백한다.
  const ranked = styleTags.length
    ? rankByStyleAndColor(pool, styleTags, productColor, colorWeight)
    : pool.slice().sort((a, b) => ((b.colorBrightness ?? 50) - (a.colorBrightness ?? 50))
      || ((a.sortOrder ?? 0) - (b.sortOrder ?? 0)) || compareIds(a, b));
  const sorted = diversifyTopTwo(ranked);

  return [...custom, ...(limit == null ? sorted : sorted.slice(0, limit))];
}

export function toLegacyMatchClothing(items, { selectedCount = LIMITS.matchClothingMax } = {}) {
  return (items || []).map((item, index) => {
    const selected = index < selectedCount;
    return toLegacyMatchItem(item, selected, index + 1);
  });
}

export function recommendLegacyMatchClothing({
  clothingType = 'top',
  targetGenders = ['women'],
  styleTags = DEFAULT_STYLE_TAGS,
  productColor = null,
  colorWeight = 0.3,
  current = [],
  defaultSelection = true,
} = {}) {
  const customItems = (current || []).filter((item) => item.isCustom).map((item) => ({
    ...item,
    thumbnailUrl: item.thumbnailUrl || item.thumb,
    imageUrl: item.imageUrl || item.thumb,
    isActive: true,
    colorBrightness: 50,
    sortOrder: 0,
  }));
  const candidates = recommendMatchingItems({
    clothingType,
    targetGenders,
    styleTags,
    productColor,
    colorWeight,
    items: [...customItems, ...seedMatchingItems],
  });
  const selectedIds = (current || [])
    .filter((item) => item.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0))
    .map((item) => item.id);
  const selectable = candidates.filter((item) => item.isCompatible !== false);
  const validSelected = selectedIds.filter((id) => selectable.some((item) => item.id === id))
    .slice(0, LIMITS.matchClothingMax);
  // 이전 선택이 새 후보군에서 전부 사라지면(예: 상의→하의 전환으로 보완 타입이 바뀜)
  // 첫 로드(toLegacyMatchClothing)와 같은 계약대로 상위 항목 하나를 기본 선택한다.
  const fallback = defaultSelection
    ? selectable.filter((item) => item.isCustom !== true).slice(0, LIMITS.matchClothingMax)
    : [];
  const effectiveSelected = validSelected.length ? validSelected : fallback.map((item) => item.id);

  return candidates.map((item) => {
    const selIndex = effectiveSelected.indexOf(item.id);
    const selected = selIndex >= 0;
    return toLegacyMatchItem(item, selected, selIndex + 1);
  });
}

export function addCustomMatchToAnalysis(analysis, item) {
  const existing = (analysis?.matchClothing || []).filter((candidate) => candidate.id !== item.id);
  const custom = { ...item, isCustom: true, isCompatible: item.isCompatible !== false, selected: false };
  return {
    item: custom,
    analysis: { ...analysis, matchClothing: [custom, ...existing] },
  };
}

export function removeCustomMatchFromAnalysis(analysis) {
  const custom = (analysis?.matchClothing || []).find((item) => item.isCustom);
  if (!custom) return { ...analysis };
  const remaining = analysis.matchClothing.filter((item) => item.id !== custom.id);
  const selected = remaining.filter((item) => item.selected)
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99))
    .slice(0, LIMITS.matchClothingMax);
  const orderById = new Map(selected.map((item, index) => [item.id, index + 1]));
  const matchClothing = remaining.map((item) => orderById.has(item.id)
    ? { ...item, selected: true, selOrder: orderById.get(item.id) }
    : { ...item, selected: false, selOrder: undefined });
  let fitProfile = analysis.fitProfile;
  if (fitProfile?.matchingFit?.clothingId === custom.id) {
    fitProfile = { ...fitProfile };
    delete fitProfile.matchingFit;
  }
  return { ...analysis, matchClothing, ...(fitProfile ? { fitProfile } : {}) };
}
