// Follow-up cards reference the generated original, never the example photograph.
const RECIPE_FIELDS = ['cutType', 'shot', 'direction', 'sideStyle', 'colorId', 'colorIds', 'modelId', 'exampleId', 'exampleSelectionOrigin', 'refScope', 'refImages', 'refAssetIds', 'matchIds', 'matchIdsOrigin', 'outerClosureState', 'horizonBackgroundMode', 'faceExposure', 'angle', 'thumb', 'previewThumb', 'baseThumb'];
export const canPlanSimilarCut = (block) => !!block && block.source === 'ai'
  && ['styling', 'horizon', 'mirror'].includes(block.cutType)
  && !block.similarSourceBlockId && !!block.exampleId;
export function createSimilarCutBlock(source, id) {
  if (!canPlanSimilarCut(source)) throw new Error('착용 사진을 기준으로 비슷한 컷을 추가할 수 있어요.');
  const child = { ...source, id, similarSourceBlockId: source.id, sectionCustom: true };
  for (const key of Object.keys(child)) {
    if (/^(space|hook|colorway|layoutRow)/.test(key)) delete child[key];
  }
  return child;
}
export function inheritSimilarCutRecipes(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  const byId = new Map(blocks.map(block => [block.id, block]));
  let changed = false;
  const out = blocks.map(block => {
    if (!block.similarSourceBlockId) return block;
    const source = byId.get(block.similarSourceBlockId);
    if (!source || source.source !== 'ai' || source.similarSourceBlockId || !['styling', 'horizon', 'mirror'].includes(source.cutType)) return block; // Preserve invalid dependencies for server validation.
    const next = { ...block };
    for (const key of RECIPE_FIELDS) {
      if (key in source) next[key] = source[key]; else delete next[key];
    }
    if (RECIPE_FIELDS.every(key => JSON.stringify(next[key]) === JSON.stringify(block[key]))) return block;
    changed = true;
    return next;
  });
  return changed ? out : blocks;
}

// Apply the same dependent-card cleanup to the UI, save payload and undo snapshot.
export function reconcileSimilarCutMutation(previous, next) {
  if (!Array.isArray(next)) return next;
  if (!Array.isArray(previous)) return inheritSimilarCutRecipes(next);
  const byId = new Map(next.map(block => [block.id, block]));
  const removed = new Set(previous.filter(block => {
    const current = byId.get(block.id);
    return !current || (block.source === 'ai' && ['styling', 'horizon', 'mirror'].includes(block.cutType)
      && (current.source !== 'ai' || !['styling', 'horizon', 'mirror'].includes(current.cutType)));
  }).map(block => block.id));
  const cleaned = next.some(block => removed.has(block.similarSourceBlockId))
    ? next.filter(block => !removed.has(block.similarSourceBlockId)) : next;
  return inheritSimilarCutRecipes(cleaned);
}
