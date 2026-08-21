export const ALLOWED_BRAND_USE_CATEGORIES = Object.freeze([
  '일반 여성 의류',
  '남성 의류',
  '캐주얼·스트릿',
  '스포츠·애슬레저',
  '뷰티·화장품',
  '액세서리·잡화',
]);

export const FORBIDDEN_BRAND_USE_CATEGORIES = Object.freeze([
  '속옷·란제리',
  '수영복·비키니',
  '성인용품',
  '주류·담배',
  '의료·성형',
  '정치·종교',
]);

export const BRAND_USE_CATEGORIES = Object.freeze([
  ...ALLOWED_BRAND_USE_CATEGORIES,
  ...FORBIDDEN_BRAND_USE_CATEGORIES,
]);
