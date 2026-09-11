/* =============================================================
   lib/limits.js — single source of tunable policy numbers.
   Credit unit costs + all the "상한"(caps) live HERE so they're
   trivial to change later. mock/db.js pulls CREDIT_COSTS from this
   file and exposes it as catalogs.creditCosts (contract shape kept).
   2026-09-11 v6 확정 단가(1cr=50원). 백엔드 config.credit_cost_* 미러.
   ============================================================= */

/** 단계별 크레딧 단가 — 이 값만 바꾸면 전 화면 예고가 함께 갱신됨 */
export const CREDIT_COSTS = Object.freeze({
  mannequinGenerate: 45, // 마네킹 단일컷 생성·재생성, 백엔드 credit_cost_mannequin_generate 미러
  mannequinAdjust: 0, // @deprecated P2: 핏 프로필 재생성으로 대체
  storyboardPerCut: 19, // 콘티에서 상세페이지 생성: 컷 1개당
  editorImage: 19, // 에디터에서 이미지 1장 생성/변형
});

/** 플랜별 가상모델·무료 수정 정책. 서버 plan_pricing.py 와 같은 값으로 유지한다. */
export const EXTENSION_MODEL_FEE = Object.freeze({
  free: 19,
  starter: 19,
  seller: 10,
  pro: 0,
});
export const FREE_MANNEQUIN_ADJUSTS = Object.freeze({
  free: 1,
  starter: 1,
  seller: 1,
  pro: 2,
});
export const BASIC_VIRTUAL_MODEL_IDS = new Set(['mA', 'mB']);

const PLAN_TIERS = new Set(Object.keys(EXTENSION_MODEL_FEE));
const VIRTUAL_MODEL_ID = /^m[A-N]$/;

export function normalizePlanTier(plan) {
  return PLAN_TIERS.has(plan) ? plan : 'free';
}

export function extensionModelFee(plan, selectedModelId) {
  if (!VIRTUAL_MODEL_ID.test(selectedModelId || '')
      || BASIC_VIRTUAL_MODEL_IDS.has(selectedModelId)) return 0;
  return EXTENSION_MODEL_FEE[normalizePlanTier(plan)];
}

export function mannequinGenerationTotal(plan, selectedModelId) {
  return CREDIT_COSTS.mannequinGenerate + extensionModelFee(plan, selectedModelId);
}

export function mannequinGenerationCtaLabel(total) {
  return `의류정보 확정 완료 · ${total} 크레딧`;
}

export function extensionModelGroupLabel(plan) {
  return `확장 · 상품당 ${EXTENSION_MODEL_FEE[normalizePlanTier(plan)]} 크레딧`;
}

export function mannequinRegenerationQuote(plan, doneCount = 0) {
  const normalizedPlan = normalizePlanTier(plan);
  const freeAdjusts = FREE_MANNEQUIN_ADJUSTS[normalizedPlan];
  const usedAdjusts = Math.max(Number(doneCount) - 1, 0);
  return {
    freeAdjusts,
    usedAdjusts,
    nextCost: Number(doneCount) <= freeAdjusts ? 0 : CREDIT_COSTS.mannequinGenerate,
  };
}

export function mannequinRegenerationCreditText(quote) {
  if (!quote || quote.nextCost !== 0) {
    return `${quote?.nextCost ?? CREDIT_COSTS.mannequinGenerate} 크레딧`;
  }
  const remaining = Math.max(Number(quote.freeAdjusts) - Number(quote.usedAdjusts), 0);
  return `무료 (남은 무료 수정 ${remaining}회)`;
}

export function mannequinRegenerationCtaLabel(quote) {
  return `수정 반영 · ${mannequinRegenerationCreditText(quote)}`;
}

/** 화면 전반에서 쓰는 상한값 (PRD §5.3 / §6.6 / §6.8 / §7.4) */
export const LIMITS = Object.freeze({
  baseColorMaxImages: 6, // 기준 색상: 전체 각도 합산 최대 (PRD §5.3)
  additionalColorMax: 3, // 추가 색상 개수 상한 (PRD §5.3)
  additionalColorMaxImages: 3, // 추가 색상당 이미지 상한 (PRD §5.3)
  sellingPointMax: 5, // 강조 특징 상한 (PRD §6.6)
  aiSuggestedPointMax: 2, // AI 추천 특징 상한 (types.js)
  matchClothingMax: 1, // 매칭 의류 선택 상한 (PRD §6.8)
  mannequinAdjustMax: undefined, // @deprecated P2: 횟수 제한 없음
});

/** @deprecated P2: 마네킹 조정 횟수 제한 폐기. */
export const ADJUST_LIMIT = undefined;
