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

/**
 * 실제 모델 얼굴이 들어가는 컷. 실제 모델을 고르면 **모든 착용 컷**이다(2026-09-11 사용자 결정).
 * 서버 게이트(facemarket.REAL_IDENTITY_CUT_TYPES)와 같은 규칙이고, 모델의 선택 동의(opt_*)는
 * 여기서도 판정에 쓰지 않는다.
 */
export const REAL_FACE_CUT_TYPES = ['horizon', 'styling', 'mirror', 'base_edit'];

/** 이 컷에 실제 모델 얼굴이 들어가는가. */
export function realFaceAllowedCut(cutType) {
  return REAL_FACE_CUT_TYPES.includes(cutType);
}

/** 실제 모델 얼굴이 들어가는 컷 수 — 과금·가격 표시의 근거. */
export function realFaceCutCount(blocks) {
  return (blocks || []).filter((block) => realFaceAllowedCut(block?.cutType)).length;
}

export function realModelFeeLabel(selectedModelId, models, realFaceCuts = 1) {
  // 과금 근거는 "실제 모델 얼굴이 들어간 컷 수" — 착용 컷이면 전부 여기에 든다.
  if (!isRealModelSelection(selectedModelId) || realFaceCuts < 1) return '';
  const selected = (models || []).find((model) => model.id === selectedModelId);
  return selected
    ? ` + 실제 모델 ₩${FACEMARKET_PRICING.perCut.toLocaleString('ko-KR')}`
    : ' + 실제 모델 이용료 별도';
}
