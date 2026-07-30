const MEN_TOKENS = new Set(['men', 'male', '남성', '남']);

export function genderForClothingType(clothingType, targetGenders) {
  if (clothingType === 'dress') return 'women';
  const genders = Array.isArray(targetGenders) ? targetGenders : [];
  return genders.length > 0
    && genders.every((value) => MEN_TOKENS.has(String(value).toLowerCase()))
    ? 'men' : 'women';
}

export function normalizeTargetGendersForClothingType(clothingType, targetGenders) {
  if (clothingType === 'dress') return ['women'];
  return Array.isArray(targetGenders) ? targetGenders : [];
}
