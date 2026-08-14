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

/* 생성예시는 연출 설정의 새 기준이다. 컷의 구조적 역할(cutType/shot/contentRole)은
   보존하고, 사용자가 이전 예시에 맞춰 조정한 per-cut 생성 설정만 기본값으로 돌린다.
   단, 거울 예시는 위의 명시적 G-2 예외로 구조 레시피까지 바꾼다. */
export function generationExampleSelectionPatch(block, example, {
  clothingType = 'top',
  defaultColorId = null,
  refScope = null,
} = {}) {
  const exampleId = example?.id || null;
  const structuralPatch = generationExampleStructuralRecipePatch(block, example);
  const effectiveBlock = { ...block, ...structuralPatch };
  const replacing = !!block?.exampleId && !!exampleId && block.exampleId !== exampleId;
  const scope = block?.spaceGroupId ? 'pose' : (refScope || block?.refScope || 'all');
  const patch = {
    ...structuralPatch,
    exampleId,
    exampleSelectionOrigin: exampleId ? 'user' : null,
    refScope: scope,
  };
  if (!replacing) return { patch, settingsReset: false };

  const cutType = effectiveBlock.cutType;
  return {
    settingsReset: true,
    patch: {
      ...patch,
      direction: cutType === 'mirror' ? null : (example.direction ?? effectiveBlock.direction),
      colorId: defaultColorId || effectiveBlock.colorId,
      colorIds: [],
      pose: 'auto',
      poseLabel: 'AI 자동',
      angle: 'same',
      matchIds: [],
      refImages: [],
      refAssetIds: [],
      faceExposure: cutType === 'mirror' ? 'hide' : cutType === 'product' ? null : 'same',
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(cutType) ? 'open' : null,
    },
  };
}
