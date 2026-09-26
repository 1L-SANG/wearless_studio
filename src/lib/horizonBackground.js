export const HORIZON_BACKGROUND_MODES = ['reference', 'garment-tone'];

export function horizonBackgroundMode(block) {
  return block?.cutType === 'horizon' && block?.spaceGroupId
    && block.horizonBackgroundMode === 'garment-tone' ? 'garment-tone' : 'reference';
}

// A placed set is an instance: choosing a backdrop must not change another copy
// of the same catalog set, or any lifestyle/product-only cuts.
export function updateHorizonGroupBackground(blocks, groupId, mode) {
  if (!groupId || !HORIZON_BACKGROUND_MODES.includes(mode)) return blocks;
  let changed = false;
  const next = blocks.map((block) => {
    if (block.spaceGroupId !== groupId || block.cutType !== 'horizon'
      || block.source === 'mine' || horizonBackgroundMode(block) === mode) return block;
    changed = true;
    return { ...block, horizonBackgroundMode: mode };
  });
  return changed ? next : blocks;
}

// Delete undo may run after a backdrop change. The surviving set owns the
// current setting; restoring one member must not restore an obsolete setting.
export function restoreHorizonGroupBackground(block, currentBlocks) {
  if (block?.cutType !== 'horizon' || !block.spaceGroupId) return block;
  const peer = currentBlocks.find(candidate => candidate.spaceGroupId === block.spaceGroupId
    && candidate.cutType === 'horizon' && candidate.source !== 'mine');
  return peer ? { ...block, horizonBackgroundMode: horizonBackgroundMode(peer) } : block;
}
