// Follow-up cards reference the generated original, never the example photograph.
const RECIPE_FIELDS = ['cutType', 'shot', 'direction', 'sideStyle', 'colorId', 'colorIds', 'modelId', 'exampleId', 'exampleSelectionOrigin', 'refScope', 'refImages', 'refAssetIds', 'matchIds', 'matchIdsOrigin', 'outerClosureState', 'horizonBackgroundMode', 'spaceGroupId', 'spaceVariation', 'faceExposure', 'angle', 'thumb', 'previewThumb', 'baseThumb'];
const excludedOwnership = key => /^(space|hook|colorway)/.test(key) && !['spaceGroupId', 'spaceVariation'].includes(key);
export const canPlanSimilarCut = (block) => !!block && block.source === 'ai'
  && ['styling', 'horizon'].includes(block.cutType)
  && !block.similarSourceBlockId && !!block.exampleId;
export function createSimilarCutBlock(source, id) {
  if (!canPlanSimilarCut(source)) throw new Error('착용 사진을 기준으로 비슷한 컷을 추가할 수 있어요.');
  const child = { ...source, id, similarSourceBlockId: source.id, sectionCustom: true, slotKey: null };
  for (const key of Object.keys(child)) {
    if (excludedOwnership(key) || /^layoutRow/.test(key)) delete child[key];
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
    if (!source || source.source !== 'ai' || source.similarSourceBlockId || !['styling', 'horizon'].includes(source.cutType)) return block; // Preserve invalid dependencies for server validation.
    const next = { ...block, sectionId: source.sectionId, sectionTitle: source.sectionTitle,
      pageSectionKey: source.pageSectionKey, pageSectionKind: source.pageSectionKind,
      pageSectionUsage: source.pageSectionUsage, pageDesignId: source.pageDesignId, slotKey: null };
    for (const key of RECIPE_FIELDS) {
      if (key in source) next[key] = source[key]; else delete next[key];
    }
    for (const key of Object.keys(next)) {
      if (excludedOwnership(key)) delete next[key];
    }
    if (JSON.stringify(next) === JSON.stringify(block)) return block;
    changed = true;
    return next;
  });
  return changed ? out : blocks;
}

// Swapping original cards also moves their follow-ups. Ignore insertions/deletions:
// only a changed order among surviving originals counts as moving an original.
export function keepSimilarFamiliesAfterSources(previous, next) {
  const originals = list => list.filter(block => !block.similarSourceBlockId);
  const previousIds = new Set(originals(previous).map(block => block.id));
  const nextIds = new Set(originals(next).map(block => block.id));
  const before = originals(previous).filter(block => nextIds.has(block.id));
  const after = originals(next).filter(block => previousIds.has(block.id));
  const beforeOrder = new Map(before.map((block, index) => [block.id, index]));
  const moved = new Set(after.filter((block, index) => beforeOrder.get(block.id) !== index).map(block => block.id));
  const followers = new Map();
  for (const block of next) {
    if (!moved.has(block.similarSourceBlockId)) continue;
    if (!followers.has(block.similarSourceBlockId)) followers.set(block.similarSourceBlockId, []);
    followers.get(block.similarSourceBlockId).push(block);
  }
  if (!followers.size) return next;
  return next.filter(block => !followers.has(block.similarSourceBlockId))
    .flatMap(block => [block, ...(followers.get(block.id) || [])]);
}

// Apply the same dependent-card cleanup to the UI, save payload and undo snapshot.
export function reconcileSimilarCutMutation(previous, next) {
  if (!Array.isArray(next)) return next;
  if (!Array.isArray(previous)) return inheritSimilarCutRecipes(next);
  const byId = new Map(next.map(block => [block.id, block]));
  const removed = new Set(previous.filter(block => {
    const current = byId.get(block.id);
    return !current || (block.source === 'ai' && ['styling', 'horizon'].includes(block.cutType)
      && (current.source !== 'ai' || !['styling', 'horizon'].includes(current.cutType)));
  }).map(block => block.id));
  const cleaned = next.some(block => removed.has(block.similarSourceBlockId))
    ? next.filter(block => !removed.has(block.similarSourceBlockId)) : next;
  return inheritSimilarCutRecipes(cleaned);
}
