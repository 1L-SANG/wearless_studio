import { invalidateStoryboardEntryPrefetch } from '../storyboard/storyboardEntryPrefetch.js';

// 콘티 시드(httpAdapter.getStoryboard → shapes.defaultStoryboard)가 실제로 읽는 필드.
// 이 중 하나라도 이 화면에서 다시 저장되면, 이미 데워둔 콘티 프리페치는 스테일해진다.
const STORYBOARD_SEED_PATCH_KEYS = new Set(['colors', 'clothingType', 'targetGenders']);

const PRODUCT_PATCH_KEYS = new Set([
  'name',
  'clothingType',
  'colors',
  'measurements',
  'measurementsUnknown',
  'uploadComplete',
]);

const PRODUCT_PATCH_NON_NULL_KEYS = new Set([
  'name',
  'colors',
  'measurements',
  'measurementsUnknown',
  'uploadComplete',
]);

export function splitAnalysisEditPatch(patch) {
  const productPatch = {};
  const analysisPatch = {};
  Object.entries(patch || {}).forEach(([key, value]) => {
    if (PRODUCT_PATCH_KEYS.has(key)) {
      if (!(PRODUCT_PATCH_NON_NULL_KEYS.has(key) && value == null)) {
        productPatch[key] = value;
      }
      return;
    }
    analysisPatch[key] = value;
  });
  return { productPatch, analysisPatch };
}

export function hasPatchFields(patch) {
  return !!patch && Object.keys(patch).length > 0;
}

export function mergeProductOwnedAnalysisFields(analysis, product) {
  if (!analysis) return analysis;
  return {
    ...analysis,
    clothingType: product?.clothingType ?? analysis.clothingType ?? null,
    measurements: Array.isArray(product?.measurements) ? product.measurements : (analysis.measurements || []),
    measurementsUnknown: typeof product?.measurementsUnknown === 'boolean'
      ? product.measurementsUnknown
      : !!analysis.measurementsUnknown,
  };
}

export function mergeLatestFailedAnalysisPatch(currentFailedPatch, failedPatch, latestPatch) {
  return {
    ...(currentFailedPatch || {}),
    ...(failedPatch || {}),
    ...(latestPatch || {}),
  };
}

export async function persistAnalysisEdit(api, projectId, patch) {
  const { productPatch } = splitAnalysisEditPatch(patch);
  const saved = {};
  // sbSaveNow(storyboardPersistence.js)와 같은 원칙 — 저장이 착지하기 전에 먼저 무효화해,
  // 진행 중인 저장과 레이스하는 프리페치가 곧 스테일해질 값을 캐시해 버리지 않게 한다.
  if (projectId && Object.keys(patch || {}).some((key) => STORYBOARD_SEED_PATCH_KEYS.has(key))) {
    invalidateStoryboardEntryPrefetch(projectId);
  }
  if (projectId && hasPatchFields(productPatch)) {
    saved.product = await api.saveProduct(projectId, productPatch);
  }
  // AnalysisForm의 현재 저장 shape에는 추천 갱신 컨텍스트(clothingType)와 실측 표시값도 들어 있다.
  // 서버 Product를 먼저 갱신해 생성의 정본을 맞춘 뒤, 기존 analysis 저장 계약은 유지한다.
  if (hasPatchFields(patch)) {
    saved.analysis = await api.saveAnalysis(projectId, patch);
  }
  return saved;
}
