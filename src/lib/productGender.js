const MEN_TOKENS = new Set(['men', 'male', '남성', '남']);

export function genderForClothingType(clothingType, targetGenders) {
  if (clothingType === 'dress') return 'women';
  const genders = Array.isArray(targetGenders) ? targetGenders : [];
  return genders.length > 0
    && genders.every((value) => MEN_TOKENS.has(String(value).toLowerCase()))
    ? 'men' : 'women';
}

// 대상 성별은 항상 0~1개 — 폼의 성별 칩이 단일 선택이라 2개가 저장되면 칩엔 첫 값만 켜지고
// 매칭 의류는 남녀 전부가 뜬다(2026-07-31 사용자 결정: 칩과 매칭을 일치시킨다).
export function normalizeTargetGendersForClothingType(clothingType, targetGenders) {
  if (clothingType === 'dress') return ['women'];
  return Array.isArray(targetGenders) ? targetGenders.slice(0, 1) : [];
}
