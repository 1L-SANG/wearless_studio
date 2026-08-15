/* =============================================================
   matchSelection — 매칭 선택 토글 머지 (순수 함수).

   httpAdapter 에서 떼어낸 이유: alias(@/) 없는 상대 임포트만 쓰므로 node --test 가 직접
   물어 **실행으로** 회귀를 고정할 수 있다. 이 규칙은 2026-08-15 전수조사에서 확정된
   결함(clothingType 불명 시 'bottom' 추정 → 하의 상품의 매칭 상의가 전부 선택 해제)
   때문에 텍스트 검증이 아니라 실행 검증이 필요해졌다.
   ============================================================= */
import { LIMITS } from '../limits.js';

export function mergeMatchSelection(currentMatch, matchPatch, clothingType) {
  // 원피스는 매칭 자체가 없다(선택 0) — 기존 의미 그대로.
  const noMatching = clothingType === 'dress';
  // clothingType 이 없으면 **추정하지 않는다**. 예전엔 없으면 'bottom' 으로 굳어(삼항의
  // else 가지) 하의 상품의 매칭 상의가 타입 필터에서 탈락했다 — 승격 직후 캐시엔
  // clothingType 이 없어(draftSync 가 product 로 미러하며 analysis 에서 제거) 방금 등록한
  // 내 옷은 물론 기존 선택까지 전부 해제됐다. 서버가 isCompatible 을 이미 실어 주므로,
  // 불명일 땐 타입 비교를 생략하고 그 신호에 맡긴다.
  const expectedType = noMatching || clothingType == null
    ? undefined
    : (clothingType === 'bottom' ? 'top' : 'bottom');
  const typeOk = (m) => expectedType === undefined
    || m.clothingType == null || m.clothingType === expectedType;

  const patchById = new Map(matchPatch.map((m) => [m.id, m]));
  const merged = (currentMatch || []).map((m) => {
    const p = patchById.get(m.id);
    if (!p) return m;
    return { ...m, selected: !!p.selected, selOrder: p.selected ? p.selOrder : undefined };
  });
  const ranked = merged
    .filter((m) => m.selected && m.isCompatible !== false && !noMatching && typeOk(m))
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99))
    .slice(0, LIMITS.matchClothingMax);
  const orderById = new Map(ranked.map((m, i) => [m.id, i + 1]));
  return merged.map((m) => (orderById.has(m.id)
    ? { ...m, selected: true, selOrder: orderById.get(m.id) }
    : { ...m, selected: false, selOrder: undefined }));
}
