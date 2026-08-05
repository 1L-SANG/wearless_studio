/* =============================================================
   mock/matchingRecommendation.js — recommendation helpers for
   matching clothing seeds. Keeps seed data, ranking, and legacy UI
   shape mapping separate from mock/db.js.
   ============================================================= */
import { seedMatchingItems } from './seedMatchingItems.js';

const DEFAULT_STYLE_TAGS = ['basic', 'daily', 'clean'];
const TOP_SIDE_TYPES = ['top', 'outer'];
const FULL_LENGTH_PANTS_CATEGORIES = new Set([
  '데님팬츠', '트라우저', '팬츠', '스웨트팬츠', '치노팬츠',
  'denim_pants', 'trousers', 'pants', 'sweatpants', 'chino_pants',
]);
const SKIRT_CATEGORIES = new Set(['스커트', 'skirt']);

const unique = (items) => [...new Set((items || []).filter(Boolean))];

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
  limit,
  items = seedMatchingItems,
} = {}) {
  const preferredType = getComplementaryMatchingType(clothingType);
  const genders = unique(targetGenders);
  if (!preferredType) return [];

  // 색상 밝음→어두움 순으로 나열한다(colorBrightness 100→0). 동률은 sortOrder.
  const custom = items
    .filter((item) => item.isCustom)
    .map((item) => ({ ...item, isCompatible: item.clothingType === preferredType }));
  const sorted = items
    .filter((item) => !item.isCustom)
    .filter((item) => item.isActive)
    .filter((item) => item.clothingType === preferredType)
    .filter((item) => !genders.length || item.gender === 'unisex' || genders.includes(item.gender))
    .slice()
    .sort((a, b) => ((b.colorBrightness ?? 50) - (a.colorBrightness ?? 50)) || (a.sortOrder - b.sortOrder));

  return [...custom, ...(limit ? sorted.slice(0, limit) : sorted)];
}

export function toLegacyMatchClothing(items, { selectedCount = 2 } = {}) {
  return (items || []).map((item, index) => {
    const selected = index < selectedCount;
    return toLegacyMatchItem(item, selected, index + 1);
  });
}

export function recommendLegacyMatchClothing({
  clothingType = 'top',
  targetGenders = ['women'],
  styleTags = DEFAULT_STYLE_TAGS,
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
    items: [...customItems, ...seedMatchingItems],
  });
  const selectedIds = (current || [])
    .filter((item) => item.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0))
    .map((item) => item.id);
  const selectable = candidates.filter((item) => item.isCompatible !== false);
  const validSelected = selectedIds.filter((id) => selectable.some((item) => item.id === id)).slice(0, 2);
  // 이전 선택이 새 후보군에서 전부 사라지면(예: 상의→하의 전환으로 보완 타입이 바뀜)
  // 첫 로드(toLegacyMatchClothing)와 같은 계약대로 상위 2개를 메인/서브 기본 선택한다.
  const fallback = defaultSelection
    ? selectable.filter((item) => item.isCustom !== true).slice(0, 2)
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
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99)).slice(0, 2);
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
