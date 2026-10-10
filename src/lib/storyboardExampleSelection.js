import { CONTENT_ROLES, normalizedRecipePatch } from './storyboardTaxonomy.js';

const WORN_CUT_TYPES = new Set(['styling', 'horizon', 'mirror']);

export function generationExampleStructuralRecipePatch(block, example) {
  // G-2의 의도적 예외: 거울 예시는 다른 촬영법을 직접 고르는 카드다.
  // 거울 탭이 있던 때부터 쓰던 normalizedRecipePatch 경로로 샷·방향·얼굴 규칙을 적용한다.
  if (example?.cutType === 'mirror') {
    return normalizedRecipePatch({
      ...block,
      cutType: 'mirror',
      shot: example.shot,
      direction: example.direction,
    }, CONTENT_ROLES.REAL_WEAR);
  }
  if (block?.cutType === 'mirror' && example?.cutType === 'styling') {
    return normalizedRecipePatch({
      ...block,
      cutType: 'styling',
      shot: example.shot || block.shot,
      direction: example.direction,
    }, CONTENT_ROLES.COORDINATION);
  }
  return {};
}

export function directionSpecFromExample(example) {
  const direction = ['front', 'side', 'back'].includes(example?.direction) ? example.direction : 'front';
  return {
    direction,
    sideStyle: direction === 'side' ? (example?.sideStyle === 'threeQuarter' ? 'threeQuarter' : 'profile') : null,
  };
}

/* 생성예시는 연출 설정의 새 기준이다. 컷의 구조적 역할(cutType/shot/contentRole)은
   보존하고, 사용자가 이전 예시에 맞춰 조정한 per-cut 생성 설정만 기본값으로 돌린다.
   단, 거울 예시는 위의 명시적 G-2 예외로 구조 레시피까지 바꾼다. */
/* 착용컷(스튜디오·스냅)은 **고른 예시의 방향이 곧 카드의 방향**이다 — 셀러는 갤러리에서
   "이 각도로 찍어달라"고 사진을 고른다(2026-09-23 오너 실측: 사선·옆모습·뒷면 예시를 골랐는데
   13블록 전부 direction=front 로 저장돼 생성이 정면으로만 나갔다).
   sideStyle 도 같이 옮긴다 — 방향만 side 로 두면 서버 기본값이 옆모습이라 사선 예시가
   90도 각도 교체로 새어 나간다. */
function wornDirectionPatch(cutType, example) {
  if (!['styling', 'horizon'].includes(cutType)) return {};
  const direction = example?.direction;
  if (!direction) return {};
  return directionSpecFromExample(example);
}

export function generationExampleSelectionPatch(block, example, {
  clothingType = 'top',
  defaultColorId = null,
  refScope = null,
} = {}) {
  const exampleId = example?.id || null;
  const structuralPatch = generationExampleStructuralRecipePatch(block, example);
  const effectiveBlock = { ...block, ...structuralPatch };
  const replacing = !!block?.exampleId && !!exampleId && block.exampleId !== exampleId;
  const scope = block?.spaceGroupId ? (block.cutType === 'horizon' ? 'all' : 'pose') : (refScope || block?.refScope || 'all');
  const isDetail = block?.cutType === 'product' && block?.shot === 'detail';
  const cutType = effectiveBlock.cutType;
  const patch = {
    ...structuralPatch,
    exampleId,
    exampleChoice: null,
    exampleSelectionOrigin: exampleId ? 'user' : null,
    refScope: scope,
    ...(exampleId ? wornDirectionPatch(cutType, example) : {}),
  };
  if (!replacing) return { patch, settingsReset: false };

  return {
    settingsReset: true,
    patch: {
      ...patch,
      direction: cutType === 'product'
        ? block.direction
        : cutType === 'mirror' ? null : (example.direction ?? effectiveBlock.direction),
      ...(cutType === 'mirror' ? { sideStyle: null } : {}),
      colorId: isDetail && block.detailTargetId ? effectiveBlock.colorId : defaultColorId || effectiveBlock.colorId,
      colorIds: [],
      angle: 'same',
      matchIds: [],
      refImages: [],
      refAssetIds: [],
      faceExposure: cutType === 'mirror' ? 'hide' : cutType === 'product' ? null : 'same',
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(cutType) ? 'open' : null,
    },
  };
}
