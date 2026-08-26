import {
  WARDROBE_IMAGE_MIME,
  decodeWardrobeImage,
  encodeWardrobeImage,
} from './editorLibrary.js';

export const EDITOR_IMAGE_DRAG_TYPE = WARDROBE_IMAGE_MIME;
export const EDITOR_FRAME_DRAG_TYPE = 'text/frame';
export const EDITOR_INFO_PRESET_DRAG_TYPE = 'application/x-wearless-info-preset';
/* 텍스트 프리셋 드래그의 '종류 표식'. 값이 아니라 **타입 이름**에 프리셋 키를 실어 보낸다 —
   드래그 도중에는 보안상 getData 가 막혀 types 만 읽을 수 있는데, 블록은 마우스를 올린
   순간 "어느 크기의 상자가 어디에 놓이는지"를 그려야 하기 때문이다(오너 2026-08-16).
   실제 페이로드는 오브젝트와 같은 'text/object' = `text:<키>` 로도 함께 실린다. */
export const TEXT_PRESET_DRAG_PREFIX = 'text/textpreset-';
export const textPresetKeyFromDragTypes = (types) => {
  const found = Array.from(types || []).find((type) => String(type).startsWith(TEXT_PRESET_DRAG_PREFIX));
  return found ? found.slice(TEXT_PRESET_DRAG_PREFIX.length) : null;
};
/* 오브젝트(도형·선·프리셋) 드래그의 '종류 표식'. 텍스트 프리셋과 같은 이유로 타입 이름에
   `<type>:<id>` 를 실어, 드래그 도중 블록이 어떤 오브젝트가 어디에 놓일지 미리 그린다. */
export const OBJECT_DRAG_PREFIX = 'text/objdrag-';
export const objectDescriptorFromDragTypes = (types) => {
  const found = Array.from(types || []).find((type) => String(type).startsWith(OBJECT_DRAG_PREFIX));
  if (!found) return null;
  const [type, id] = found.slice(OBJECT_DRAG_PREFIX.length).split(':');
  return type ? { type, id: id || null } : null;
};

// 오브젝트 드롭 미리보기 상자 크기 — addShape / buildObjectPreset 기본 치수와 맞춘다.
const OBJECT_PRESET_BOX = {
  'text-box': { w: 520, h: 150 },
  'single-bubble': { w: 320, h: 100 },
  'qa-bubbles': { w: 630, h: 254 },
  divider: { w: 620, h: 24 },
  'arrow-callout': { w: 390, h: 41 },
  'label-badge': { w: 190, h: 62 },
};
export function objectDropBox(type, id) {
  if (type === 'preset') return OBJECT_PRESET_BOX[id] || { w: 300, h: 120 };
  if (type === 'line') return { w: 240, h: 24 };
  if (id === 'bubble') return { w: 320, h: 100 };
  return { w: 140, h: 140 };
}

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

export function fitImageToFrameSlot(slot, image) {
  if (!slot || slot.imageSizing !== 'natural-height') return {};
  const sourceWidth = Number(image?.width || image?.w);
  const sourceHeight = Number(image?.height || image?.h);
  const frameWidth = Number(slot.w);
  if (!(sourceWidth > 0) || !(sourceHeight > 0) || !(frameWidth > 0)) return {};
  return { h: Math.max(24, Math.round(frameWidth * sourceHeight / sourceWidth)) };
}

function reflowImageRows(elements, groupId) {
  if (!groupId) return elements;
  const members = elements.filter((element) => element.imageRowFlowGroup === groupId);
  if (members.length < 2) return elements;

  const rowNumbers = [...new Set(members.map((element) => Number(element.imageRowFlowRow) || 0))]
    .sort((a, b) => a - b);
  const firstRow = members.filter((element) => (Number(element.imageRowFlowRow) || 0) === rowNumbers[0]);
  let rowTop = Math.min(...firstRow.map((element) => Number(element.y) || 0));
  const positions = new Map();

  rowNumbers.forEach((rowNumber, rowIndex) => {
    const row = members.filter((element) => (Number(element.imageRowFlowRow) || 0) === rowNumber);
    row.forEach((element) => positions.set(element.id, rowTop));
    const rowHeight = Math.max(...row.map((element) => Number(element.h) || 0));
    // 간격 0(칸끼리 붙인 프레임)은 "값 없음"이 아니라 의도한 값이다 — `||` 로 받으면
    // 0 이 falsy 라 20 으로 되살아나 사진 사이에 틈이 생긴다(오너 8/16 지적의 함정).
    const gapOf = (element) => {
      const raw = Number(element.imageRowFlowGap);
      return Number.isFinite(raw) ? raw : 20;
    };
    const rowGap = rowIndex < rowNumbers.length - 1 ? Math.max(...row.map(gapOf)) : 0;
    rowTop += rowHeight + rowGap;
  });

  return elements.map((element) => (
    positions.has(element.id) ? { ...element, y: positions.get(element.id) } : element
  ));
}

export function fitImageToFrameBlock(block, slotId, image) {
  const slot = block?.elements?.find((element) => element.id === slotId);
  const geometry = fitImageToFrameSlot(slot, image);
  if (!Object.keys(geometry).length) return block;

  let resizedElements = block.elements.map((element) => (
    element.id === slotId ? { ...element, ...geometry } : element
  ));
  resizedElements = reflowImageRows(resizedElements, slot.imageRowFlowGroup);
  if (!slot.imageFlowGroup) return { ...block, elements: resizedElements };

  const resizedSlot = resizedElements.find((element) => element.id === slotId);
  const flowTop = resizedSlot.y + resizedSlot.h + (Number(slot.imageFlowGap) || 20);
  return {
    ...block,
    elements: resizedElements.map((element) => (
      element.id !== slotId && element.imageFlowGroup === slot.imageFlowGroup
        ? { ...element, y: flowTop + (Number(element.imageFlowOffset) || 0) }
        : element
    )),
  };
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
      radius: slot.radius ?? 0,
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
