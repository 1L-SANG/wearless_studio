const RETRY_FIELDS = Object.freeze([
  'contentRole',
  'colorId',
  'cutType',
  'direction',
  'shot',
  'faceExposure',
  'pose',
  'outerClosureState',
  'exampleId',
  'modelId',
  'matchIds',
  'refScope',
]);

/**
 * 실패한 에디터 슬롯을 원래 콘티 계약으로 다시 생성한다.
 * 일반 AI 패널의 기본값으로 재구성하면 시그니처 exampleId, 매칭 의류, 모델 같은
 * 생성 정본이 유실되므로 sourceBlockId로 원본 블록을 찾아 필요한 필드만 복사한다.
 */
export function buildFailedCutRetry(storyboard, sourceBlockId) {
  if (!sourceBlockId || !Array.isArray(storyboard)) return null;
  const block = storyboard.find((item) => item?.id === sourceBlockId);
  if (!block || block.source !== 'ai' || !block.cutType) return null;

  const request = { mode: 'new' };
  for (const field of RETRY_FIELDS) {
    if (block[field] !== undefined) request[field] = block[field];
  }
  request.matchIds = Array.isArray(block.matchIds) ? [...block.matchIds] : [];

  const refAssetIds = (block.refImages || [])
    .map((item) => item?.assetId)
    .filter(Boolean);
  if (refAssetIds.length) request.refAssetIds = refAssetIds;

  return {
    request,
    sourceBlockId,
    title: block.title || '이 컷',
    thumb: block.thumb || null,
    signature: typeof block.exampleId === 'string' && block.exampleId.startsWith('sig_'),
  };
}
