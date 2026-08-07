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
  // 디테일 컷의 방향은 예시에 내재된 속성 — 셀러는 방향 UI 없이 예시만 고르고,
  // 예시의 direction 라벨(미기재=front)이 서버의 근거 사진(Detail/BackDetail) 선택을
  // 결정한다(2026-08-07 오너 결정). 첫 선택·교체 모두 적용.
  const isDetail = block?.cutType === 'product' && block?.shot === 'detail';
  const patch = {
    exampleId,
    exampleSelectionOrigin: exampleId ? 'user' : null,
    refScope: scope,
    ...(isDetail && exampleId
      ? { direction: example?.direction === 'back' ? 'back' : 'front' } : {}),
  };
  if (!replacing) return { patch, settingsReset: false };

  const cutType = block?.cutType;
  return {
    settingsReset: true,
    patch: {
      ...patch,
      // 디테일은 미기재 예시=front 로 확정(이전 back 잔존 방지). 그 외엔 기존 규칙 유지.
      direction: isDetail
        ? (example?.direction === 'back' ? 'back' : 'front')
        : (example.direction ?? block.direction),
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
