import { createMeasurementFields } from '../measurementSchema.js';

// analyzeProduct 의 shape 뼈대 — AnalysisForm 이 무가드로 읽는 필드 전부 포함(계약 §6).
// AI 산출 필드(clothingType/materials/styleTags 등)는 콜러가 덮어쓴다.
export function defaultAnalysisShape(clothingType = 'top') {
  return {
    clothingType: null, subCategory: null, targetGenders: [],
    // enum 밖 의류의 자유 명칭(AG-01 추측 + 셀러 주관식 수정, 계약 §3.2). mock/db.js 에는
    // 있는데 여기 없어서 http 경로에서만 유실됐다 — AI 가 "후드 집업" 을 추측해도 폼에 안 떴다.
    customCategory: null,
    fit: null, suggestedName: '',
    materials: [], sellingPoints: [], aiSuggestedPoints: [], mdComment: '',
    styleTags: [], swatchSuggestions: [],
    selectedModelId: null, stylingModelId: null, brandUseCategory: null, models: [],
    matchClothing: [],
    // AG-01 파생(셀러 미편집). 저장이 REPLACE 라 shape 에 없으면 한 번의 저장으로 사라지고,
    // 그러면 거울 셀카 원본의 반전된 로고가 그대로 생성 컷에 남는다.
    sourceMirrored: false,
    frontClosure: null,
    // 공개 AG-01이 발급한 서버 서명 handoff. 폼에는 노출하지 않고 로그인 승격 때만 소비한다.
    confirmedGptProductEvidenceHandoff: null,
    detailRecommendations: { version: 1, status: 'unavailable', candidates: [] },
    detailRecommendationsHandoff: null,
    washCare: '', locked: false, measurementsUnknown: false,
    measurements: createMeasurementFields(clothingType),
    fitProfile: null,
  };
}
