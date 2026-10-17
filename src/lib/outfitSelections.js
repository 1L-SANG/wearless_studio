const SLOTS = Object.freeze({
  outer: ['inner', 'bottom', 'footwear', 'bag', 'accessory'],
  top: ['bottom', 'footwear', 'bag', 'accessory'],
  bottom: ['top', 'footwear', 'bag', 'accessory'],
  dress: ['footwear', 'bag', 'accessory'],
});

export const OUTFIT_SLOT_LABELS = Object.freeze({ inner: '이너', top: '상의', bottom: '하의',
  footwear: '신발', bag: '가방', accessory: '액세서리' });

export const outfitSlotsFor = (clothingType) => SLOTS[clothingType] || [];
export const complementaryOutfitSlot = (clothingType) => (
  clothingType === 'bottom' ? 'top' : ['top', 'outer'].includes(clothingType) ? 'bottom' : null
);

export function normalizeOutfitSelections(value, clothingType, matchClothing = []) {
  const slots = {};
  const complementary = complementaryOutfitSlot(clothingType);
  for (const slot of outfitSlotsFor(clothingType)) {
    const selection = value?.slots?.[slot];
    const legacy = slot === complementary ? matchClothing.find((item) => item.selected
      && item.isCompatible !== false && (item.clothingType == null || item.clothingType === slot)) : null;
    slots[slot] = {
      itemId: typeof selection?.itemId === 'string' && selection.itemId ? selection.itemId
        : selection ? null : legacy?.id || null,
      origin: selection?.origin === 'user' ? 'user' : 'ai',
    };
  }
  return { version: 1, slots,
    setIndex: Number.isInteger(value?.setIndex) && value.setIndex >= 0 && value.setIndex < 3 ? value.setIndex : 0,
    optional: { bag: value?.optional?.bag === true, accessory: value?.optional?.accessory === true } };

}

export function outfitSelectionsForTypeChange(analysis, clothingType) {
  const previous = normalizeOutfitSelections(analysis.outfitSelections, analysis.clothingType, analysis.matchClothing);
  const role = complementaryOutfitSlot(analysis.clothingType);
  // Migrate a saved legacy pick in its original role before pruning impossible roles.
  if (!analysis.outfitSelections?.slots?.[role] && previous.slots[role]?.itemId) {
    previous.slots[role].origin = 'user';
  }
  return normalizeOutfitSelections(previous, clothingType);
}

export function syncOutfitLegacySelection(analysis) {
  if (!analysis?.outfitSelections) return analysis;
  const outfitSelections = normalizeOutfitSelections(analysis.outfitSelections, analysis.clothingType, analysis.matchClothing);
  const slot = complementaryOutfitSlot(analysis.clothingType);
  const selectedId = outfitSelections.slots[slot]?.itemId || null;
  const matchClothing = (analysis.matchClothing || []).map((item) => ({ ...item,
    selected: item.id === selectedId,
    selOrder: item.id === selectedId ? 1 : undefined,
  }));
  return { ...analysis, outfitSelections, matchClothing };
}

export function setOutfitSelection(analysis, slot, itemId) {
  if (!outfitSlotsFor(analysis.clothingType).includes(slot)) return null;
  const complementary = complementaryOutfitSlot(analysis.clothingType);
  const candidates = slot === complementary ? analysis.matchClothing || []
    : analysis.outfitRecommendation?.candidates?.[slot] || [];
  if (itemId != null && !candidates.some((item) => item.id === itemId && item.isCompatible !== false
    && (slot !== complementary || item.clothingType == null || item.clothingType === complementary))) return null;
  const outfitSelections = normalizeOutfitSelections(analysis.outfitSelections, analysis.clothingType, analysis.matchClothing);
  const changed = outfitSelections.slots[slot]?.itemId !== (itemId || null);
  outfitSelections.slots[slot] = { itemId: itemId || null, origin: 'user' };
  const synced = syncOutfitLegacySelection({ ...analysis, outfitSelections });
  return { outfitSelections, ...(slot === complementary && changed ? { matchClothing: synced.matchClothing } : {}) };
}

export function applyOutfitSet(analysis, index) {
  const set = analysis.outfitRecommendation?.sets?.[index];
  if (!Number.isInteger(index) || !set) return null;
  const outfitSelections = normalizeOutfitSelections(analysis.outfitSelections, analysis.clothingType, analysis.matchClothing);
  for (const slot of outfitSlotsFor(analysis.clothingType)) {
    const prior = outfitSelections.slots[slot];
    if (prior.origin === 'user' && prior.itemId === null) continue;
    if (Object.hasOwn(outfitSelections.optional, slot) && !outfitSelections.optional[slot]) continue;
    outfitSelections.slots[slot] = { itemId: set.slots[slot] ?? null, origin: 'ai' };
  }
  outfitSelections.setIndex = index;
  const synced = syncOutfitLegacySelection({ ...analysis, outfitSelections });
  return { outfitSelections, outfitSetIndex: index, matchClothing: synced.matchClothing };
}

export function toggleOutfitOptional(analysis, slot, enabled) {
  if (!['bag', 'accessory'].includes(slot) || typeof enabled !== 'boolean') return null;
  const outfitSelections = normalizeOutfitSelections(analysis.outfitSelections, analysis.clothingType, analysis.matchClothing);
  outfitSelections.optional[slot] = enabled;
  const prior = outfitSelections.slots[slot];
  if (enabled && !prior.itemId && prior.origin !== 'user') {
    const set = analysis.outfitRecommendation?.sets?.[outfitSelections.setIndex];
    outfitSelections.slots[slot] = { itemId: set?.slots?.[slot] ?? null, origin: 'ai' };
  }
  return { outfitSelections };
}

// Status reads own suggestions and their signed handoff, never the seller's draft.
export function outfitRecommendationPatch(response) {
  return Object.fromEntries(['outfitRecommendation', 'outfitRecommendationHandoff']
    .filter(key => Object.hasOwn(response || {}, key)).map(key => [key, response[key]]));
}

// 저장 중에도 선택할 수 있으므로 직전 user 선택이 오래된 응답보다 우선한다.
export function mergeOutfitAnalysis(current, response) {
  if (!current || !response) return current;
  const next = { ...current };
  for (const key of ['matchClothing', 'outfitSelections', 'outfitRecommendation', 'outfitRecommendationHandoff']) {
    if (Object.hasOwn(response, key)) next[key] = response[key];
  }
  if (!next.outfitSelections) return next;
  const incoming = normalizeOutfitSelections(next.outfitSelections, current.clothingType, next.matchClothing);
  const sameContext = response.outfitRecommendation && current.outfitRecommendation
    && current.outfitRecommendation.contextKey === response.outfitRecommendation.contextKey;
  const lateRecommendation = sameContext || (current.outfitRecommendation?.status === 'pending'
    && !current.outfitRecommendation?.contextKey && !response.outfitRecommendation?.contextKey);
  for (const slot of outfitSlotsFor(current.clothingType)) {
    const latest = current.outfitSelections?.slots?.[slot];
    if (latest && (latest.origin === 'user' || lateRecommendation)) incoming.slots[slot] = { ...latest };
  }
  if (current.outfitSelections?.optional) incoming.optional = { ...current.outfitSelections.optional };
  if ((!response.outfitRecommendation || sameContext) && current.outfitSelections?.setIndex != null) {
    incoming.setIndex = current.outfitSelections.setIndex;
  }
  return syncOutfitLegacySelection({ ...next, outfitSelections: incoming });
}

export function outfitSelectionIds(value) {
  return Object.fromEntries(Object.entries(value?.slots || {}).filter(([slot, choice]) => choice?.itemId && (!['bag', 'accessory'].includes(slot) || value?.optional?.[slot] === true))
    .sort(([left], [right]) => left.localeCompare(right)).map(([slot, choice]) => [slot, choice.itemId]));
}

export const isOutfitContextPatch = (patch) => ['clothingType', 'subCategory', 'customCategory', 'targetGenders', 'styleTags', 'colors', 'fit', 'materials']
  .some((key) => Object.hasOwn(patch || {}, key));

export function outfitContextKey(analysis, product) {
  return JSON.stringify([analysis?.clothingType, analysis?.subCategory, analysis?.customCategory,
    analysis?.targetGenders, analysis?.styleTags, analysis?.fit, analysis?.materials,
    (product?.colors || analysis?.colors || []).map((color) =>
      [color.id, color.name, color.swatchId, color.isBase, (color.images || []).map((image) => [image.id, image.slot, image.src])])]);
}

export function mergeOutfitSaveResponse(current, response, { requestContextKey, product } = {}) {
  if (requestContextKey && requestContextKey !== outfitContextKey(current, product)) return current;
  return mergeOutfitAnalysis(current, response);
}

// A full draft re-save is not a new paid recommendation request.
export function hasOutfitContextChange(current, patch) {
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort()
      .map(key => [key, canonical(value[key])]));
    return value ?? null;
  };
  return ['clothingType', 'subCategory', 'customCategory', 'targetGenders', 'styleTags', 'colors', 'fit', 'materials']
    .some(key => {
      if (!Object.hasOwn(patch || {}, key) || (key === 'clothingType' && patch[key] == null)) return false;
      const value = (source) => ['targetGenders', 'styleTags'].includes(key) && Array.isArray(source?.[key])
        ? [...source[key]].sort() : source?.[key];
      return JSON.stringify(canonical(value(current))) !== JSON.stringify(canonical(value(patch)));
    });
}
