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
  fit: item.fit ?? null,
  length: item.length ?? null,
  fitCategory: item.fitCategory ?? null,
  isCustom: item.isCustom === true,
  isCompatible: item.isCompatible !== false,
  cutoutStatus: item.cutoutStatus ?? null,
  selected: selOrder != null,
  ...(selOrder != null ? { selOrder } : {}),
});

// 누끼 상태 폴링은 분석을 통째로 치환하면 안 된다 — 저장 왕복 중에 5초 틱이 끼면
// 편집 중이던 값이 한 틱 되돌아간다. 폴링이 새로 가져오는 정보는 매칭 목록뿐이라
// 그것만 얹는다. 목록이 비정상(배열 아님)이면 아무것도 바꾸지 않는다.
export const mergeMatchClothing = (prev, nextAnalysis) => {
  const next = nextAnalysis?.matchClothing;
  if (!prev || !Array.isArray(next)) return prev;
  return { ...prev, matchClothing: next };
};

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
