import { detailTargetPresentation } from '../../lib/detailRecommendations.js';
import { failedCutPresentation } from '../../lib/cutFailureMessages.js';
import { uniqueGenerationCutCount } from '../../lib/generationCutCount.js';
import { isSupportedCutType } from '../../lib/storyboardTaxonomy.js';
import { referenceScopeForBlock } from '../../lib/referenceMode.js';

const RETRY_FIELDS = Object.freeze([
  'contentRole',
  'colorId',
  'cutType',
  'direction',
  'sideStyle',
  'shot',
  'detailTargetId',
  'detailTargetOrigin',
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
export function buildFailedCutRetry(storyboard, sourceBlockId, modelSelection = null, product = null, { editorBlocks = [], failure = {} } = {}) {
  if (!sourceBlockId || !Array.isArray(storyboard)) return null;
  const block = storyboard.find((item) => item?.id === sourceBlockId);
  if (!block || block.source !== 'ai' || !isSupportedCutType(block.cutType)) return null;

  let request = { mode: 'new', retryBlockId: block.id };
  let { retryable, guidance } = failedCutPresentation(failure);
  let fillImage = null;
  for (const field of RETRY_FIELDS) {
    if (block[field] !== undefined) request[field] = block[field];
  }
  if (block.cutType === 'horizon' && block.spaceGroupId) {
    for (const field of ['spaceGroupId', 'spaceVariation', 'spaceSetMemberOrder', 'horizonBackgroundMode']) {
      if (block[field] !== undefined) request[field] = block[field];
    }
  }
  request.refScope = referenceScopeForBlock(request);
  if (modelSelection) {
    const selectedModelId = modelSelection.selectedModelId
      || modelSelection.selected_model_id
      || null;
    // 실제 모델도 모든 착용 컷에 그대로 간다 — 예전의 스타일링 대역(가상 모델) 치환 없음.
    request.modelId = block.cutType === 'product' ? null : selectedModelId;
  }
  request.matchIds = Array.isArray(block.matchIds) ? [...block.matchIds] : [];

  const refAssetIds = (block.refImages || [])
    .map((item) => item?.assetId)
    .filter(Boolean);
  if (refAssetIds.length) request.refAssetIds = refAssetIds;

  if (block.similarSourceBlockId) {
    const original = editorBlocks.flatMap(item => item.elements || []).find(element =>
      element.type === 'image' && element.sourceBlockId === block.similarSourceBlockId && element.src);
    retryable = Boolean(original);
    guidance = original ? '원본 사진으로 비슷한 컷을 만들어요' : '원본 컷을 먼저 만들어 주세요';
    request = original ? { mode: 'vary', variationMode: 'similar', changes: [], retryBlockId: block.id,
      source: { src: original.src, cutType: original.cutType || block.cutType } } : null;
  }

  if (failure.genFailReason === 'duplicate_cut_source_failed') {
    const sameRecipe = candidate => candidate.source === 'ai'
      && ['sideStyle', 'modelId', 'matchIds'].every(field =>
        JSON.stringify(candidate[field] ?? null) === JSON.stringify(block[field] ?? null))
      && uniqueGenerationCutCount([
        { ...candidate, contentRole: null, sectionRole: null },
        { ...block, contentRole: null, sectionRole: null },
      ]) === 1;
    const original = failure.genFailSourceBlockId !== undefined
      ? storyboard.find(candidate => typeof failure.genFailSourceBlockId === 'string'
        && candidate?.id === failure.genFailSourceBlockId
        && candidate.id !== block.id && candidate.source === 'ai' && candidate.cutType)
      : storyboard.slice(0, storyboard.indexOf(block)).find(sameRecipe);
    fillImage = original ? editorBlocks.flatMap(item => item.elements || []).find(element =>
      element.type === 'image' && element.sourceBlockId === original.id && element.src
      && !element.genPending && !element.genFailed && !element.slotCleared) || null : null;
    retryable = Boolean(fillImage);
    guidance = fillImage ? '원본 사진으로 빈 칸을 채워요' : '원본 컷을 먼저 만들어 주세요';
    request = null;
  }

  return {
    request, retryable, guidance, fillImage,
    sourceBlockId,
    title: block.title || '이 컷',
    thumb: detailTargetPresentation(block, product, modelSelection?.detailRecommendations)?.src || (block.shot === 'detail' ? null : block.thumb) || null,
    detailLabel: detailTargetPresentation(block, product, modelSelection?.detailRecommendations)?.label || null,
    signature: typeof block.exampleId === 'string' && block.exampleId.startsWith('sig_'),
  };
}
