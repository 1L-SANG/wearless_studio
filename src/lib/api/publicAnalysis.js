export async function analyzePublicDraft(product, options, { remote, local }) {
  const analysis = await remote.publicAnalyze(product, options);
  // 진짜 AI 필드를 로컬 draft source 에 먼저 반영하면 mock 추천기가 styleTags 로 후보를 채우고,
  // 이후 게스트 편집/로그인 draftSync 도 같은 분석값을 읽는다.
  const stored = await local.saveAnalysis(null, analysis);
  // 저장본을 **그대로 돌려주지 않는다**: 그 싱글톤에는 직전 제작의 잔여 필드(특히 colors)가
  // 남아 있고, 응답의 colors 는 상품 패치로 갈라져 셀러가 방금 올린 사진을 교체한다
  // (2026-08-17 사고). 저장본에서 가져올 값은 추천기가 채운 매칭 후보뿐이다.
  return stored?.matchClothing
    ? { ...analysis, matchClothing: stored.matchClothing }
    : analysis;
}
