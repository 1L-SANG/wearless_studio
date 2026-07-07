/* =============================================================
   lib/api/shapes — 클라 소유 기본 shape (계약 §6).
   httpAdapter 가 mock db.js 에 의존하지 않도록, AI 분석이 산출하지 못하는
   필드(models·selectedModelId·측정 구조 등)의 편집용 기본값을 여기 둔다.
   AI 산출 필드는 analyzeProduct 콜러가 덮어쓴다.
   ============================================================= */

// 인물모델 썸네일(public/models/). women/men 베이스. (인물모델 서버 배선은 별도 — 현재 편집용 기본값.)
const DEFAULT_MODELS = [
  { id: 'mA', name: '모델 A', gender: 'women', thumb: '/models/women/w1.webp', recommended: true },
  { id: 'mB', name: '모델 B', gender: 'men', thumb: '/models/men/m1.webp', recommended: false },
  { id: 'mC', name: '모델 C', gender: 'men', thumb: '/models/men/m2.webp', recommended: false },
];

// 실측 템플릿 — key 는 영문 토큰(계약 §4). value 는 AI 미산출 → null(사용자 직접 입력, PRD §6.5).
const MEASUREMENT_TEMPLATE = [
  { key: 'totalLength', value: null, unit: 'cm' },
  { key: 'shoulderWidth', value: null, unit: 'cm' },
  { key: 'chestWidth', value: null, unit: 'cm' },
  { key: 'sleeveLength', value: null, unit: 'cm' },
];

// analyzeProduct 의 shape 뼈대 — AnalysisForm 이 무가드로 읽는 필드 전부 포함(계약 §6).
// AI 산출 필드(clothingType/materials/styleTags 등)는 콜러가 덮어쓴다.
export function defaultAnalysisShape() {
  return {
    clothingType: null, subCategory: null, targetGenders: [],
    fit: null, suggestedName: '',
    materials: [], sellingPoints: [], aiSuggestedPoints: [],
    styleTags: [], swatchSuggestions: [],
    selectedModelId: 'mA', models: DEFAULT_MODELS.map((m) => ({ ...m })),
    matchClothing: [],
    washCare: '', locked: false, measurementsUnknown: false,
    measurements: MEASUREMENT_TEMPLATE.map((m) => ({ ...m })),
    fitProfile: null,
  };
}
