export const ALLOWED_BRAND_USE_CATEGORIES = Object.freeze([
  '일반 의류',
  '액티브웨어',
  '홈웨어·잠옷',
]);

export const FORBIDDEN_BRAND_USE_CATEGORIES = Object.freeze([
  '속옷',
  '수영복',
]);

export const BRAND_USE_CATEGORIES = Object.freeze([
  ...ALLOWED_BRAND_USE_CATEGORIES,
  ...FORBIDDEN_BRAND_USE_CATEGORIES,
]);
