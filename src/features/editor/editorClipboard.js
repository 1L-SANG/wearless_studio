const cloneEditorValue = (value) => {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
};

const selectionBounds = (elements) => elements.reduce((bounds, element) => {
  const x = Number(element.x) || 0;
  const y = Number(element.y) || 0;
  const w = Math.max(0, Number(element.w) || 0);
  const h = Math.max(0, Number(element.h) || 0);
  return {
    left: Math.min(bounds.left, x),
    top: Math.min(bounds.top, y),
    right: Math.max(bounds.right, x + w),
    bottom: Math.max(bounds.bottom, y + h),
  };
}, { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity });

const pasteAxisOffset = (min, max, limit, distance) => {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return 0;
  if (!Number.isFinite(limit)) return distance;
  if (max + distance <= limit) return distance;
  if (min - distance >= 0) return -distance;
  return Math.max(-min, Math.min(limit - max, distance));
};

export function copyEditorElements(blocks, selectedIds) {
  if (!Array.isArray(blocks) || !selectedIds?.length) return null;
  const selected = new Set(selectedIds);
  const block = blocks.find((candidate) => candidate.elements?.some((element) => selected.has(element.id)));
  if (!block) return null;
  const elements = (block.elements || []).filter((element) => selected.has(element.id)).map(cloneEditorValue);
  return elements.length ? { blockId: block.id, elements } : null;
}

export function pasteEditorElements(block, copiedElements, createId, distance = 24) {
  if (!block || !Array.isArray(copiedElements) || !copiedElements.length || typeof createId !== 'function') return null;
  const source = copiedElements.map(cloneEditorValue);
  const bounds = selectionBounds(source);
  const blockHeight = Number(block.h);
  const dx = pasteAxisOffset(bounds.left, bounds.right, 1000, distance);
  const dy = pasteAxisOffset(bounds.top, bounds.bottom, Number.isFinite(blockHeight) ? blockHeight : Infinity, distance);
  const groupIds = new Map();
  const bubblePairIds = new Map();
  const remap = (map, oldId, prefix) => {
    if (!oldId) return oldId;
    if (!map.has(oldId)) map.set(oldId, createId(prefix));
    return map.get(oldId);
  };
  const elements = source.map((element) => ({
    ...element,
    id: createId('el'),
    ...(element.groupId ? { groupId: remap(groupIds, element.groupId, 'grp') } : {}),
    ...(element.bubblePairId ? { bubblePairId: remap(bubblePairIds, element.bubblePairId, 'pair') } : {}),
    x: (Number(element.x) || 0) + dx,
    y: (Number(element.y) || 0) + dy,
  }));
  return { elements, selectedIds: elements.map((element) => element.id), offset: [dx, dy] };
}

/* 블록(프레임) 통째 복사·붙여넣기.
   복사본은 **셀러가 직접 만든 일반 블록**이 된다 — 자동 관리 표식(auto·info·infoType·
   contentRole·templateId)과 콘티 연결(sourceBlockId)을 뗀다. 남겨 두면 정보 템플릿·배송 블록
   정리·생성 완료 병합이 복사본을 원본의 중복으로 보고 덮어쓰거나 지운다. 보이는 것(배경·높이·
   요소 배치·사진)은 그대로다. */
const BLOCK_AUTOMATION_KEYS = ['auto', 'info', 'infoType', 'contentRole', 'templateId'];
const ELEMENT_AUTOMATION_KEYS = ['sourceBlockId', 'genPending', 'genFailed', 'slotCleared'];

export function copyEditorBlock(blocks, blockId) {
  if (!Array.isArray(blocks) || !blockId) return null;
  const block = blocks.find((candidate) => candidate.id === blockId);
  return block ? { kind: 'block', blockId: block.id, block: cloneEditorValue(block) } : null;
}

export function pasteEditorBlock(copiedBlock, createId) {
  if (!copiedBlock || typeof createId !== 'function') return null;
  const source = cloneEditorValue(copiedBlock);
  const groupIds = new Map();
  const bubblePairIds = new Map();
  const remap = (map, oldId, prefix) => {
    if (!oldId) return oldId;
    if (!map.has(oldId)) map.set(oldId, createId(prefix));
    return map.get(oldId);
  };
  const block = { ...source, id: createId('b') };
  for (const key of BLOCK_AUTOMATION_KEYS) delete block[key];
  block.elements = (source.elements || []).map((element) => {
    const next = {
      ...element,
      id: createId('el'),
      ...(element.groupId ? { groupId: remap(groupIds, element.groupId, 'grp') } : {}),
      ...(element.bubblePairId ? { bubblePairId: remap(bubblePairIds, element.bubblePairId, 'pair') } : {}),
    };
    for (const key of ELEMENT_AUTOMATION_KEYS) delete next[key];
    return next;
  });
  return block;
}
