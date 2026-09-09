import { AI_MODEL_IDS } from './aiModels.js';
import { FACEMARKET_PRICING } from '../../lib/facemarketPricing.js';

export function resolveSelectedModelId({
  selectedModelId,
  targetGenders,
  models,
  modelsLoading,
  aiModels,
}) {
  if (modelsLoading) return selectedModelId;

  const targetGender = targetGenders?.[0];
  const licensable = models.filter((model) => model.hasActiveLicense);
  const selectedAi = aiModels.find((model) => model.id === selectedModelId);
  // AI 모델은 성별 칩과 같은 성별만 화면에 뜬다 — 칩을 바꿔 선택이 그리드에서 사라지면
  // 새 성별의 첫 모델로 옮긴다(보이지 않는 모델이 선택된 채로 남지 않게, 2026-08-01).
  const aiGenderMismatch = selectedAi && targetGender && selectedAi.gender !== targetGender;
  const valid = (selectedAi && !aiGenderMismatch)
    || licensable.some((model) => model.id === selectedModelId);
  if (valid) return selectedModelId;

  const pool = targetGender
    ? aiModels.filter((model) => model.gender === targetGender)
    : aiModels;
  return (pool[0] || aiModels[0])?.id;
}

// 가상모델 판정은 카탈로그 단일 출처에서 가져온다 — 손으로 다시 적으면 새 모델을 넣을 때
// 빠뜨려 유료 실제 모델로 오분류된다(2026-08-17 mF~mN 사고).
const VIRTUAL_MODEL_IDS = AI_MODEL_IDS;

export function isRealModelSelection(selectedModelId) {
  return !!selectedModelId && !VIRTUAL_MODEL_IDS.has(selectedModelId);
}

export function resolveStylingModelId({
  selectedModelId,
  stylingModelId,
  targetGenders,
  aiModels,
}) {
  if (!isRealModelSelection(selectedModelId)) return null;

  const targetGender = targetGenders?.[0];
  const pool = targetGender
    ? aiModels.filter((model) => model.gender === targetGender)
    : aiModels;
  if (pool.some((model) => model.id === stylingModelId)) return stylingModelId;
  return (pool[0] || aiModels[0])?.id || null;
}

export function stylingModelPatchForAnalysis(analysis, aiModels) {
  if (!analysis) return null;
  const stylingModelId = resolveStylingModelId({
    selectedModelId: analysis.selectedModelId || analysis.selected_model_id,
    stylingModelId: analysis.stylingModelId || analysis.styling_model_id,
    targetGenders: analysis.targetGenders || analysis.target_genders,
    aiModels,
  });
  return stylingModelId !== (
    analysis.stylingModelId || analysis.styling_model_id || null
  )
    ? { stylingModelId }
    : null;
}

export function realModelFeeLabel(selectedModelId, models, horizonCutCount = 1) {
  if (!isRealModelSelection(selectedModelId) || horizonCutCount < 1) return '';
  const selected = (models || []).find((model) => model.id === selectedModelId);
  return selected
    ? ` + 실제 모델 ₩${(FACEMARKET_PRICING.perCut * horizonCutCount).toLocaleString('ko-KR')}`
    : ' + 실제 모델 이용료 별도';
}
