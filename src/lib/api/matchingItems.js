import { LIMITS } from '../limits.js';

// Match-candidate API row → analysis.matchClothing row.
// Keep this mapper small so mock/http contract tests share one source of truth.
export const toMatchItem = (item, selOrder) => ({
  id: item.id,
  name: item.name,
  gender: item.gender,
  thumb: item.thumb,
  imageUrl: item.imageUrl,
  thumbnailUrl: item.thumbnailUrl,
  clothingType: item.clothingType ?? null,
  category: item.category ?? null,
  colorName: item.colorName ?? null,
  colorGroup: item.colorGroup ?? null,
  colorBrightness: item.colorBrightness ?? null,
  fit: item.fit ?? null,
  length: item.length ?? null,
  fitCategory: item.fitCategory ?? null,
  isCustom: item.isCustom === true,
  isCompatible: item.isCompatible !== false,
  selected: selOrder != null,
  ...(selOrder != null ? { selOrder } : {}),
});

export const normalizeMatchClothingSelection = (items) => {
  const selectedOrder = new Map(
    (items || []).filter((item) => item.selected)
      .sort((left, right) => (left.selOrder || 99) - (right.selOrder || 99))
      .slice(0, LIMITS.matchClothingMax)
      .map((item, index) => [item.id, index + 1]),
  );
  return (items || []).map((item) => {
    const selOrder = selectedOrder.get(item.id);
    return {
      ...item,
      selected: selOrder != null,
      ...(selOrder != null ? { selOrder } : { selOrder: undefined }),
    };
  });
};

export const normalizeMatchIds = (ids) => (
  Array.isArray(ids) ? ids.filter(Boolean).slice(0, LIMITS.matchClothingMax) : []
);

export const reconcileMatchCompatibility = (items, clothingType) => {
  const expectedType = clothingType === 'dress'
    ? null
    : (clothingType === 'bottom' ? 'top' : 'bottom');
  const compatible = (item) => expectedType !== null
    && (item.clothingType == null || item.clothingType === expectedType);
  const selectedOrder = new Map(
    (items || []).filter((item) => item.selected && compatible(item))
      .sort((left, right) => (left.selOrder || 99) - (right.selOrder || 99))
      .slice(0, LIMITS.matchClothingMax)
      .map((item, index) => [item.id, index + 1]),
  );
  return (items || []).map((item) => {
    const selOrder = selectedOrder.get(item.id);
    return {
      ...item,
      isCompatible: compatible(item),
      selected: selOrder != null,
      ...(selOrder != null ? { selOrder } : { selOrder: undefined }),
    };
  });
};
