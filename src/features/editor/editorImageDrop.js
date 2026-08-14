import {
  WARDROBE_IMAGE_MIME,
  decodeWardrobeImage,
  encodeWardrobeImage,
} from './editorLibrary.js';

export const EDITOR_IMAGE_DRAG_TYPE = WARDROBE_IMAGE_MIME;
export const EDITOR_FRAME_DRAG_TYPE = 'text/frame';
export const EDITOR_INFO_PRESET_DRAG_TYPE = 'application/x-wearless-info-preset';
export const encodeEditorImageDrag = encodeWardrobeImage;
export const decodeEditorImageDrag = decodeWardrobeImage;

export function acceptsEditorBlockInsert(types) {
  const available = Array.from(types || []);
  return [EDITOR_IMAGE_DRAG_TYPE, EDITOR_FRAME_DRAG_TYPE, EDITOR_INFO_PRESET_DRAG_TYPE]
    .some((type) => available.includes(type));
}

export function viewportPointToBlock({ clientX, clientY, blockLeft, blockTop, scale = 1 }) {
  const safeScale = Number(scale) > 0 ? Number(scale) : 1;
  return {
    x: Math.round((clientX - blockLeft) / safeScale),
    y: Math.round((clientY - blockTop) / safeScale),
  };
}

export function placeImageInBlock({
  blockHeight,
  imageWidth,
  imageHeight,
  dropX = 500,
  dropY,
}) {
  const height = Math.max(120, Number(blockHeight) || 300);
  const margin = Math.min(40, Math.max(0, (height - 24) / 2));
  const availableWidth = 1000 - margin * 2;
  const availableHeight = Math.max(24, height - margin * 2);
  const sourceWidth = Number(imageWidth);
  const sourceHeight = Number(imageHeight);
  const ratio = sourceWidth > 0 && sourceHeight > 0 ? sourceWidth / sourceHeight : 4 / 5;

  let width = availableWidth;
  let fittedHeight = width / ratio;
  if (fittedHeight > availableHeight) {
    fittedHeight = availableHeight;
    width = fittedHeight * ratio;
  }

  const w = Math.max(24, Math.min(availableWidth, Math.round(width)));
  const h = Math.max(24, Math.min(availableHeight, Math.round(fittedHeight)));
  const centerY = Number.isFinite(Number(dropY)) ? Number(dropY) : height / 2;
  const centerX = Number.isFinite(Number(dropX)) ? Number(dropX) : 500;
  const x = Math.round(Math.max(margin, Math.min(1000 - margin - w, centerX - w / 2)));
  const y = Math.round(Math.max(margin, Math.min(height - margin - h, centerY - h / 2)));

  return { x, y, w, h };
}

export function findImageDropSlot(elements, point) {
  const frameSlots = (elements || []).filter((element) => (
    element.type === 'image' && element.frameSlot
  ));
  if (!point) return frameSlots.find((element) => !element.src) || null;
  // Full-canvas template backgrounds sit behind smaller foreground slots. A drop over a
  // decorative card should prefer the smallest matching slot, but a drop anywhere else
  // still needs to fill the background rather than creating an unrelated loose image.
  return frameSlots
    .filter((element) => (
      point.x >= element.x
      && point.x <= element.x + element.w
      && point.y >= element.y
      && point.y <= element.y + element.h
    ))
    .sort((a, b) => (a.w * a.h) - (b.w * b.h))[0] || null;
}

export function pendingImageImportTarget({ elements, blockHeight, point, slotId = null }) {
  const slot = slotId
    ? (elements || []).find((element) => (
      element.id === slotId && element.type === 'image' && element.frameSlot
    )) || null
    : findImageDropSlot(elements, point);
  if (slot) {
    return {
      slotId: slot.id,
      x: slot.x,
      y: slot.y,
      w: slot.w,
      h: slot.h,
      radius: slot.radius || 10,
      ...(slot.rotate ? { rotate: slot.rotate } : {}),
    };
  }

  return {
    slotId: null,
    ...placeImageInBlock({
      blockHeight,
      imageWidth: 4,
      imageHeight: 5,
      dropX: point?.x,
      dropY: point?.y,
    }),
    radius: 12,
  };
}
