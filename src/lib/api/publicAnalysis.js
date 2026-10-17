import { isOutfitContextPatch, mergeOutfitSaveResponse, outfitContextKey,
  syncOutfitLegacySelection } from '../outfitSelections.js';
import { stripLocalCustomMatch } from '../customMatchPromotion.js';

export async function analyzePublicDraft(product, options, { remote, local }) {
  const analysis = await remote.publicAnalyze(product, options);
  if (options?.isCurrent && !await options.isCurrent()) return null;
  // 진짜 AI 필드를 로컬 draft source 에 먼저 반영하면 mock 추천기가 styleTags 로 후보를 채우고,
  // 이후 게스트 편집/로그인 draftSync 도 같은 분석값을 읽는다.
  // 응답의 matchClothing 은 기본 shape 의 빈 배열이라 빼고 저장한다. 같이 넘기면 저장소가
  // "빈 목록 = 비우기"로 읽어 방금 채운 후보를 지우고, 첫 진입 매칭 의류가 0개가 된다(2026-10-10).
  const authoritativeOutfit = !!analysis.outfitRecommendation;
  const { matchClothing: _emptyShape, ...aiFields } = analysis;
  const payload = authoritativeOutfit ? analysis : { ...aiFields,
    outfitSelections: null, outfitRecommendation: null, outfitRecommendationHandoff: null };
  // 추천은 실제 입력 상품의 색으로 계산한다. 목 초기 상품의 검정색이 AI 제안을 덮지 않게
  // payload가 아닌 저장 옵션으로 넘겨, 사진을 분석 응답에 섞지 않는다.
  const stored = await local.saveAnalysis(null, payload, { isCurrent: options?.isCurrent, product, serverCatalog: authoritativeOutfit });
  // 저장본을 **그대로 돌려주지 않는다**: 그 싱글톤에는 직전 제작의 잔여 필드(특히 colors)가
  // 남아 있고, 응답의 colors 는 상품 패치로 갈라져 셀러가 방금 올린 사진을 교체한다
  // (2026-08-17 사고). 저장본에서 가져올 값은 추천기가 채운 매칭 후보뿐이다.
  if (authoritativeOutfit) return analysis;
  return stored?.matchClothing
    ? { ...analysis, matchClothing: stored.matchClothing }
    : analysis;
}
export async function savePublicAnalysisEdit(patch, { remote, local }) {
  const current = await local.getAnalysis(null);
  const authoritative = !!current?.outfitRecommendation;
  const previousProduct = authoritative && isOutfitContextPatch(patch) ? await local.getProduct(null) : null;
  const saved = await local.saveAnalysis(null, patch, { preserveCatalog: authoritative });
  if (!authoritative || !isOutfitContextPatch(patch)) return saved;
  const product = await local.getProduct(null);
  const requestContextKey = outfitContextKey(saved, product);
  if (requestContextKey === outfitContextKey(current, previousProduct)) return saved;
  const recommended = await remote.publicRecommendOutfit(product, stripLocalCustomMatch(saved));
  const latest = await local.getAnalysis(null);
  let merged = mergeOutfitSaveResponse(latest, recommended, { requestContextKey, product: await local.getProduct(null) });
  if (merged === latest) return latest;
  // 공개 추천 목록에 없는 로컬 내 옷은 현재 제작에서만 보존한다.
  const custom = (latest.matchClothing || []).filter((item) => item.isCustom);
  if (custom.length) merged.matchClothing = [...custom, ...merged.matchClothing.filter((item) => !item.isCustom)];
  merged = syncOutfitLegacySelection(merged);
  return local.saveAnalysis(null, merged, { serverCatalog: true });
}
