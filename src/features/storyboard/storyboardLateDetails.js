/** 늦게 온 추천은 카탈로그만 바꾼다. 이미 구성한 콘티 블록은 그대로 둔다. */
export function createLateDetailCatalog() {
  let saved = null;
  return {
    remember(projectId, recommendations) { saved = { projectId, recommendations }; },
    merge(projectId, catalogs) {
      return catalogs && saved?.projectId === projectId
        ? { ...catalogs, detailRecommendations: saved.recommendations } : catalogs;
    },
  };
}
