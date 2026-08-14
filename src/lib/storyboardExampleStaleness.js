/* =============================================================
   lib/storyboardExampleStaleness — 저장된 콘티 블록이 물고 있는 "낱개" 생성예시
   (공간 세트 그룹에 속하지 않은 exampleId — 일반 생성예시든, 세트 단품을 참고용으로
   고른 것이든)가 "현재 분석"(성별·의류 종류)에 여전히 맞는지 판정하고, 안 맞으면 뗀다.

   서버 검증(routes.py PUT storyboard → content_roles.validate_storyboard_example_references,
   그리고 spaceGroupId 없이 ss_ 예시만 고른 블록의 space_set_assets.resolve_published_example_reference)
   이 블록마다 보는 4가지를 그대로 재현한다:
     1) unknown_example_id      — 카탈로그에서 못 찾음(또는 'all' 변형 미공개)
     2) example_not_applicable  — 예시가 현재 의류 종류를 취급하지 않음
     3) example_cut_mismatch    — 예시의 컷 종류 ≠ 블록의 컷 종류
     4) example_gender_mismatch — 예시 성별 ≠ 기대 성별(제품 컷은 성별 없음이 기대값)

   제품 컷(cutType==='product')은 서버 규칙상 기대 성별이 항상 null 이고, 제품 예시는
   원래 성별을 안 실으므로 — 성별이 바뀌어도 이 카테고리는 절대 stale 판정을 받지 않는다.
   (이 파일은 storyboardSpaceSetStaleness.js 의 자매 모듈 — 그쪽은 spaceGroupId 로 묶인
   블록을, 이 파일은 묶이지 않은 블록을 담당한다. 겹치지 않게 spaceGroupId 가 있는 블록은
   건드리지 않는다 — 그 판정은 세트 전체 단위(setApplicableClothingTypes)로 이미 끝났고,
   여기서 멤버 단위(applicableClothingTypes) 기준으로 다시 보면 결과가 달라질 수 있다.)
   ============================================================= */

export const EXAMPLE_STALE_REASONS = Object.freeze({
  UNKNOWN_ID: 'unknown_example_id',
  NOT_APPLICABLE: 'example_not_applicable',
  CUT_MISMATCH: 'example_cut_mismatch',
  GENDER_MISMATCH: 'example_gender_mismatch',
});

function isPublishedAll(example) {
  return Array.isArray(example?.variants) && example.variants.includes('all');
}

// 카탈로그에서 exampleId 하나를 찾는다. catalog 는 hydratedCatalogs.genExamples —
// 일반 생성예시와 공간 세트 멤버(setOnly, ss_ 접두)가 이미 같은 배열에 합쳐져 있다
// (withStoryboardSpaceSetExamples). 두 출처 모두 applicableClothingTypes/cutType/
// gender/variants 필드를 같은 모양으로 노출하므로 한 함수로 같이 볼 수 있다.
function findExample(catalog, exampleId) {
  return (catalog || []).find((item) => item && item.id === exampleId) || null;
}

// 블록 하나의 exampleId 가 { gender, clothingType } 아래서 여전히 유효한지 판정.
// 유효하면 null, 아니면 서버가 낼 에러 코드와 같은 이유 문자열.
export function staleExampleReason(block, catalog, { gender, clothingType } = {}) {
  const exampleId = block && block.exampleId;
  if (!exampleId) return null;               // 예시를 아직 안 고른 블록 — 볼 것 없음
  if (block.spaceGroupId) return null;        // 세트 그룹 소속 — storyboardSpaceSetStaleness.js 소관
  const entry = findExample(catalog, exampleId);
  if (!entry || !isPublishedAll(entry)) return EXAMPLE_STALE_REASONS.UNKNOWN_ID;
  if (!(entry.applicableClothingTypes || []).includes(clothingType)) {
    return EXAMPLE_STALE_REASONS.NOT_APPLICABLE;
  }
  if (entry.cutType !== block.cutType) return EXAMPLE_STALE_REASONS.CUT_MISMATCH;
  // 서버(content_roles.validate_storyboard_example_references)와 동일: 제품 컷은 기대
  // 성별이 항상 없음(null) — 제품 예시는 성별을 안 싣는다, 그래서 성별이 바뀌어도 안 낡는다.
  const expectedGender = block.cutType === 'product' ? null : gender;
  if ((entry.gender ?? null) !== expectedGender) return EXAMPLE_STALE_REASONS.GENDER_MISMATCH;
  return null;
}

// exampleId 선택만 제거 — 카드·순서·셀러 이미지는 그대로 둔다. baseThumb 로 썸네일을
// 되돌리고, refScope 도 함께 비운다(exampleId 없이 refScope='pose'가 남으면 화면이
// "포즈 필수"로 잘못 읽는다 — 공간 세트 쪽과 같은 이유).
// (후킹 프레임 전환 등 "컷의 틀이 바뀌어 예시가 더는 안 맞는" 자리에서도 같은 규칙을
// 쓰도록 export — 선택 제거 방식이 두 갈래로 갈라지지 않게 한다.)
export function clearExampleSelection(block) {
  return {
    ...block,
    exampleId: null,
    exampleSelectionOrigin: null,
    refScope: null,
    thumb: block.baseThumb || block.thumb,
    baseThumb: null,
  };
}

// 서버가 저장을 400으로 거절하며 meta.exampleId 로 지목한 선택만 떼어낸다.
// 클라이언트 카탈로그 기준으론 유효해 보여도(발행 회전 직후의 구/신 카탈로그 스큐)
// 서버 레지스트리가 정본이므로, 지목된 낱개 선택을 걷어내고 재배정할 수 있게 한다.
// 세트 그룹 소속 블록은 여기서도 건드리지 않는다(세트 검증은 별도 코드로 온다).
export function stripExampleSelectionsById(blocks, exampleId) {
  if (!Array.isArray(blocks) || !exampleId) return blocks;
  let changed = false;
  const next = blocks.map((block) => {
    if (!block || block.exampleId !== exampleId || block.spaceGroupId) return block;
    changed = true;
    return clearExampleSelection(block);
  });
  return changed ? next : blocks;
}

// 보드 전체를 훑어 낡은 낱개 예시 선택만 떼어낸 새 배열을 돌려준다. 뗄 것이 없으면 원본
// 참조를 그대로 돌려준다. 뒤이어 assignGenerationExamples 가 exampleId=null·
// exampleSelectionOrigin=null 인 블록을 "미배정"으로 보고 현재 성별에 맞는 예시를 다시
// 채운다 — 선택 로직을 여기서 새로 만들지 않고 그 기존 배정기를 그대로 재사용한다.
export function stripStaleExampleSelections(blocks, catalog, context = {}) {
  if (!Array.isArray(blocks)) return blocks;
  let changed = false;
  const next = blocks.map((block) => {
    if (!block || !staleExampleReason(block, catalog, context)) return block;
    changed = true;
    return clearExampleSelection(block);
  });
  return changed ? next : blocks;
}
