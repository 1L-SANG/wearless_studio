import { fitProfileCategory, subcategoryLength } from '../lib/fitAxes.js';

/** 목 상품의 바닥 사진 판독. 긴팔은 사진만으로 확신하지 않는 예시다. */
export function mockFitRead(analysis) {
  const category = fitProfileCategory(analysis.clothingType, analysis.subCategory);
  const hasSleeve = ['top', 'outer', 'dress'].includes(category);
  const short = ['tshirt', 'mini_dress'].includes(analysis.subCategory);
  const reading = (value, confident = value !== null) => ({ value, confident });
  return {
    photoType: 'flat', sleeveType: hasSleeve ? (short ? 'short' : 'long') : null,
    topLength: reading(category === 'top' ? 'basic' : null),
    hemLength: reading(['skirt', 'dress'].includes(category) ? subcategoryLength(analysis.subCategory) || 'midi' : null),
    pantsLength: reading(category === 'pants' && analysis.subCategory !== 'leggings' ? 'ankle_cover' : null),
    sleeve: reading(hasSleeve ? (short ? 'short' : 'wrist') : null, hasSleeve && short),
    frontHem: reading(category === 'top' ? 'straight' : null),
  };
}
