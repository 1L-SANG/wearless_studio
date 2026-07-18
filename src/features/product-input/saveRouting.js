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
