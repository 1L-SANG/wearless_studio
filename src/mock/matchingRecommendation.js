/* =============================================================
   mock/matchingRecommendation.js — recommendation helpers for
   matching clothing seeds. Keeps seed data, ranking, and legacy UI
   shape mapping separate from mock/db.js.
   ============================================================= */
import { seedMatchingItems } from './seedMatchingItems.js';

const DEFAULT_STYLE_TAGS = ['basic', 'daily', 'clean'];
const TOP_SIDE_TYPES = ['top', 'outer', 'dress'];

const unique = (items) => [...new Set((items || []).filter(Boolean))];
const overlapCount = (left, right) => {
  const rightSet = new Set(right || []);
  return (left || []).reduce((count, tag) => count + (rightSet.has(tag) ? 1 : 0), 0);
};

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
  const tags = unique(styleTags.length ? styleTags : DEFAULT_STYLE_TAGS);

  const scored = items
    .filter((item) => item.isActive)
    .filter((item) => item.clothingType === preferredType)
    .filter((item) => !genders.length || item.gender === 'unisex' || genders.includes(item.gender))
    .map((item) => ({ item, score: overlapCount(item.styleTags, tags) }))
    .sort((a, b) => (b.score - a.score) || (a.item.sortOrder - b.item.sortOrder))
    .map(({ item }) => item);

  return limit ? scored.slice(0, limit) : scored;
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
      selected,
      ...(selected ? { selOrder: index + 1 } : {}),
    };
  });
}
