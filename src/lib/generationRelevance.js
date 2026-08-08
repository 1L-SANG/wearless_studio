// 분석 확정 후 이미 생성된 마네킹을 다시 만들지 판단하는 탭 세션 전용 신호의 키 분류.
// dirty 값 자체는 project별 sessionStorage에 두어 같은 탭의 새로고침은 견디되, localStorage의
// flow 영속 대상에는 넣지 않는다. 탭을 닫은 뒤 과거 편집 의도까지 복원하지 않기 위해서다.
const generationRelevantAnalysisKeys = new Set([
  'matchClothing',
  'clothingType',
  'subCategory',
  'customCategory',
  'targetGenders',
  'fit',
  'fitProfile',
]);

export function isGenerationRelevantAnalysisPatch(patch) {
  return !!patch && Object.keys(patch).some((key) => generationRelevantAnalysisKeys.has(key));
}
