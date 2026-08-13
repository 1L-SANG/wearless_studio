import {
  WARDROBE_IMAGE_MIME,
  decodeWardrobeImage,
  encodeWardrobeImage,
} from './editorLibrary.js';

export const EDITOR_IMAGE_DRAG_TYPE = WARDROBE_IMAGE_MIME;
export const encodeEditorImageDrag = encodeWardrobeImage;
export const decodeEditorImageDrag = decodeWardrobeImage;

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
  const emptySlots = (elements || []).filter((element) => (
    element.type === 'image' && element.frameSlot && !element.src
  ));
  if (!point) return emptySlots[0] || null;
  return emptySlots.find((element) => (
    point.x >= element.x
    && point.x <= element.x + element.w
    && point.y >= element.y
    && point.y <= element.y + element.h
  )) || null;
}

export function pendingImageImportTarget({ elements, blockHeight, point }) {
  const slot = findImageDropSlot(elements, point);
  if (slot) {
    return {
      slotId: slot.id,
      x: slot.x,
      y: slot.y,
      w: slot.w,
      h: slot.h,
      radius: slot.radius || 10,
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
