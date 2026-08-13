/* Pure selection helpers shared by the canvas event handlers and node tests. */

import { mergeSpeechBubbleElements } from './editorBubbleFit.js';

const LEGACY_LIBRARY_GROUP_SIZE = {
  'text-box': 2,
  'qa-bubbles': 2,
  divider: 1,
  'arrow-callout': 2,
  'label-badge': 2,
};

export function selectionIdsForElement(elements, element) {
  if (!element?.id) return [];
  // A background/line is a real top-level layer: clicking it must keep that
  // parent individually selectable (and therefore individually deletable).
  // Text is the convenient composite hit target and selects its whole object.
  if (element.type !== 'text') return [element.id];
  if (element.groupId) {
    const grouped = (elements || [])
      .filter((candidate) => candidate.groupId === element.groupId)
      .map((candidate) => candidate.id);
    return grouped.length ? grouped : [element.id];
  }
  // Object-library items saved before groupId shipped still retain their item
  // marker. Normalize usually repairs them on load; this fallback also makes a
  // partially restored document safe during the first render.
  if (element.libraryItemId) {
    const all = elements || [];
    const index = all.findIndex((candidate) => candidate.id === element.id);
    const size = LEGACY_LIBRARY_GROUP_SIZE[element.libraryItemId] || 1;
    if (index >= 0 && size > 1) {
      let start = index;
      while (start > 0 && all[start - 1].libraryItemId === element.libraryItemId && !all[start - 1].groupId) start -= 1;
      const offset = index - start;
      const chunkStart = start + Math.floor(offset / size) * size;
      const grouped = all.slice(chunkStart, chunkStart + size)
        .filter((candidate) => candidate.libraryItemId === element.libraryItemId)
        .map((candidate) => candidate.id);
      if (grouped.length > 1) return grouped;
    }
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

export function shouldPreserveMultiSelectionOnPointerDown({ selected, selectionCount, additive }) {
  return Boolean(selected && selectionCount > 1 && !additive);
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
