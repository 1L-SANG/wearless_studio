// 분석 확정 후 이미 생성된 마네킹을 다시 만들지 판단하는 탭 세션 전용 신호.
// flow 영속 대상에 넣지 않는다: 새 브라우저 세션의 복원은 명시적 이어서일 뿐, 과거 탭의
// 미확정 편집 의도까지 재생성 트리거로 복원하면 안 된다.
const generationRelevantAnalysisKeys = new Set([
  'matchClothing',
  'clothingType',
  'subCategory',
  'customCategory',
  'targetGenders',
  'fit',
  'fitProfile',
  'mannequinBody',
]);

export function isGenerationRelevantAnalysisPatch(patch) {
  return !!patch && Object.keys(patch).some((key) => generationRelevantAnalysisKeys.has(key));
}
