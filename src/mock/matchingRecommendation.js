/* =============================================================
   mock/matchingRecommendation.js — recommendation helpers for
   matching clothing seeds. Keeps seed data, ranking, and legacy UI
   shape mapping separate from mock/db.js.
   ============================================================= */
import { seedMatchingItems } from './seedMatchingItems.js';

const DEFAULT_STYLE_TAGS = ['basic', 'daily', 'clean'];
const TOP_SIDE_TYPES = ['top', 'outer', 'dress'];

const unique = (items) => [...new Set((items || []).filter(Boolean))];

export function getComplementaryMatchingType(clothingType) {
  return TOP_SIDE_TYPES.includes(clothingType) ? 'bottom' : 'top';
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

  // 색상 밝음→어두움 순으로 나열한다(colorBrightness 100→0). 동률은 sortOrder.
  const sorted = items
    .filter((item) => item.isActive)
    .filter((item) => item.clothingType === preferredType)
    .filter((item) => !genders.length || item.gender === 'unisex' || genders.includes(item.gender))
    .slice()
    .sort((a, b) => ((b.colorBrightness ?? 50) - (a.colorBrightness ?? 50)) || (a.sortOrder - b.sortOrder));

  return limit ? sorted.slice(0, limit) : sorted;
}

export function toLegacyMatchClothing(items, { selectedCount = 2 } = {}) {
  return (items || []).map((item, index) => {
    const selected = index < selectedCount;
    return {
      id: item.id,
      name: item.name,
      thumb: item.thumbnailUrl,
      imageUrl: item.imageUrl,
      thumbnailUrl: item.thumbnailUrl,
      gender: item.gender,
      selected,
      ...(selected ? { selOrder: index + 1 } : {}),
    };
  });
}

export function recommendLegacyMatchClothing({
  clothingType = 'top',
  targetGenders = ['women'],
  styleTags = DEFAULT_STYLE_TAGS,
  current = [],
} = {}) {
  const candidates = recommendMatchingItems({ clothingType, targetGenders, styleTags });
  const selectedIds = (current || [])
    .filter((item) => item.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0))
    .map((item) => item.id);
  const validSelected = selectedIds.filter((id) => candidates.some((item) => item.id === id)).slice(0, 2);
  // 이전 선택이 새 후보군에서 전부 사라지면(예: 상의→하의 전환으로 보완 타입이 바뀜)
  // 첫 로드(toLegacyMatchClothing)와 같은 계약대로 상위 2개를 메인/서브 기본 선택한다.
  const effectiveSelected = validSelected.length ? validSelected : candidates.slice(0, 2).map((item) => item.id);

  return candidates.map((item) => {
    const selIndex = effectiveSelected.indexOf(item.id);
    const selected = selIndex >= 0;
    return {
      id: item.id,
      name: item.name,
      thumb: item.thumbnailUrl,
      imageUrl: item.imageUrl,
      thumbnailUrl: item.thumbnailUrl,
      gender: item.gender,
      selected,
      ...(selected ? { selOrder: selIndex + 1 } : {}),
    };
  });
}
