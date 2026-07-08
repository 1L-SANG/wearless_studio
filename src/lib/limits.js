/* =============================================================
   lib/limits.js — single source of tunable policy numbers.
   Credit unit costs + all the "상한"(caps) live HERE so they're
   trivial to change later. mock/db.js pulls CREDIT_COSTS from this
   file and exposes it as catalogs.creditCosts (contract shape kept).
   These are PROTOTYPE values — final policy lands with the backend
   (handoff/00_README §4, PRD §12).
   ============================================================= */

/** 단계별 크레딧 단가 — 이 값만 바꾸면 전 화면 예고가 함께 갱신됨 */
export const CREDIT_COSTS = Object.freeze({
  mannequinGenerate: 2, // 마네킹 단일컷 생성·재생성 — 백엔드 credit_cost_mannequin_generate 미러
  mannequinAdjust: 0, // @deprecated P2: 핏 프로필 재생성으로 대체
  storyboardPerCut: 1, // 콘티 → 상세페이지 생성: 컷 1개당
  editorImage: 1, // 에디터에서 이미지 1장 생성/변형
});

/** 화면 전반에서 쓰는 상한값 (PRD §5.3 / §6.6 / §6.8 / §7.4) */
export const LIMITS = Object.freeze({
  baseColorMaxImages: 6, // 기준 색상: 전체 각도 합산 최대 (PRD §5.3)
  additionalColorMax: 3, // 추가 색상 개수 상한 (PRD §5.3)
  additionalColorMaxImages: 3, // 추가 색상당 이미지 상한 (PRD §5.3)
  sellingPointMax: 5, // 강조 특징 상한 (PRD §6.6)
  aiSuggestedPointMax: 2, // AI 추천 특징 상한 (types.js)
  matchClothingMax: 2, // 매칭 의류 선택 상한 (PRD §6.8)
  mannequinAdjustMax: undefined, // @deprecated P2: 횟수 제한 없음
});

/** @deprecated P2: 마네킹 조정 횟수 제한 폐기. */
export const ADJUST_LIMIT = undefined;
