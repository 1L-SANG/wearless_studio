export async function analyzePublicDraft(product, options, { remote, local }) {
  const analysis = await remote.publicAnalyze(product, options);
  // 진짜 AI 필드를 로컬 draft source에 먼저 반영하면 mock 추천기가 styleTags로 후보를 채우고,
  // 이후 게스트 편집/로그인 draftSync도 같은 분석값을 읽는다.
  return local.saveAnalysis(null, analysis);
}
