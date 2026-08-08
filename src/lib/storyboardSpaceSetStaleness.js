/* =============================================================
   lib/storyboardSpaceSetStaleness — 저장된 콘티 블록의 공간 세트 바인딩이
   "현재 분석"(성별·의류 종류)에 여전히 맞는지 판정하고, 맞지 않으면 바인딩만 뗀다.

   서버 검증(routes.py PUT storyboard → space_set_assets.bind_storyboard_space_sets)이
   블록마다 3가지를 본다 — 이 모듈은 그 3가지를 그대로 클라이언트에서 미리 재현한다:
     1) space_set_gender_mismatch  — 세트 성별 ≠ 현재 성별
     2) space_set_not_applicable   — 세트가 현재 의류 종류를 취급하지 않음
     3) space_set_variation_mismatch — 블록의 공간 변화 설정 ≠ 세트 발행 값
   (+ 세트 자체를 못 찾는 경우 — 레지스트리 개편 등. 서버도 이 그룹을 unknown_space_set 으로 거부한다.)

   세트는 셀러가 고른 것이고(spaceGroupId), 카탈로그가 세트마다 성별 하나만 묶으므로(다른
   성별 대응 세트가 없다) 자동 치환은 불가능하다 — 할 수 있는 건 바인딩을 떼서 카드를
   "다시 골라야 하는" 빈 상태로 되돌리는 것뿐이다. 카드 자체·순서·셀러가 올린 이미지는
   건드리지 않는다.
   ============================================================= */
import { inferStoryboardSpaceSet } from './storyboardSpaceSetCatalog.js';

export const SPACE_SET_STALE_REASONS = Object.freeze({
  UNKNOWN_SET: 'unknown_space_set',
  GENDER_MISMATCH: 'space_set_gender_mismatch',
  NOT_APPLICABLE: 'space_set_not_applicable',
  VARIATION_MISMATCH: 'space_set_variation_mismatch',
});

// 한 블록의 spaceGroupId 가 가리키는 세트가 { gender, clothingType } 아래서 여전히
// 유효한지 판정. 유효하면 null, 아니면 서버가 낼 에러 코드와 같은 이유 문자열.
export function staleSpaceSetReason(block, { gender, clothingType } = {}) {
  const groupId = block && block.spaceGroupId;
  if (!groupId) return null;   // 세트에 속하지 않은 블록 — 볼 것 없음
  const set = inferStoryboardSpaceSet(groupId);
  if (!set) return SPACE_SET_STALE_REASONS.UNKNOWN_SET;
  if (set.gender !== gender) return SPACE_SET_STALE_REASONS.GENDER_MISMATCH;
  const applicable = set.setApplicableClothingTypes || set.applicableClothingTypes || [];
  if (!applicable.includes(clothingType)) return SPACE_SET_STALE_REASONS.NOT_APPLICABLE;
  if (block.spaceVariation !== set.spaceVariation) return SPACE_SET_STALE_REASONS.VARIATION_MISMATCH;
  return null;
}

// 바인딩만 제거 — 카드·순서·셀러 이미지(ownImages/refImages 등)는 그대로 둔다.
// exampleId 도 함께 비운다: spaceGroupId 만 떼면 서버가 이 블록을 "세트 밖 개별 생성예시"
// (ss_ 로 시작하는 exampleId)로 다시 검증하는데, 그 예시도 같은 세트 소속이라 똑같이
// 성별/의류 종류가 안 맞아 또 400이 난다 — 반쪽만 떼면 저장 불가 상태가 그대로 남는다.
function stripSpaceSetBinding(block) {
  return {
    ...block,
    spaceGroupId: null,
    spaceVariation: null,
    exampleId: null,
    exampleSelectionOrigin: null,
    refScope: null,
    thumb: block.baseThumb || block.thumb,
    baseThumb: null,
  };
}

// 보드 전체를 훑어 낡은 세트 바인딩만 떼어낸 새 배열을 돌려준다. 뗄 것이 없으면
// 원본 blocks 참조를 그대로 돌려줘 sbStable 비교(변경 없음 판정)가 저렴하게 끝나게 한다.
export function stripStaleSpaceSetBindings(blocks, context = {}) {
  if (!Array.isArray(blocks)) return blocks;
  let changed = false;
  const next = blocks.map((block) => {
    if (!block || !staleSpaceSetReason(block, context)) return block;
    changed = true;
    return stripSpaceSetBinding(block);
  });
  return changed ? next : blocks;
}
