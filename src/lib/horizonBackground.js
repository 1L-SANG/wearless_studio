export const HORIZON_BACKGROUND_MODES = ['reference', 'garment-tone'];

export function horizonBackgroundMode(block) {
  return block?.cutType === 'horizon' && block?.spaceGroupId
    && block.horizonBackgroundMode === 'garment-tone' ? 'garment-tone' : 'reference';
}

// 콘티보드를 떠날 때 옷 색 측정을 부를지 정하는 클라 게이트. 서버가 세트 종류와 기존 측정을 다시 보고
// 필요 없으면 건너뛰므로, 여기서는 '옷 색에 맞춤'을 고른 AI 호리존 세트 컷이 있는지만 본다.
export function needsGarmentColorMeasurement(blocks) {
  return (blocks || []).some(block => block?.source !== 'mine' && block?.cutType === 'horizon'
    && block?.spaceGroupId && horizonBackgroundMode(block) === 'garment-tone');
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
