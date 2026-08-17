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

/* 격자·프레임 안의 사진 자리 — 여기 사진을 지우면 요소를 없애는 게 아니라 **빈 자리로
   되돌린다**(오너 2026-08-17). 4장짜리 격자는 따로 만든 사진 4장을 나란히 붙여 둔 것이라
   한 장만 지우는 건 맞지만, 자리까지 사라지면 격자가 무너지고 다시 넣은 사진이 칸 크기를
   모른 채 블록을 통째로 덮는다. 빈 자리로 남기면 '＋ 여기에 사진 넣기'가 그 자리에 뜨고
   드롭도 그 칸에 스냅된다. */
const PHOTO_SLOT_BLOCK_KINDS = new Set(['twocol', 'threecol', 'grid2x2', 'colorcmp']);

export function isPhotoSlotElement(block, element) {
  if (element?.type !== 'image') return false;
  if (element.frameSlot) return true;   // 프레임 탭 템플릿의 사진 칸
  // 콘티에서 조립된 사진 행·격자의 '칸'만 — sourceBlockId 가 그 표식이다. 블록 종류만 보면
  // 그 블록에 나중에 얹은 낱장 사진까지 칸으로 오인해, Delete 해도 빈 칸으로만 바뀌며
  // 영영 못 지우게 된다(2026-08-17 리뷰).
  return PHOTO_SLOT_BLOCK_KINDS.has(block?.kind) && Boolean(element.sourceBlockId);
}

/** 사진이 **든** 자리는 비우고(요소 유지), 이미 빈 자리와 그 밖의 요소는 지운다.
    빈 자리까지 남기면 프레임 템플릿의 남는 칸이나 한 번 비운 칸을 영영 못 없앤다. */
export function removeSelectedElements(blocks, selectedIds) {
  const selected = new Set(selectedIds || []);
  if (!selected.size) return blocks;
  return blocks.map((block) => ({
    ...block,
    elements: block.elements.reduce((kept, element) => {
      if (!selected.has(element.id)) { kept.push(element); return kept; }
      if (!isPhotoSlotElement(block, element) || !element.src) return kept;   // 지운다
      // 비운다 — 자리·크기·모서리는 그대로, 사진에 딸린 것만 걷어낸다.
      // sourceBlockId(콘티 컷과의 연결)는 **남긴다**: 지우면 완료 병합의 안전 검사
      // (canSafelyMergeServerBlocks)가 "배치가 서버와 다르다"고 판단해 대기 중 편집분을
      // 통째로 서버본으로 갈아끼운다. 대신 slotCleared 로 "셀러가 일부러 비웠다"를 남겨
      // 병합·자동 채움이 이 자리를 되살리지 않게 한다(2026-08-17 검증).
      const { crop: _crop, genFailed: _genFailed, genPending: _genPending, ...rest } = element;
      kept.push({ ...rest, src: null, cutType: null, frameSlot: true, slotCleared: true });
      return kept;
    }, []),
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
