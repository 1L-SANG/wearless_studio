/* =============================================================
   features/editor/editorLibrary.js — 상세페이지 반복 작업용 로컬 라이브러리.
   DOM/React와 무관한 순수 정의만 두어 클릭 추가와 drag 추가가 같은 결과를 쓴다.
   ============================================================= */

export const WARDROBE_IMAGE_MIME = 'application/x-wearless-image';
export const DEFAULT_BUBBLE_STROKE = '#b9b9be';
export const DEFAULT_BUBBLE_STROKE_WIDTH = 1;
export const DEFAULT_BUBBLE_RADIUS = 45;

const slot = (x, y, w, h, radius = 10) => ({ x, y, w, h, radius });

export const FRAME_LIBRARY_ITEMS = [
  {
    id: 'single', label: '1컷 풀폭', recommended: true, h: 600,
    slots: [slot(40, 40, 920, 520)],
  },
  {
    id: 'split2', label: '2분할', recommended: true, h: 580,
    slots: [slot(40, 60, 450, 460), slot(510, 60, 450, 460)],
  },
  {
    id: 'grid3', label: '3컷 구성', recommended: true, h: 580,
    slots: [slot(40, 60, 293, 460), slot(353, 60, 294, 460), slot(667, 60, 293, 460)],
  },
  {
    id: 'grid4', label: '2 × 2', recommended: true, h: 640,
    slots: [slot(40, 40, 450, 270), slot(510, 40, 450, 270), slot(40, 330, 450, 270), slot(510, 330, 450, 270)],
  },
  {
    id: 'hero2', label: '큰 사진 + 2장', recommended: true, h: 600,
    slots: [slot(40, 50, 580, 500), slot(640, 50, 320, 240), slot(640, 310, 320, 240)],
  },
  {
    id: 'colorcmp', label: '컬러 비교', recommended: true, h: 580,
    slots: [slot(40, 60, 293, 460, 16), slot(353, 60, 294, 460, 16), slot(667, 60, 293, 460, 16)],
  },
  {
    id: 'ba', label: 'Before / After', recommended: false, h: 580,
    slots: [slot(40, 60, 450, 460), slot(510, 60, 450, 460)],
  },
];

export const OBJECT_LIBRARY_ITEMS = [
  { id: 'text-box', label: '반투명 텍스트 박스', preview: 'TEXT' },
  { id: 'single-bubble', label: '말풍선', preview: '말풍선' },
  { id: 'qa-bubbles', label: 'Q&A 말풍선', preview: 'Q · A' },
  { id: 'divider', label: '구분선', preview: '—' },
  { id: 'arrow-callout', label: '화살표 콜아웃', preview: 'POINT →' },
  { id: 'label-badge', label: 'POINT 배지', preview: 'POINT' },
];

const clamp01 = (value) => Math.min(1, Math.max(0, Number.isFinite(Number(value)) ? Number(value) : 1));

export function normalizeHexColor(value) {
  const raw = String(value || '').trim().replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(raw)) {
    return `#${raw.split('').map((character) => character + character).join('').toUpperCase()}`;
  }
  if (/^[0-9a-f]{6}$/i.test(raw)) return `#${raw.toUpperCase()}`;
  return null;
}

export function colorWithOpacity(color = '#ffffff', opacity = 1) {
  const alpha = clamp01(opacity);
  if (alpha >= 1) return color || '#ffffff';
  const raw = String(color || '#ffffff').trim();
  const short = /^#([0-9a-f]{3})$/i.exec(raw);
  const full = /^#([0-9a-f]{6})$/i.exec(raw);
  const hex = short
    ? short[1].split('').map((c) => c + c).join('')
    : full?.[1];
  if (!hex) return raw;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${+alpha.toFixed(3)})`;
}

export function buildFrameBlock(frameOrId, idFn) {
  const definition = typeof frameOrId === 'string'
    ? FRAME_LIBRARY_ITEMS.find((item) => item.id === frameOrId)
    : frameOrId;
  if (!definition || !Array.isArray(definition.slots) || !definition.slots.length) {
    throw new Error(`[editorLibrary] unknown frame: ${typeof frameOrId === 'string' ? frameOrId : frameOrId?.id}`);
  }
  return {
    id: idFn('b'),
    name: definition.label,
    kind: 'styling',
    contentRole: 'custom',
    bg: '#ffffff',
    bgOpacity: 1,
    h: definition.h,
    elements: definition.slots.map((item) => ({
      id: idFn('el'),
      type: 'image',
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      src: null,
      radius: item.radius ?? 10,
      frameSlot: true,
    })),
  };
}

export function buildImageBlock(image, idFn) {
  if (!image?.src) throw new Error('[editorLibrary] image source is required');
  const blockId = idFn('b');
  const sourceWidth = Number(image.width || image.w);
  const sourceHeight = Number(image.height || image.h);
  const imageWidth = 880;
  const imageHeight = sourceWidth > 0 && sourceHeight > 0
    ? Math.max(1, Math.round(imageWidth * sourceHeight / sourceWidth))
    : 1320;
  const element = {
    id: idFn('el'),
    type: 'image',
    x: 60,
    y: 50,
    w: imageWidth,
    h: imageHeight,
    src: image.src,
    radius: 0,
    ...(image.cutType ? { cutType: image.cutType } : {}),
    ...(image.userUploaded ? { userUploaded: true } : {}),
    ...(image.wardrobeGroup ? { wardrobeGroup: image.wardrobeGroup } : {}),
  };
  return {
    id: blockId,
    name: '이미지',
    kind: 'styling',
    contentRole: 'custom',
    bg: '#ffffff',
    bgOpacity: 1,
    h: imageHeight + 100,
    elements: [element],
  };
}

const text = (idFn, groupId, x, y, w, h, value, style = {}) => ({
  id: idFn('el'), type: 'text', groupId, x, y, w, h, text: value, style,
});
const rect = (idFn, groupId, x, y, w, h, fill, radius, opacity = 1) => ({
  id: idFn('el'), type: 'shape', shape: 'rect', groupId, x, y, w, h, fill, radius, opacity,
});
const speechBubble = (idFn, groupId, x, y, w, h, value, style, fill, bubbleFit, flipX = false) => ({
  id: idFn('el'), type: 'text', shape: 'bubble', groupId, x, y, w, h, text: value, style, fill,
  stroke: DEFAULT_BUBBLE_STROKE, strokeWidth: DEFAULT_BUBBLE_STROKE_WIDTH, radius: DEFAULT_BUBBLE_RADIUS,
  bubbleFit, ...(flipX ? { flipX: true } : {}),
});
const line = (idFn, groupId, x, y, w, shape = 'line') => ({
  id: idFn('el'), type: 'line', shape, groupId, x, y, w, h: 24, stroke: '#0e0d14', strokeWidth: 3,
});

export function buildObjectPreset(presetId, { x = 120, y = 120, idFn }) {
  if (!OBJECT_LIBRARY_ITEMS.some((item) => item.id === presetId)) {
    throw new Error(`[editorLibrary] unknown object preset: ${presetId}`);
  }
  const groupId = idFn('grp');
  let elements;
  if (presetId === 'text-box') {
    elements = [
      rect(idFn, groupId, x, y, 520, 150, '#0e0d14', 18, 0.94),
      text(idFn, groupId, x + 30, y + 38, 460, 72, '강조할 내용을 입력하세요', { size: 26, weight: 600, color: '#ffffff', lineHeight: 36 }),
    ];
  } else if (presetId === 'single-bubble') {
    elements = [
      {
        ...speechBubble(idFn, groupId, x, y, 320, 100, '내용을 입력하세요',
          { size: 20, weight: 500, color: '#000000', lineHeight: 29 }, '#FFFFFF',
          { maxWidth: 560, padX: 24, padTop: 20, padBottom: 38, anchor: 'left' }),
        stroke: '#000000',
        strokeWidth: 2,
        radius: 28,
      },
    ];
  } else if (presetId === 'qa-bubbles') {
    elements = [
      speechBubble(idFn, groupId, x, y, 380, 104, 'Q. 가장 궁금한 점은?',
        { size: 20, weight: 600, color: '#0e0d14' }, '#ffffff',
        { maxWidth: 620, padX: 24, padTop: 22, padBottom: 42, anchor: 'left' }),
      speechBubble(idFn, groupId, x + 110, y + 112, 520, 142, 'A. 답변을 간결하게 입력하세요.',
        { size: 19, weight: 500, color: '#0e0d14', lineHeight: 29 }, '#dcecff',
        { maxWidth: 660, padX: 30, padTop: 26, padBottom: 50, anchor: 'right' }, true),
    ];
  } else if (presetId === 'divider') {
    elements = [line(idFn, groupId, x, y + 12, 620)];
  } else if (presetId === 'arrow-callout') {
    elements = [
      text(idFn, groupId, x, y, 180, 36, 'POINT', { size: 22, weight: 700, color: '#0e0d14', tracking: 1 }),
      line(idFn, groupId, x + 150, y + 5, 240, 'arrow-r'),
    ];
  } else {
    elements = [
      rect(idFn, groupId, x, y, 190, 62, '#0e0d14', 31),
      text(idFn, groupId, x + 20, y + 18, 150, 28, 'POINT', { size: 18, weight: 700, color: '#ffffff', align: 'center', tracking: 1.5 }),
    ];
  }
  const minX = Math.min(...elements.map((element) => element.x));
  const maxX = Math.max(...elements.map((element) => element.x + element.w));
  const minY = Math.min(...elements.map((element) => element.y));
  const shiftX = minX < 0 ? -minX : maxX > 1000 ? 1000 - maxX : 0;
  const shiftY = minY < 0 ? -minY : 0;
  return elements.map((element) => ({
    ...element,
    x: element.x + shiftX,
    y: element.y + shiftY,
    libraryItemId: presetId,
  }));
}

export function encodeWardrobeImage(image, dimensions = {}) {
  const payload = { src: image?.src || null, cutType: image?.cutType || null };
  if (image?.id) payload.id = image.id;
  if (image?.userUploaded) payload.userUploaded = true;
  if (image?.wardrobeGroup) payload.wardrobeGroup = image.wardrobeGroup;
  const width = Number(dimensions.width || image?.width);
  const height = Number(dimensions.height || image?.height);
  if (width > 0) payload.width = width;
  if (height > 0) payload.height = height;
  return JSON.stringify(payload);
}

export function decodeWardrobeImage(value) {
  try {
    const parsed = JSON.parse(value || '');
    if (!parsed || typeof parsed.src !== 'string' || !parsed.src) return null;
    const image = { src: parsed.src, cutType: parsed.cutType || null };
    if (parsed.id) image.id = parsed.id;
    if (parsed.userUploaded) image.userUploaded = true;
    if (parsed.wardrobeGroup) image.wardrobeGroup = parsed.wardrobeGroup;
    if (Number(parsed.width) > 0) image.width = Number(parsed.width);
    if (Number(parsed.height) > 0) image.height = Number(parsed.height);
    return image;
  } catch {
    return null;
  }
}
