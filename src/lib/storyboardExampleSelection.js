import { isSupportedCutType } from './storyboardTaxonomy.js';

const WORN_CUT_TYPES = new Set(['styling', 'horizon']);

export function directionSpecFromExample(example) {
  const direction = ['front', 'side', 'back'].includes(example?.direction) ? example.direction : 'front';
  return {
    direction,
    sideStyle: direction === 'side' ? (example?.sideStyle === 'threeQuarter' ? 'threeQuarter' : 'profile') : null,
  };
}

/* 생성예시는 연출 설정의 새 기준이다. 컷의 구조적 역할(cutType/shot/contentRole)은
   보존하고, 사용자가 이전 예시에 맞춰 조정한 per-cut 생성 설정만 기본값으로 돌린다.
 */
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
  if (example?.cutType != null && !isSupportedCutType(example.cutType)) return { patch: {}, settingsReset: false };
  const exampleId = example?.id || null;
  const effectiveBlock = block;
  const replacing = !!block?.exampleId && !!exampleId && block.exampleId !== exampleId;
  const scope = block?.spaceGroupId ? (block.cutType === 'horizon' ? 'all' : 'pose') : (refScope || block?.refScope || 'all');
  const isDetail = block?.cutType === 'product' && block?.shot === 'detail';
  const cutType = effectiveBlock.cutType;
  const patch = {
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
        : (example.direction ?? effectiveBlock.direction),
      colorId: isDetail && block.detailTargetId ? effectiveBlock.colorId : defaultColorId || effectiveBlock.colorId,
      colorIds: [],
      angle: 'same',
      matchIds: [],
      refImages: [],
      refAssetIds: [],
      faceExposure: cutType === 'product' ? null : 'same',
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(cutType) ? 'open' : null,
    },
  };
}
