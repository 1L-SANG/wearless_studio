export const ALLOWED_BRAND_USE_CATEGORIES = Object.freeze([
  '상의',
  '하의',
  '아우터',
  '원피스',
  '니트·스웨터',
  '데님',
  '셋업·수트',
  '스커트',
  '트레이닝·애슬레저',
  '잡화·액세서리',
  '뷰티·화장품',
]);

export const FORBIDDEN_BRAND_USE_CATEGORIES = Object.freeze([
  '속옷·란제리',
  '수영복·비키니',
]);

export const BRAND_USE_CATEGORIES = Object.freeze([
  ...ALLOWED_BRAND_USE_CATEGORIES,
  ...FORBIDDEN_BRAND_USE_CATEGORIES,
]);
