/* Pure selection helpers shared by the canvas event handlers and node tests. */

import { mergeSpeechBubbleElements } from './editorBubbleFit.js';

const LEGACY_LIBRARY_GROUP_SIZE = {
  'text-box': 2,
  'single-bubble': 1,
  'qa-bubbles': 1,
  divider: 1,
  'arrow-callout': 2,
  'label-badge': 2,
};

export function selectionIdsForElement(elements, element) {
  if (!element?.id) return [];
  // Object-library presets and speech-bubble pairs are complete visual
  // objects. Picking any of their primitives selects the whole saved group;
  // unrelated user-created elements may still carry group metadata without
  // losing their individual text-drag behaviour.
  const selectsWholeGroup = Boolean(element.groupId
    && (element.libraryItemId || (element.type === 'text' && element.shape === 'bubble')));
  if (!selectsWholeGroup) return [element.id];
  const grouped = (elements || [])
    .filter((candidate) => candidate.groupId === element.groupId)
    .map((candidate) => candidate.id);
  return grouped.length ? grouped : [element.id];
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
    let elements = mergeSpeechBubbleElements(originalElements);
    if (elements !== originalElements) changed = true;
    const qaGroupCounts = new Map();
    elements.forEach((element) => {
      if (element.libraryItemId !== 'qa-bubbles' || !element.groupId) return;
      qaGroupCounts.set(element.groupId, (qaGroupCounts.get(element.groupId) || 0) + 1);
    });
    if ([...qaGroupCounts.values()].some((count) => count > 1)) {
      elements = elements.map((element) => element.libraryItemId === 'qa-bubbles'
        && qaGroupCounts.get(element.groupId) > 1
        ? { ...element, groupId: `qa-bubble:${block.id}:${element.id}` }
        : element);
      changed = true;
    }
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
  const isObjectLibraryGroup = Boolean(element?.groupId && element?.libraryItemId);
  return Boolean(!additive && element?.type === 'text'
    && element?.shape !== 'bubble' && !isObjectLibraryGroup);
}

/** Pick the first real element exposed below a blank part of a wide text box.
 * Normal text only qualifies when the pointer is on one of its rendered glyph
 * lines; visual objects and composite text use their full visible bounds. */
export function selectableElementBelowBlankText(elements, currentId, candidateIds, glyphHitIds = []) {
  const byId = new Map((elements || []).map((element) => [element.id, element]));
  const glyphHits = new Set(glyphHitIds || []);
  for (const id of candidateIds || []) {
    const element = byId.get(id);
    if (!element || element.id === currentId || element.hidden || element.locked) continue;
    const normalText = element.type === 'text' && element.shape !== 'bubble' && !element.fullTextHitArea
      && !(element.groupId && element.libraryItemId);
    if (normalText && !glyphHits.has(element.id)) continue;
    return element;
  }
  return null;
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

/** 레이어 창 드래그로 요소 순서 바꾸기 — 끌어 놓은 요소를 대상 자리로 옮긴 elements 배열.
    두 인덱스를 **꺼내기 전에** 모두 구해야 한다. 먼저 꺼내면 제거로 뒤 인덱스가 하나씩
    당겨져, 바로 위 칸에 놓는 경우(가장 흔한 조작) 목표가 출발 자리와 같아져 제자리에
    도로 꽂혔다 — "위로 옮기기"가 통째로 먹통이던 원인(오너 2026-08-16). */
export function reorderElements(elements, fromId, toId) {
  const next = [...(elements || [])];
  const from = next.findIndex((element) => element.id === fromId);
  const to = next.findIndex((element) => element.id === toId);
  if (from < 0 || to < 0 || from === to) return elements;
  const [moved] = next.splice(from, 1);
  // 꺼낸 뒤의 `to` 가 그대로 정답이다. 뒤로 끌면 제거로 인덱스가 하나 당겨지지만 '대상의
  // 뒷자리'로 넣어야 해서 +1 이 되어 상쇄되고, 앞으로 끌면 대상 앞자리가 곧 `to` 다.
  next.splice(to, 0, moved);
  return next;
}

export function removeSelectedBlock(blocks, selectedBlockId) {
  if (!selectedBlockId) return blocks;
  return (blocks || []).filter((block) => block.id !== selectedBlockId);
}
