/* =============================================================
   customMatchDraftStore — draft(비프로젝트) 단계 "내 옷"의 승격 키 보관소.

   왜 별도 저장소인가: 매칭 목록은 여러 곳(refreshMatchClothing·추천 재계산)이
   `toLegacyMatchItem` 화이트리스트로 **재구성**한다. 아이템 객체에 실어둔 승격 키
   (sourceAssetIds)는 그 투영을 지나는 순간 소멸했고(2026-08-14 전수조사 — 모달이
   닫히며 자동 refresh 가 확정 전에 키를 지움), 썸네일은 폴백으로 살아 카드가 멀쩡해
   보여서 유실이 조용했다. 목록 재구성과 무관한 모듈 상태로 분리해 끊는다.

   상대 임포트만 쓴다 — node --test 가 alias 없이 직접 물어 회귀를 고정할 수 있게.
   ============================================================= */

let draft = null; // { assetIds: string[] } | null

export function rememberCustomMatchDraft({ assetIds }) {
  draft = Array.isArray(assetIds) && assetIds.length
    ? { assetIds: [...assetIds] }
    : null;
}

export function clearCustomMatchDraft() {
  draft = null;
}

export function readCustomMatchDraft() {
  return draft ? { assetIds: [...draft.assetIds] } : null;
}
