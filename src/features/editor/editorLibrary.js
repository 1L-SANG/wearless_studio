/* =============================================================
   features/editor/editorLibrary.js — 상세페이지 반복 작업용 로컬 라이브러리.
   DOM/React와 무관한 순수 정의만 두어 클릭 추가와 drag 추가가 같은 결과를 쓴다.
   ============================================================= */

export const WARDROBE_IMAGE_MIME = 'application/x-wearless-image';
export const DEFAULT_BUBBLE_STROKE = '#b9b9be';
export const DEFAULT_BUBBLE_STROKE_WIDTH = 1;
export const DEFAULT_BUBBLE_RADIUS = 45;

const templateExample = (id) => `/assets/editor/kiwi-examples/kiwi-${id}.jpg`;

const slot = (x, y, w, h, radius = 10) => ({ x, y, w, h, radius });

const templatePhoto = (x, y, w, h, options = {}) => ({
  type: 'image', x, y, w, h, src: options.example || null, frameSlot: true, checkerboard: true,
  radius: options.radius === 'pill' ? Math.round(Math.min(w, h) / 2) : (options.radius || 0),
  ...(options.example ? { exampleImage: true } : {}),
  ...(options.stroke ? { stroke: options.stroke, strokeWidth: options.strokeWidth || 2 } : {}),
  ...(options.dash ? { dash: options.dash } : {}),
  ...(options.rotate ? { rotate: options.rotate } : {}),
  ...(options.fit ? { fit: options.fit } : {}),
});
const templateText = (x, y, w, h, value, style = {}) => ({
  type: 'text', x, y, w, h, text: value, fullTextHitArea: true,
  style: {
    font: style.font || 'Pretendard', size: style.size || 28, weight: style.weight || 400,
    color: style.color || '#0e0d14', lineHeight: style.lineHeight || Math.round((style.size || 28) * 1.4),
    align: style.align || 'left', tracking: style.tracking || 0,
  },
});
const templateShape = (x, y, w, h, fill, options = {}) => ({
  type: 'shape', shape: options.shape || 'rect', x, y, w, h, fill,
  radius: options.radius || 0, opacity: options.opacity ?? 1,
  ...(options.stroke ? { stroke: options.stroke, strokeWidth: options.strokeWidth || 2 } : {}),
  ...(options.dash ? { dash: options.dash } : {}),
  ...(options.rotate ? { rotate: options.rotate } : {}),
});
const templateLine = (x, y, w, options = {}) => ({
  type: 'line', shape: options.shape || 'line', x, y, w, h: 24,
  stroke: options.stroke || '#0e0d14', strokeWidth: options.strokeWidth || 2,
  dash: options.dash || 'solid', ...(options.rotate ? { rotate: options.rotate } : {}),
});
const kiwiTemplate = ({ id, label, h, bg, elements }) => ({
  id: `kiwi-${id}`,
  label,
  recommended: false,
  template: true,
  bg,
  h,
  elements,
  slots: elements.filter((element) => element.type === 'image' && element.frameSlot),
  // The supplied artwork is a catalog thumbnail only. Inserted blocks are native elements.
  preview: `/assets/editor/kiwi-templates/kiwi-${id}-overlay.png`,
});

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
  kiwiTemplate({
    id: '1', label: '리뷰 카드', h: 1460, bg: '#f3f1ee', elements: [
      templateText(300, 150, 400, 48, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(220, 235, 560, 110, 'Reviews', { font: 'Cormorant', size: 88, weight: 700, align: 'center' }),
      templateText(150, 390, 700, 90, '편안한 착용감과 감각적인 실루엣을\n직접 경험한 고객 후기를 소개합니다.', { size: 30, color: '#666666', lineHeight: 42, align: 'center' }),
      ...[
        [82, 570, -2, '가볍고 시원해서 한여름에도\n답답함 없이 잘 입고 있어요.'],
        [525, 570, 2, '허리 밴딩이 편안해서\n데일리룩으로 딱이에요.'],
        [82, 955, 2, '체형에 자연스럽게 어울리고\n편하게 움직일 수 있어요.'],
        [525, 955, -2, '세탁 후에도 변형이 적어서\n관리하기 쉬웠어요.'],
      ].flatMap(([x, y, rotate, copy]) => [
        templateShape(x, y, 390, 320, '#ffffff', { radius: 8, rotate }),
        templatePhoto(x + 48, y - 36, 126, 126, { radius: 'pill', ...(x === 82 && y === 570 ? { example: templateExample(1) } : {}) }),
        templateText(x + 48, y + 125, 300, 130, copy, { size: 23, lineHeight: 35 }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '2', label: '룩북 콜라주', h: 1498, bg: '#f4f2ef', elements: [
      templateText(300, 125, 400, 48, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(220, 220, 560, 115, '#Look01', { font: 'Cormorant', size: 88, weight: 700, align: 'center' }),
      templateText(135, 370, 730, 92, '하루를 특별하게 만드는 데일리 스타일을\n한 장의 룩북으로 만나보세요.', { size: 30, color: '#666666', lineHeight: 42, align: 'center' }),
      templateShape(164, 540, 500, 650, '#ffffff', { radius: 6 }),
      templatePhoto(180, 563, 466, 609, { example: templateExample(2) }),
      templateShape(533, 625, 200, 180, '#f57d9c', { radius: 4, rotate: 2 }),
      templateText(560, 665, 150, 95, 'Daily\nLook', { font: 'Cormorant', size: 36, lineHeight: 43, align: 'center' }),
      templatePhoto(0, 1010, 258, 488, { fit: 'contain' }),
      templatePhoto(258, 1010, 364, 488, { fit: 'contain' }),
      templatePhoto(622, 850, 378, 648, { fit: 'contain' }),
    ],
  }),
  kiwiTemplate({
    id: '3', label: '스타일 노트', h: 1580, bg: '#f6fbff', elements: [
      templateText(300, 80, 400, 52, "Our Brand's", { font: 'Cormorant', size: 36, align: 'center' }),
      templateText(180, 170, 640, 110, 'Style Note', { font: 'Cormorant', size: 86, weight: 700, align: 'center' }),
      templateText(175, 320, 650, 55, '하루를 특별하게 만들어주는 스타일링', { size: 30, color: '#666666', align: 'center' }),
      templateShape(145, 460, 725, 910, '#ffffff', { radius: 4, rotate: -2 }),
      templatePhoto(166, 489, 683, 851, { example: templateExample(3) }),
      templateShape(744, 650, 205, 185, '#eafa72', { radius: 3, rotate: 1 }),
      templateText(775, 705, 150, 90, 'Daily\nLook', { font: 'Cormorant', size: 35, lineHeight: 42, align: 'center' }),
      templateShape(76, 1168, 388, 215, '#2a1c12', { radius: 4, rotate: -7 }),
      templatePhoto(103, 1186, 346, 174, { rotate: -7 }),
    ],
  }),
  kiwiTemplate({
    id: '4', label: '썸머 이벤트', h: 1610, bg: '#f4f4f4', elements: [
      templateText(300, 12, 400, 45, 'SUMMER', { size: 26, weight: 500, tracking: 5, align: 'center' }),
      templateText(235, 54, 530, 95, 'EVENT', { font: 'Cormorant', size: 82, weight: 700, align: 'center' }),
      templateShape(90, 225, 780, 1020, '#ffffff', { radius: 38 }),
      templatePhoto(125, 266, 85, 85, { radius: 'pill' }),
      templateText(225, 280, 260, 45, '브랜드 이름', { size: 28, weight: 700 }),
      templatePhoto(127, 371, 744, 736, { example: templateExample(4) }),
      templatePhoto(597, 905, 323, 325),
      templateText(125, 1130, 500, 45, '♥  ○  △', { size: 34 }),
      templateText(125, 1190, 700, 45, '#여름패션  #퀄리티보장', { size: 26 }),
      templateText(145, 1345, 710, 70, '상품 리뷰 추첨 이벤트!', { size: 46, weight: 700, align: 'center' }),
      templateText(190, 1430, 620, 45, '07.07(MON) ~ 07.13(SUN)', { size: 24, color: '#999999', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '5', label: '컬러 비교 카드', h: 1407, bg: '#eef8fb', elements: [
      templateText(300, 90, 400, 85, 'COLOR', { size: 54, weight: 700, tracking: 2, align: 'center' }),
      ...['#ffffff', '#328cca', '#3c4f8b', '#111111'].map((fill, index) => templateShape(385 + index * 62, 190, 50, 50, fill, { shape: 'circle', stroke: '#dddddd', strokeWidth: 1 })),
      ...[
        [100, 310, 'WHITE'], [525, 310, 'BEIGE'], [100, 797, 'KHAKI'], [525, 797, 'BLACK'],
      ].flatMap(([x, y, label]) => [
        templateShape(x, y, 375, 438, '#ffffff', { radius: 4 }),
        templatePhoto(x + 24, y + 28, 328, 328, { fit: 'contain', ...(x === 100 && y === 310 ? { example: templateExample(5) } : {}) }),
        templateText(x + 40, y + 382, 295, 40, label, { size: 28, align: 'center' }),
      ]),
    ],
  }),
  kiwiTemplate({
    id: '10', label: 'SNS 상품 카드', h: 1510, bg: '#d5d5d5', elements: [
      templatePhoto(0, 0, 1000, 1510),
      templateShape(0, 0, 1000, 285, '#111111', { opacity: 0.42 }),
      templateText(320, 45, 360, 58, 'Product Name', { font: 'Cormorant', size: 46, weight: 700, color: '#ffffff', align: 'center' }),
      templateText(360, 120, 280, 80, 'Information\nInformation', { size: 26, color: '#ffffff', lineHeight: 38, align: 'center' }),
      templateShape(140, 284, 720, 1120, '#ffffff', { radius: 42 }),
      templatePhoto(176, 323, 103, 103, { radius: 'pill' }),
      templateText(310, 343, 360, 40, 'Product Name', { size: 27, weight: 700 }),
      templateText(310, 386, 360, 35, 'Product Information', { size: 22, color: '#aaaaaa' }),
      templateText(765, 354, 55, 30, '•••', { size: 30, weight: 700, align: 'center' }),
      templatePhoto(175, 444, 680, 710, { example: templateExample(10) }),
      templateText(175, 1185, 650, 40, '♥   ○   △                                      ▮', { size: 30 }),
      templateText(175, 1240, 650, 38, '2,860,606 likes.', { size: 24, weight: 600 }),
      templateText(175, 1280, 650, 70, 'Product Name  데일리하게 입기 좋은 아이템!', { size: 23, lineHeight: 34 }),
      templateText(175, 1350, 650, 42, '#베이직아이템  #니트  #데일리룩', { size: 24, weight: 700 }),
    ],
  }),
  kiwiTemplate({
    id: '11', label: '체크 포인트', h: 1248, bg: '#dcc4be', elements: [
      templateText(275, 45, 450, 60, 'CHECK POINT', { font: 'Cormorant', size: 48, weight: 700, color: '#ffffff', align: 'center' }),
      templatePhoto(120, 110, 768, 1018, { radius: 'pill' }),
      templateShape(675, 345, 300, 92, '#ffffff', { radius: 24 }),
      templateText(700, 366, 245, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(118, 655, 305, 92, '#ffffff', { radius: 24 }),
      templateText(143, 676, 250, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(477, 952, 310, 92, '#ffffff', { radius: 24 }),
      templateText(502, 973, 255, 58, '상품의 특징을 2줄 이내로\n적어주세요.', { size: 21, lineHeight: 28 }),
      templateShape(685, 195, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
      templateShape(300, 505, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
      templateShape(600, 805, 120, 120, 'transparent', { shape: 'circle', stroke: '#ffffff', strokeWidth: 4, opacity: 0.85 }),
    ],
  }),
  kiwiTemplate({
    id: '12', label: '핫 키워드', h: 1335, bg: '#222224', elements: [
      templateText(55, 35, 390, 100, '지금 가장 떠오르는\n패션 핫 키워드', { size: 30, weight: 500, color: '#ffffff', lineHeight: 42 }),
      templateShape(480, 0, 520, 155, 'transparent', { radius: 260, stroke: '#ffffff', strokeWidth: 1 }),
      templateText(610, 20, 300, 120, 'HOT\nKEYWORD', { size: 47, weight: 700, color: '#ffffff', lineHeight: 55, align: 'center' }),
      templateLine(0, 145, 1000, { stroke: '#dddddd', strokeWidth: 1 }),
      templateText(55, 210, 650, 80, 'KIWI TREND', { size: 66, weight: 700, color: '#ffffff' }),
      templateText(55, 285, 700, 85, 'HOT SUMMER', { size: 66, weight: 700, color: '#70e978' }),
      templatePhoto(55, 435, 428, 651, { radius: 'pill', example: templateExample(12) }),
      templatePhoto(508, 435, 430, 651, { radius: 'pill' }),
      templateText(55, 1185, 300, 75, '23 SS', { size: 70, weight: 700, color: '#ffffff' }),
      templateText(650, 1215, 300, 45, "BRAND'S PICK", { size: 34, color: '#ffffff', align: 'right' }),
    ],
  }),
  kiwiTemplate({
    id: '13', label: '빅 세일 이벤트', h: 1647, bg: '#fbf9e9', elements: [
      templateText(285, 205, 430, 62, 'Summer', { font: 'Cormorant', size: 52, weight: 600, color: '#45744b', align: 'center' }),
      templateText(150, 270, 700, 180, 'BIG SALE\nEVENT', { font: 'Cormorant', size: 92, weight: 700, color: '#45744b', lineHeight: 95, align: 'center' }),
      templatePhoto(98, 425, 814, 890, { radius: 'pill', example: templateExample(13) }),
      templateShape(0, 1290, 1000, 357, '#9be5df', { opacity: 0.9 }),
      templateText(195, 1510, 610, 55, '여름 시즌 특별 할인 이벤트', { size: 34, weight: 600, color: '#356b54', align: 'center' }),
    ],
  }),
  kiwiTemplate({
    id: '14', label: '패션 폴라로이드', h: 1508, bg: '#222222', elements: [
      templatePhoto(0, 0, 1000, 1508),
      templateShape(615, 120, 310, 350, '#ffffff', { radius: 3, rotate: 20 }),
      templatePhoto(645, 155, 260, 278, { rotate: 20, example: templateExample(14) }),
      templateShape(535, 510, 335, 430, '#ffffff', { radius: 3, rotate: -14 }),
      templatePhoto(575, 550, 270, 365, { rotate: -14 }),
      templateShape(685, 980, 285, 380, '#ffffff', { radius: 3, rotate: 14 }),
      templatePhoto(725, 1020, 232, 324, { rotate: 14 }),
      templateText(65, 70, 540, 80, 'Brand Fashion', { size: 58, weight: 700, color: '#ffffff' }),
      templateText(55, 1030, 470, 250, 'Modern\nChic', { font: 'Cormorant', size: 92, weight: 700, color: '#ffffff', lineHeight: 90 }),
    ],
  }),
  kiwiTemplate({
    id: '15', label: '디테일 콜아웃', h: 1336, bg: '#eeeeee', elements: [
      templatePhoto(0, 0, 1000, 1336),
      templateText(55, 45, 430, 72, 'CHECK POINT', { font: 'Cormorant', size: 48, weight: 700, color: '#ffffff' }),
      templatePhoto(150, 265, 245, 245, { radius: 'pill', stroke: '#ffffff', strokeWidth: 5, dash: 'dashed', example: templateExample(15) }),
      templateShape(405, 366, 375, 66, '#fff7f4', { radius: 2 }),
      templateText(425, 382, 335, 42, '어깨라인이 살아있는 디자인', { size: 24, align: 'center' }),
      templatePhoto(520, 750, 315, 315, { radius: 'pill', stroke: '#ffffff', strokeWidth: 5, dash: 'dashed' }),
      templateShape(485, 1085, 365, 66, '#fff7f4', { radius: 2 }),
      templateText(505, 1101, 325, 42, '허리라인 위로 핀턱 디테일', { size: 24, align: 'center' }),
    ],
  }),
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
  const sourceElements = definition.elements || definition.slots.map((item) => ({
    ...item,
    type: 'image',
    src: null,
    frameSlot: true,
  }));
  return {
    id: idFn('b'),
    name: definition.label,
    ...(definition.template ? { templateId: definition.id } : {}),
    kind: 'styling',
    contentRole: 'custom',
    bg: definition.bg || '#ffffff',
    bgOpacity: 1,
    h: definition.h,
    elements: sourceElements.map((item) => ({
      id: idFn('el'),
      ...item,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      ...(item.type === 'image' ? {
        src: item.src || null,
        radius: item.radius ?? 10,
        frameSlot: Boolean(item.frameSlot),
      } : {}),
    })),
  };
}

const LEGACY_KIWI_OVERLAY = /\/kiwi-(\d+)-overlay\.png(?:\?.*)?$/;

function copyLegacySlotSource(slot, legacySlot) {
  if (!legacySlot?.src) return slot.exampleImage ? { ...slot, src: null } : slot;
  const crop = legacySlot.crop && legacySlot.w && legacySlot.h ? {
    ox: Math.round(legacySlot.crop.ox * slot.w / legacySlot.w),
    oy: Math.round(legacySlot.crop.oy * slot.h / legacySlot.h),
    iw: Math.round(legacySlot.crop.iw * slot.w / legacySlot.w),
    ih: Math.round(legacySlot.crop.ih * slot.h / legacySlot.h),
  } : undefined;
  return {
    ...slot,
    src: legacySlot.src,
    ...(crop ? { crop } : {}),
    ...(legacySlot.cutType ? { cutType: legacySlot.cutType } : {}),
    ...(legacySlot.userUploaded ? { userUploaded: true } : {}),
    ...(legacySlot.wardrobeGroup ? { wardrobeGroup: legacySlot.wardrobeGroup } : {}),
    ...(legacySlot.hidden ? { hidden: true } : {}),
    ...(legacySlot.opacity != null ? { opacity: legacySlot.opacity } : {}),
  };
}

/**
 * Early Kiwi frames were saved as editable photo slots plus a locked screenshot.
 * Replace those blocks with native editor elements while preserving filled photos.
 */
export function upgradeLegacyKiwiTemplateBlocks(blocks, idFn) {
  if (!Array.isArray(blocks)) return blocks;
  return blocks.map((block) => {
    const legacyOverlay = block?.elements?.find((element) => (
      element.type === 'template-overlay' && LEGACY_KIWI_OVERLAY.test(String(element.src || ''))
    ));
    const templateNumber = legacyOverlay
      ? LEGACY_KIWI_OVERLAY.exec(String(legacyOverlay.src || ''))?.[1]
      : null;
    const definition = templateNumber
      ? FRAME_LIBRARY_ITEMS.find((item) => item.id === `kiwi-${templateNumber}`)
      : null;
    if (!definition) return block;

    const legacySlots = block.elements.filter((element) => element.type === 'image' && element.frameSlot);
    let slotIndex = 0;
    const upgraded = buildFrameBlock(definition, idFn);
    return {
      ...upgraded,
      id: block.id,
      h: Math.max(upgraded.h, Number(block.h) || 0),
      elements: upgraded.elements.map((element) => {
        if (element.type !== 'image' || !element.frameSlot) return element;
        return copyLegacySlotSource(element, legacySlots[slotIndex++]);
      }),
    };
  });
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
  const groupId = presetId === 'qa-bubbles' ? null : idFn('grp');
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
      speechBubble(idFn, idFn('grp'), x, y, 380, 104, 'Q. 가장 궁금한 점은?',
        { size: 20, weight: 600, color: '#0e0d14' }, '#ffffff',
        { maxWidth: 620, padX: 24, padTop: 22, padBottom: 42, anchor: 'left' }),
      speechBubble(idFn, idFn('grp'), x + 110, y + 112, 520, 142, 'A. 답변을 간결하게 입력하세요.',
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

export function objectPresetInitialSelectionIds(presetId, elements) {
  const ids = (elements || []).map((element) => element.id).filter(Boolean);
  return presetId === 'qa-bubbles' ? ids.slice(0, 1) : ids;
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
