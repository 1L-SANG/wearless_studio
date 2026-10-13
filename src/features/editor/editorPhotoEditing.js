import { applySlotFillToInfo } from './presets/infoPresets.js';
import { getBlockRenderHeight } from './editorGeometry.js';
import { isPhotoSlotElement } from './editorSelection.js';

export function replaceEditorPhoto(blocks, elementId, image) {
  const block = blocks.find(b => b.elements.some(el => el.id === elementId));
  const el = block?.elements.find(e => e.id === elementId);
  if (!image?.src || !el || el.type !== 'image' || el.locked || el.hidden || el.genPending || el.src === image.src) return blocks;
  const filled = applySlotFillToInfo(block, elementId, image);
  return blocks.map(b => b !== block ? b : { ...filled, elements: filled.elements.map(e => {
    if (e.id !== elementId) return e;
    const { slotCleared, genFailed, genFailReason, genFailRetryable, genFailSourceBlockId, genPending, ...rest } = e;
    return rest;
  }) });
}

// Viewport rectangles: intent requires both an interior pointer and a substantial
// overlap. Large photos may enter a short empty block, which expands after drop.
export function photoMoveDestination({ sourceBlockId, blocks, pointer, photoRect, gesture = 'move' }) {
  if (gesture !== 'move' || !pointer || !photoRect) return null;
  return blocks.find(b => {
    if (b.id === sourceBlockId) return false;
    const inset = Math.min(24, b.width / 4, b.height / 4);
    if (pointer.x < b.left + inset || pointer.x > b.left + b.width - inset
      || pointer.y < b.top + inset || pointer.y > b.top + b.height - inset) return false;
    const w = Math.max(0, Math.min(photoRect.left + photoRect.width, b.left + b.width) - Math.max(photoRect.left, b.left));
    const h = Math.max(0, Math.min(photoRect.top + photoRect.height, b.top + b.height) - Math.max(photoRect.top, b.top));
    return w * h >= Math.min(photoRect.width * photoRect.height, b.width * b.height) * 0.6;
  }) || null;
}

export function transferEditorPhoto(blocks, { elementId, sourceBlockId, targetBlockId, x, y, emptyId, generationActive = false }) {
  const source = blocks.find(b => b.id === sourceBlockId), target = blocks.find(b => b.id === targetBlockId);
  const el = source?.elements.find(e => e.id === elementId);
  // A grouped photo stays with its group; the photo-only gesture never tears it out.
  if (generationActive || !source || !target || source === target || !el || el.type !== 'image'
    || !el.src || el.locked || el.hidden || el.groupId || el.genPending || el.system) return blocks;
  const slot = isPhotoSlotElement(source, el);
  if (slot && !emptyId) return blocks;
  const cleared = slot ? applySlotFillToInfo(source, elementId, { src: null, cutType: null }) : source;
  const { frameSlot, imageSizing, imageRowFlowGroup, imageRowFlowRow, imageRowFlowGap,
    imageFlowGroup, imageFlowOffset, imageFlowGap, ...appearance } = el;
  const moved = { ...appearance, x: Math.round(Math.max(0, Math.min(1000 - el.w, x))), y: Math.round(Math.max(0, y)) };
  return blocks.map(b => {
    if (b === source) return { ...cleared, elements: cleared.elements.flatMap(e => {
      if (e.id !== elementId) return [e];
      if (!slot) return [];
      const { sourceBlockId: origin, ...empty } = e;
      return [{ ...empty, id: emptyId, frameSlot: true, slotCleared: true }];
    }) };
    if (b === target) {
      const next = { ...b, elements: [...b.elements, moved] };
      return { ...next, h: getBlockRenderHeight(next) };
    }
    return b;
  });
}
