const WORN_CUT_TYPES = new Set(['styling', 'horizon']);

/* 디테일 컷 방향 규칙의 단일 정본 — 예시의 direction 라벨이 결정하고, 미기재는 front.
   선택 패치·자동 배정·에디터 패널·샷 전환 확정이 전부 이 함수를 쓴다(규칙 분산 방지). */
export const detailDirectionFromExample = (example) => (example?.direction === 'back' ? 'back' : 'front');

export function directionSpecFromExample(example, { cutType = 'styling', detailTarget = null } = {}) {
  const allowed = cutType === 'product' ? ['front', 'back'] : ['front', 'side', 'back'];
  const source = cutType === 'product' && detailTarget ? detailTarget : example;
  const direction = allowed.includes(source?.direction) ? source.direction : 'front';
  return {
    direction,
    sideStyle: direction === 'side' ? (source?.sideStyle === 'threeQuarter' ? 'threeQuarter' : 'profile') : null,
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
  if (!['styling', 'horizon', 'product'].includes(cutType)) return {};
  const direction = example?.direction;
  if (!direction) return {};
  if (cutType === 'product') return { direction: detailDirectionFromExample(example) };
  return directionSpecFromExample(example, { cutType });
}

export function generationExampleSelectionPatch(block, example, {
  clothingType = 'top',
  defaultColorId = null,
  refScope = null,
} = {}) {
  const exampleId = example?.id || null;
  const effectiveBlock = block;
  const replacing = !!block?.exampleId && !!exampleId && block.exampleId !== exampleId;
  const scope = block?.spaceGroupId ? (block.cutType === 'horizon' ? 'all' : 'pose') : (refScope || block?.refScope || 'all');
  // 디테일 컷의 방향은 예시에 내재된 속성 — 셀러는 방향 UI 없이 예시만 고르고,
  // 예시의 direction 라벨(미기재=front)이 서버의 근거 사진(Detail/BackDetail) 선택을
  // 결정한다(2026-08-07 오너 결정). 첫 선택·교체 모두 적용.
  const isDetail = block?.cutType === 'product' && block?.shot === 'detail';
  const cutType = effectiveBlock.cutType;
  const patch = {
    exampleId,
    exampleChoice: null,
    exampleSelectionOrigin: exampleId ? 'user' : null,
    refScope: scope,
    ...(exampleId && !(isDetail && block.detailTargetId) ? wornDirectionPatch(cutType, example) : {}),
    ...(isDetail && !block.detailTargetId && exampleId ? { direction: detailDirectionFromExample(example) } : {}),
  };
  if (!replacing) return { patch, settingsReset: false };

  return {
    settingsReset: true,
    patch: {
      ...patch,
      // 디테일은 미기재 예시=front 로 확정(이전 back 잔존 방지). 그 외엔 기존 규칙 유지.
      direction: isDetail
        ? (block.detailTargetId ? block.direction : detailDirectionFromExample(example))
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
