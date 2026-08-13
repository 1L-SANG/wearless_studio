/* Pure selection helpers shared by the canvas event handlers and node tests. */

import { mergeSpeechBubbleElements } from './editorBubbleFit.js';

const LEGACY_LIBRARY_GROUP_SIZE = {
  'text-box': 2,
  'single-bubble': 1,
  'qa-bubbles': 2,
  divider: 1,
  'arrow-callout': 2,
  'label-badge': 2,
};

export function selectionIdsForElement(elements, element) {
  if (!element?.id) return [];
  // Every primitive remains individually selectable. In particular, dragging
  // copy must never pull its grouped background/line along with it. A unified
  // speech bubble is itself the visible object; only those explicit bubble
  // groups retain their whole-object selection behaviour.
  if (element.type !== 'text' || element.shape !== 'bubble') return [element.id];
  if (element.groupId) {
    const grouped = (elements || [])
      .filter((candidate) => candidate.groupId === element.groupId)
      .map((candidate) => candidate.id);
    return grouped.length ? grouped : [element.id];
  }
  return [element.id];
}

function boxContainsCenter(parent, child) {
  const cx = Number(child.x || 0) + Number(child.w || 0) / 2;
  const cy = Number(child.y || 0) + Number(child.h || 0) / 2;
  return cx >= Number(parent.x || 0) && cx <= Number(parent.x || 0) + Number(parent.w || 0)
    && cy >= Number(parent.y || 0) && cy <= Number(parent.y || 0) + Number(parent.h || 0);
}

export function normalizeEditorSelectionGroups(blocks) {
  let changed = false;
  const normalized = (blocks || []).map((block) => {
    const originalElements = block.elements || [];
    const elements = mergeSpeechBubbleElements(originalElements);
    if (elements !== originalElements) changed = true;
    const groupById = new Map();

    for (let index = 0; index < elements.length;) {
      const first = elements[index];
      const size = first?.libraryItemId && !first.groupId ? (LEGACY_LIBRARY_GROUP_SIZE[first.libraryItemId] || 1) : 1;
      if (size > 1) {
        const members = [];
        for (let offset = 0; offset < size; offset += 1) {
          const candidate = elements[index + offset];
          if (!candidate || candidate.libraryItemId !== first.libraryItemId || candidate.groupId) break;
          members.push(candidate);
        }
        if (members.length > 1) {
          const groupId = `legacy-object:${block.id}:${members[0].id}`;
          members.forEach((member) => groupById.set(member.id, groupId));
          index += members.length;
          continue;
        }
      }
      index += 1;
    }

    // Old FAQ blocks used visual cards/bubbles without selection metadata.
    // Their own info contract is a safe boundary for spatial parent→text repair;
    // do not infer relationships in arbitrary user-made blocks.
    if (block.infoType === 'faq') {
      elements.filter((element) => element.type === 'shape' && !element.groupId && !groupById.has(element.id)).forEach((shape) => {
        const texts = elements.filter((element) => element.type === 'text' && !element.groupId
          && !groupById.has(element.id) && boxContainsCenter(shape, element));
        if (!texts.length) return;
        const groupId = `legacy-faq:${block.id}:${shape.id}`;
        groupById.set(shape.id, groupId);
        texts.forEach((text) => groupById.set(text.id, groupId));
      });
    }

    if (!groupById.size) return elements === originalElements ? block : { ...block, elements };
    changed = true;
    return {
      ...block,
      elements: elements.map((element) => groupById.has(element.id)
        ? { ...element, groupId: groupById.get(element.id) }
        : element),
    };
  });
  return changed ? normalized : blocks;
}

export function shouldClearEditorSelection(target) {
  if (!target?.closest) return true;
  return !target.closest('[data-elid], .canvas-block, .moveable-control-box, .align-bar');
}

export function isEditorGrayWorkspaceTarget(target) {
  if (!target?.closest) return false;
  return !target.closest('.canvas-block, .moveable-control-box');
}

export function shouldPreserveMultiSelectionOnPointerDown({ selected, selectionCount, additive }) {
  return Boolean(selected && selectionCount > 1 && !additive);
}

export function shouldStartTextOnlyDrag(element, additive) {
  return Boolean(!additive && element?.type === 'text' && element?.shape !== 'bubble');
}

export function shouldPassGroupDragArea(elements) {
  const selected = (elements || []).filter(Boolean);
  // Q&A 말풍선은 각 요소 자체가 완성된 오브젝트라 자식 선택을 위해 포인터를 통과시킬
  // 이유가 없다. Moveable의 그룹 드래그 영역이 직접 포인터를 받아야 선택 후 이동된다.
  return !selected.length || !selected.every((element) => element.type === 'text' && element.shape === 'bubble');
}

function intersectsRect(first, second) {
  if (!first || !second) return false;
  return first.left < second.right && first.right > second.left
    && first.top < second.bottom && first.bottom > second.top;
}

export function selectionIdsInsideMarquee(elements, elementRects, marqueeRect) {
  const selectable = (elements || []).filter((element) => !element.hidden && !element.locked);
  const selectableIds = new Set(selectable.map((element) => element.id));
  const selectedIds = new Set();
  selectable.forEach((element) => {
    const rect = elementRects instanceof Map ? elementRects.get(element.id) : elementRects?.[element.id];
    if (!intersectsRect(marqueeRect, rect)) return;
    selectionIdsForElement(elements, element).forEach((id) => {
      if (selectableIds.has(id)) selectedIds.add(id);
    });
  });
  return (elements || []).map((element) => element.id).filter((id) => selectedIds.has(id));
}

export function isEditorDeleteKey(event) {
  if (event?.key !== 'Delete' && event?.key !== 'Backspace') return false;
  const target = event.target || {};
  if (/input|textarea|select/i.test(String(target.tagName || '')) || target.isContentEditable) return false;
  return true;
}

export function removeSelectedElements(blocks, selectedIds) {
  const selected = new Set(selectedIds || []);
  if (!selected.size) return blocks;
  return blocks.map((block) => ({
    ...block,
    elements: block.elements.filter((element) => !selected.has(element.id)),
  }));
}

export function removeSelectedBlock(blocks, selectedBlockId) {
  if (!selectedBlockId) return blocks;
  return (blocks || []).filter((block) => block.id !== selectedBlockId);
}
