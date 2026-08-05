const WORN_CUT_TYPES = new Set(['styling', 'horizon', 'mirror']);

/* 생성예시는 연출 설정의 새 기준이다. 컷의 구조적 역할(cutType/shot/contentRole)은
   보존하고, 사용자가 이전 예시에 맞춰 조정한 per-cut 생성 설정만 기본값으로 돌린다. */
export function generationExampleSelectionPatch(block, example, {
  clothingType = 'top',
  defaultColorId = null,
  refScope = null,
} = {}) {
  const exampleId = example?.id || null;
  const replacing = !!block?.exampleId && !!exampleId && block.exampleId !== exampleId;
  const scope = block?.spaceGroupId ? 'pose' : (refScope || block?.refScope || 'all');
  const patch = {
    exampleId,
    exampleSelectionOrigin: exampleId ? 'user' : null,
    refScope: scope,
  };
  if (!replacing) return { patch, settingsReset: false };

  const cutType = block?.cutType;
  return {
    settingsReset: true,
    patch: {
      ...patch,
      direction: example.direction ?? block.direction,
      colorId: defaultColorId || block.colorId,
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
