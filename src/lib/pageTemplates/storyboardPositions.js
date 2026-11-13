import { rekeySeparatedSpaceRuns } from '../storyboardSpaceSets.js';
import { spaceSetGroupId } from '../storyboardSpaceSetCatalog.js';

const HIDDEN = new Set(['intro', 'for_you']);
export const isHiddenStoryboardKind = kind => HIDDEN.has(kind);

export const storyboardPositionId = (block, element) => block.pageSectionKey && element.slotKey
  ? `${block.pageSectionKey}:${element.slotKey}:${element.sourceBlockId || 'reuse'}`
  : `${block.id}:${element.id}`;

// Geometry belongs to the presentation. viewId never enters saved blocks.
export function storyboardPositions(page = [], sectionKey) {
  let top = 0;
  const positions = [];
  for (const block of page) {
    if (block.pageSectionKey !== sectionKey || !Number.isFinite(block.h) || block.h <= 0) continue;
    for (const element of block.elements || []) {
      if (element.type !== 'image' || Number.isFinite(element.blur) || element.hidden || !['x', 'y', 'w', 'h'].every(key => Number.isFinite(element[key])) || element.w <= 0 || element.h <= 0) continue;
      positions.push({ viewId: storyboardPositionId(block, element), sourceBlockId: element.sourceBlockId,
        slotKey: element.slotKey, element, top: top + element.y, area: element.w * element.h });
    }
    top += block.h;
  }
  positions.sort((a, b) => a.top - b.top || a.element.x - b.element.x);
  const areas = positions.map(item => item.area), max = Math.max(...areas), min = Math.min(...areas);
  const mixed = max >= min * 1.7;
  const counts = {}, totals = {};
  positions.forEach(item => { item.size = mixed ? item.area >= max * .8 ? '큰 자리' : '작은 자리' : '자리'; totals[item.size] = (totals[item.size] || 0) + 1; });
  return positions.map((item, index) => {
    counts[item.size] = (counts[item.size] || 0) + 1;
    return { ...item, index, label: positions.length < 2 ? null
      : `${item.size}${mixed && totals[item.size] === 1 ? '' : ` ${counts[item.size]}`}` };
  });
}

export function positionSwapReason(block) {
  if (!block) return '사진을 찾을 수 없어요';
  if (block.similarSourceBlockId) return '비슷한 컷은 원본 사진과 함께 옮겨요';
  if (block.pointItem?.kind === 'wornCrop' || block.borrowed || block.fallback) return '다른 사진에서 따온 자리는 옮길 수 없어요';
  if (!['wear', 'points'].includes(block.pageSectionKind)) return '이 사진은 정해진 자리에 들어가요';
  if (block.pageSectionKind === 'wear' && (/(?:^|\.)(?:front|side|back)(?:\.|$)/.test(block.slotKey || '') || block.spaceVariation === 'rotation')) return '앞, 옆, 뒤 사진은 정해진 자리에 들어가요';
  return null;
}

export function storyboardPositionSwapReason(blocks, sourceId, targetId) {
  const a = blocks.find(block => block.id === sourceId), b = blocks.find(block => block.id === targetId);
  const reason = positionSwapReason(a) || positionSwapReason(b);
  if (reason) return reason;
  if (a.pageSectionKey !== b.pageSectionKey || a.cutType !== b.cutType) return '같은 섹션의 같은 종류 사진끼리 옮겨요';
  if (a.pageSectionKind === 'points') {
    const lead = blocks.find(block => block.pageSectionKey === a.pageSectionKey && Array.isArray(block.pointItems));
    const bound = id => lead?.pointItems.some(item => item.sourceBlockId === id);
    if (Boolean(bound(sourceId)) !== Boolean(bound(targetId))) return '같은 종류의 디테일 자리끼리 옮겨요';
  }
  return null;
}

export function swapStoryboardPositions(blocks, sourceId, targetId) {
  if (sourceId === targetId || storyboardPositionSwapReason(blocks, sourceId, targetId)) return blocks;
  const followers = id => blocks.filter(block => block.id === id || block.similarSourceBlockId === id);
  const first = followers(sourceId), second = followers(targetId);
  const changedIds = new Set([...first, ...second].map(block => block.id));
  let next = blocks.flatMap(block => block.id === sourceId ? second : block.id === targetId ? first : changedIds.has(block.id) ? [] : [block]);
  // Explicit detail order is part of the section's existing pointItems contract.
  const a = blocks.find(block => block.id === sourceId);
  if (a.pageSectionKind === 'points') next = next.map(block => {
    if (block.pageSectionKey !== a.pageSectionKey || !Array.isArray(block.pointItems)) return block;
    const source = block.pointItems.find(item => item.sourceBlockId === sourceId);
    const target = block.pointItems.find(item => item.sourceBlockId === targetId);
    return source && target ? { ...block, pointItems: block.pointItems.map(item => item === source ? target : item === target ? source : item) } : block;
  });
  const used = new Set(next.map(block => block.spaceGroupId));
  let serial = 0;
  return rekeySeparatedSpaceRuns(next, (setId, run) => {
    let id;
    do { id = spaceSetGroupId(setId, `position-${run.start}-${++serial}`); } while (used.has(id));
    used.add(id); return id;
  });
}
